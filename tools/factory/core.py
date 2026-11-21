"""Factory core: script pack -> voice (edge-tts, word timing) -> shots -> graded segments -> subtitles + music -> mp4. Local only.

Script pack (written by Claude, JSON): {channel, slug, title, description, tags, thumb:{text, query}, beats:[{text, v:[visual,...]}]}
visual = {"t":"photo","q":"search words"} | {"t":"big","big":"65 km","small":"width of the gap"} | {"t":"quote","text":"..","by":".."}
       | {"t":"list","title":"..","lines":[..]} | {"t":"card","text":"BIG WORDS"} | {"t":"clip","path":"local.mp4"}
A beat with several visuals shows them one after another; a beat with one photo query pulls extra distinct photos
so no shot is held longer than the channel pace (profile.pace_max seconds)."""
import asyncio
import hashlib
import json
import random
import re
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from . import assets, graphics

ROOT = Path(__file__).resolve().parents[2]
FF, FPS, W, H = "ffmpeg", 30, 1920, 1080
FONTS = ROOT / "assets" / "fonts"
GAP = 0.28


def run(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(" ".join(map(str, cmd))[:300] + "\n" + r.stderr[-1200:])
    return r.stdout


def dur(p):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).strip())


def profile(key):
    p = json.loads((ROOT / "factory" / "profiles" / f"{key}.json").read_text(encoding="utf-8"))
    p.setdefault("pace_max", 4.5)
    p.setdefault("accent", "#e8b04a")
    p.setdefault("grade", "eq=contrast=1.06:saturation=1.05,vignette=PI/5")
    p.setdefault("music", [])
    return p


# ----------------------------------------------------------------------------- voice
def tts_beat(text, prof, out: Path):
    """Return (wav path, [(word, start, end)]) for one beat; cached."""
    import edge_tts
    h = hashlib.md5((prof["voice"] + prof["rate"] + prof["pitch"] + text).encode()).hexdigest()[:12]
    mp3, js = out / f"{h}.mp3", out / f"{h}.json"
    if not (mp3.exists() and js.exists()):
        async def go():
            words = []
            comm = edge_tts.Communicate(text, prof["voice"], rate=prof["rate"], pitch=prof["pitch"], boundary="WordBoundary")
            with open(mp3, "wb") as f:
                async for ch in comm.stream():
                    if ch["type"] == "audio":
                        f.write(ch["data"])
                    elif ch["type"] == "WordBoundary":
                        words.append((ch["text"], ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7))
            js.write_text(json.dumps(words), encoding="utf-8")
        for i in range(4):
            try:
                asyncio.run(go()); break
            except Exception as e:  # noqa: BLE001
                print("tts retry", e); asyncio.run(asyncio.sleep(3))
    return mp3, json.loads(js.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------- cards
def _font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def grad_bg(prof, seed=0):
    random.seed(seed)
    a = prof["accent"].lstrip("#")
    c = tuple(int(a[i:i + 2], 16) for i in (0, 2, 4))
    im = Image.new("RGB", (W, H), (10, 12, 16))
    px = ImageDraw.Draw(im)
    for y in range(H):
        k = y / H
        px.line((0, y, W, y), fill=(int(14 + c[0] * 0.07 * (1 - k)), int(16 + c[1] * 0.07 * (1 - k)), int(20 + c[2] * 0.09 * (1 - k))))
    return im


def card_image(v, prof, path: Path, seed=0):
    im = grad_bg(prof, seed)
    d = ImageDraw.Draw(im)
    acc = prof["accent"]
    t = v["t"]
    if t == "big":
        d.text((W // 2, 450), str(v["big"]), font=_font("Anton-Regular.ttf", 360), fill=acc, anchor="mm")
        d.text((W // 2, 700), str(v.get("small", "")).upper(), font=_font("Montserrat-ExtraBold.ttf", 54), fill="#e9edf3", anchor="mm")
    elif t == "quote":
        txt = _wrap(d, '"' + v["text"] + '"', _font("Cinzel-Bold.ttf", 76), 1500)
        d.multiline_text((W // 2, 470), txt, font=_font("Cinzel-Bold.ttf", 76), fill="#f2f2f2", anchor="mm", align="center", spacing=18)
        d.text((W // 2, 800), "— " + v.get("by", ""), font=_font("Montserrat-ExtraBold.ttf", 44), fill=acc, anchor="mm")
    elif t == "list":
        d.text((200, 190), v.get("title", "").upper(), font=_font("Anton-Regular.ttf", 100), fill=acc)
        for i, ln in enumerate(v["lines"][:6]):
            d.text((200, 360 + i * 120), f"{i + 1}", font=_font("Anton-Regular.ttf", 80), fill=acc)
            d.text((300, 370 + i * 120), ln, font=_font("Montserrat-ExtraBold.ttf", 62), fill="#e9edf3")
    else:  # card
        txt = _wrap(d, v.get("text", "").upper(), _font("Anton-Regular.ttf", 230), 1650)
        d.multiline_text((W // 2, H // 2), txt, font=_font("Anton-Regular.ttf", 230), fill=acc, anchor="mm", align="center", spacing=10)
    im.save(path)


def _wrap(d, text, font, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) > maxw and cur:
            lines.append(cur); cur = w
        else:
            cur = t
    lines.append(cur)
    return "\n".join(lines)


# ----------------------------------------------------------------------------- shots
# Moving clips first (cut 2.5-5.8 s windows straight from the source url), photos rare (Ken Burns), never two photos in a row,
# no clip/window reused inside one video, transitions by mood (cut / dip / sparing light flash), SFX events returned for the mixer.
TENSE = re.compile(r"\b(war|death|dead|killed|kill|massacre|betray\w*|murder\w*|fire|burn\w*|blood\w*|siege|attack\w*|collaps\w*|fell|crash\w*|execut\w*|doomed|never|disaster|explo\w*)\b", re.I)
REVEAL = re.compile(r"\b(then|until|suddenly|but|truth|secret|discovered|finally|revealed|instead|except)\b", re.I)
CARD_MAX, CLIP_MAX, CHUNK = 3.2, 5.8, 4.2
IA_UA = "VideoFactory/1.0 (documentary tool; honest client)"


_AH = []


def _ahash(path):
    try:
        im = Image.open(path).convert("L").resize((8, 8), Image.BILINEAR)
        px = list(im.getdata()); m = sum(px) / 64
        return sum(1 << i for i, v in enumerate(px) if v > m)
    except Exception:  # noqa: BLE001
        return None


def _dupe(path):
    """True if this picture (any file name) looks like one already used in the video: 8x8 average-hash within 6 bits."""
    h = _ahash(path)
    if h is None:
        return False
    if any(bin(h ^ o).count("1") <= 6 for o in _AH):
        return True
    _AH.append(h)
    return False


def photo_pool(q, n, used: set):
    """n distinct photos for query q, trying shorter queries if the search is thin."""
    words = q.split()
    tries = [q] + [" ".join(words[:k]) for k in (3, 2, 1) if len(words) > k]
    got = []
    for t in tries:
        for it in assets.search_images(t, 8):
            tt = assets._title(it.get("credit", ""))
            if it["id"] not in used and tt not in used and it["id"] not in [g["id"] for g in got]:
                if not assets.photo_relevant(q, it) or _dupe(it["path"]):
                    continue
                got.append(it)
        if len(got) >= n:
            break
    return got[:n]


def kenburns(img_path, d, out: Path, prof, idx, trans=None):
    """Photo -> graded moving shot of d seconds (slow zoom/pan, direction varies by idx)."""
    n = max(2, int(round(d * FPS)))
    im = Image.open(img_path).convert("RGB")
    if im.width / im.height < 0.95:                     # true portrait only: sharp photo centred over its own blurred copy
        bg = im.resize((3840, int(3840 * im.height / im.width)), Image.LANCZOS)
        top = (bg.height - 2160) // 2
        bg = bg.crop((0, max(top, 0), 3840, max(top, 0) + 2160)).filter(ImageFilter.GaussianBlur(40))
        bg = Image.eval(bg, lambda v: int(v * 0.45))
        fg = im.resize((int(2000 * im.width / im.height), 2000), Image.LANCZOS)
        bg.paste(fg, ((3840 - fg.width) // 2, 80))
        im = bg
    else:
        s = max(3200 / im.width, 1800 / im.height, 1)
        k = min(s, 4200 / im.width)
        if k != 1:
            im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    tmp = out.with_suffix(".jpg")
    im.save(tmp, quality=93)
    z0, z1 = (1.0, 1.16) if idx % 2 == 0 else (1.16, 1.0)
    px = ["(iw-iw/zoom)/2", "0", "(iw-iw/zoom)"][idx % 3]
    py = ["(ih-ih/zoom)/2", "(ih-ih/zoom)/2", "0"][idx % 3]
    zexpr = f"{z0}+({z1}-{z0})*on/{n}"
    vf = (f"zoompan=z='{zexpr}':x='{px}':y='{py}':d={n}:s={W}x{H}:fps={FPS},{prof['grade']},"
          f"noise=alls=3:allf=t{_fade(trans)},format=yuv420p")
    run([FF, "-y", "-loglevel", "error", "-i", tmp, "-vf", vf, "-t", f"{d:.3f}", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "3", out])
    tmp.unlink(missing_ok=True)


def _fade(trans):
    if trans == "flash":
        return ",fade=t=in:st=0:d=0.16:color=white"
    if trans == "dip":
        return ",fade=t=in:st=0:d=0.22"
    return ""


def still(png, d, out: Path, prof, trans=None):
    n = int(d * FPS)
    vf = (f"zoompan=z='1+0.05*on/{n}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d={n}:s={W}x{H}:fps={FPS},"
          f"noise=alls=4:allf=t{_fade(trans)},format=yuv420p")
    run([FF, "-y", "-loglevel", "error", "-i", png, "-vf", vf, "-t", f"{d:.3f}", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "3", out])


def probe(src):
    """(w, h, duration) of a clip source, cached by url."""
    f = assets.CACHE / ("pr_" + hashlib.md5(src.encode()).hexdigest()[:12] + ".json")
    if f.exists():
        return tuple(json.loads(f.read_text()))
    try:
        o = run(["ffprobe", "-v", "error", "-user_agent", IA_UA, "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                 "-of", "csv=p=0:s=,", src]).split()
        w, h = o[0].split(",")[:2]
        v = (int(w), int(h), float(o[1].split(",")[0]) if len(o) > 1 else 0.0)
    except Exception as e:  # noqa: BLE001
        print("probe failed", " ".join(str(e)[-200:].split()))
        return (0, 0, 0.0)
    f.write_text(json.dumps(v))
    return v


def detect_crop(src, start, http):
    """Baked-in black bars (pillarbox/letterbox inside the file) -> 'crop=w:h:x:y' or None."""
    r = subprocess.run([FF, "-hide_banner", *http, "-ss", f"{start:.2f}", "-i", str(src), "-t", "2", "-vf", "cropdetect=30:2:0", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r)
    return tuple(map(int, m[-1])) if m else None


def cut_clip(src, start, d, out: Path, prof, trans, aspect):
    """Cut [start, start+d) from src (url or file), crop baked-in bars, scale/crop to 1080p, grade, optional fade. Very narrow sources get a blurred fill."""
    grade = prof["grade"]
    http = ["-user_agent", IA_UA, "-rw_timeout", "40000000"] if str(src).startswith("http") else []
    pre = ""
    cr = detect_crop(src, start, http)
    if cr and cr[0] >= 200 and cr[1] >= 200:
        aspect = cr[0] / cr[1]
        pre = f"crop={cr[0] // 2 * 2}:{cr[1] // 2 * 2}:{cr[2]}:{cr[3]},"
        if cr[1] < 300:
            raise RuntimeError("source too small after crop")
    if aspect < 1.2:
        fc = (f"[0:v]{pre.rstrip(',') or 'null'},split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=40:6,eq=brightness=-0.18[bg];"
              f"[b]scale=-2:{H}[fg];[bg][fg]overlay=(W-w)/2:0,{grade},noise=alls=3:allf=t{_fade(trans)},format=yuv420p[o]")
        vf = ["-filter_complex", fc, "-map", "[o]"]
    else:
        vf = ["-vf", f"{pre}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},{grade},noise=alls=3:allf=t{_fade(trans)},format=yuv420p"]
    run([FF, "-y", "-loglevel", "error", *http, "-ss", f"{start:.2f}", "-i", src, "-t", f"{d:.3f}", *vf, "-r", FPS, "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "3", out])


def shot_ok(path):
    """Reject black, blown-out or static (title card / freeze) cuts."""
    try:
        r = subprocess.run([FF, "-v", "error", "-i", str(path), "-vf", "fps=4,scale=96:54,signalstats,metadata=print:file=-", "-f", "null", "-"],
                           capture_output=True, text=True).stdout
    except Exception:  # noqa: BLE001
        return True
    ya = [float(x) for x in re.findall(r"YAVG=([\d.]+)", r)]
    yd = [float(x) for x in re.findall(r"YDIF=([\d.]+)", r)]
    if not ya:
        return False
    avg = sum(ya) / len(ya)
    if avg < 22 or avg > 232:
        return False
    return not (yd and sum(yd) / len(yd) < 0.35)


_FACE = [None]
YUNET = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"      # Apache-2.0


def _face_det():
    if _FACE[0] is None:
        import cv2
        m = ROOT / "factory" / "models" / "yunet.onnx"
        if not m.exists() or m.stat().st_size < 100000:
            m.parent.mkdir(parents=True, exist_ok=True)
            r = assets._get(YUNET)
            if not r:
                _FACE[0] = False
                return False
            m.write_bytes(r.content)
        _FACE[0] = cv2.FaceDetectorYN.create(str(m), "", (640, 360), 0.75)
    return _FACE[0]


def frame_ok(path):
    """Frame-level reject: talking-head / interview look = a large face (>=15% of frame height) in >=2 of 4 sampled frames (YuNet via OpenCV)."""
    try:
        import cv2
        det = _face_det()
    except Exception:  # noqa: BLE001
        return True
    if not det:
        return True
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    big = 0
    for k in range(4):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (k + 0.5) / 4))
        ok, fr = cap.read()
        if not ok:
            continue
        _, fs = det.detect(cv2.resize(fr, (640, 360)))
        if fs is not None and any(f[3] > 360 * 0.15 for f in fs):
            big += 1
    cap.release()
    return big < 2


class Picker:
    """Chooses unique moving-clip windows for a video; remembers every window used."""

    def __init__(self, prof, work: Path, broll):
        self.prof, self.work, self.broll = prof, work, list(broll)
        self.win = {}          # source id -> [(start, end)]
        self.credits = []
        self.avoid = re.compile("|".join(prof.get("avoid", [])) or r"(?!x)x", re.I)
        self.n = 0

    def _windows(self, src, length, d):
        used = self.win.get(src["id"], [])
        rng = random.Random(src["id"])
        lo, hi = (0.0, max(length - d, 0)) if length < 60 else (length * 0.06, max(length * 0.94 - d, length * 0.06))
        cand = [lo + (hi - lo) * i / 11 for i in range(12)]
        rng.shuffle(cand)
        return [c for c in cand if all(c + d + 2 < u0 or c > u1 + 2 for u0, u1 in used)]

    def clip(self, queries, d, out: Path, trans, loose=False):
        seen = set()
        for q in queries:
            if not q or q in seen:
                continue
            seen.add(q)
            srcs = [s for s in assets.search_clips(q, 4, ctx="*" if loose else " ".join(queries)) if len(self.win.get(s["id"], [])) < 3 and not self.avoid.search(s["credit"])]
            srcs.sort(key=lambda s: len(self.win.get(s["id"], [])))     # unused sources first = more variety
            for s in srcs[:6]:
                loc = assets.localize(s)
                if not loc:
                    continue
                w, h, ln = probe(loc)
                if not w:
                    continue
                length = ln or s.get("length") or 0
                if h < 360 or length < d + 0.5:             # skip postage-stamp sources: upscaled mush
                    continue
                for st in self._windows(s, length, d)[:2]:
                    self.win.setdefault(s["id"], []).append((st, st + d))
                    try:
                        cut_clip(loc, st, d, out, self.prof, trans, w / max(h, 1))
                    except RuntimeError as e:
                        print("cut failed", s["id"], str(e)[-120:]); break
                    if shot_ok(out) and frame_ok(out):
                        self.credits.append(s["credit"])
                        return True
        return False


def mood(text):
    if TENSE.search(text):
        return "tense"
    if REVEAL.search(text):
        return "reveal"
    return "calm"


def plan_shots(beats, timing, prof, work: Path, broll=()):
    """Return (shots, credits, events). shots = [(path, dur)], events = [(time_s, 'whoosh'|'impact')]. Every beat covered exactly."""
    used_photos, shots, events = set(), [], []
    _AH.clear()
    (work / "shots").mkdir(exist_ok=True)
    pk = Picker(prof, work, list(broll) + list(prof.get("broll", [])))
    prev, since_photo, since_flash, photo_t, t = "none", 99, 99, 0.0, 0.0
    credits = []
    total = sum(tm["dur"] for tm in timing)
    for bi, (b, tm) in enumerate(zip(beats, timing)):
        D = tm["dur"]
        vis = b.get("v") or [{"t": "photo", "q": b.get("q", "")}]
        if b.get("graphic"):                                   # locally drawn map / timeline / document shot leads the beat
            vis = [{"t": "graphic", "g": b["graphic"]}] + [v for v in vis if v["t"] not in ("big", "quote", "list", "card", "graphic")][:1]
        isc = lambda v: v["t"] in ("big", "quote", "list", "card", "graphic")
        ncard = sum(1 for v in vis if isc(v))
        nmed = len(vis) - ncard
        cd = min(CARD_MAX, D / len(vis)) if nmed else D / max(ncard, 1)
        gd = min(6.0, max(cd, D / len(vis) if nmed else D / max(ncard, 1)))
        dd = lambda v: gd if v["t"] == "graphic" else cd
        rest = max(D - sum(dd(v) for v in vis if isc(v)), 0.0)
        mood_b = mood(b["text"])
        # slots: (visual, duration)
        slots = []
        for v in vis:
            if isc(v):
                slots.append((v, dd(v)))
            else:
                span = rest / nmed
                k = max(1, int(-(-span // CLIP_MAX)), int(round(span / CHUNK)))
                slots += [(v, span / k)] * k
        for si, (v, sd) in enumerate(slots):
            first = si == 0
            trans = "cut"
            if first and bi > 0:
                if mood_b == "reveal" and since_flash >= 6 and sd >= 2:
                    trans = "flash"
                elif mood_b == "calm" and bi % 2 == 0:
                    trans = "dip"
            sp = work / "shots" / f"b{bi:03d}_{si}.mp4"
            if v["t"] == "graphic":
                try:
                    graphics.render(v["g"], prof, sd, sp); kind = "graphic"
                except Exception as e:  # noqa: BLE001
                    print("graphic failed, card instead:", str(e)[-150:])
                    png = sp.with_suffix(".png")
                    card_image(dict(t="card", text=v["g"].get("label") or v["g"].get("headline") or "THE ROUTE"), prof, png, bi + si); still(png, sd, sp, prof, trans); kind = "card"
            elif isc(v):
                png = sp.with_suffix(".png")
                card_image(v, prof, png, bi + si); still(png, sd, sp, prof, trans)
                kind = "card"
                if v["t"] == "big":
                    events.append((t, "impact"))
            else:
                alts = v["q"] if isinstance(v.get("q"), list) else [x.strip() for x in (v.get("q") or _keyword(b["text"])).split("|")]
                if v.get("alt"):
                    alts = alts + [x.strip() for x in str(v["alt"]).split("|")]
                q = alts[0]
                words = q.split()
                qs = alts + [" ".join(words[:k]) for k in (3, 2) if len(words) > k]
                kind = None
                want_photo = (v["t"] == "photo" and prev != "photo" and since_photo >= 4 and photo_t < 0.18 * max(t, 20) and bi > 0
                              and any(x[0].isupper() for x in words))
                if want_photo:
                    pool = photo_pool(q, 1, used_photos)
                    if pool:
                        used_photos.update((pool[0]["id"], assets._title(pool[0].get("credit", "")))); credits.append(pool[0]["credit"])
                        kenburns(pool[0]["path"], min(sd, 4.6), sp.with_name(sp.stem + "_p.mp4"), prof, bi + si, trans)
                        sp = sp.with_name(sp.stem + "_p.mp4"); kind = "photo"
                if kind is None and v["t"] != "clip" and pk.clip(qs, sd, sp, trans):
                    kind = "clip"
                if kind is None and v["t"] == "clip":
                    cut_clip(v["path"], 0, sd, sp, prof, trans, 1.78); kind = "clip"
                if kind is None:
                    if pk.clip(pk.broll, sd, sp, trans):                    # themed b-roll keeps it moving
                        kind = "clip"
                if kind is None:
                    pool = photo_pool(q, 1, used_photos)
                    if pool:
                        used_photos.update((pool[0]["id"], assets._title(pool[0].get("credit", "")))); credits.append(pool[0]["credit"])
                        sp = sp.with_name(sp.stem + "_p.mp4")
                        kenburns(pool[0]["path"], sd, sp, prof, bi + si, trans); kind = "photo"
                if kind is None:                                            # last resort: styled keyword card, never a repeat
                    png = sp.with_suffix(".png")
                    card_image(dict(t="card", text=_keyword(b["text"])), prof, png, bi + si); still(png, sd, sp, prof, trans); kind = "card"
            if kind == "photo":
                since_photo, photo_t = 0, photo_t + sd
            else:
                since_photo += 1
            if trans == "flash":
                since_flash = 0; events.append((max(t - 0.12, 0), "whoosh"))
            else:
                since_flash += 1
                if trans == "dip" and bi % 4 == 0:
                    events.append((max(t - 0.1, 0), "whoosh"))
            if first and bi > 0 and mood_b == "tense" and kind != "card" and sum(1 for e in events if e[1] == "impact" and t - e[0] < 12) == 0:
                events.append((t, "impact"))
            prev = kind
            shots.append((sp, sd)); t += sd
    credits += pk.credits
    return shots, sorted(set(credits)), events


def _keyword(text):
    ws = [w.strip(".,;:!?\"'").upper() for w in text.split() if len(w) > 4]
    return " ".join(ws[:2]) or "WATCH"


# ----------------------------------------------------------------------------- synthesized SFX (no samples, no licences)
def synth_sfx(events, total, path: Path, sr=48000):
    import numpy as np
    import wave
    rng = np.random.default_rng(7)
    out = np.zeros(int((total + 2) * sr), dtype=np.float32)

    def whoosh(d=0.75):
        n = int(d * sr)
        x = rng.standard_normal(n).astype(np.float32)
        env = np.sin(np.linspace(0, np.pi, n)) ** 2 * np.linspace(0.6, 1.0, n)
        a = np.linspace(0.02, 0.45, n)                      # rising low-pass sweep
        y, s = np.empty(n, np.float32), 0.0
        for i in range(n):
            s += a[i] * (x[i] - s); y[i] = s
        y = y * env
        return 0.55 * y / (np.abs(y).max() + 1e-6)

    def impact(d=1.5):
        n = int(d * sr)
        tt = np.arange(n) / sr
        f = 34 + 40 * np.exp(-tt * 9)
        y = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-tt * 3.2)
        nz = rng.standard_normal(n).astype(np.float32) * np.exp(-tt * 28) * 0.4
        y = y + np.convolve(nz, np.ones(24) / 24, "same")
        return 0.8 * y.astype(np.float32) / (np.abs(y).max() + 1e-6)

    bank = {"whoosh": whoosh(), "impact": impact()}
    for tm, kind in events:
        s = bank[kind]; i = int(max(tm, 0) * sr)
        out[i:i + len(s)] += s[:len(out) - i]
    out = np.clip(out, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((out * 32000).astype("<i2").tobytes())


# ----------------------------------------------------------------------------- subtitles / audio
def ass_from_words(all_words, path: Path, prof):
    def t(x):
        return f"{int(x // 3600)}:{int(x % 3600 // 60):02d}:{x % 60:05.2f}"
    acc = prof["accent"].lstrip("#")
    bgr = acc[4:6] + acc[2:4] + acc[0:2]
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Default,Montserrat ExtraBold,72,&H00FFFFFF,&H00{bgr},&H00101010,&H80000000,1,0,0,0,100,100,0,0,1,5,1,2,120,120,70,1\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    lines = []
    for beat in all_words:                      # all_words = list of per-beat word lists: never group across beats
        grp = []
        for w in beat:
            grp.append(w)
            if len(grp) >= 4:
                lines.append(grp); grp = []
        if grp:
            lines.append(grp)
    ev = []
    for g in lines:
        s, e = g[0][1], g[-1][2] + 0.05
        txt = " ".join(x[0] for x in g).upper()
        ev.append(f"Dialogue: 0,{t(s)},{t(e)},Default,,0,0,0,,{txt}")
    path.write_text(head + "\n".join(ev), encoding="utf-8")


def pick_music(prof):
    lib = sorted((ROOT / "assets" / "music").glob("*.mp3"))
    pref = [m for m in lib if any(k.lower() in m.stem.lower() for k in prof["music"])] or lib
    return pref[0] if pref else None


# ----------------------------------------------------------------------------- build
def build(pack_path, preview=0):
    pack = json.loads(Path(pack_path).read_text(encoding="utf-8"))
    prof = profile(pack["channel"])
    work = ROOT / "factory" / "out" / pack["channel"] / pack["slug"]
    (work / "tts").mkdir(parents=True, exist_ok=True)
    beats = pack["beats"][:preview] if preview else pack["beats"]
    # 1 voice
    wavs, timing, allw, t0 = [], [], [], 0.0
    gap = work / "tts" / "gap.wav"
    run([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", GAP, gap])
    for i, b in enumerate(beats):
        mp3, words = tts_beat(b["text"], prof, work / "tts")
        w = work / "tts" / f"{i:03d}.wav"
        run([FF, "-y", "-loglevel", "error", "-i", mp3, "-ar", "24000", "-ac", "1", w])
        d = dur(w)
        timing.append(dict(start=t0, dur=d + GAP))
        allw.append([(x[0], t0 + x[1], t0 + x[2]) for x in words])
        wavs += [w, gap]; t0 += d + GAP
    lst = work / "tts" / "list.txt"
    lst.write_text("\n".join(f"file '{Path(p).as_posix()}'" for p in wavs))
    run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", work / "voice.wav"])
    total = t0
    # 2 shots
    shots, credits, events = plan_shots(beats, timing, prof, work, pack.get("broll", []))
    synth_sfx(events, total, work / "sfx.wav")
    sl = work / "shots" / "list.txt"
    sl.write_text("\n".join(f"file '{Path(p).as_posix()}'" for p, _ in shots))
    # 3 subtitles
    ass = work / "subs.ass"
    ass_from_words(allw, ass, prof)
    # 4 mix + render
    music = pick_music(prof)
    out = work / ("preview.mp4" if preview else "final.mp4")
    fonts = FONTS.as_posix().replace(":", r"\:")
    vf = f"subtitles='{ass.as_posix().replace(':', chr(92) + ':')}':fontsdir='{fonts}'"
    cmd = [FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", sl, "-i", work / "voice.wav"]
    cmd += ["-i", work / "sfx.wav"]
    if music:
        cmd += ["-stream_loop", "-1", "-i", music]
        af = ("[1:a]asplit=3[v][sc1][sc2];[3:a]volume=0.55[m];[m][sc1]sidechaincompress=threshold=0.03:ratio=9:attack=15:release=450[d];"
              "[2:a]volume=0.9[x];[x][sc2]sidechaincompress=threshold=0.06:ratio=3:attack=10:release=250[dx];"
              "[v][d][dx]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.95[a]")
        cmd += ["-filter_complex", f"[0:v]{vf}[vv];{af}", "-map", "[vv]", "-map", "[a]"]
    else:
        af = ("[1:a]asplit=2[v][sc];[2:a]volume=0.9[x];[x][sc]sidechaincompress=threshold=0.06:ratio=3:attack=10:release=250[dx];"
              "[v][dx]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
        cmd += ["-filter_complex", f"[0:v]{vf}[vv];{af}", "-map", "[vv]", "-map", "[a]"]
    cmd += ["-t", f"{total:.3f}", "-r", FPS, "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
            "-threads", "6", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    run(cmd)
    (work / "credits.txt").write_text("\n".join(credits), encoding="utf-8")
    print(f"ok {out} {total / 60:.2f} min, {len(shots)} shots, {len(credits)} credits")
    return out

