"""Free, key-less footage/photo sources for the factory (no Claude, no sign-up).
Wikimedia Commons (images, public domain / CC-BY / CC-BY-SA only, credit collected), NASA Image Library (public domain),
Library of Congress is added later. Everything is cached in factory/cache/ so a query is fetched once.
search_images(query, n) -> list of dict(path, credit, source, w, h)."""
import hashlib
import json
import re
import time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "factory" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "VideoFactory/1.0 (documentary video tool, low-rate, honours Retry-After)"}
OK_LIC = re.compile(r"public domain|pd|cc0|cc[- ]by(?![- ]nc)|cc[- ]by-sa|attribution|no known copyright", re.I)
BAD_LIC = re.compile(r"\bnc\b|non-?commercial|noderiv|\bnd\b|fair use|copyrighted|all rights", re.I)

BAD_NAME = re.compile(r"banknote|\bTL\b|stamp|coin|signature|autograph|poster|flag|seal of|emblem|\.(svg|gif|tif|tiff|pdf|djvu|webm|ogv|ogg|mp3|wav)$|logo|flag of|coat of arms|map of|locator|diagram|icon", re.I)


_last = [0.0]


def _get(url, params=None, tries=5):
    """Polite GET: >=1.2 s between requests, honours Retry-After on 429 (Wikimedia throttles bursts)."""
    for i in range(tries):
        wait = 1.2 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            _last[0] = time.time()
            if r.status_code == 200:
                return r
            if r.status_code == 429:
                time.sleep(min(int(r.headers.get("Retry-After", 20 * (i + 1))), 90))
            else:
                time.sleep(3 + 3 * i)
        except requests.RequestException:
            time.sleep(3 + 3 * i)
    return None


def _strip(h):
    return re.sub(r"<[^>]+>", "", h or "").strip()


def wikimedia(query: str, n: int = 6, min_w: int = 1400) -> list:
    r = _get("https://commons.wikimedia.org/w/api.php", dict(
        action="query", format="json", generator="search", gsrnamespace=6, gsrsearch=query, gsrlimit=max(n * 4, 12),
        prop="imageinfo", iiprop="url|size|mime|extmetadata", iiurlwidth=1920))
    out = []
    if not r:
        return out
    pages = (r.json().get("query") or {}).get("pages") or {}
    for p in sorted(pages.values(), key=lambda x: x.get("index", 0)):
        ii = (p.get("imageinfo") or [{}])[0]
        if not ii.get("thumburl") or ii.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if BAD_NAME.search(p.get("title", "")) or ii.get("width", 0) < min_w or ii.get("width", 0) < ii.get("height", 1) * 1.1:
            continue                                   # landscape, big enough, not a logo/diagram
        md = ii.get("extmetadata", {})
        lic = _strip((md.get("LicenseShortName") or {}).get("value"))
        if not lic or BAD_LIC.search(lic) or not OK_LIC.search(lic):
            continue
        who = _strip((md.get("Artist") or {}).get("value"))[:80] or "Wikimedia Commons"
        out.append(dict(title=p["title"], ow=ii.get("width", 0), url=ii["thumburl"], credit=f"{p['title'][5:]} - {who} - {lic} - Wikimedia Commons", source="wikimedia",
                        w=ii.get("thumbwidth"), h=ii.get("thumbheight"), id="wm" + str(p.get("pageid"))))
        if len(out) >= n:
            break
    return out


def wikipedia(query: str, n: int = 6, min_w: int = 700) -> list:
    """Photos from the Wikipedia article that best matches the query (high relevance for named people/places/events)."""
    r = _get("https://en.wikipedia.org/w/api.php", dict(action="query", format="json", list="search", srsearch=query, srlimit=1))
    hits = ((r.json().get("query") or {}).get("search") or []) if r else []
    if not hits:
        return []
    title = hits[0]["title"]
    r = _get("https://en.wikipedia.org/w/api.php", dict(action="query", format="json", generator="images", titles=title, gimlimit=40))
    files = [p["title"] for p in ((r.json().get("query") or {}).get("pages") or {}).values()] if r else []
    files = [f for f in files if not BAD_NAME.search(f)]
    out = []
    for i in range(0, len(files), 20):
        r = _get("https://commons.wikimedia.org/w/api.php", dict(
            action="query", format="json", titles="|".join(files[i:i + 20]), prop="imageinfo", iiprop="url|size|mime|extmetadata", iiurlwidth=1920))
        for p in ((r.json().get("query") or {}).get("pages") or {}).values() if r else []:
            ii = (p.get("imageinfo") or [{}])[0]
            if not ii.get("thumburl") or ii.get("mime") not in ("image/jpeg", "image/png") or ii.get("width", 0) < min_w:
                continue
            md = ii.get("extmetadata", {})
            lic = _strip((md.get("LicenseShortName") or {}).get("value"))
            if not lic or BAD_LIC.search(lic) or not OK_LIC.search(lic):
                continue
            who = _strip((md.get("Artist") or {}).get("value"))[:80] or "Wikimedia Commons"
            out.append(dict(title=p["title"], ow=ii.get("width", 0), url=ii["thumburl"], credit=f"{p['title'][5:]} - {who} - {lic} - Wikimedia Commons", source="wikipedia",
                            w=ii.get("thumbwidth"), h=ii.get("thumbheight"), id="wp" + str(p.get("pageid")),
                            land=ii.get("width", 1) >= ii.get("height", 1)))
    qw = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", query)]
    def score(x):                                              # title words from the query first, landscape next
        t = x["credit"].lower()
        return (-sum(w in t for w in qw), not x["land"])
    out.sort(key=score)
    return out[:n]


def nasa(query: str, n: int = 6) -> list:
    r = _get("https://images-api.nasa.gov/search", dict(q=query, media_type="image"))
    out = []
    for it in ((r.json().get("collection") or {}).get("items") or [])[:n * 3] if r else []:
        d = (it.get("data") or [{}])[0]
        links = it.get("links") or []
        if not links:
            continue
        url = links[0]["href"].replace("~thumb", "~large") if "~thumb" in links[0]["href"] else links[0]["href"]
        out.append(dict(url=url, credit=f"{d.get('title', '')[:80]} - NASA ({d.get('nasa_id')})", source="nasa", w=1600, h=900, id="na" + str(d.get("nasa_id"))))
        if len(out) >= n:
            break
    return out

BAD_PHOTO = re.compile(r"obama|trump|biden|bush|president|diver|snorkel|pmnm|noaa|visitor|tourist|today|modern|premiere|actor|actress|cast of|film still|movie|\bISS\d|astronaut|expedition \d|starr-|fws |albatross|wildlife|laysan|(?<!\d)\d{6}-N-\d|\b(grave|tomb|gravestone|cemetery|memorial|monument|statue|museum|replica|model|cosplay|reenact\w*|stamp|banknote|coin)\b", re.I)

_PH_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20[0-3]\d)(?!\d)")


def _photo_bad(x):

    t = _title(x.get("credit", ""))

    return bool(BAD_PHOTO.search(t) or _PH_YEAR.search(t))


def photo_relevant(query: str, item: dict) -> bool:
    """Photo title must share a real keyword (prefix-matched) with the query; stops random modern pictures sneaking in as fallback."""
    words = [w.lower() for w in re.findall(r"[A-Za-z']{4,}", query) if w.lower() not in STOP and w.lower() not in GENERIC][:4]
    t = _title(item.get("credit", "")).lower()
    cr = item.get("credit", "")
    pd_ = bool(re.search(r"public domain|cc0|no known", cr, re.I)) or bool(re.search(r"(?<!\d)(1[0-8]\d\d|19[0-4]\d)(?!\d)", t))
    return pd_ and (not words or any(w[:5] in t for w in words))      # period picture (PD / dated before 1950) that names the subject


def search_images(query: str, n: int = 6, sources=("wikipedia", "wikimedia")) -> list:
    """Download (cached) up to n images for the query; returns dicts with local path."""
    key = hashlib.md5((query + "|" + ",".join(sources)).encode()).hexdigest()[:12]
    idx = CACHE / f"q_{key}.json"
    if idx.exists():
        res = json.loads(idx.read_text(encoding="utf-8"))

        res = [x for x in res if Path(x["path"]).exists() and not _photo_bad(x)]
        if res:
            return res[:n]
    res = []
    for s in sources:
        for it in {"wikimedia": wikimedia, "wikipedia": wikipedia, "nasa": nasa}[s](query, n):
            p = CACHE / f"{it['id']}.jpg"
            if not p.exists():
                if it.get("title") and "/thumb/" not in it["url"]:      # original file (smaller than 1920): use the API's 1280 thumbnail
                    if it.get("ow", 0) <= 1300:
                        continue
                    rr = _get("https://commons.wikimedia.org/w/api.php", dict(action="query", format="json", titles=it["title"],
                                                                              prop="imageinfo", iiprop="url", iiurlwidth=1280))
                    try:
                        it["url"] = list((rr.json()["query"]["pages"]).values())[0]["imageinfo"][0]["thumburl"]
                    except Exception:  # noqa: BLE001
                        continue
                    if "/thumb" not in it["url"]:
                        continue
                r = _get(it["url"])
                if not r or len(r.content) < 20000:
                    continue
                p.write_bytes(r.content)
            it["path"] = str(p)
            res.append(it)
    if res:
        idx.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")

    return [x for x in res if not _photo_bad(x)][:n]


# ============================================================================= MOVING VIDEO (free / legal, keyless)
# search_clips(query, n) -> [dict(id, url, length, credit, source, ref)]; clips are NOT downloaded whole: core cuts 3-6 s
# windows straight from the url with ffmpeg (HTTP range reads), so a 2 h archive.org film costs only the seconds used.
# Pexels/Pixabay: only if PEXELS_API_KEY / PIXABAY_API_KEY exist in the environment (none in repo today -> skipped, no sign-up).
import os

IA_OK_LIC = re.compile(r"publicdomain|/zero/|cc0|/by/|/by-sa/", re.I)
IA_BAD_TITLE = re.compile(r"castle films|teaser|lecture|interview|conference|tutorial|webinar|presentation|speech|trailer|full movie|episode|season|porn|sex|nude|erotic", re.I)
# footage that carries its own logo / captions / AI-slop, or is modern politics: never usable as documentary b-roll
BAD_CLIP = re.compile(r"terra x|zdf|bilderfest|bartholomai|messter|thessaloniki 1911|monde en vid|road to liberty|haptic lab|kite|protest|demonstrat|water cannon|snow cannon|rally|election|campaign|tiktok|screen ?recording|slideshow|"

                      r"gameplay|minecraft|animation test|logo|intro|outro|karaoke|lyrics|\bAI\b generated", re.I)
STOP = set("the of and a an in on at to for with from by is was were history story true what why how real".split())


def _kw(q, k=4):
    ws = [w for w in re.findall(r"[A-Za-z0-9']{3,}", q) if w.lower() not in STOP]
    return ws[:k]

# --- automatic rejection rules for historical documentary b-roll (title/description based; frame-based checks live in core.frame_ok)

BAD_MODERN = re.compile(r"x-47|f-35|f-22|f/a-18|uav|ucav|unmanned|jet\b|osprey|nimitz|cvn|lcs|arrested landing|helicopter|sand art|music|official|song|lyric|sheeran|feat\.|day in the life|explained|how to|navy history|operation .* (ex|exercise)|albatross|bird|wildlife|animal|fish|coral|sand dollar|cilia|plant|flower|insect|whale|dolphin|turtle|seal\b|interview|press conference|meeting with|talk show|panel|webinar|vlog|reaction|covid|corona|pandemic|\bmask|drone|4k|uhd|hdr|gopro|timelapse|time-lapse|"

                        r"selfie|tourist|walking tour|dashcam|livestream|news report|prime minister|president|patriarch|synagogue service|prayer|mass in|concert|"

                        r"double exposure|overlay|superimpos|dissolve|montage|collage|slideshow|ai[- ]generated|simulation|render(ing)?\b|3d|360", re.I)

BAD_AERIAL = re.compile(r"aerial|from above|bird'?s? eye|flyover|fly-over|overflight|helicopter view|satellite", re.I)

YEAR = re.compile(r"(?<!\d)(19[89]\d|20[0-3]\d)(?!\d)")

GENERIC = set("footage film video clip clips newsreel documentary history historical view scene scenes old vintage archive archival movie 1930s 1940s".split())


def _title(cr):

    """File title inside a credit string ('<title>.ext - artist - licence - source' or '<title> - Internet Archive ...')."""

    m = re.match(r"(.*?\.(?:webm|ogv|mp4|mpg|jpe?g|png|tiff?))", cr, re.I)

    return m.group(1) if m else cr.split(" - ")[0]


def clip_ok(query: str, src: dict, ctx: str = "") -> bool:

    """False for footage that cannot be period documentary b-roll: logos/politics (BAD_CLIP), modern or talking-head/double-exposure files (BAD_MODERN),

    aerial/drone shots nobody asked for, files dated 1980+ unless the query names that year, and anything whose title shares no real

    keyword with the query (relevance: >= half of the query's meaningful words, prefix-matched, must appear in the clip title)."""

    cr = src.get("credit", "")

    title = _title(cr)

    if BAD_CLIP.search(cr) or BAD_MODERN.search(title):

        return False

    if BAD_AERIAL.search(title) and not BAD_AERIAL.search(query):

        return False

    if any(y not in query for y in YEAR.findall(title)) or re.search(r"(?<!\d)20[0-3]\d[01]\d[0-3]\d(?!\d)", title):

        return False

    nat = re.findall(r"german|japanese|british|american|soviet|russian|italian|french|chinese", title, re.I)
    if nat and any(n.lower() not in (query + " " + ctx).lower() for n in nat):
        return False                                   # wrong side/country (e.g. a German battleship for a Japanese one)
    words = [w.lower() for w in re.findall(r"[A-Za-z']{4,}", query) if w.lower() not in STOP and w.lower() not in GENERIC][:4]

    if not words:

        return True

    tl = title.lower()

    hit = sum(1 for w in words if w[:5] in tl)

    if ctx == "*":                                      # themed b-roll queries: any one real keyword is enough
        return hit >= 1
    return hit >= (len(words) if len(words) <= 2 else -(-len(words) * 3 // 5))


def _cached_json(name, fn):
    p = CACHE / f"{name}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            pass
    v = fn()
    p.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    return v


def _ia_files(ident):
    """Pick the best streamable mp4 (and its length) of an archive.org item via the public metadata API."""
    def go():
        r = _get(f"https://archive.org/metadata/{ident}")
        if not r:
            return {}
        m = r.json()
        files = [f for f in m.get("files", []) if f.get("name", "").lower().endswith((".mp4", ".ogv", ".webm", ".mpeg", ".mpg"))
                 and "thumb" not in f.get("name", "").lower()]
        pref = {"h.264 hd": 0, "h.264": 1, "mpeg4": 2, "512kb mpeg4": 3, "h.264 ia": 1, "ogg video": 6}
        files = [f for f in files if f.get("format", "").lower() in pref]
        if not files:
            return {}
        files.sort(key=lambda f: (pref[f["format"].lower()], -int(f.get("size") or 0)))
        f = files[0]
        try:
            length = float(f.get("length") or 0)
        except ValueError:
            length = 0.0
        md = m.get("metadata", {})
        coll = md.get("collection") or []
        return dict(name=f["name"], length=length, title=str(md.get("title", ident))[:80], lic=str(md.get("licenseurl", "")),
                    coll=coll if isinstance(coll, list) else [coll])
    return _cached_json("iaf_" + hashlib.md5(ident.encode()).hexdigest()[:12], go)


def archive_video(query: str, n: int = 4) -> list:
    ws = _kw(query, 3)
    if not ws:
        return []
    phrase = " ".join(ws)
    fieldq = lambda t: f'(title:({t}) OR subject:({t}))'
    qs = [fieldq('"' + phrase + '"') if len(ws) > 1 else fieldq(ws[0]), fieldq(" AND ".join(ws))]
    out, seen = [], set()
    for sq in qs:
        full = f'{sq} AND mediatype:movies AND (collection:prelinger OR collection:nasa OR collection:usgovfilms OR collection:fedflix)'
        r = _get("https://archive.org/advancedsearch.php", [("q", full), ("fl[]", "identifier"), ("fl[]", "title"), ("rows", 12),
                                                            ("output", "json"), ("sort[]", "downloads desc")])
        for d in ((r.json().get("response") or {}).get("docs") or []) if r else []:
            ident = d["identifier"]
            if ident in seen or IA_BAD_TITLE.search(d.get("title", "")):
                continue
            seen.add(ident)
            f = _ia_files(ident)
            if not f or f.get("length", 0) < 20 or f.get("length", 0) > 4 * 3600:
                continue
            colls = [str(c).lower() for c in f.get("coll", [])]
            if "periscopefilm" in colls or BAD_LIC.search(f.get("lic", "")) or re.search(r"-n[cd]", f.get("lic", ""))                     or not any(c in ("prelinger", "nasa", "usgovfilms", "fedflix") for c in colls):     # only collections that are public domain / US gov
                continue
            out.append(dict(id="ia_" + ident, ref=ident, url=f"https://archive.org/download/{ident}/{f['name']}", length=f["length"],
                            credit=f"{f['title']} - Internet Archive ({ident}) - Public Domain / free to reuse", source="archive"))
            if len(out) >= n:
                return out
    return out


def wikimedia_video(query: str, n: int = 4) -> list:
    r = _get("https://commons.wikimedia.org/w/api.php", dict(
        action="query", format="json", generator="search", gsrnamespace=6, gsrsearch=f"{' '.join(_kw(query, 4))} filetype:video", gsrlimit=max(n * 3, 10),
        prop="videoinfo", viprop="url|size|mime|derivatives|extmetadata"))
    out = []
    for p in sorted(((r.json().get("query") or {}).get("pages") or {}).values(), key=lambda x: x.get("index", 0)) if r else []:
        vi = (p.get("videoinfo") or [{}])[0]
        md = vi.get("extmetadata", {})
        lic = _strip((md.get("LicenseShortName") or {}).get("value"))
        if not lic or BAD_LIC.search(lic) or not OK_LIC.search(lic) or (vi.get("duration") or 0) < 4 or IA_BAD_TITLE.search(p["title"]):
            continue
        ders = [d for d in vi.get("derivatives", []) if "webm" in d.get("type", "") and d.get("width", 0) <= 1280 and d.get("src")]
        ders.sort(key=lambda d: -d["width"])
        url = ders[0]["src"] if ders else vi.get("url")
        if not url or vi.get("width", 0) < 640:
            continue
        who = _strip((md.get("Artist") or {}).get("value"))[:80] or "Wikimedia Commons"
        out.append(dict(id="wv" + str(p["pageid"]), ref=p["title"], url=url, length=float(vi.get("duration") or 0),
                        credit=f"{p['title'][5:]} - {who} - {lic} - Wikimedia Commons", source="wikimedia_video"))
        if len(out) >= n:
            break
    return out


def nasa_video(query: str, n: int = 4) -> list:
    r = _get("https://images-api.nasa.gov/search", dict(q=" ".join(_kw(query, 4)), media_type="video"))
    out = []
    for it in ((r.json().get("collection") or {}).get("items") or [])[:n * 2] if r else []:
        d = (it.get("data") or [{}])[0]
        nid = d.get("nasa_id")
        a = _get(f"https://images-api.nasa.gov/asset/{requests.utils.quote(str(nid))}") if nid else None
        if not a:
            continue
        hrefs = [x["href"] for x in (a.json().get("collection") or {}).get("items", []) if x.get("href", "").lower().endswith(".mp4")]
        pick = next((h for k in ("~medium", "~large", "~mobile", "~orig") for h in hrefs if k in h), None)
        if pick:
            out.append(dict(id="nv" + re.sub(r"\W", "", str(nid))[:40], ref=str(nid), url=pick.replace("http://", "https://"), length=0.0,
                            credit=f"{d.get('title', '')[:80]} - NASA ({nid}) - Public Domain", source="nasa_video"))
        if len(out) >= n:
            break
    return out


def stock_video(query: str, n: int = 4) -> list:
    """Pexels / Pixabay video, ONLY when an API key already exists in the environment."""
    out = []
    if os.environ.get("PEXELS_API_KEY"):
        try:
            r = requests.get("https://api.pexels.com/videos/search", params=dict(query=query, per_page=n, orientation="landscape"),
                             headers={**UA, "Authorization": os.environ["PEXELS_API_KEY"]}, timeout=30)
            for v in (r.json().get("videos") or []) if r.ok else []:
                f = sorted([x for x in v["video_files"] if x.get("width", 0) <= 1920 and x.get("file_type") == "video/mp4"], key=lambda x: -x["width"])
                if f:
                    out.append(dict(id="px%s" % v["id"], ref=str(v["id"]), url=f[0]["link"], length=float(v.get("duration") or 0),
                                    credit=f"Video by {v.get('user', {}).get('name', 'Pexels')} - Pexels", source="pexels"))
        except Exception:  # noqa: BLE001
            pass
    if os.environ.get("PIXABAY_API_KEY"):
        try:
            r = requests.get("https://pixabay.com/api/videos/", params=dict(key=os.environ["PIXABAY_API_KEY"], q=query, per_page=max(n, 3)), headers=UA, timeout=30)
            for v in (r.json().get("hits") or []) if r.ok else []:
                u = (v.get("videos", {}).get("large") or v.get("videos", {}).get("medium") or {}).get("url")
                if u:
                    out.append(dict(id="pb%s" % v["id"], ref=str(v["id"]), url=u, length=float(v.get("duration") or 0),
                                    credit=f"Video by {v.get('user', 'Pixabay')} - Pixabay", source="pixabay"))
        except Exception:  # noqa: BLE001
            pass
    return out[:n]


def search_clips(query: str, n: int = 4, sources=("wikimedia_video", "archive", "nasa_video", "stock"), ctx: str = "") -> list:
    """Cached list of cuttable moving-video sources for a query (cheapest relevance first)."""
    fns = {"archive": archive_video, "wikimedia_video": wikimedia_video, "nasa_video": nasa_video, "stock": stock_video}
    key = hashlib.md5((query + "|" + ",".join(sources)).encode()).hexdigest()[:12]

    def go():
        res = []
        for s in sources:
            try:
                res += fns[s](query, n)
            except Exception as e:  # noqa: BLE001
                print("clip source failed", s, e)
        return res

    return [x for x in _cached_json("vq_" + key, go) if clip_ok(query, x, ctx)][: max(n, 1) * 2]


def _curl():
    """Git-for-Windows curl (OpenSSL) when present: Wikimedia's video CDN answers 429 to Python requests / Windows schannel curl but serves
    OpenSSL curl at the same polite rate. Same honest User-Agent either way."""
    for c in ("C:/Program Files/Git/mingw64/bin/curl.exe", "C:/Program Files (x86)/Git/mingw64/bin/curl.exe"):
        if Path(c).exists():
            return c
    return "curl"


_wm_block = [0.0]


def localize(src: dict):
    """Source -> something ffmpeg can read. Wikimedia files are downloaded once (polite, honours Retry-After, 150 MB cap) because
    its upload servers throttle range requests; archive.org / NASA / stock are read by HTTP range straight from the url."""
    if src["source"] != "wikimedia_video":
        return src["url"]
    p = CACHE / f"{src['id']}.webm"
    if p.exists() and p.stat().st_size > 50000:
        return str(p)
    if time.time() < _wm_block[0]:
        return None
    import subprocess
    tmp = p.with_suffix(".part")
    for i in range(4):
        wait = 1.5 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        r = subprocess.run([_curl(), "-sS", "-L", "--fail", "--max-filesize", str(150_000_000), "--max-time", "300", "-A", UA["User-Agent"],
                            "-o", str(tmp), src["url"]], capture_output=True, text=True)
        _last[0] = time.time()
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 50000:
            tmp.replace(p)
            return str(p)
        if "429" in r.stderr:                                  # honour the throttle: back off, skip Wikimedia video for 10 min
            _wm_block[0] = time.time() + 600
        break
    tmp.unlink(missing_ok=True)
    return None
