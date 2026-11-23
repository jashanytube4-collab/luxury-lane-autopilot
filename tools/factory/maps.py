"""Maps engine (profile engine == 'maps'): one continuous animated map (Natural Earth, public domain) that the camera flies through.
Scenes are drawn with PIL (Mercator, dark relief look + gold borders) and piped to ffmpeg. Neutral/factual: disputed areas are hatched and footnoted.

A beat carries  "scene": {...} | [{...},{...}]  (shown one after another, optional "w" weight). All keys optional:
  view:   {"lat":..,"lon":..,"span":deg_lon} | {"fit":["Poland","Lithuania",[54.4,23.3]]}   camera target (flies from previous scene)
  fill:   [{"c":"Poland","color":"red|gold|teal|blue|green|grey|#hex","style":"solid|disputed","label":"POLAND","at":0.5}]
  stages: [{"fill":[...],"caption":"label shown while stage is active"}]       fills that change over time (border changes), cross-faded
  pins:   [{"name":"Kaliningrad","lat":54.7,"lon":20.5,"dir":"r|l|u|d","at":1.0}]
  arrows: [{"pts":[[lat,lon],..],"label":"..","color":"gold|cyan|red","at":1.0,"dur":1.6}]      glowing movement/route arrows
  supply: [{"pts":[[lat,lon],..],"label":".."}]                                  dotted moving supply line
  ruler:  [{"a":[lat,lon],"b":[lat,lon],"label":"optional (auto km)"}]
  front:  [{"pts":[[lat,lon],..],"label":"CONTACT LINE"}]                       shaded front-line band (red)
  xs:     [{"lat":..,"lon":..}]   red X (closed / blocked)
  chips:  [{"text":"ALMOST NONE","color":"red","lat":..,"lon":..}]  or without lat/lon -> placed top-left
  big:    {"text":"65 KM","sub":"at its narrowest"}                               big gold number, top centre
  timeline: [{"label":"Founded","at":0.2},..]                                    strip across the top, items light up
  panel:  {"title":"..","big":"3x","caption":"..","bars":[{"label":"A","value":12}]}   split map + stat panel (map shifts left)
  card:   {"title":"IT IS ABOUT THREE THINGS","items":["..",".."]}                 numbered promise card over a dimmed map
  text:   "ONE LAST DISTINCTION"           big statement over a dimmed map
  src:    "UN, World Bank"                small source footer          names: true|false (faint country names, default true)"""
import json
import math
import re
import subprocess
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter
import numpy as np

from . import assets, core
from .tech import font, rgb, ease, mix, prog, out_back, glow

W, H, FPS = 1920, 1080, 30
CEN_Y = 470                                   # map centre line (subtitles live below ~880)
NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
SEA_A, SEA_B = (8, 15, 24), (15, 29, 43)
LAND = (44, 46, 48)
GOLD = (201, 162, 39)
PALETTE = {"red": (205, 52, 48), "gold": (214, 170, 40), "teal": (38, 160, 150), "blue": (64, 120, 205), "green": (70, 165, 90),
           "grey": (120, 124, 130), "cyan": (70, 205, 230), "white": (240, 240, 240), "orange": (225, 125, 40)}
MONT, ANTON = "Montserrat-ExtraBold.ttf", "Anton-Regular.ttf"
_G = {}
_CAM = [None]                                  # camera state carried scene to scene (cx, cy, ww)


def col(c, default=GOLD):
    if isinstance(c, (list, tuple)):
        return tuple(c[:3])
    if isinstance(c, str):
        if c.startswith("#"):
            return rgb(c)
        return PALETTE.get(c, default)
    return default


# ----------------------------------------------------------------------------- data
def merc(lat, lon):
    lat = max(min(lat, 84.0), -84.0)
    return (lon + 180.0) / 360.0, 0.5 - math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / (2 * math.pi)


def _load(name):
    p = assets.CACHE / name
    if not p.exists() or p.stat().st_size < 10000:
        r = assets._get(NE + name.replace("ne_", "ne_", 1))
        if not r:
            raise RuntimeError("Natural Earth download failed: " + name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
    return json.loads(p.read_text(encoding="utf-8"))


def _ring(r):
    a = np.array(r, dtype=np.float64)
    lon, lat = a[:, 0], np.clip(a[:, 1], -84, 84)
    x = (lon + 180) / 360
    y = 0.5 - np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) / (2 * np.pi)
    return np.stack([x, y], 1).astype(np.float32)


def data():
    if "c" in _G:
        return _G
    cs = []
    for f in _load("ne_50m_admin_0_countries.geojson")["features"]:
        g, pr = f["geometry"], f["properties"]
        parts = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        polys = [_ring(p[0]) for p in parts]
        polys = [p for p in polys if len(p) > 3]
        if not polys:
            continue
        names = {str(pr.get(k, "")).lower() for k in ("ADMIN", "NAME", "NAME_LONG", "SOVEREIGNT", "ISO_A3", "ADM0_A3", "BRK_NAME", "FORMAL_EN")}
        big = max(polys, key=lambda p: np.ptp(p[:, 0]) * np.ptp(p[:, 1]))
        cs.append(dict(names=names, polys=polys, bb=[(p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()) for p in polys],
                       big=big, cen=big.mean(0), label=str(pr.get("NAME", "")).upper()))
    _G["c"] = cs
    rv = []
    try:
        for f in _load("ne_50m_rivers_lake_centerlines.geojson")["features"]:
            g = f["geometry"]
            for ln in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]):
                if len(ln) > 3:
                    rv.append(_ring(ln))
    except Exception:  # noqa: BLE001
        pass
    _G["r"] = [(r, (r[:, 0].min(), r[:, 1].min(), r[:, 0].max(), r[:, 1].max())) for r in rv]
    rng = np.random.default_rng(3)
    n = Image.fromarray((rng.random((H // 4, W // 4)) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(2))
    _G["noise"] = np.asarray(n, dtype=np.float32) / 255.0
    sea = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    base = np.array(SEA_A, np.float32) * (1 - sea) + np.array(SEA_B, np.float32) * sea
    base = np.broadcast_to(base, (H, W, 3)) + (_G["noise"][:, :, None] - 0.5) * 7
    _G["sea"] = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    st = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(st)
    for k in range(-H, W, 14):
        sd.line((k, 0, k + H, H), fill=255, width=4)
    _G["stripes"] = st
    return _G


def find(name):
    n = str(name).lower()
    d = data()["c"]
    for c in d:
        if n in c["names"]:
            return c
    raise KeyError("unknown country: " + str(name))


def haversine(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def smooth(pts, per=14):
    """Catmull-Rom through world-coord points -> dense polyline."""
    p = [np.array(q, np.float64) for q in pts]
    if len(p) < 3:
        return np.array([p[0] + (p[-1] - p[0]) * t for t in np.linspace(0, 1, per * 2)])
    p = [p[0]] + p + [p[-1]]
    out = []
    for i in range(1, len(p) - 2):
        for t in np.linspace(0, 1, per, endpoint=False):
            out.append(0.5 * ((2 * p[i]) + (-p[i - 1] + p[i + 1]) * t + (2 * p[i - 1] - 5 * p[i] + 4 * p[i + 1] - p[i + 2]) * t * t
                              + (-p[i - 1] + 3 * p[i] - 3 * p[i + 1] + p[i + 2]) * t ** 3))
    out.append(p[-2])
    return np.array(out)


def wpts(pts):
    return [merc(a, b) for a, b in pts]


# ----------------------------------------------------------------------------- camera
def fit(spec, split=False):
    if "fit" not in spec:
        x, y = merc(spec["lat"], spec["lon"])
        return x, y, float(spec["span"]) / 360.0
    xs, ys = [], []
    for it in spec["fit"]:
        if isinstance(it, (list, tuple)):
            x, y = merc(*it)
            xs.append(x); ys.append(y)
        else:
            try:
                b = find(it)["big"]
            except KeyError:
                continue                                    # sub-national names: use coordinate entries
            xs += [float(b[:, 0].min()), float(b[:, 0].max())]
            ys += [float(b[:, 1].min()), float(b[:, 1].max())]
    bw, bh = max(xs) - min(xs), max(ys) - min(ys)
    usable = 0.52 if split else 0.74
    ww = max(bw / usable, bh * (W / H) / 0.58, 0.004)
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, ww


def cam_at(c0, c1, e):
    (x0, y0, w0), (x1, y1, w1) = c0, c1
    dist = math.hypot(x1 - x0, (y1 - y0))
    lw = math.log(w0) + (math.log(w1) - math.log(w0)) * e
    top = max(w0, w1, dist * 1.25)
    if top > max(w0, w1):
        lw += (math.log(top) - math.log(max(w0, w1))) * math.sin(math.pi * e) ** 1.2
    return x0 + (x1 - x0) * e, y0 + (y1 - y0) * e, math.exp(lw)


# ----------------------------------------------------------------------------- drawing helpers
def pill(im, xy, text, size=34, fill=(8, 10, 14, 225), fg=(245, 245, 245), under=GOLD, anchor="l", pad=14, alpha=1.0):
    d = ImageDraw.Draw(im, "RGBA")
    f = font(MONT, size)
    tw = d.textlength(text, font=f)
    x, y = xy
    x0 = x - tw / 2 - pad if anchor == "m" else x - tw - pad if anchor == "r" else x - pad
    box = (x0, y - size * 0.78, x0 + tw + 2 * pad, y + size * 0.52)
    a = int(255 * alpha)
    d.rectangle(box, fill=fill[:3] + (int(fill[3] * alpha),))
    if under:
        d.rectangle((box[0], box[3] - 4, box[2], box[3]), fill=tuple(under) + (a,))
    d.text((box[0] + pad, (box[1] + box[3]) / 2 - 2), text, font=f, fill=tuple(fg) + (a,), anchor="lm")
    return box


def dash_line(d, pts, fill, width, on=18, off=12, phase=0.0):
    pos = -phase % (on + off)
    for a, b in zip(pts[:-1], pts[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < 1e-3:
            continue
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        s = pos - (on + off)
        while s < L:
            s0, s1 = max(s, 0), min(s + on, L)
            if s1 > s0:
                d.line((a[0] + ux * s0, a[1] + uy * s0, a[0] + ux * s1, a[1] + uy * s1), fill=fill, width=width)
            s += on + off
        pos = (pos + L) % (on + off)


def cum_len(pl):
    seg = np.hypot(*np.diff(pl, axis=0).T) if len(pl) > 1 else np.zeros(1)
    return np.concatenate([[0], np.cumsum(seg)])


def upto(pl, c, frac):
    """sub-polyline from start to fraction of its length (in screen px), returns (list of pts)."""
    tgt = c[-1] * frac
    k = int(np.searchsorted(c, tgt))
    pts = [tuple(p) for p in pl[:k]]
    if 0 < k < len(pl):
        s = (tgt - c[k - 1]) / max(c[k] - c[k - 1], 1e-9)
        pts.append(tuple(pl[k - 1] + (pl[k] - pl[k - 1]) * s))
    return pts


# ----------------------------------------------------------------------------- scene
class Scene:
    def __init__(self, sc, prof, d, cam0, idx):
        self.sc, self.prof, self.d, self.idx = sc, prof, d, idx
        self.acc = rgb(prof.get("accent", "#c9a227"))
        self.events = []
        self.split = bool(sc.get("panel"))
        v = sc.get("view") or self.autoview()
        self.cam1 = fit(v, self.split)
        self.cam0 = cam0 or self.cam1
        self.move = min(1.9, 0.5 * d) if cam0 else 0.01
        moved = math.hypot(self.cam1[0] - self.cam0[0], self.cam1[1] - self.cam0[1]) > 0.002 or abs(math.log(self.cam1[2] / self.cam0[2])) > 0.15
        if moved and cam0:
            self.events.append((0.05, "m_pan"))
        self.t_next = self.move * 0.7 + 0.2
        self.items = {}
        self.build()

    def autoview(self):
        fl = [f["c"] for f in self.fills_raw()]
        if fl:
            return {"fit": fl}
        return {"lat": 30, "lon": 20, "span": 150}

    def fills_raw(self):
        out = list(self.sc.get("fill", []))
        for s in self.sc.get("stages", []):
            out += s.get("fill", [])
        return out

    def at(self, it, step=0.8, ev=None):
        t = float(it["at"]) if "at" in it else self.t_next
        if "at" not in it:
            self.t_next += step
        if ev:
            self.events.append((t, ev))
        return t

    def build(self):
        sc = self.sc
        self.fills = []
        for f in sc.get("fill", []):
            self.fills.append(dict(f, t=self.at(f, 0.55), stage=None))
        self.stages = []
        for si, s in enumerate(sc.get("stages", [])):
            t = self.at(s, max(self.d * 0.8 / max(len(sc["stages"]), 1), 1.0), "m_thud" if si else None)
            self.stages.append(dict(t=t, caption=s.get("caption", ""), fill=[dict(f) for f in s.get("fill", [])]))
        self.pins = [dict(p, t=self.at(p, 0.5, "m_ping")) for p in sc.get("pins", [])]
        self.arrows = []
        for a in sc.get("arrows", []):
            wp = smooth(wpts(a["pts"]))
            self.arrows.append(dict(a, t=self.at(a, 1.4, "m_draw"), dur=a.get("dur", 1.6), wp=wp, dash=False))
        for a in sc.get("supply", []):
            self.arrows.append(dict(a, t=self.at(a, 1.4, "m_draw"), dur=a.get("dur", 1.4), wp=smooth(wpts(a["pts"])), dash=True,
                                    color=a.get("color", "cyan")))
        self.rulers = []
        for r in sc.get("ruler", []):
            r = dict(r)
            r.setdefault("label", f"{haversine(r['a'], r['b']):,.0f} KM")
            self.rulers.append(dict(r, t=self.at(r, 1.0, "m_draw")))
        self.fronts = [dict(f, t=self.at(f, 1.0, "m_thud"), wp=smooth(wpts(f["pts"]))) for f in sc.get("front", [])]
        self.xs = [dict(x, t=self.at(x, 0.5, "m_thud")) for x in sc.get("xs", [])]
        self.chips = [dict(c, t=self.at(c, 0.6, "m_ping")) for c in sc.get("chips", [])]
        self.big = dict(sc["big"], t=self.at(sc["big"], 1.0, "m_thud")) if sc.get("big") else None
        self.tl = [dict(e, t=float(e.get("at", 0.3 + i * 0.9))) for i, e in enumerate(sc.get("timeline", []))]
        for e in self.tl:
            self.events.append((e["t"], "m_ping"))
        self.panel = dict(sc["panel"], t=self.at(sc["panel"], 0.6, "m_ping")) if sc.get("panel") else None
        self.card = dict(sc["card"], t=0.25) if sc.get("card") else None
        if self.card:
            for i in range(len(self.card["items"])):
                self.events.append((0.7 + i * 0.85, "m_ping"))
        self.text = sc.get("text")
        self.dim = 0.62 if (self.card or self.text) else 0.0
        self.disputed = any(f.get("style") == "disputed" for f in self.fills_raw())

    # --- camera / projection -------------------------------------------------
    def camera(self, t):
        e = ease(t / self.move) if self.move > 0.05 else 1.0
        cx, cy, ww = cam_at(self.cam0, self.cam1, e)
        k = ease(max(t - self.move, 0) / max(self.d - self.move, 0.1))
        ww *= 1 - 0.05 * k                                   # slow push-in while holding
        z = W / ww
        sx = W * (0.30 if self.split else 0.5)
        return cx, cy, z, sx, CEN_Y

    # --- frame ----------------------------------------------------------------
    def frame(self, t):
        cx, cy, z, sx, sy = self.camera(t)
        D = data()
        x0, y0 = cx - sx / z, cy - sy / z
        x1, y1 = cx + (W - sx) / z, cy + (H - sy) / z
        ww = (x1 - x0)
        step = 6 if ww > 0.6 else 4 if ww > 0.3 else 2 if ww > 0.12 else 1

        def scr(a):
            a = a[::step] if step > 1 and len(a) > 40 else a
            return np.column_stack([(a[:, 0] - cx) * z + sx, (a[:, 1] - cy) * z + sy])

        def visible(bb):
            return not (bb[2] < x0 or bb[0] > x1 or bb[3] < y0 or bb[1] > y1)
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        vis = []
        for c in D["c"]:
            for p, bb in zip(c["polys"], c["bb"]):
                if visible(bb):
                    pl = scr(p)
                    md.polygon([tuple(q) for q in pl.tolist()], fill=255)
                    vis.append((c, pl, bb))
        mask = mask.filter(ImageFilter.GaussianBlur(0.8))
        sea = D["sea"].copy()
        halo = mask.filter(ImageFilter.GaussianBlur(16)).point(lambda v: int(v * 0.55))
        shelf = Image.new("RGB", (W, H), (22, 52, 70))
        sea = Image.composite(shelf, sea, ImageChops.multiply(halo, ImageChops.invert(mask)))
        d0 = ImageDraw.Draw(sea)
        gs = 10 if ww > 0.25 else 5 if ww > 0.08 else 1 if ww > 0.02 else 0.5
        for lon in np.arange(-180, 181, gs):
            gx = ((lon + 180) / 360 - cx) * z + sx
            if -2 < gx < W + 2:
                d0.line((gx, 0, gx, H), fill=(20, 33, 47), width=1)
        for lat in np.arange(-80, 81, gs):
            gy = (merc(lat, 0)[1] - cy) * z + sy
            if -2 < gy < H + 2:
                d0.line((0, gy, W, gy), fill=(20, 33, 47), width=1)
        landc = np.zeros((H, W, 3), np.float32) + np.array(LAND, np.float32)
        landc += (D["noise"][:, :, None] - 0.5) * 16
        land = Image.fromarray(np.clip(landc, 0, 255).astype(np.uint8))
        im = Image.composite(land, sea, mask).convert("RGBA")
        # fills
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        hatch = Image.new("L", (W, H), 0)
        hd = ImageDraw.Draw(hatch)
        fills = self.active_fills(t)
        lab = []
        for f, a in fills:
            try:
                c = find(f["c"])
            except KeyError:
                continue
            cc = col(f.get("color", "red"))
            for p, bb in zip(c["polys"], c["bb"]):
                if not visible(bb):
                    continue
                pl = [tuple(q) for q in scr(p).tolist()]
                if f.get("style") == "disputed":
                    hd.polygon(pl, fill=int(255 * a))
                    od.polygon(pl, fill=cc + (int(70 * a),))
                else:
                    od.polygon(pl, fill=cc + (int(150 * a),))
                    od.line(pl + pl[:1], fill=tuple(min(255, int(v * 1.3)) for v in cc) + (int(255 * a),), width=3)
            if f.get("label", f.get("c")) and a > 0.5 and f.get("label", "") != "":
                cen = c["cen"]
                if "lbl" in f:
                    cen = merc(*f["lbl"])
                lab.append((f.get("label", c["label"]).upper(), ((cen[0] - cx) * z + sx, (cen[1] - cy) * z + sy), a, f))
        stripe = ImageChops.multiply(hatch, D["stripes"])
        ov.paste(Image.new("RGBA", (W, H), (240, 220, 160, 150)), (0, 0), stripe)
        im.alpha_composite(ov)
        # borders + rivers
        bd = ImageDraw.Draw(im, "RGBA")
        lw = 1 if ww > 0.5 else 2
        for c, pl, bb in vis:
            bd.line([tuple(q) for q in pl.tolist()] + [tuple(pl[0])], fill=GOLD + (170,), width=lw, joint="curve")
        if ww < 0.45:
            for r, bb in D["r"]:
                if visible(bb):
                    bd.line([tuple(q) for q in scr(r).tolist()], fill=(70, 130, 165, 150), width=2 if ww < 0.1 else 1)
        # names
        if self.sc.get("names", True):
            best = {}
            for c, pl, bb in vis:
                best[id(c)] = max(best.get(id(c), 0), float(pl[:, 0].max() - pl[:, 0].min()))
            for c, pl, bb in vis:
                wpx = float(pl[:, 0].max() - pl[:, 0].min())
                if wpx > 190 and c["label"] and wpx == best[id(c)]:
                    mx, my = pl.mean(0)
                    if 40 < mx < W - 40 and 60 < my < 860:
                        f = font(MONT, 26)
                        bd.text((mx, my), c["label"], font=f, fill=(150, 156, 164, 130), anchor="mm")
        for text, (lx, ly), a, f in lab:
            if 40 < lx < W - 40 and 70 < ly < 850:
                pill(im, (lx, ly), text, 34, alpha=min(a, 1), anchor="m", under=col(f.get("color", "red")))
        # fronts
        glowl = Image.new("RGBA", (W // 2, H // 2), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glowl)
        over = ImageDraw.Draw(im, "RGBA")
        for fr in self.fronts:
            a = ease((t - fr["t"]) / 0.8)
            if a <= 0:
                continue
            pl = scr(fr["wp"])
            pts = [tuple(q) for q in pl.tolist()]
            over.line(pts, fill=(205, 52, 48, int(80 * a)), width=44, joint="curve")
            over.line(pts, fill=(235, 70, 60, int(255 * a)), width=5, joint="curve")
            cl = cum_len(pl)
            for s in np.arange(0, cl[-1], 34):
                k = int(np.searchsorted(cl, s))
                if 0 < k < len(pl):
                    p, q = pl[k - 1], pl[k]
                    n = np.array([-(q[1] - p[1]), q[0] - p[0]]); n /= max(np.hypot(*n), 1e-6)
                    over.line((p[0], p[1], p[0] + n[0] * 16 * a, p[1] + n[1] * 16 * a), fill=(235, 70, 60, int(255 * a)), width=3)
            mid = pts[len(pts) // 2]
            pill(im, (mid[0], mid[1] - 52), fr.get("label", "CONTACT LINE"), 26, anchor="m", under=(235, 70, 60), alpha=a)
        # arrows / supply
        for ar in self.arrows:
            p = (t - ar["t"]) / ar["dur"]
            if p <= 0:
                continue
            pe = ease(min(p, 1))
            pl = scr(ar["wp"])
            cl = cum_len(pl)
            cc = col(ar.get("color", "gold"), GOLD)
            if ar["dash"]:
                pts = upto(pl, cl, pe)
                if len(pts) > 1:
                    over = ImageDraw.Draw(im, "RGBA")
                    sub = np.array(pts)
                    sc_ = cum_len(sub)
                    for s in np.arange((-t * 55) % 26, sc_[-1], 26):
                        k = int(np.searchsorted(sc_, s))
                        if 0 < k < len(sub):
                            u = (s - sc_[k - 1]) / max(sc_[k] - sc_[k - 1], 1e-6)
                            q = sub[k - 1] + (sub[k] - sub[k - 1]) * u
                            over.ellipse((q[0] - 5, q[1] - 5, q[0] + 5, q[1] + 5), fill=cc + (235,), outline=(10, 14, 20, 255))
                    lbl_pt = pts[len(pts) // 2]
            else:
                pts = upto(pl, cl, pe)
                if len(pts) > 1:
                    half = [(x / 2, y / 2) for x, y in pts]
                    gd.line(half, fill=cc + (190,), width=14, joint="curve")
                    over = ImageDraw.Draw(im, "RGBA")
                    over.line(pts, fill=(10, 12, 16, 230), width=13, joint="curve")
                    over.line(pts, fill=cc + (255,), width=8, joint="curve")
                    a_, b_ = np.array(pts[-4 if len(pts) > 4 else 0]), np.array(pts[-1])
                    dv = b_ - a_
                    dv /= max(np.hypot(*dv), 1e-6)
                    nv = np.array([-dv[1], dv[0]])
                    tip = b_ + dv * 22
                    over.polygon([tuple(tip), tuple(b_ - dv * 10 + nv * 22), tuple(b_ - dv * 10 - nv * 22)], fill=cc + (255,), outline=(10, 12, 16, 255))
                    lbl_pt = pts[len(pts) // 2]
            if len(pts) > 1 and ar.get("label") and p > 0.45:
                pill(im, (lbl_pt[0], lbl_pt[1] - 40), ar["label"].upper(), 26, anchor="m", under=cc, alpha=ease((p - 0.45) / 0.3))
        gl = glowl.filter(ImageFilter.GaussianBlur(6)).resize((W, H), Image.BILINEAR)
        im.alpha_composite(gl)
        # rulers
        over = ImageDraw.Draw(im, "RGBA")
        for r in self.rulers:
            p = ease((t - r["t"]) / 0.9)
            if p <= 0:
                continue
            a = np.array(wpts([r["a"]])[0]); b = np.array(wpts([r["b"]])[0])
            sa = ((a - [cx, cy]) * z + [sx, sy]); sb = ((b - [cx, cy]) * z + [sx, sy])
            e_ = sa + (sb - sa) * p
            dash_line(over, [tuple(sa), tuple(e_)], (245, 245, 245, 235), 4, 20, 12, t * 30)
            dv = (sb - sa) / max(np.hypot(*(sb - sa)), 1e-6)
            nv = np.array([-dv[1], dv[0]]) * 16
            for q in (sa, e_):
                over.line((q[0] - nv[0], q[1] - nv[1], q[0] + nv[0], q[1] + nv[1]), fill=(245, 245, 245, 255), width=4)
            if p > 0.8:
                m = (sa + sb) / 2
                pill(im, (m[0], m[1] - 34), r["label"].upper(), 36, anchor="m", fill=(8, 10, 14, 235), under=(245, 245, 245), alpha=ease((p - 0.8) / 0.2))
        # red X
        over = ImageDraw.Draw(im, "RGBA")
        for x in self.xs:
            p = out_back(min(max((t - x["t"]) / 0.35, 0), 1))
            if p <= 0:
                continue
            q = ((np.array(merc(x["lat"], x["lon"])) - [cx, cy]) * z + [sx, sy])
            s = 34 * p
            for w_, c_ in ((16, (10, 10, 12, 255)), (9, (235, 55, 50, 255))):
                over.line((q[0] - s, q[1] - s, q[0] + s, q[1] + s), fill=c_, width=w_)
                over.line((q[0] - s, q[1] + s, q[0] + s, q[1] - s), fill=c_, width=w_)
        # pins
        for pn in self.pins:
            p = out_back(min(max((t - pn["t"]) / 0.4, 0), 1))
            if p <= 0:
                continue
            q = ((np.array(merc(pn["lat"], pn["lon"])) - [cx, cy]) * z + [sx, sy])
            px, py = q
            if not (-50 < px < W + 50 and 50 < py < 870):
                continue
            pulse = (t * 1.4) % 1
            over.ellipse((px - 14 - 30 * pulse, py - 14 - 30 * pulse, px + 14 + 30 * pulse, py + 14 + 30 * pulse), outline=self.acc + (int(200 * (1 - pulse)),), width=3)
            r = 13 * p
            over.ellipse((px - r, py - r, px + r, py + r), fill=self.acc + (255,), outline=(255, 255, 255, 255), width=3)
            dr = pn.get("dir", "r")
            off = {"r": (26, 0, "l"), "l": (-26, 0, "r"), "u": (0, -42, "m"), "d": (0, 52, "m")}[dr]
            pill(im, (px + off[0], py + off[1]), pn["name"].upper(), 28, anchor=off[2], alpha=min(p, 1))
        # big number
        if self.big:
            p = ease((t - self.big["t"]) / 0.35)
            if p > 0:
                f = font(ANTON, 170)
                dd = ImageDraw.Draw(im, "RGBA")
                y = 150 - (1 - p) * 30
                dd.text((W // 2, y), self.big["text"].upper(), font=f, fill=self.acc + (int(255 * p),), anchor="mm", stroke_width=7, stroke_fill=(0, 0, 0, int(255 * p)))
                if self.big.get("sub"):
                    pill(im, (W // 2, y + 120), self.big["sub"].upper(), 32, anchor="m", alpha=p)
        # stage captions
        cap = self.stage_caption(t)
        if cap:
            pill(im, (W // 2, 120), cap[0].upper(), 38, anchor="m", alpha=cap[1])
        # chips
        for i, c in enumerate(self.chips):
            p = out_back(min(max((t - c["t"]) / 0.35, 0), 1))
            if p <= 0:
                continue
            if "lat" in c:
                q = ((np.array(merc(c["lat"], c["lon"])) - [cx, cy]) * z + [sx, sy])
                xy = (q[0], q[1])
            else:
                xy = (180, 150 + i * 70)
            cc = col(c.get("color", "red"))
            pill(im, xy, c["text"].upper(), 30, fill=cc + (245,), under=None, anchor="m" if "lat" in c else "l", alpha=min(p, 1))
        # timeline strip (top)
        if self.tl:
            self.draw_timeline(im, t)
        if self.panel:
            self.draw_panel(im, t)
        if self.dim:
            k = ease(t / 0.4)
            shade = Image.new("RGBA", (W, H), (6, 9, 14, int(255 * self.dim * k)))
            im.alpha_composite(shade)
        if self.card:
            self.draw_card(im, t)
        if self.text:
            self.draw_text(im, t)
        if self.disputed:
            pill(im, (40, 1048), "HATCHED = DISPUTED / SPECIAL STATUS; BORDERS AS COMMONLY RECOGNISED", 20, fill=(8, 10, 14, 200), under=None, fg=(190, 195, 205))
        elif self.sc.get("src"):
            pill(im, (40, 1048), "SOURCE: " + self.sc["src"].upper(), 20, fill=(8, 10, 14, 200), under=None, fg=(190, 195, 205))
        return im

    def active_fills(self, t):
        out = [(f, ease((t - f["t"]) / 0.6)) for f in self.fills]
        for i, s in enumerate(self.stages):
            nxt = self.stages[i + 1]["t"] if i + 1 < len(self.stages) else 1e9
            a_in = ease((t - s["t"]) / 0.7)
            a_out = 1 - ease((t - nxt) / 0.7)
            a = a_in * a_out if i + 1 < len(self.stages) else a_in
            if a > 0:
                out += [(f, a) for f in s["fill"]]
        return [(f, a) for f, a in out if a > 0]

    def stage_caption(self, t):
        best = None
        for i, s in enumerate(self.stages):
            if s["caption"] and t >= s["t"]:
                nxt = self.stages[i + 1]["t"] if i + 1 < len(self.stages) else 1e9
                best = (s["caption"], ease((t - s["t"]) / 0.4) * (1 - ease((t - nxt + 0.3) / 0.3)))
        return best

    def draw_timeline(self, im, t):
        d = ImageDraw.Draw(im, "RGBA")
        n = len(self.tl)
        x0, x1, y = 200, W - 200, 78
        d.rectangle((x0 - 40, y - 46, x1 + 40, y + 76), fill=(8, 10, 14, 200))
        d.line((x0, y, x1, y), fill=(90, 96, 106, 255), width=4)
        last = max([i for i, e in enumerate(self.tl) if t >= e["t"]], default=-1)
        if last >= 0:
            xe = x0 + (x1 - x0) * last / max(n - 1, 1)
            d.line((x0, y, xe, y), fill=self.acc + (255,), width=6)
        for i, e in enumerate(self.tl):
            x = x0 + (x1 - x0) * i / max(n - 1, 1)
            on = t >= e["t"]
            p = out_back(min(max((t - e["t"]) / 0.3, 0), 1)) if on else 0
            r = 8 + 6 * p
            d.ellipse((x - r, y - r, x + r, y + r), fill=(self.acc if on else (70, 76, 86)) + (255,), outline=(255, 255, 255, 255 if on else 90), width=2)
            d.text((x, y + 38), e["label"].upper(), font=font(MONT, 22), fill=(245, 245, 245, 255) if on else (120, 126, 136, 255), anchor="mm")

    def draw_panel(self, im, t):
        p = self.panel
        a = ease((t - p["t"]) / 0.5)
        if a <= 0:
            return
        x0 = int(W - 780 + (1 - a) * 160)
        box = (x0, 120, x0 + 700, 820)
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle(box, fill=(8, 10, 14, int(232 * a)), outline=self.acc + (int(255 * a),), width=3)
        d.rectangle((box[0], box[1], box[2], box[1] + 8), fill=self.acc + (int(255 * a),))
        d.text((box[0] + 40, box[1] + 56), p.get("title", "").upper(), font=font(MONT, 30), fill=self.acc + (int(255 * a),), anchor="lm")
        y = box[1] + 150
        if p.get("big"):
            m = re.match(r"^(\D*)([\d,.]+)(.*)$", str(p["big"]))
            txt = str(p["big"])
            if m:
                try:
                    v = float(m.group(2).replace(",", ""))
                    k = ease((t - p["t"] - 0.2) / 1.0)
                    dec = len(m.group(2).split(".")[1]) if "." in m.group(2) else 0
                    num = f"{v * k:,.{dec}f}"
                    txt = m.group(1) + num + m.group(3)
                except ValueError:
                    pass
            d.text((box[0] + 40, y + 50), txt.upper(), font=font(ANTON, 150), fill=(245, 245, 245, int(255 * a)), anchor="lm", stroke_width=4, stroke_fill=(0, 0, 0, int(255 * a)))
            y += 160
        if p.get("caption"):
            for i, ln in enumerate(self.wrap(d, p["caption"], font(MONT, 30), 620)):
                d.text((box[0] + 40, y + i * 42), ln, font=font(MONT, 30), fill=(205, 210, 220, int(255 * a)), anchor="lm")
            y += 42 * (i + 1) + 30
        bars = p.get("bars", [])
        if bars:
            mx = max(b["value"] for b in bars)
            for i, b in enumerate(bars):
                by = y + i * 66
                k = ease((t - p["t"] - 0.5 - i * 0.2) / 0.7)
                d.text((box[0] + 40, by), b["label"].upper(), font=font(MONT, 24), fill=(205, 210, 220, 255), anchor="lm")
                d.rectangle((box[0] + 40, by + 18, box[0] + 40 + 480 * b["value"] / mx * k, by + 38), fill=(self.acc if i == 0 else (110, 118, 130)) + (255,))
                d.text((box[2] - 40, by + 28), f"{b['value']:,}", font=font(MONT, 26), fill=(245, 245, 245, int(255 * k)), anchor="rm")

    @staticmethod
    def wrap(d, text, f, maxw):
        out, cur = [], ""
        for w in text.split():
            if d.textlength((cur + " " + w).strip(), font=f) > maxw and cur:
                out.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        return out + [cur]

    def draw_card(self, im, t):
        c = self.card
        d = ImageDraw.Draw(im, "RGBA")
        a = ease(t / 0.4)
        d.text((W // 2, 150), c["title"].upper(), font=font(ANTON, 96), fill=self.acc + (int(255 * a),), anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0, int(255 * a)))
        for i, it in enumerate(c["items"]):
            p = out_back(min(max((t - 0.7 - i * 0.85) / 0.4, 0), 1))
            if p <= 0:
                continue
            y = 330 + i * 150
            x = 330 - (1 - min(p, 1)) * 60
            al = int(255 * min(p, 1))
            d.rectangle((x, y - 52, x + 1260, y + 52), fill=(8, 10, 14, int(235 * min(p, 1))), outline=self.acc + (al,), width=3)
            d.rectangle((x, y - 52, x + 104, y + 52), fill=self.acc + (al,))
            d.text((x + 52, y), str(i + 1), font=font(ANTON, 80), fill=(10, 10, 12, al), anchor="mm")
            d.text((x + 140, y), it.upper(), font=font(MONT, 42), fill=(245, 245, 245, al), anchor="lm")

    def draw_text(self, im, t):
        a = ease(t / 0.4)
        d = ImageDraw.Draw(im, "RGBA")
        txt = self.text.upper()
        f = font(ANTON, 150)
        lines = self.wrap(d, txt, f, W - 360)
        y0 = 430 - (len(lines) - 1) * 80
        for i, ln in enumerate(lines):
            d.text((W // 2, y0 + i * 165), ln, font=f, fill=(245, 245, 245, int(255 * a)), anchor="mm", stroke_width=6, stroke_fill=(0, 0, 0, int(255 * a)))
        d.rectangle((W // 2 - 130, y0 + len(lines) * 165 - 60, W // 2 + 130, y0 + len(lines) * 165 - 52), fill=self.acc + (int(255 * a),))


# ----------------------------------------------------------------------------- render + planning
def render_scene(sc, prof, idx, chap, d, out: Path, nframes, trans=None):
    S = Scene(sc, prof, d, _CAM[0], idx)
    cmd = [core.FF, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", FPS, "-i", "-",
           "-vf", f"{prof['grade']}{core._fade(trans)},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-threads", "3", out]
    p = subprocess.Popen([str(c) for c in cmd], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(nframes):
            im = S.frame(i / FPS).convert("RGB")
            p.stdin.write(im.tobytes())
        p.stdin.close()
        err = p.stderr.read().decode()[-600:]
        p.wait()
        if p.returncode:
            raise RuntimeError(err)
    finally:
        if p.poll() is None:
            p.kill()
    cx, cy, z, sx, sy = S.camera(S.d)
    _CAM[0] = (cx, cy, W / z)
    return S.events


def auto_scene(b):
    return {"fill": [], "view": {"lat": 30, "lon": 20, "span": 150}}


def plan_shots(beats, timing, prof, work: Path, broll=()):
    shots, events, t = [], [], 0.0
    _CAM[0] = None
    (work / "shots").mkdir(exist_ok=True)
    chap, since_flash = 0, 99
    for bi, (b, tm) in enumerate(zip(beats, timing)):
        D = tm["dur"]
        scs = b.get("scene") or auto_scene(b)
        scs = scs if isinstance(scs, list) else [scs]
        ws = [float(s.get("w", 1)) for s in scs]
        mood = core.mood(b["text"])
        if bi > 0 and b.get("chapter", bi % 4 == 0):
            chap += 1
        st = t
        for si, sc in enumerate(scs):
            sd = D * ws[si] / sum(ws)
            f0, f1 = round(st * FPS), round((st + sd) * FPS)
            nf = max(f1 - f0, 2)
            trans = None
            if si == 0 and bi > 0:
                if mood == "reveal" and since_flash >= 8:
                    trans = "flash"; since_flash = 0
                elif b.get("chapter", bi % 4 == 0):
                    trans = "dip"
            since_flash += 1
            sp = work / "shots" / f"m{bi:03d}_{si}.mp4"
            evs = render_scene(sc, prof, bi * 3 + si, chap, nf / FPS, sp, nf, trans)
            events += [(st + e, k) for e, k in evs]
            if trans == "flash":
                events.append((max(st - 0.05, 0), "m_thud"))
            shots.append((sp, nf / FPS))
            st += nf / FPS
        t = st
        t = max(t, tm["start"] + D) if abs(t - (tm["start"] + D)) < 0.1 else t
    return shots, [], events


def sfx_bank(sr):
    rng = np.random.default_rng(21)
    n = int(0.32 * sr)
    x = rng.standard_normal(n).astype(np.float32)
    env = np.minimum(np.arange(n) / (0.04 * sr), 1) * np.exp(-np.arange(n) / sr * 7)
    pen = np.convolve(x, np.ones(6) / 6, "same") * env
    pen = pen - np.convolve(pen, np.ones(60) / 60, "same")
    draw = 0.34 * pen / (np.abs(pen).max() + 1e-6)
    m = int(0.35 * sr)
    tt = np.arange(m) / sr
    ping = (np.sin(2 * np.pi * 988 * tt) * np.exp(-tt * 14) + 0.4 * np.sin(2 * np.pi * 1480 * tt) * np.exp(-tt * 20)).astype(np.float32) * 0.2
    k = int(0.9 * sr)
    t2 = np.arange(k) / sr
    thud = (np.sin(2 * np.pi * np.cumsum(52 + 60 * np.exp(-t2 * 16)) / sr) * np.exp(-t2 * 5.5)).astype(np.float32) * 0.75
    w = int(0.9 * sr)
    y = np.zeros(w, np.float32); s = 0.0
    xs = rng.standard_normal(w).astype(np.float32)
    a = np.linspace(0.03, 0.22, w)
    for i in range(w):
        s += a[i] * (xs[i] - s); y[i] = s
    pan = 0.22 * y * np.sin(np.linspace(0, np.pi, w)) ** 2 / (np.abs(y).max() + 1e-6)
    return {"m_draw": draw, "m_ping": ping, "m_thud": thud, "m_pan": pan}
