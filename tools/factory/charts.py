"""Charts engine (Barrel Brief / Rate Desk / Hidden Markets): data scenes drawn locally with PIL, piped to ffmpeg. profile engine == 'charts'.
Style family = the competitor look (light paper-grey UI, heavy condensed Anton titles, red = the problem, blue = the baseline,
SOURCE line under every chart, red % pill callouts), polished: smooth easing, count-up numbers, 2x supersampled shapes, big phone-size type.

A beat may carry  "scene": {...}  or a list [{...},{...}] shown one after another across the beat (optional "w" weight).
Common keys: title, source ("World Bank"), prefix/suffix/dec (number format), pills:[{"text","x","y","at"}] free annotations.
  bars     {"items":[{"label","value","icon"?}],"hl":[idx],"hline":{"value","label"}}      horizontal bars, count-up
  columns  {"items":[...],"hl":[idx]}                                                      vertical columns
  line     {"x":[labels],"series":[{"name","values":[..]}] or "values":[..],"area":true,"call":[{"i":idx,"text":"+82%"}]}
  race     {"years":[..],"rows":[{"label","values":[per year]}],"top":6}                   bar-chart race, soft-rank motion
  stack    {"parts":[{"label","value"}],"mode":"bar|donut"}                                proportions
  waterfall{"items":[{"label","value"},..,{"label":"Total","total":true}]}
  map      {"region":[lon0,lat0,lon1,lat1],"items":[{"country","color":"red|blue|accent","label"?,"value"?}],"arcs":[{"from","to"}],"mode":"fill|bubble"}
  big      {"big":42,"caption":"US gallons in a barrel","icon":"barrel","tone":"red|blue|ink"}
  compare  {"left":{"label","value","note","icon"},"right":{..},"win":"left|right"}
  text     {"text":"Watch the *barrel*. The pump *follows*."}          (*word* = red)
icons: drop barrel coin bank ship flame house globe up down."""
import json
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

from . import core, assets
from .tech import font, ease, out_back, mix, prog, tokens, fit_rich, wrap_tokens

W, H, FPS = 1920, 1080, 30
BOT = 826
ANTON, BODY = "Anton-Regular.ttf", "Montserrat-ExtraBold.ttf"
INK, MUTE, GRID = (22, 27, 38), (96, 105, 120), (196, 201, 210)
RED, BLUE, SLATE, WHITE = (201, 45, 66), (30, 86, 168), (118, 132, 156), (255, 255, 255)
SERIES = [INK, RED, BLUE, None]
_BG = {}


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def bg_image():
    if "bg" not in _BG:
        import numpy as np
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        k = (yy / H)[..., None]
        a, b = np.array([241, 243, 246], np.float32), np.array([212, 216, 223], np.float32)
        im = a + (b - a) * k
        r = np.sqrt(((xx - W * 0.5) / (W * 0.62)) ** 2 + ((yy - H * 0.42) / (H * 0.7)) ** 2)[..., None]
        im = im + 9 * np.clip(1 - r, 0, 1) - 7 * np.clip(r - 0.9, 0, 1)
        im = im + np.random.default_rng(3).normal(0, 1.1, (H, W, 1)).astype(np.float32)     # fine paper grain
        _BG["bg"] = Image.fromarray(np.clip(im, 0, 255).astype("uint8")).convert("RGBA")
    return _BG["bg"]


def fmt(v, sc, dec=None):
    dec = sc.get("dec", 0) if dec is None else dec
    s = f"{abs(v):,.{dec}f}"
    sign = "-" if v < 0 and float(s.replace(",", "")) > 0 else ""
    return f"{sign}{sc.get('prefix', '')}{s}{sc.get('suffix', '')}"


class Cv:
    """Per-frame canvas: shapes on a 2x layer (anti-aliased after reduce), text on a 1x layer."""

    def __init__(self):
        self.S = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0)); self.sd = ImageDraw.Draw(self.S)
        self.T = Image.new("RGBA", (W, H), (0, 0, 0, 0)); self.td = ImageDraw.Draw(self.T)

    @staticmethod
    def c(col, a=255):
        return tuple(col[:3]) + (int(max(0, min(255, a))),)

    def rect(self, box, r=0, fill=None, a=255, outline=None, width=2):
        x0, y0, x1, y1 = box
        if x1 - x0 < 1 or y1 - y0 < 1:
            return
        r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
        self.sd.rounded_rectangle((x0 * 2, y0 * 2, x1 * 2, y1 * 2), r * 2, fill=self.c(fill, a) if fill else None,
                                  outline=self.c(outline, a) if outline else None, width=int(width * 2))

    def line(self, pts, w, col, a=255):
        if len(pts) >= 2:
            self.sd.line([(x * 2, y * 2) for x, y in pts], fill=self.c(col, a), width=max(int(w * 2), 1), joint="curve")

    def poly(self, pts, col, a=255):
        if len(pts) >= 3:
            self.sd.polygon([(x * 2, y * 2) for x, y in pts], fill=self.c(col, a))

    def ell(self, cx, cy, r, fill=None, a=255, outline=None, width=2):
        self.sd.ellipse(((cx - r) * 2, (cy - r) * 2, (cx + r) * 2, (cy + r) * 2), fill=self.c(fill, a) if fill else None,
                        outline=self.c(outline, a) if outline else None, width=int(width * 2))

    def text(self, x, y, s, f, col, a=255, anchor="la"):
        if a > 3:
            self.td.text((x, y), s, font=f, fill=self.c(col, a), anchor=anchor)

    def pill(self, cx, cy, s, size=46, col=RED, fg=WHITE, a=255, scale=1.0):
        f = font(ANTON, size * scale)
        w = f.getlength(s) + 44 * scale
        h = size * 1.35 * scale
        self.rect((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), h / 2, col, a)
        self.text(cx, cy + 2, s, f, fg, a, "mm")

    def done(self, base):
        im = base.copy()
        im.alpha_composite(self.S.reduce(2))
        im.alpha_composite(self.T)
        return im.convert("RGB")


class Ctx:
    def __init__(self, prof, d):
        self.prof, self.d = prof, d
        self.acc = rgb(prof.get("accent", "#e8b04a"))
        self.events = []
        self.top = 100
        self.A = max(min(d * 0.62, 2.6), 0.9)       # animation span: everything finishes well before the scene ends
        self.cols = [BLUE, RED, self.acc, SLATE, INK, (60, 160, 120)]


# ----------------------------------------------------------------------------- shared pieces
def header(base, sc, ctx):
    d = ImageDraw.Draw(base)
    y = 62
    if sc.get("title"):
        for s in range(88, 50, -4):
            f = font(ANTON, s)
            if f.getlength(sc["title"].upper()) <= 1640:
                break
        d.text((W / 2, y), sc["title"].upper(), font=f, fill=INK, anchor="mt")
        y += int(s * 1.28)
    if sc.get("source"):
        d.text((W / 2, y - 4), "SOURCE: " + sc["source"].upper(), font=font(BODY, 25), fill=MUTE, anchor="mt")
        y += 40
    ctx.top = y + 26 if (sc.get("title") or sc.get("source")) else 100


def count_events(ctx, t0, dur, n=10):
    for i in range(n):
        ctx.events.append((t0 + dur * i / n, "ctick"))


def nice_ticks(lo, hi, n=4):
    span = max(hi - lo, 1e-9)
    raw = span / n
    p = 10 ** math.floor(math.log10(raw))
    step = min((m * p for m in (1, 2, 2.5, 5, 10) if m * p >= raw), default=p)
    a = math.floor(lo / step) * step
    b = math.ceil(hi / step) * step
    return [a + i * step for i in range(int(round((b - a) / step)) + 1)], step


def draw_pills(sc, ctx, cv, t):
    for p in sc.get("pills", []):
        e = out_back((t - p.get("at", 1.0)) / 0.35)
        if e > 0.02:
            cv.pill(p["x"], p["y"], p["text"], p.get("size", 44), RED if p.get("tone", "red") == "red" else BLUE, WHITE, 255 * min(e, 1), max(e, 0.1))
    for p in sc.get("pills", []):
        ctx.events.append((p.get("at", 1.0), "cpop"))


# ----------------------------------------------------------------------------- icons (drawn locally)
def icon(cv, name, cx, cy, s, col=INK, a=255):
    k = s / 100.0
    P = lambda pts: [(cx + x * k, cy + y * k) for x, y in pts]
    if name == "drop":
        cv.poly(P([(0, -50), (-30, -6), (-36, 14), (0, 14), (36, 14), (30, -6)]), col, a)
        cv.ell(cx, cy + 14 * k, 36 * k, col, a)
    elif name == "barrel":
        cv.rect((cx - 36 * k, cy - 48 * k, cx + 36 * k, cy + 48 * k), 22 * k, col, a)
        for yy in (-24, 24):
            cv.rect((cx - 38 * k, cy + yy * k - 4 * k, cx + 38 * k, cy + yy * k + 4 * k), 2, WHITE, a)
    elif name == "coin":
        cv.ell(cx, cy, 46 * k, col, a)
        cv.ell(cx, cy, 36 * k, None, a, WHITE, 3)
        cv.text(cx, cy + 2, "$", font(ANTON, 58 * k), WHITE, a, "mm")
    elif name == "bank":
        cv.poly(P([(0, -48), (-50, -22), (50, -22)]), col, a)
        for xx in (-34, -11, 11, 34):
            cv.rect((cx + (xx - 6) * k, cy - 16 * k, cx + (xx + 6) * k, cy + 28 * k), 1, col, a)
        cv.rect((cx - 50 * k, cy + 34 * k, cx + 50 * k, cy + 46 * k), 2, col, a)
    elif name == "ship":
        cv.poly(P([(-54, 6), (54, 6), (36, 38), (-36, 38)]), col, a)
        for i, xx in enumerate((-36, -12, 12)):
            cv.rect((cx + xx * k, cy - 22 * k, cx + (xx + 22) * k, cy + 2 * k), 1, SLATE if i % 2 else col, a)
    elif name == "flame":
        cv.poly(P([(0, -52), (26, -14), (36, 14), (22, 42), (0, 50), (-22, 42), (-36, 14), (-22, -10), (-8, -4)]), col, a)
        cv.poly(P([(0, 4), (14, 26), (0, 42), (-14, 26)]), WHITE, int(a * 0.85))
    elif name == "house":
        cv.poly(P([(0, -50), (-52, -4), (52, -4)]), col, a)
        cv.rect((cx - 38 * k, cy - 6 * k, cx + 38 * k, cy + 44 * k), 2, col, a)
        cv.rect((cx - 9 * k, cy + 14 * k, cx + 9 * k, cy + 44 * k), 2, WHITE, a)
    elif name == "globe":
        cv.ell(cx, cy, 46 * k, None, a, col, 5)
        cv.ell(cx, cy, 20 * k, None, a, col, 4)
        cv.line(P([(-46, 0), (46, 0)]), 4 * k, col, a)
        cv.line(P([(0, -46), (0, 46)]), 4 * k, col, a)
    elif name in ("up", "down"):
        sg = -1 if name == "up" else 1
        c2 = RED if name == "up" else BLUE
        cv.poly(P([(0, sg * 46), (-40, -sg * 4), (-14, -sg * 4), (-14, -sg * 46), (14, -sg * 46), (14, -sg * 4), (40, -sg * 4)]), c2, a)


# ----------------------------------------------------------------------------- scenes: bars / columns
def s_bars(sc, ctx, base):
    items = sc["items"][:7]
    n = len(items)
    hl = sc.get("hl", [])
    hl = hl if isinstance(hl, list) else [hl]
    top, vmax = ctx.top, float(sc.get("max") or max(abs(float(i["value"])) for i in items) or 1)
    rh = min(118, (BOT - top - 10) / n)
    ic = any(i.get("icon") for i in items)
    lf = font(BODY, 42)
    lw = min(max(lf.getlength(i["label"]) for i in items), 560)
    x0 = 120 + (96 if ic else 0) + lw + 36
    x1 = W - 120 - 230
    A = ctx.A
    for i in range(n):
        ctx.events.append((0.25 + i * A * 0.09, "cpop"))
    count_events(ctx, 0.3, A * 0.7)
    d = ImageDraw.Draw(base)
    for i, it in enumerate(items):
        yc = top + rh * (i + 0.5)
        d.text((x0 - 30, yc), it["label"], font=lf, fill=INK, anchor="rm")
    d.line((x0, top, x0, top + rh * n), fill=GRID, width=3)

    def frame(t, cv):
        for i, it in enumerate(items):
            yc = top + rh * (i + 0.5)
            st = 0.25 + i * A * 0.09
            e = ease((t - st) / (A * 0.62))
            v = float(it["value"])
            col = RED if i in hl else BLUE
            wv = (x1 - x0) * abs(v) / vmax * e
            bh = rh * 0.62
            cv.rect((x0, yc - bh / 2, x0 + wv, yc + bh / 2), 8, col)
            if it.get("icon"):
                icon(cv, it["icon"], 120 + 40, yc, 70, INK, 255 * min(e * 3, 1))
            cv.text(x0 + wv + 22, yc + 2, fmt(v * e, sc), font(ANTON, 60), RED if i in hl else INK, 255 * min(e * 4, 1), "lm")
        if sc.get("hline"):
            hv = sc["hline"]
            hx = x0 + (x1 - x0) * float(hv["value"]) / vmax
            he = ease((t - A * 0.5) / 0.5)
            for yy in range(int(top), int(top + (rh * n) * he), 26):
                cv.line([(hx, yy), (hx, yy + 14)], 4, INK)
            cv.pill(hx, top - 18, hv.get("label", ""), 34, INK, WHITE, 255 * he)
        draw_pills(sc, ctx, cv, t)
    return frame


def s_columns(sc, ctx, base):
    items = sc["items"][:8]
    n = len(items)
    hl = sc.get("hl", [])
    hl = hl if isinstance(hl, list) else [hl]
    top = ctx.top + 70
    bot = BOT - 90
    vmax = float(sc.get("max") or max(float(i["value"]) for i in items))
    L, R = 150, W - 150
    slot = (R - L) / n
    bw = min(slot * 0.62, 190)
    A = ctx.A
    count_events(ctx, 0.3, A * 0.7)
    d = ImageDraw.Draw(base)
    d.line((L - 20, bot, R + 20, bot), fill=GRID, width=3)
    lf = font(BODY, 36 if n > 6 else 40)
    for i, it in enumerate(items):
        d.text((L + slot * (i + 0.5), bot + 18), it["label"], font=lf, fill=INK, anchor="mt")

    def frame(t, cv):
        for i, it in enumerate(items):
            e = ease((t - 0.2 - i * A * 0.08) / (A * 0.6))
            v = float(it["value"])
            cx = L + slot * (i + 0.5)
            hh = (bot - top) * v / vmax * e
            cv.rect((cx - bw / 2, bot - hh, cx + bw / 2, bot), 8, RED if i in hl else BLUE)
            cv.text(cx, bot - hh - 14, fmt(v * e, sc), font(ANTON, 56), RED if i in hl else INK, 255 * min(e * 4, 1), "ms")
        draw_pills(sc, ctx, cv, t)
    return frame


# ----------------------------------------------------------------------------- line / area
def s_line(sc, ctx, base):
    series = sc.get("series") or [{"name": "", "values": sc["values"]}]
    n = len(series[0]["values"])
    allv = [float(v) for s in series for v in s["values"]]
    lo = sc.get("min", 0 if min(allv) >= 0 and not sc.get("tight") else min(allv))
    ticks, step = nice_ticks(lo, max(allv) * 1.06, 4)
    lo, hi = ticks[0], ticks[-1]
    top = ctx.top + 36
    bot = BOT - 64
    L, R = 250, W - 150
    X = lambda i: L + (R - L) * i / max(n - 1, 1)
    Y = lambda v: bot - (bot - top) * (v - lo) / (hi - lo)
    d = ImageDraw.Draw(base)
    f = font(BODY, 30)
    for tv in ticks:
        d.line((L - 10, Y(tv), R + 10, Y(tv)), fill=GRID, width=2)
        d.text((L - 28, Y(tv)), fmt(tv, sc, 0 if step >= 1 else 1), font=f, fill=MUTE, anchor="rm")
    xl = sc.get("x") or []
    stepx = max(1, math.ceil(len(xl) / 8))
    for i, lab in enumerate(xl):
        if i % stepx == 0 or i == len(xl) - 1:
            d.text((X(i), bot + 20), str(lab), font=f, fill=MUTE, anchor="mt")
    A = ctx.A * 1.25
    calls = sc.get("call", [])
    for c in calls:
        ctx.events.append((A * c["i"] / max(n - 1, 1) * 0.9 + 0.3, "cpop"))
    count_events(ctx, 0.3, A * 0.9, 8)
    cols = [c or ctx.acc for c in SERIES]

    def frame(t, cv):
        r = ease((t - 0.3) / A) * (n - 1)
        for si, s in enumerate(series):
            vs = [float(v) for v in s["values"]]
            k = int(math.floor(r))
            pts = [(X(i), Y(vs[i])) for i in range(min(k, n - 1) + 1)]
            if k < n - 1:
                fr = r - k
                pts.append((X(k) + (X(k + 1) - X(k)) * fr, Y(vs[k] + (vs[k + 1] - vs[k]) * fr)))
            if sc.get("area", True) and len(series) == 1 and len(pts) > 1:
                cv.poly(pts + [(pts[-1][0], bot), (pts[0][0], bot)], BLUE, 46)
            cv.line(pts, 8, cols[si % 4])
            hx, hy = pts[-1]
            col = cols[si % 4]
            if len(series) > 1:
                cv.text(L + 20 + si * 330, top - 28, "● " + s.get("name", ""), font(BODY, 32), col, 255, "lm")
            if not calls:
                cv.ell(hx, hy, 13, RED); cv.ell(hx, hy, 13, None, 255, WHITE, 4)
                vv = vs[min(k, n - 1)] + ((vs[min(k + 1, n - 1)] - vs[min(k, n - 1)]) * (r - k) if k < n - 1 else 0)
                cv.pill(min(hx, R - 70), hy - 62, fmt(vv, sc), 44, RED if si == 0 else col)
        for c in calls:
            fr_i = c["i"] / max(n - 1, 1)
            e = out_back((r / max(n - 1, 1) - fr_i) / 0.06 + 0.0)
            if r >= c["i"] and e > 0.02:
                px, py = X(c["i"]), Y(float(series[c.get("s", 0)]["values"][c["i"]]))
                cv.line([(px, py), (px, py - 54)], 4, RED, 200)
                cv.ell(px, py, 15 * max(min(e, 1.2), 0.2), RED); cv.ell(px, py, 15, None, 255, WHITE, 4)
                cv.pill(px, py - 82, c["text"], 50, RED, WHITE, 255 * min(e, 1), max(e, 0.1))
        draw_pills(sc, ctx, cv, t)
    return frame


# ----------------------------------------------------------------------------- race
def s_race(sc, ctx, base):
    rows, years = sc["rows"], sc["years"]
    nk = len(years)
    topn = sc.get("top", 6)
    top = ctx.top + 10
    rh = (BOT - top - 10) / topn
    vmax = max(max(float(v) for v in r["values"]) for r in rows) * 1.0
    lf = font(BODY, 38)
    lw = min(max(lf.getlength(r["label"]) for r in rows), 520)
    x0, x1 = 120 + lw + 30, W - 120 - 220
    cols = ctx.cols
    A = ctx.A * 1.6
    count_events(ctx, 0.3, A * 0.9, 12)

    def val(r, p):
        k = min(int(p), nk - 2) if nk > 1 else 0
        fr = ease(p - k) if nk > 1 else 0
        return float(r["values"][k]) + (float(r["values"][min(k + 1, nk - 1)]) - float(r["values"][k])) * fr

    def frame(t, cv):
        p = min(max((t - 0.3) / A, 0), 1) * (nk - 1)
        cur = [val(r, p) for r in rows]
        yr = years[min(int(round(p)), nk - 1)]
        cv.text(W - 120, BOT - 20, str(yr), font(ANTON, 230), (200, 205, 214), 255, "rs")
        k = vmax * 0.012
        for i, r in enumerate(rows):
            rank = sum(1 / (1 + math.exp(max(min(-(cur[j] - cur[i] + (j < i) * 1e-6) / k, 50), -50))) for j in range(len(rows)) if j != i)
            a = max(0.0, min(1.0, topn - 0.35 - rank)) * 255
            yc = top + rh * (rank + 0.5)
            bh = rh * 0.66
            wv = (x1 - x0) * cur[i] / vmax
            cv.rect((x0, yc - bh / 2, x0 + wv, yc + bh / 2), 8, cols[i % len(cols)], a)
            cv.text(x0 - 26, yc, r["label"], lf, INK, a, "rm")
            cv.text(x0 + wv + 20, yc + 2, fmt(cur[i], sc), font(ANTON, 54), INK, a, "lm")
        draw_pills(sc, ctx, cv, t)
    return frame


# ----------------------------------------------------------------------------- stack / donut
def s_stack(sc, ctx, base):
    parts = sc["parts"]
    tot = sum(float(p["value"]) for p in parts)
    cols = ctx.cols
    A = ctx.A
    count_events(ctx, 0.3, A * 0.7)
    donut = sc.get("mode") == "donut"
    top = ctx.top
    if donut:
        cx, cy, R, th = 640, (top + BOT) / 2 + 10, 250, 100
    else:
        L, Rr, y0, bh = 120, W - 120, top + 120, 170

    def frame(t, cv):
        e = ease((t - 0.25) / (A * 0.8))
        acc = 0.0
        for i, p in enumerate(parts):
            v = float(p["value"])
            share = v / tot
            col = RED if i in sc.get("hl", [0 if sc.get("hl0") else -1]) else cols[i % len(cols)]
            if donut:
                a0, a1 = -90 + 360 * acc, -90 + 360 * min(acc + share, e)
                acc += share
                if a1 > a0 + 0.2:
                    cv.sd.pieslice(((cx - R) * 2, (cy - R) * 2, (cx + R) * 2, (cy + R) * 2), a0, a1, fill=cv.c(col))
                ly = cy - 170 + i * 120 if len(parts) <= 4 else cy - 220 + i * 100
                cv.rect((1040, ly - 24, 1090, ly + 26), 8, col, 255 * min(e * 3, 1))
                cv.text(1114, ly - 40, p["label"], font(BODY, 38), INK, 255 * min(e * 3, 1), "lt")
                cv.text(1114, ly + 8, fmt(v * e if e < 1 else v, sc), font(ANTON, 52), col, 255 * min(e * 3, 1), "lt")
            else:
                xa = L + (Rr - L) * acc
                xb = L + (Rr - L) * min(acc + share, e)
                acc += share
                if xb > xa:
                    cv.rect((xa, y0, xb, y0 + bh), 0, col)
                    cv.line([(xa, y0), (xa, y0 + bh)], 4, (241, 243, 246))
                mid = (xa + L + (Rr - L) * acc) / 2
                narrow = (Rr - L) * share < 260
                ly = y0 + bh + 34 + (170 if narrow and i % 2 else 0)
                al = 255 * ease((e - acc + share) / max(share, 0.05))
                cv.line([(mid, y0 + bh + 8), (mid, ly - 8)], 3, col, al * 0.6)
                cv.text(mid, ly, p["label"], font(BODY, 34), INK, al, "ma")
                cv.text(mid, ly + 44, fmt(v * min(e, 1), sc), font(ANTON, 66), col, al, "ma")
        if donut:
            cv.ell(cx, cy, R - th, (241, 243, 246), 255)
            cv.text(cx, cy, fmt(tot * e, sc), font(ANTON, 90), INK, 255, "mm")
        draw_pills(sc, ctx, cv, t)
    return frame


# ----------------------------------------------------------------------------- waterfall
def s_waterfall(sc, ctx, base):
    items = sc["items"]
    n = len(items)
    cum, cur = [], 0.0
    for i, it in enumerate(items):
        if it.get("total"):
            if i == 0 and it.get("value") is not None:
                cur = float(it["value"])
            cum.append((0.0, cur))
        else:
            v = float(it["value"])
            cum.append((cur, cur + v)); cur += v
    lo = min(0, min(min(a, b) for a, b in cum))
    hi = max(max(a, b) for a, b in cum) * 1.12
    top, bot = ctx.top + 60, BOT - 100
    L, R = 150, W - 150
    slot = (R - L) / n
    bw = min(slot * 0.6, 190)
    Y = lambda v: bot - (bot - top) * (v - lo) / (hi - lo)
    A = ctx.A
    count_events(ctx, 0.3, A * 0.6, 6)
    d = ImageDraw.Draw(base)
    d.line((L - 20, Y(0), R + 20, Y(0)), fill=GRID, width=3)
    lf = font(BODY, 36)
    for i, it in enumerate(items):
        lab = it["label"]
        d.text((L + slot * (i + 0.5), bot + 26), lab, font=lf, fill=INK, anchor="mt")

    def frame(t, cv):
        for i, it in enumerate(items):
            e = ease((t - 0.25 - i * A * 0.16) / (A * 0.34))
            a, b = cum[i]
            cx = L + slot * (i + 0.5)
            tot = it.get("total")
            col = INK if tot else (BLUE if float(it["value"]) >= 0 else RED)
            ya, yb = Y(a), Y(a + (b - a) * e)
            cv.rect((cx - bw / 2, min(ya, yb), cx + bw / 2, max(ya, yb)), 6, col)
            if i < n - 1 and e > 0.9:
                nxt = cum[i + 1][0] if not items[i + 1].get("total") else b
                cv.line([(cx + bw / 2, Y(b)), (cx + slot - bw / 2, Y(b))], 3, MUTE, 160)
            val = b - a
            s = fmt(val, sc) if tot else (("+" if val >= 0 else "") + fmt(val, sc))
            if val < 0 and not tot:
                s = fmt(val, sc)
            cv.text(cx, min(ya, yb) - 14, s, font(ANTON, 58), col, 255 * min(e * 3, 1), "ms")
        draw_pills(sc, ctx, cv, t)
    return frame


# ----------------------------------------------------------------------------- map
_NE = []
ALIAS = {"USA": "United States of America", "US": "United States of America", "UK": "United Kingdom", "UAE": "United Arab Emirates"}


def countries():
    if not _NE:
        p = assets.CACHE / "ne_110m_countries.json"
        if not p.exists() or p.stat().st_size < 10000:
            r = assets._get(core.graphics.NE_URL)
            if not r:
                raise RuntimeError("Natural Earth download failed")
            p.write_bytes(r.content)
        for f in json.loads(p.read_text(encoding="utf-8"))["features"]:
            g = f["geometry"]
            parts = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            rings = [pt[0] for pt in parts]
            big = max(rings, key=lambda r: (max(x for x, _ in r) - min(x for x, _ in r)) * (max(y for _, y in r) - min(y for _, y in r)))
            c = (sum(x for x, _ in big) / len(big), sum(y for _, y in big) / len(big))
            pr = f["properties"]
            _NE.append(dict(names={str(pr.get(k, "")).lower() for k in ("NAME", "ADMIN", "NAME_LONG", "SOVEREIGNT")}, rings=rings, c=c))
    return _NE


def find(name):
    nm = ALIAS.get(name, name).lower()
    for c in countries():
        if nm in c["names"]:
            return c
    raise RuntimeError("country not found: " + name)


def s_map(sc, ctx, base):
    reg = sc.get("region", [-168, -56, 178, 84])
    top = ctx.top + 4
    bx0, by0, bx1, by1 = 80, top, W - 80, BOT
    s = min((bx1 - bx0) / (reg[2] - reg[0]), (by1 - by0) / (reg[3] - reg[1]))
    ox = bx0 + ((bx1 - bx0) - s * (reg[2] - reg[0])) / 2
    oy = by0 + ((by1 - by0) - s * (reg[3] - reg[1])) / 2
    P = lambda lon, lat: (ox + (lon - reg[0]) * s, oy + (reg[3] - lat) * s)
    land = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
    ld = ImageDraw.Draw(land)
    for c in countries():
        for ring in c["rings"]:
            pts = [(P(x, y)[0] * 2, P(x, y)[1] * 2) for x, y in ring]
            ld.polygon(pts, fill=(196, 202, 212, 255), outline=(244, 246, 249, 255))
    base.alpha_composite(land.reduce(2))
    items = sc.get("items", [])
    arcs = sc.get("arcs", [])
    A = ctx.A
    bubble = sc.get("mode") == "bubble"
    vmax = max([float(i.get("value", 1)) for i in items] or [1])
    cols = {"red": RED, "blue": BLUE, "accent": ctx.acc, "ink": INK}
    for i in range(len(items)):
        ctx.events.append((0.4 + i * A * 0.2, "cpop"))
    for i in range(len(arcs)):
        ctx.events.append((0.4 + A * 0.5 + i * 0.3, "cwhoosh"))

    def frame(t, cv):
        for i, it in enumerate(items):
            e = ease((t - 0.4 - i * A * 0.2) / 0.6)
            c = find(it["country"])
            col = cols[it.get("color", "red")]
            if bubble:
                px, py = P(*c["c"])
                r = 18 + 70 * math.sqrt(float(it.get("value", 1)) / vmax)
                cv.ell(px, py, r * e, col, 215)
            else:
                for ring in c["rings"]:
                    cv.poly([P(x, y) for x, y in ring], col, 255 * e)
        for i, ar in enumerate(arcs):
            a, b = P(*find(ar["from"])["c"]), P(*find(ar["to"])["c"])
            e = ease((t - 0.4 - A * 0.5 - i * 0.3) / (A * 0.6))
            mx, my = (a[0] + b[0]) / 2, min(a[1], b[1]) - abs(b[0] - a[0]) * 0.28 - 30
            pts = [((1 - u) ** 2 * a[0] + 2 * (1 - u) * u * mx + u * u * b[0], (1 - u) ** 2 * a[1] + 2 * (1 - u) * u * my + u * u * b[1])
                   for u in [k / 40 * e for k in range(41)]]
            cv.line(pts, 11, ctx.acc, 70); cv.line(pts, 5, ctx.acc)
            cv.ell(pts[-1][0], pts[-1][1], 10, WHITE); cv.ell(pts[-1][0], pts[-1][1], 10, None, 255, ctx.acc, 4)
        for i, it in enumerate(items):
            lab = it.get("label")
            e = ease((t - 0.9 - i * A * 0.2) / 0.4)
            if lab and e > 0.02:
                px, py = P(*find(it["country"])["c"])
                cv.pill(px, py - (80 if bubble else 0) - 14 * (1 - e) + it.get("dy", 0), lab, 36, INK, WHITE, 255 * e)
        draw_pills(sc, ctx, cv, t)
    return frame


# ----------------------------------------------------------------------------- big number / compare / text
def s_big(sc, ctx, base):
    tone = {"red": RED, "blue": BLUE, "ink": INK}.get(sc.get("tone", "red"), RED)
    A = ctx.A
    v = float(sc["big"])
    full = fmt(v, sc)
    for sz in range(430, 150, -10):
        if font(ANTON, sz).getlength(full) <= (1280 if sc.get("icon") else 1640):
            break
    cx = W / 2 + (120 if sc.get("icon") else 0)
    cy = ctx.top + (BOT - ctx.top) * 0.46
    count_events(ctx, 0.2, A * 0.6)
    ctx.events.append((0.2, "chit"))
    cap = sc.get("caption", "").upper()

    def frame(t, cv):
        e = ease((t - 0.2) / (A * 0.7))
        pe = out_back((t - 0.15) / 0.5)
        cv.text(cx, cy + 26 * (1 - min(pe, 1)), fmt(v * e, sc), font(ANTON, sz), tone, 255 * min(pe * 2, 1), "mm")
        if sc.get("icon"):
            icon(cv, sc["icon"], cx - font(ANTON, sz).getlength(full) / 2 - 150, cy, 230, INK, 255 * min(pe * 2, 1))
        ce = ease((t - 0.5) / 0.5)
        cv.rect((cx - 110 * ce, cy + sz * 0.62, cx + 110 * ce, cy + sz * 0.62 + 12), 6, tone)
        cv.text(cx, cy + sz * 0.62 + 70, cap, font(BODY, 58), INK, 255 * ce, "mm")
        draw_pills(sc, ctx, cv, t)
    return frame


def s_compare(sc, ctx, base):
    A = ctx.A
    top = ctx.top + 10
    cy0, cy1 = top, BOT - 20
    sides = [sc["left"], sc["right"]]
    win = sc.get("win", "right")
    cols = [BLUE, RED] if win == "right" else [RED, BLUE]
    count_events(ctx, 0.3, A * 0.6)
    ctx.events.append((A * 0.8, "chit"))

    def frame(t, cv):
        for i, sd in enumerate(sides):
            e = ease((t - 0.15 - i * 0.18) / 0.5)
            x0 = 110 + i * 920
            off = (1 - e) * (-120 if i == 0 else 120)
            cv.rect((x0 + off, cy0, x0 + 780 + off, cy1), 30, WHITE, 255 * e)
            cv.rect((x0 + off, cy0, x0 + 780 + off, cy0 + 14), 7, cols[i], 255 * e)
            if sd.get("icon"):
                icon(cv, sd["icon"], x0 + 390 + off, cy0 + 110, 120, cols[i], 255 * e)
            cv.text(x0 + 390 + off, cy0 + 215, sd["label"].upper(), font(BODY, 40), MUTE, 255 * e, "mm")
            ve = ease((t - 0.4 - i * 0.25) / (A * 0.6))
            v = float(sd["value"])
            sz = 160
            while font(ANTON, sz).getlength(fmt(v, sc, sd.get("dec"))) > 700:
                sz -= 10
            cv.text(x0 + 390 + off, (cy0 + cy1) / 2 + 60, fmt(v * ve, sc, sd.get("dec")), font(ANTON, sz), cols[i], 255 * e, "mm")
            if sd.get("note"):
                cv.text(x0 + 390 + off, cy1 - 70, sd["note"], font(BODY, 34), INK, 255 * e, "mm")
        ve = out_back((t - 0.6) / 0.4)
        cv.ell(W / 2, (cy0 + cy1) / 2, 62 * max(min(ve, 1.1), 0), INK)
        cv.text(W / 2, (cy0 + cy1) / 2 + 2, "VS", font(ANTON, 56), WHITE, 255 * min(max(ve, 0), 1), "mm")
        draw_pills(sc, ctx, cv, t)
    return frame


def s_text(sc, ctx, base):
    import re
    toks = [(w.replace("*", ""), "*" in w) for w in re.findall(r"\*[^*]+\*[.,!?]?|\S+", sc["text"].upper())]
    f = font(ANTON, 90); lines = []
    for sz in range(190, 88, -6):
        f = font(ANTON, sz)
        lines = wrap_tokens(toks, f, 1560)
        if len(lines) * sz * 1.12 <= 560:
            break
    lh = sz * 1.12
    words = []
    y0 = (H - 180) / 2 - len(lines) * lh / 2 + 20
    sp = f.getlength(" ")
    for li, ln in enumerate(lines):
        lw = sum(f.getlength(w) for w, _ in ln) + sp * (len(ln) - 1)
        x = W / 2 - lw / 2
        for w, hl in ln:
            words.append((w, hl, x, y0 + li * lh)); x += f.getlength(w) + sp
    ctx.events.append((0.2, "cpop"))
    n = len(words)
    step = min(0.28, ctx.A * 0.8 / max(n, 1))

    def frame(t, cv):
        for i, (w, hl, x, y) in enumerate(words):
            e = ease((t - 0.15 - i * step) / 0.3)
            cv.text(x, y + 40 * (1 - e), w, f, RED if hl else INK, 255 * e, "la")
        draw_pills(sc, ctx, cv, t)
    return frame


SCENES = {"bars": s_bars, "columns": s_columns, "line": s_line, "race": s_race, "stack": s_stack, "waterfall": s_waterfall,
          "map": s_map, "big": s_big, "compare": s_compare, "text": s_text}


# ----------------------------------------------------------------------------- render + plan
def build_scene(sc, prof, d):
    ctx = Ctx(prof, d)
    base = bg_image().copy()
    header(base, sc, ctx)
    frame = SCENES[sc["kind"]](sc, ctx, base)
    return ctx, base, frame


def render_scene(sc, prof, d, out: Path, nframes, trans=None, still_at=None):
    ctx, base, frame = build_scene(sc, prof, d)
    if still_at is not None:
        cv = Cv(); frame(still_at, cv); return cv.done(base)
    cmd = [core.FF, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", FPS, "-i", "-",
           "-vf", f"{core._fade(trans)[1:] + ',' if trans else ''}format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
           "-pix_fmt", "yuv420p", "-threads", "3", out]
    p = subprocess.Popen([str(c) for c in cmd], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(nframes):
            cv = Cv()
            frame(i / FPS, cv)
            p.stdin.write(cv.done(base).tobytes())
        p.stdin.close()
        err = p.stderr.read().decode()[-600:]
        p.wait()
        if p.returncode:
            raise RuntimeError(err)
    finally:
        if p.poll() is None:
            p.kill()
    return ctx.events


def auto_scene(b):
    ws = [w.strip(".,;:!?\"'") for w in b["text"].split() if len(w) > 3]
    return {"kind": "text", "text": " ".join(ws[:4]) or b["text"][:30]}


def plan_shots(beats, timing, prof, work: Path, broll=()):
    shots, events, credits, t = [], [], [], 0.0
    (work / "shots").mkdir(exist_ok=True)
    since_flash = 99
    for bi, (b, tm) in enumerate(zip(beats, timing)):
        D = tm["dur"]
        scs = b.get("scene") or auto_scene(b)
        scs = scs if isinstance(scs, list) else [scs]
        ws = [float(s.get("w", 1)) for s in scs]
        mood = core.mood(b["text"])
        st = t
        for si, sc in enumerate(scs):
            sd = D * ws[si] / sum(ws)
            nf = max(round((st + sd) * FPS) - round(st * FPS), 2)
            trans = None
            if si == 0 and bi > 0:
                if mood == "reveal" and since_flash >= 5:
                    trans = "flash"; since_flash = 0
                elif mood == "calm" and bi % 3 == 0:
                    trans = "dip"
            since_flash += 1
            sp = work / "shots" / f"c{bi:03d}_{si}.mp4"
            evs = render_scene(sc, prof, nf / FPS, sp, nf, trans)
            events += [(st + e, k) for e, k in evs]
            if si > 0 or bi > 0:
                events.append((max(st - 0.05, 0), "cwhoosh"))
            if sc.get("source"):
                credits.append("Data: " + sc["source"])
            shots.append((sp, nf / FPS)); st += nf / FPS
        t = st
        t = max(t, tm["start"] + D) if abs(t - (tm["start"] + D)) < 0.1 else t
    for c in b.get("credits", []) if beats else []:
        credits.append(c)
    return shots, sorted(set(credits)), events


def sfx_bank(sr):
    import numpy as np
    rng = np.random.default_rng(5)
    n = int(0.022 * sr)
    tk = rng.standard_normal(n).astype(np.float32) * np.exp(-np.arange(n) / sr * 260)
    tk = tk - np.convolve(tk, np.ones(30) / 30, "same")
    tick = 0.16 * tk / (np.abs(tk).max() + 1e-6)
    m = int(0.18 * sr)
    tt = np.arange(m) / sr
    pop = (np.sin(2 * np.pi * np.cumsum(440 + 520 * np.exp(-tt * 26)) / sr) * np.exp(-tt * 18)).astype(np.float32) * 0.3
    k = int(0.9 * sr)
    t2 = np.arange(k) / sr
    hit = (np.sin(2 * np.pi * np.cumsum(46 + 70 * np.exp(-t2 * 14)) / sr) * np.exp(-t2 * 5)).astype(np.float32) * 0.7
    w = int(0.5 * sr)
    x = rng.standard_normal(w).astype(np.float32)
    env = np.sin(np.linspace(0, np.pi, w)) ** 2
    y = np.zeros(w, np.float32); s = 0.0
    a = np.linspace(0.04, 0.4, w)
    for i in range(w):
        s += a[i] * (x[i] - s); y[i] = s
    whoosh = 0.32 * y * env / (np.abs(y).max() + 1e-6)
    return {"ctick": tick, "cpop": pop, "chit": hit, "cwhoosh": whoosh}
