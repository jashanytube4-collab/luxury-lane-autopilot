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
UA = {"User-Agent": "VideoFactory/1.0 (documentary video tool, low-rate, honours Retry-After) python-requests"}
OK_LIC = re.compile(r"public domain|pd|cc0|cc[- ]by(?![- ]nc)|cc[- ]by-sa|attribution|no known copyright", re.I)
BAD_LIC = re.compile(r"\bnc\b|non-?commercial|noderiv|\bnd\b|fair use|copyrighted|all rights", re.I)
BAD_NAME = re.compile(r"banknote|TL|stamp|coin|signature|autograph|poster|flag|seal of|emblem|\.(svg|gif|tif|tiff|pdf|djvu|webm|ogv|ogg|mp3|wav)$|logo|flag of|coat of arms|map of|locator|diagram|icon", re.I)


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


def search_images(query: str, n: int = 6, sources=("wikipedia", "wikimedia")) -> list:
    """Download (cached) up to n images for the query; returns dicts with local path."""
    key = hashlib.md5((query + "|" + ",".join(sources)).encode()).hexdigest()[:12]
    idx = CACHE / f"q_{key}.json"
    if idx.exists():
        res = json.loads(idx.read_text(encoding="utf-8"))
        res = [x for x in res if Path(x["path"]).exists()]
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
    return res[:n]
