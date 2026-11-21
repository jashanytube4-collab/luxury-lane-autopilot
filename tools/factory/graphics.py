"""Locally drawn animated graphics (no Claude, no paid assets): map pan/zoom between two places, growing timeline, newspaper/document slow zoom
with a highlight box. Frames are drawn with PIL and piped to ffmpeg. Map data = Natural Earth (public domain), places by lat/lon or Wikipedia coordinates.

graphic = {"type":"map","from":{"name":"Pearl Harbor","lat":21.3,"lon":-157.9},"to":{"name":"Midway"}, "label":"1,300 miles"}
        | {"type":"timeline","title":"..","events":[{"date":"1453","label":"The city falls"},...]}
        | {"type":"document","masthead":"THE DAILY NEWS","headline":"JAPANESE FLEET SIGHTED","lines":[...],"highlight":"SIGHTED"}"""
import json
import math
import random
import subprocess
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter

from . import assets

ROOT = Path(__file__).resolve().parents[2]
W, H, FPS = 1920, 1080, 30
FONTS = ROOT / "assets" / "fonts"
NE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson"


def _font(n, s):
    return ImageFont.truetype(str(FONTS / n), s)


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def _mix(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def _lerp(a, b, e):
    return a + (b - a) * e


# ----------------------------------------------------------------------------- map
def _countries():
    p = assets.CACHE / "ne_110m_countries.json"
    if not p.exists() or p.stat().st_size < 10000:
        r = assets._get(NE_URL)
        if not r:
            raise RuntimeError("Natural Earth download failed")
        p.write_bytes(r.content)
    polys = []
    for f in json.loads(p.read_text(encoding="utf-8"))["features"]:
        g = f["geometry"]
        parts = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for part in parts:
            polys.append(part[0])
    return polys


def geocode(place: dict):
    if "lat" in place and "lon" in place:
        return float(place["lat"]), float(place["lon"])

    def go():
        r = assets._get("https://en.wikipedia.org/w/api.php", dict(action="query", format="json", prop="coordinates", generator="search",
                                                                  gsrsearch=place["name"], gsrlimit=1, colimit=1))
        for pg in ((r.json().get("query") or {}).get("pages") or {}).values() if r else []:
            c = (pg.get("coordinates") or [None])[0]
            if c:
                return [c["lat"], c["lon"]]
        raise RuntimeError("cannot geocode " + place["name"])
    return tuple(assets._cached_json("geo_" + "".join(ch for ch in place["name"].lower() if ch.isalnum())[:40], go))


def render_map(g, prof, d, out: Path):
    acc, bg = _rgb(prof["accent"]), (11, 14, 20)
    a, b = geocode(g["from"]), geocode(g["to"])
    S = 10.0                                              # master canvas px per degree (equirectangular, 3600x1800)
    proj = lambda lat, lon: ((lon + 180) * S, (90 - lat) * S)
    master = Image.new("RGB", (3600, 1800), bg)
    md = ImageDraw.Draw(master)
    for gx in range(-180, 181, 15):
        md.line([proj(-90, gx), proj(90, gx)], fill=(22, 27, 36), width=2)
    for gy in range(-90, 91, 15):
        md.line([proj(gy, -180), proj(gy, 180)], fill=(22, 27, 36), width=2)
    for ring in _countries():
        md.polygon([proj(la, lo) for lo, la in ring], fill=_mix(bg, acc, 0.17), outline=_mix(bg, acc, 0.55))
    master = master.filter(ImageFilter.GaussianBlur(0.6))
    ax, ay = proj(*a)
    bx, by = proj(*b)
    mx, my = (ax + bx) / 2, (ay + by) / 2
    span = max(abs(bx - ax) * 1.0 / 0.62, abs(by - ay) * 1.0 / 0.62 * W / H, 700)      # view width framing both places with margin
    z_far = min(W / span, 3.0)
    z_near = z_far * 1.9
    f_name, t_name = g["from"]["name"].upper(), g["to"]["name"].upper()
    n = int(d * FPS)
    fn = _font("Montserrat-ExtraBold.ttf", 44)
    pipe = _pipe(out)
    ctrl = lambda p0, p1: ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2 - 0.15 * math.hypot(p1[0] - p0[0], p1[1] - p0[1]))
    for i in range(n):
        k = i / max(n - 1, 1)
        if k < 0.30:                                      # close on A, pull out
            e = _ease(k / 0.30); z = _lerp(z_near, z_far, e); cx, cy = _lerp(ax, mx, e), _lerp(ay, my, e)
        elif k < 0.62:
            z, cx, cy = z_far, mx, my
        else:                                             # push in on B
            e = _ease((k - 0.62) / 0.38); z = _lerp(z_far, z_near * 0.8, e); cx, cy = _lerp(mx, bx, e), _lerp(my, by, e)
        vw, vh = W / z, H / z
        x0 = min(max(cx - vw / 2, 0), 3600 - vw)
        y0 = min(max(cy - vh / 2, 0), 1800 - vh)
        frame = master.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + vw, y0 + vh))
        fd = ImageDraw.Draw(frame, "RGBA")
        sc = lambda px, py: ((px - x0) * z, (py - y0) * z)
        p0, p1 = sc(ax, ay), sc(bx, by)
        cm = ctrl(p0, p1)
        prog = _ease((k - 0.15) / 0.5)
        steps = 120
        for s in range(int(steps * prog)):
            t = s / steps
            x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * cm[0] + t * t * p1[0]
            y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * cm[1] + t * t * p1[1]
            if s % 2 == 0:
                fd.ellipse((x - 5, y - 5, x + 5, y + 5), fill=acc + (235,))
        pulse = 10 + 6 * math.sin(i / 5)
        for (px, py), name, show in ((p0, f_name, True), (p1, t_name, prog > 0.9 or k > 0.62)):
            if not show:
                continue
            fd.ellipse((px - pulse * 2, py - pulse * 2, px + pulse * 2, py + pulse * 2), outline=acc + (120,), width=3)
            fd.ellipse((px - 11, py - 11, px + 11, py + 11), fill=acc + (255,), outline=(255, 255, 255, 255), width=3)
            tw = fd.textlength(name, font=fn)
            lx = min(max(px - tw / 2, 40), W - tw - 40)
            ly = min(max(py + 34, 40), H - 130)
            fd.rectangle((lx - 14, ly - 6, lx + tw + 14, ly + 56), fill=(8, 10, 14, 215))
            fd.text((lx, ly), name, font=fn, fill=(240, 240, 240, 255))
        if g.get("label") and k > 0.5:
            al = int(255 * _ease((k - 0.5) / 0.15))
            fd.text((W // 2, 150), g["label"].upper(), font=_font("Anton-Regular.ttf", 120), fill=acc + (al,), anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0, al))
        _write(pipe, _finish(frame))
    _close(pipe)


# ----------------------------------------------------------------------------- timeline
def render_timeline(g, prof, d, out: Path):
    from . import core
    acc = _rgb(prof["accent"])
    ev = g["events"][:6]
    n = int(d * FPS)
    big, small, lab = _font("Anton-Regular.ttf", 100), _font("Montserrat-ExtraBold.ttf", 40), _font("Montserrat-ExtraBold.ttf", 38)
    pipe = _pipe(out)
    x0, x1, y = 230, W - 230, 560
    pos = [x0 + (x1 - x0) * i / max(len(ev) - 1, 1) for i in range(len(ev))]
    base = core.grad_bg(prof, 3)
    for i in range(n):
        k = i / max(n - 1, 1)
        fr = base.copy()
        fd = ImageDraw.Draw(fr, "RGBA")
        if g.get("title"):
            fd.text((W // 2, 120), g["title"].upper(), font=small, fill=(220, 224, 230, 255), anchor="mm")
        xe = x0 + (x1 - x0) * _ease(k / 0.85)
        fd.line((x0, y, x1, y), fill=(60, 66, 78, 255), width=6)
        fd.line((x0, y, xe, y), fill=acc + (255,), width=10)
        fd.ellipse((xe - 12, y - 12, xe + 12, y + 12), fill=(255, 255, 255, 255))
        for j, (e, px) in enumerate(zip(ev, pos)):
            a = _ease((k - ((px - x0) / (x1 - x0) * 0.85 + 0.02)) / 0.07)
            if a <= 0:
                continue
            up, al = j % 2 == 0, int(255 * a)
            fd.line((px, y - 20, px, y - 95) if up else (px, y + 20, px, y + 95), fill=acc + (al,), width=4)
            fd.ellipse((px - 20, y - 20, px + 20, y + 20), fill=acc + (al,), outline=(255, 255, 255, al), width=4)
            fd.text((px, y - 135 if up else y + 150), str(e["date"]), font=big if len(str(e["date"])) < 8 else small, fill=acc + (al,), anchor="mm")
            lns = _wrap(fd, e.get("label", ""), lab, 330)
            ly = y - 240 - (len(lns) - 1) * 44 if up else y + 225
            for q, ln in enumerate(lns):
                fd.text((px, ly + q * 44), ln, font=lab, fill=(235, 238, 244, al), anchor="mm")
        _write(pipe, _finish(fr))
    _close(pipe)


def _wrap(d, text, font, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) > maxw and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    return lines + [cur] if cur else lines


# ----------------------------------------------------------------------------- document
def render_document(g, prof, d, out: Path):
    acc = _rgb(prof["accent"])
    rnd = random.Random(5)
    PW, PH = 1500, 2000
    paper = Image.new("RGB", (PW, PH), (226, 214, 188))
    pd = ImageDraw.Draw(paper)
    for _ in range(9000):
        x, y, c = rnd.randrange(PW), rnd.randrange(PH), rnd.randint(-14, 8)
        pd.point((x, y), fill=(226 + c, 214 + c, 188 + c))
    ink = (34, 30, 26)
    pd.text((PW // 2, 130), g.get("masthead", "THE DAILY CHRONICLE").upper(), font=_font("Cinzel-Bold.ttf", 110), fill=ink, anchor="mm")
    pd.line((90, 215, PW - 90, 215), fill=ink, width=6)
    pd.line((90, 230, PW - 90, 230), fill=ink, width=2)
    hf = _font("Anton-Regular.ttf", 150)
    hy, hbox = 300, None
    hl = g.get("highlight", "").upper()
    for ln in _wrap(pd, g.get("headline", "").upper(), hf, PW - 200):
        pd.text((100, hy), ln, font=hf, fill=ink)
        if hl and hl in ln and not hbox:
            s = ln.index(hl)
            xa = 100 + pd.textlength(ln[:s], font=hf)
            hbox = (xa - 16, hy + 22, xa + pd.textlength(hl, font=hf) + 16, hy + 200)
        hy += 165
    y = hy + 40
    pd.line((90, y, PW - 90, y), fill=ink, width=3)
    y += 40
    cols, cw = 3, (PW - 180 - 120) / 3
    for c in range(cols):                                 # greeked columns: looks like a page, invents no facts
        cx, cy = 90 + c * (cw + 60), y
        while cy < PH - 120:
            pd.rectangle((cx, cy, cx + (cw * rnd.uniform(0.55, 1.0) if rnd.random() < 0.18 else cw), cy + 14), fill=(96, 90, 82))
            cy += 34
    bf = _font("Montserrat-ExtraBold.ttf", 34)
    for q, ln in enumerate((g.get("lines") or [])[:3]):   # optional real lines drawn over the first column
        top = y + q * 170
        pd.rectangle((86, top - 8, 94 + cw, top + 136), fill=(226, 214, 188))
        for j, t in enumerate(_wrap(pd, ln, bf, cw)[:3]):
            pd.text((90, top + j * 42), t, font=bf, fill=ink)
        if hl and not hbox and hl.lower() in ln.lower():
            hbox = (80, top - 10, 100 + cw, top + 136)
    paper = paper.filter(ImageFilter.GaussianBlur(0.5))
    hbox = hbox or (100, 300, PW - 100, 480)
    n = int(d * FPS)
    pipe = _pipe(out)
    cxh, cyh = (hbox[0] + hbox[2]) / 2, (hbox[1] + hbox[3]) / 2
    for i in range(n):
        k = i / max(n - 1, 1)
        e = _ease(k)
        vw = PW / (1.05 + 0.75 * e)
        vh = vw * H / W
        cx, cy = _lerp(PW / 2, cxh, e * 0.85), _lerp(PH * 0.28, cyh, e * 0.9)
        x0, y0 = min(max(cx - vw / 2, 0), PW - vw), min(max(cy - vh / 2, 0), PH - vh)
        fr = paper.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + vw, y0 + vh))
        fd = ImageDraw.Draw(fr, "RGBA")
        s = W / vw
        if k > 0.35:
            wd = ((hbox[2] - hbox[0]) * s) * _ease((k - 0.35) / 0.2)
            bx0, by0, by1 = (hbox[0] - x0) * s, (hbox[1] - y0) * s, (hbox[3] - y0) * s
            fd.rectangle((bx0, by0, bx0 + wd, by1), fill=acc + (70,))
            fd.rectangle((bx0, by0, bx0 + wd, by1), outline=acc + (255,), width=7)
        _write(pipe, _finish(fr))
    _close(pipe)


# ----------------------------------------------------------------------------- shared
_VG = [None]


def _finish(im):
    if _VG[0] is None:
        v = Image.new("L", (W, H), 255)
        dv = ImageDraw.Draw(v)
        for r in range(0, 420, 6):
            dv.rectangle((r, r, W - r, H - r), outline=int(255 - 70 * (1 - r / 420) ** 2))
        v = v.filter(ImageFilter.GaussianBlur(60))
        _VG[0] = Image.merge("RGB", (v, v, v))
    return ImageChops.multiply(im.convert("RGB"), _VG[0])


def _pipe(out):
    return subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-threads", "3", str(out)], stdin=subprocess.PIPE)


def _write(p, im):
    p.stdin.write(im.tobytes())


def _close(p):
    p.stdin.close()
    p.wait()
    if p.returncode:
        raise RuntimeError("graphic encode failed")


def render(g, prof, d, out: Path):
    {"map": render_map, "timeline": render_timeline, "document": render_document}[g["type"]](g, prof, d, out)
