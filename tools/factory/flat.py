"""Flat engine (Abyss Archive / Leviathan Lore): flat-vector animal scenes drawn locally with PIL, piped to ffmpeg. profile engine == 'flat'.
Style family = the competitors (dark teal water gradient + light rays + drifting particles, flat grey animals, tiny diver for scale,
sparse small tracked caps text, thin rules, one red accent word), polished: eased motion, parallax, count-ups, 2x supersampled sprites.
All animals are drawn procedurally (no downloads, nothing to credit).

A beat may carry "scene": {...} or a list [{...},{...}] shown one after another (optional "w" weight). *word* = accent colour.
  ocean {"animal","len":0.62,"y":0.52,"dir":1|-1,"diver":true,"label","sub","cap","depth":0..1,"glow":true,"school":true}
  depth {"to":1000,"marks":[{"d":200,"t":"SUNLIGHT FADES"}],"animal":"sperm_whale","label":"..","unit":"M"}      diver descends a depth gauge
  scale {"title","items":[{"animal":"blue_whale","m":30,"label":"BLUE WHALE"},{"animal":"diver","m":1.8},{"animal":"bus","m":12}]}
  bars  {"title","unit":"KM/H","items":[{"label","value","animal"?}],"hl":[idx],"dec":0,"prefix":""}   size / bite force / speed bars
  pin   {"title","region":[lon0,lat0,lon1,lat1],"pins":[{"lon","lat","label"}]}                      simple map pins on Natural Earth land
  cards {"title","cards":[{"animal","label"}],"hl":idx}
  big   {"big":300,"prefix":"~","suffix":" TEETH","caption":"..","animal":"shark"}
  text  {"text":"You would *never* see it coming"}
animals: blue_whale sperm_whale orca shark fish squid diver bus ship"""
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import core
from .tech import font, ease, mix, prog

W, H, FPS = 1920, 1080, 30
ANTON, MONT = "Anton-Regular.ttf", "Montserrat-ExtraBold.ttf"
INK, MUTED, DIM = (228, 234, 238), (132, 156, 168), (70, 98, 112)
RED = (224, 69, 43)
S = 2                                                  # sprite supersampling
_SP, _BG, _RAYS, _LAND = {}, {}, {}, {}


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ----------------------------------------------------------------------------- animal sprites (flat vector, face right)
def _shade(canvas_wh, body_pts, col, belly, belly_frac, cy, extra=(), line=None):
    """body polygon filled col; lower part (below cy + belly_frac*half) lightened; extra = [(pts, colour)] drawn on top (inside body only if flagged)."""
    w, h = canvas_wh
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).polygon(body_pts, fill=255)
    lay = Image.new("RGBA", (w, h), col + (255,))
    if belly:
        bl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ys = [p[1] for p in body_pts]
        y_cut = cy + (max(ys) - cy) * belly_frac
        ImageDraw.Draw(bl).rectangle((0, y_cut, w, h), fill=belly + (255,))
        bl = bl.filter(ImageFilter.GaussianBlur(S * 1.5))
        lay.alpha_composite(bl)
    for pts, c in extra:
        ImageDraw.Draw(lay).polygon(pts, fill=c)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(lay, (0, 0), m)
    return out


def _outline_pts(L, hf, cy, x0, n=90, top=1.0, bot=1.0):
    ts = [(x0 + (i / n) * L, cy - hf(i / n) * L * top) for i in range(n + 1)]
    bs = [(x0 + (i / n) * L, cy + hf(i / n) * L * bot) for i in range(n, -1, -1)]
    return ts + bs


def _cetacean(L, kind):
    L = int(L)
    cw, ch = int(L * 1.14) * S, int(L * 0.42) * S
    LL, x0, cy = L * S, int(L * 0.02) * S, int(L * 0.27) * S
    a = {"blue_whale": 0.09, "sperm_whale": 0.125, "orca": 0.11}[kind]

    def hf(u):
        if u < 0.3:
            p = {"sperm_whale": 6, "blue_whale": 2.4, "orca": 1.7}[kind]
            return a * (1 - ((0.3 - u) / 0.3) ** p) ** (0.4 if kind == "sperm_whale" else 0.5)
        return a * (1 - ((u - 0.3) / 0.7) ** 1.7) ** 0.8 + 0.005

    col = {"blue_whale": (112, 128, 140), "sperm_whale": (108, 112, 112), "orca": (14, 18, 24)}[kind]
    belly = {"blue_whale": (150, 164, 172), "sperm_whale": (134, 138, 136), "orca": (238, 242, 246)}[kind]
    pts = _outline_pts(LL, hf, cy, x0, top=1.0, bot=1.06)
    h_at = lambda u: hf(u) * LL
    fl = [(x0 + .92 * LL, cy - .004 * LL), (x0 + 1.035 * LL, cy - .072 * LL), (x0 + 1.065 * LL, cy - .045 * LL), (x0 + 1.0 * LL, cy),
          (x0 + 1.07 * LL, cy + .045 * LL), (x0 + 1.04 * LL, cy + .075 * LL), (x0 + .92 * LL, cy + .012 * LL)]
    dh = {"blue_whale": .035, "sperm_whale": .03, "orca": .13}[kind]
    du = {"blue_whale": .72, "sperm_whale": .66, "orca": .42}[kind]
    dorsal = [(x0 + (du - .06) * LL, cy - h_at(du - .06)), (x0 + (du + .045) * LL, cy - h_at(du + .045)), (x0 + du * LL, cy - h_at(du) - dh * LL)]
    flip = [(x0 + .27 * LL, cy + h_at(.27) * .7), (x0 + .39 * LL, cy + h_at(.39) * .7), (x0 + .31 * LL, cy + h_at(.3) + .06 * LL)]
    body = _shade((cw, ch), pts, col, belly, 0.12 if kind != "orca" else 0.15, cy)
    d = ImageDraw.Draw(body)
    fc = tuple(max(c - 14, 0) for c in col)
    d.polygon(fl, fill=fc + (255,))
    d.polygon(dorsal, fill=fc + (255,))
    d.polygon(flip, fill=fc + (255,))
    ex, ey = x0 + .12 * LL, cy - h_at(.12) * .25
    if kind == "orca":
        d.ellipse((ex + .03 * LL, ey - .03 * LL, ex + .1 * LL, ey + .005 * LL), fill=(240, 244, 248, 255))
        d.polygon([(x0 + .52 * LL, cy - h_at(.52) * .5), (x0 + .6 * LL, cy - h_at(.6) * .3), (x0 + .5 * LL, cy - h_at(.5) * .1)], fill=(120, 128, 136, 255))
    d.ellipse((ex - 4 * S, ey - 4 * S, ex + 4 * S, ey + 4 * S), fill=(10, 14, 18, 255))
    mouth = [(x0 + .02 * LL, cy + h_at(.04) * .15), (x0 + .2 * LL, cy + h_at(.2) * .45)]
    d.line(mouth, fill=tuple(max(c - 40, 0) for c in col) + (255,), width=2 * S)
    img = body.resize((cw // S, ch // S), Image.LANCZOS)
    return img, {"eye": (ex / S, ey / S), "fy": cy / S}


def _shark(L):
    L = int(L)
    cw, ch = int(L * 1.12) * S, int(L * 0.4) * S
    LL, x0, cy = L * S, int(L * 0.02) * S, int(L * 0.26) * S
    a = 0.07

    def hf(u):
        if u < 0.38:
            return a * (1 - ((0.38 - u) / 0.38) ** 1.6) ** 0.7
        return a * (1 - ((u - 0.38) / 0.62) ** 1.35) ** 0.9 + 0.008
    pts = _outline_pts(LL, hf, cy, x0, top=1, bot=0.9)
    hh = lambda u: hf(u) * LL
    dorsal = [(x0 + .33 * LL, cy - hh(.33)), (x0 + .46 * LL, cy - hh(.46)), (x0 + .41 * LL, cy - hh(.4) - .11 * LL)]
    pect = [(x0 + .3 * LL, cy + hh(.3) * .7), (x0 + .4 * LL, cy + hh(.4) * .8), (x0 + .24 * LL, cy + hh(.3) + .1 * LL)]
    tail = [(x0 + .84 * LL, cy), (x0 + .96 * LL, cy - .15 * LL), (x0 + 1.0 * LL, cy - .15 * LL), (x0 + .95 * LL, cy - .01 * LL),
            (x0 + .985 * LL, cy + .085 * LL), (x0 + .95 * LL, cy + .085 * LL)]
    body = _shade((cw, ch), pts, (92, 108, 120), (200, 210, 214), 0.22, cy)
    d = ImageDraw.Draw(body)
    for q in (dorsal, pect, tail):
        d.polygon(q, fill=(76, 92, 104, 255))
    ex, ey = x0 + .1 * LL, cy - hh(.1) * .2
    d.ellipse((ex - 3 * S, ey - 3 * S, ex + 3 * S, ey + 3 * S), fill=(8, 12, 16, 255))
    for k in range(3):
        gx = x0 + (.27 + .012 * k) * LL
        d.line([(gx, cy - hh(.27) * .3), (gx - 2 * S, cy + hh(.27) * .35)], fill=(60, 74, 86, 255), width=S)
    d.line([(x0 + .01 * LL, cy + hh(.1) * .5), (x0 + .15 * LL, cy + hh(.2) * .55)], fill=(50, 62, 72, 255), width=S)
    return body.resize((cw // S, ch // S), Image.LANCZOS), {"eye": (ex / S, ey / S), "fy": cy / S}


def _fish(L):
    L = int(L)
    cw, ch = int(L * 1.1) * S, int(L * 0.5) * S
    LL, cy = L * S, int(L * 0.25) * S
    im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, cy - .17 * LL, .82 * LL, cy + .17 * LL), fill=(150, 170, 178, 255))
    d.polygon([(.74 * LL, cy), (1.05 * LL, cy - .2 * LL), (1.0 * LL, cy), (1.05 * LL, cy + .2 * LL)], fill=(120, 140, 150, 255))
    d.ellipse((.14 * LL, cy - .06 * LL, .2 * LL, cy), fill=(10, 14, 18, 255))
    return im.resize((cw // S, ch // S), Image.LANCZOS), {"eye": (.17 * L, .25 * L), "fy": .25 * L}


def _squid(L):
    L = int(L)
    cw, ch = int(L * 1.05) * S, int(L * 0.34) * S
    LL, cy = L * S, int(L * 0.17) * S
    red, dk = (178, 52, 52, 255), (140, 38, 42, 255)
    im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((.6 * LL, cy - .1 * LL, 1.04 * LL, cy + .1 * LL), fill=red)          # mantle (rear, right)
    d.polygon([(.86 * LL, cy), (1.04 * LL, cy - .15 * LL), (1.04 * LL, cy + .15 * LL)], fill=dk)   # fins
    d.ellipse((.42 * LL, cy - .07 * LL, .66 * LL, cy + .07 * LL), fill=red)          # head
    d.ellipse((.5 * LL, cy - .045 * LL, .56 * LL, cy + .005 * LL), fill=(14, 10, 12, 255))
    for k in range(8):
        yy = cy + (k - 3.5) * .016 * LL
        d.line([(.44 * LL, yy), (.22 * LL, yy + (k - 3.5) * .02 * LL), (0, yy + (k - 3.5) * .045 * LL)], fill=red, width=int(.012 * LL))
    for k in (3, 4):
        d.line([(.44 * LL, cy), (.2 * LL, cy + (k - 3.5) * .05 * LL), (0.02 * LL, cy + (k - 3.5) * .1 * LL)], fill=dk, width=int(.008 * LL))
    return im.resize((cw // S, ch // S), Image.LANCZOS), {"eye": (.53 * L, .15 * L), "fy": .17 * L}


def _diver(L):
    L = int(L)
    cw, ch = int(L * 1.1) * S, int(L * 0.4) * S
    LL, cy = L * S, int(L * 0.2) * S
    im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((.2 * LL, cy - .05 * LL, .62 * LL, cy + .05 * LL), .05 * LL, fill=(22, 30, 40, 255))     # torso
    d.rounded_rectangle((.55 * LL, cy - .035 * LL, .92 * LL, cy + .035 * LL), .035 * LL, fill=(30, 40, 52, 255))  # legs
    d.polygon([(.9 * LL, cy - .02 * LL), (1.06 * LL, cy - .09 * LL), (1.08 * LL, cy + .05 * LL), (.9 * LL, cy + .035 * LL)], fill=(236, 190, 40, 255))
    d.rounded_rectangle((.26 * LL, cy - .1 * LL, .5 * LL, cy - .035 * LL), .03 * LL, fill=(200, 206, 210, 255))   # tank
    d.rounded_rectangle((.1 * LL, cy - .03 * LL, .34 * LL, cy + .025 * LL), .025 * LL, fill=(30, 40, 52, 255))    # arm
    d.ellipse((.05 * LL, cy - .06 * LL, .19 * LL, cy + .045 * LL), fill=(26, 34, 44, 255))                       # head
    d.rounded_rectangle((.02 * LL, cy - .04 * LL, .1 * LL, cy + .01 * LL), .02 * LL, fill=(120, 190, 210, 255))   # mask
    return im.resize((cw // S, ch // S), Image.LANCZOS), {"eye": (.06 * L, .2 * L), "fy": .2 * L}


def _bus(L):
    L = int(L)
    cw, ch = int(L * 1.02) * S, int(L * 0.3) * S
    LL = L * S
    im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    top, bot = .02 * LL, .22 * LL
    d.rounded_rectangle((.005 * LL, top, .995 * LL, bot), .02 * LL, fill=(232, 176, 34, 255))
    d.rectangle((.005 * LL, bot - .04 * LL, .995 * LL, bot), fill=(190, 140, 24, 255))
    for k in range(8):
        x = (.06 + k * .105) * LL
        d.rounded_rectangle((x, top + .025 * LL, x + .08 * LL, top + .09 * LL), .008 * LL, fill=(40, 56, 66, 255))
    for x in (.18, .78):
        d.ellipse(((x - .045) * LL, bot - .04 * LL, (x + .045) * LL, bot + .05 * LL), fill=(18, 22, 28, 255))
        d.ellipse(((x - .02) * LL, bot - .015 * LL, (x + .02) * LL, bot + .025 * LL), fill=(120, 128, 134, 255))
    d.text((.5 * LL, top + .13 * LL), "CITY BUS", font=font(MONT, max(int(.03 * LL), 8)), fill=(90, 62, 10, 255), anchor="mm")
    return im.resize((cw // S, ch // S), Image.LANCZOS), {"eye": (L * .5, L * .1), "fy": L * .12}


def _ship(L):
    L = int(L)
    cw, ch = int(L * 1.0) * S, int(L * 0.4) * S
    LL = L * S
    im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    y = .28 * LL
    d.polygon([(0, y - .08 * LL), (LL, y - .08 * LL), (.9 * LL, y + .05 * LL), (.06 * LL, y + .05 * LL)], fill=(34, 42, 50, 255))
    d.rectangle((0, y - .08 * LL, LL, y - .065 * LL), fill=(176, 56, 44, 255))
    d.rectangle((.3 * LL, y - .15 * LL, .7 * LL, y - .08 * LL), fill=(214, 220, 224, 255))
    d.rectangle((.4 * LL, y - .2 * LL, .6 * LL, y - .15 * LL), fill=(188, 196, 202, 255))
    d.rectangle((.64 * LL, y - .22 * LL, .7 * LL, y - .15 * LL), fill=(176, 56, 44, 255))
    for k in range(6):
        d.rectangle(((.33 + k * .06) * LL, y - .13 * LL, (.37 + k * .06) * LL, y - .1 * LL), fill=(40, 56, 66, 255))
    return im.resize((cw // S, ch // S), Image.LANCZOS), {"eye": (L * .5, L * .2), "fy": L * .2}


MAKERS = {"blue_whale": lambda L: _cetacean(L, "blue_whale"), "sperm_whale": lambda L: _cetacean(L, "sperm_whale"),
          "orca": lambda L: _cetacean(L, "orca"), "shark": _shark, "fish": _fish, "squid": _squid, "diver": _diver, "bus": _bus, "ship": _ship}


def sprite(name, L, flip=False):
    k = (name, int(L), flip)
    if k not in _SP:
        im, meta = MAKERS[name](max(int(L), 6))
        if flip:
            meta = dict(meta, eye=(im.width - meta["eye"][0], meta["eye"][1]))
            im = ImageOps.mirror(im)
        _SP[k] = (im, meta)
    return _SP[k]


def aspect(name):
    im, _ = sprite(name, 400)
    return im.height / 400.0


# ----------------------------------------------------------------------------- background (water gradient, rays, particles)
def water(depth):
    k = round(depth, 1)
    if k not in _BG:
        top = mix((26, 84, 104), (6, 18, 30), min(1, k * 1.15))
        bot = mix((8, 38, 52), (2, 6, 12), min(1, k * 1.15 + .1))
        col = Image.new("RGB", (1, H))
        for y in range(H):
            col.putpixel((0, y), tuple(int(v) for v in mix(top, bot, ease(y / H))))
        _BG[k] = col.resize((W, H)).convert("RGBA")
    return _BG[k]


def rays():
    if "r" not in _RAYS:
        im = Image.new("RGBA", (W * 2, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for i in range(9):
            x = int(W * 2 * (i + .5) / 9 + (i % 3 - 1) * 90)
            wd = 70 + (i * 53) % 110
            d.polygon([(x - wd // 3, 0), (x + wd // 3, 0), (x + wd * 2 + 160, H), (x - wd * 2 + 160, H)], fill=(150, 215, 230, 20 + (i * 7) % 14))
        _RAYS["r"] = im.filter(ImageFilter.GaussianBlur(26))
    return _RAYS["r"]


def backdrop(t, depth, seed):
    bg = water(depth).copy()
    if depth < .85:
        r = rays()
        ox = int((t * 14 + seed * 130) % W)
        lay = r.crop((ox, 0, ox + W, H))
        a = lay.getchannel("A").point(lambda v: int(v * max(0, 1 - depth * 1.2)))
        lay.putalpha(a)
        bg.alpha_composite(lay)
    d = ImageDraw.Draw(bg, "RGBA")
    for i in range(46):                                   # drifting plankton
        h1 = (i * 7919 + seed * 104729) % 10007
        x = (h1 * 13 % W + t * (6 + i % 5 * 3)) % W
        y = (H - (h1 * 7 % H) - t * (10 + i % 6 * 4)) % H
        r_ = 1 + i % 3
        d.ellipse((x - r_, y - r_, x + r_, y + r_), fill=(190, 225, 235, 30 + i % 5 * 16))
    return bg


# ----------------------------------------------------------------------------- drawing helpers
def T(im, s, x, y, size, fill=INK, a=1.0, anchor="mm", fname=MONT, track=0):
    f = font(fname, size)
    if a <= 0.01 or not s:
        return
    wd = sum(f.getlength(c) + track for c in s) - track if track else f.getlength(s)
    lay = Image.new("RGBA", (int(wd) + 40, int(size * 1.6)), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    if track:
        cx = 20
        for c in s:
            d.text((cx, lay.height // 2), c, font=f, fill=fill + (255,), anchor="lm")
            cx += f.getlength(c) + track
    else:
        d.text((20, lay.height // 2), s, font=f, fill=fill + (255,), anchor="lm")
    if a < .99:
        lay.putalpha(lay.getchannel("A").point(lambda v: int(v * a)))
    px = x - 20 - (wd / 2 if anchor[0] == "m" else wd if anchor[0] == "r" else 0)
    im.paste(lay, (int(px), int(y - lay.height / 2)), lay)


def rich(im, s, x, y, size, acc, a=1.0, anchor="mm", fname=MONT, track=0, base=INK):
    """text with *accent* words (centered/left)."""
    parts, on = [], False
    for chunk in s.split("*"):
        if chunk:
            parts.append((chunk, acc if on else base))
        on = not on
    f = font(fname, size)
    ws = [f.getlength(c) + track * len(c) for c, _ in parts]
    tot = sum(ws)
    cx = x - tot / 2 if anchor[0] == "m" else x
    for (c, col), w in zip(parts, ws):
        T(im, c, cx, y, size, col, a, "l", fname, track)
        cx += w


def blit(im, sp, x, y, a=1.0):
    if a <= .01:
        return
    if a < .99:
        sp = sp.copy()
        sp.putalpha(sp.getchannel("A").point(lambda v: int(v * a)))
    im.paste(sp, (int(x), int(y)), sp)


def glow_dot(im, x, y, r, col, a=1.0):
    lay = Image.new("RGBA", (int(r * 6), int(r * 6)), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for k in range(8, 0, -1):
        d.ellipse((r * 3 - r * k / 2.2, r * 3 - r * k / 2.2, r * 3 + r * k / 2.2, r * 3 + r * k / 2.2), fill=col + (int(10 * a),))
    d.ellipse((r * 3 - r / 3, r * 3 - r / 3, r * 3 + r / 3, r * 3 + r / 3), fill=(255, 190, 170, int(255 * a)))
    im.paste(lay, (int(x - r * 3), int(y - r * 3)), lay)


def title(im, ctx, text, t, y=92):
    rich(im, text, W / 2, y, 34, ctx.acc, a=ease(prog(t, .15, .7)), track=5)
    ln = ease(prog(t, .2, .9)) * 70
    if ln > 1:
        d = ImageDraw.Draw(im)
        d.line([(W / 2 - ln, y + 34), (W / 2 + ln, y + 34)], fill=DIM, width=2)


class Ctx:
    def __init__(self, prof, idx, chap, d):
        self.acc = rgb(prof.get("accent", "#e0452b"))
        self.idx, self.chap, self.d = idx, chap, d
        self.events = []
        self.tone = prof.get("tone", 0)


# ----------------------------------------------------------------------------- scenes: each returns (depth, seed, frame(t, im))
def s_ocean(sc, ctx):
    name = sc.get("animal", "blue_whale")
    L = int(W * sc.get("len", .62))
    dr = sc.get("dir", 1)
    flip = dr < 0
    sp, meta = sprite(name, L, flip)
    dv = sprite("diver", max(int(L * 1.8 / {"blue_whale": 28, "sperm_whale": 16, "orca": 7, "shark": 5.5, "squid": 12}.get(name, 10)), 40), flip) if sc.get("diver") else None
    y0 = H * sc.get("y", .5)
    d = ctx.d
    ctx.events.append((0.1, "bubbles")) if sc.get("diver") else None

    def frame(t, im):
        e = ease(t / max(d, .1))
        x = (W - sp.width) / 2 + dr * (e - .5) * W * .1
        y = y0 - sp.height / 2 + math.sin(t * .9 + ctx.idx) * 9
        if sc.get("school"):
            for k in range(14):
                fs, _ = sprite("fish", 46 + k % 3 * 10, False)
                fx = (W * (.1 + (k * .137) % .8) + (t * (60 + k * 5)) * -dr) % (W + 200) - 100
                blit(im, fs, fx, H * (.18 + (k * .31) % .5) + math.sin(t + k) * 12, .55)
        blit(im, sp, x, y, ease(prog(t, 0, .6)))
        if sc.get("glow"):
            ex, ey = meta["eye"]
            glow_dot(im, x + ex, y + ey, 16 + 5 * math.sin(t * 3), ctx.acc, ease(prog(t, .5, 1.1)))
        if dv:
            dsp, _ = dv
            xd = W * (.15 if dr > 0 else .75) - dr * e * W * .05
            blit(im, dsp, xd, H * .22 + math.sin(t * .7) * 10, ease(prog(t, .2, .8)))
        if sc.get("label"):
            lx, ly = x + sp.width * (.5), y + sp.height + 64
            a = ease(prog(t, .6, 1.1))
            T(im, sc["label"], lx, ly, 38, INK, a, "mm", MONT, 5)
            if sc.get("sub"):
                T(im, sc["sub"], lx, ly + 44, 26, MUTED, a, "mm", MONT, 2)
        if sc.get("cap"):
            rich(im, sc["cap"], W / 2, 170 if not dv else 110, 44, ctx.acc, ease(prog(t, .35, .9)), track=4)
    return sc.get("depth", .3), ctx.idx, frame


def s_depth(sc, ctx):
    to = float(sc.get("to", 1000))
    unit = sc.get("unit", "M")
    gx, y0, y1 = int(W * .74), 170, 770
    d = ctx.d
    marks = sc.get("marks", [])
    animal = sc.get("animal")
    diver, _ = sprite("diver", 150)
    dvr = diver.rotate(-90, expand=True)
    asp = sprite(animal, 560)[0] if animal else None
    ctx.events.append((0.1, "bubbles"))
    ctx.events.append((max(d * .55, .5), "ping")) if animal else None
    tmax = to * 1.12
    Y = lambda m: y0 + (y1 - y0) * m / tmax
    step = 10 ** int(math.log10(max(to / 4, 1)))
    step = step * (5 if to / step > 8 else 2 if to / step > 5 else 1)

    def frame(t, im):
        e = ease(prog(t, .25, max(d - .6, 1.0)))
        cur = to * e
        dd = ImageDraw.Draw(im, "RGBA")
        dd.line([(gx, y0), (gx, y1)], fill=DIM + (255,), width=2)
        m = 0
        while m <= tmax:
            yy = Y(m)
            dd.line([(gx - 12, yy), (gx, yy)], fill=DIM + (255,), width=2)
            T(im, f"{int(m):,} {unit}" if m else f"0 {unit}", gx + 22, yy, 22, DIM, 1, "l", MONT, 2)
            m += step
        for mk in marks:
            a = ease(prog(cur, mk["d"] - to * .02, mk["d"] + to * .03))
            yy = Y(mk["d"])
            T(im, mk["t"], gx - 34, yy, 24, INK, a, "r", MONT, 3)
            dd.line([(gx - 28, yy), (gx - 12, yy)], fill=ctx.acc + (int(255 * a),), width=3)
        my = Y(cur)
        dd.polygon([(gx - 2, my), (gx - 24, my - 13), (gx - 24, my + 13)], fill=ctx.acc + (255,))
        blit(im, dvr, gx - 180 - dvr.width / 2, my - dvr.height / 2 + 30)
        T(im, f"{int(cur):,}", gx - 260, 460, 150, INK, 1, "r", ANTON)
        T(im, unit + (" DOWN" if to >= 100 else ""), gx - 250, 490, 34, ctx.acc, 1, "l", MONT, 5)
        if asp is not None:
            a = ease(prog(t, d * .55, d * .55 + .8))
            blit(im, asp, W * .12, Y(to) - asp.height / 2 + math.sin(t) * 8, a)
            if sc.get("label"):
                T(im, sc["label"], W * .12 + asp.width / 2, Y(to) + asp.height / 2 + 36, 30, INK, a, "mm", MONT, 4)
    return .08 + .9 * min(1, to / 4000), ctx.idx, frame


def s_scale(sc, ctx):
    items = sc["items"]
    ctx.events.append((.4, "whoosh"))
    x0, availW = 210, W - 420
    mx = max(i["m"] for i in items)
    scl = availW / mx
    top = 160 if sc.get("title") else 130

    def total(sl):
        return sum(max(aspect(i["animal"]) * i["m"] * sl, 34) + 100 for i in items)
    while total(scl) > 700:
        scl *= .93
    rows, y = [], top
    for k, it in enumerate(items):
        Lpx = max(int(it["m"] * scl), 24)
        sp, _ = sprite(it["animal"], Lpx)
        rows.append((it, sp, y, k))
        y += sp.height + 100
    shift = (860 - y) / 2 * .6

    def frame(t, im):
        if sc.get("title"):
            title(im, ctx, sc["title"], t)
        dd = ImageDraw.Draw(im, "RGBA")
        for it, sp, ry, k in rows:
            a = ease(prog(t, .3 + k * .5, .85 + k * .5))
            yy = ry + shift
            blit(im, sp, x0 + (1 - a) * -40, yy, a)
            ly = yy + sp.height + 26
            ln = ease(prog(t, .5 + k * .5, 1.3 + k * .5))
            x1 = x0 + sp.width * ln
            dd.line([(x0, ly), (x1, ly)], fill=MUTED + (int(255 * a),), width=2)
            for xe in (x0, x1):
                dd.line([(xe, ly - 8), (xe, ly + 8)], fill=MUTED + (int(255 * a),), width=2)
            v = it["m"] * ln
            txt = (f"~{v:.0f} M" if it["m"] >= 10 else f"~{v:.1f} M")
            T(im, txt, x0 + sp.width / 2, ly + 30, 34, INK, a, "mm", MONT, 4)
            if it.get("label"):
                T(im, it["label"], x0, yy - 20 if sp.height > 50 else yy - 10, 24, ctx.acc if it.get("hl") else MUTED, a, "l", MONT, 4)
    return .25, ctx.idx, frame


def s_bars(sc, ctx):
    its = sc["items"]
    mx = max(i["value"] for i in its)
    hl = set(sc.get("hl", []))
    dec, pre, unit = sc.get("dec", 0), sc.get("prefix", ""), sc.get("unit", "")
    bx0, bx1 = 640, 1640
    rh = min(112, 600 // len(its))
    top = 230
    ctx.events.append((.5, "whoosh"))
    ctx.events.append((.9 + len(its) * .22, "impact")) if hl else None

    def frame(t, im):
        title(im, ctx, sc.get("title", ""), t)
        dd = ImageDraw.Draw(im, "RGBA")
        for k, it in enumerate(its):
            a = ease(prog(t, .3 + k * .22, .8 + k * .22))
            e = ease(prog(t, .5 + k * .22, 1.5 + k * .22))
            y = top + k * rh
            T(im, it["label"].upper(), bx0 - 40, y, 30, INK if k in hl else MUTED, a, "r", MONT, 3)
            dd.rounded_rectangle((bx0, y - 20, bx1, y + 20), 6, fill=(255, 255, 255, int(14 * a)))
            wv = (bx1 - bx0) * it["value"] / mx * e
            if wv > 4:
                dd.rounded_rectangle((bx0, y - 20, bx0 + wv, y + 20), 6, fill=(ctx.acc if k in hl else (96, 138, 156)) + (int(255 * a),))
            v = it["value"] * e
            T(im, f"{pre}{v:,.{dec}f}{(' ' + unit) if unit else ''}", bx0 + wv + 24, y, 30, INK, a * min(1, e * 4), "l", MONT, 2)
    return .35, ctx.idx, frame


def land(region):
    k = tuple(region)
    if k not in _LAND:
        from . import charts
        bx0, by0, bx1, by1 = 110, 150, W - 110, 820
        sc_ = min((bx1 - bx0) / (region[2] - region[0]), (by1 - by0) / (region[3] - region[1]))
        ox = bx0 + ((bx1 - bx0) - sc_ * (region[2] - region[0])) / 2
        oy = by0 + ((by1 - by0) - sc_ * (region[3] - region[1])) / 2
        P = lambda lon, lat: (ox + (lon - region[0]) * sc_, oy + (region[3] - lat) * sc_)
        lay = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        for c in charts.countries():
            for ring in c["rings"]:
                ld.polygon([(P(x, y)[0] * 2, P(x, y)[1] * 2) for x, y in ring], fill=(28, 66, 80, 255), outline=(52, 108, 124, 255))
        _LAND[k] = (lay.reduce(2), P)
    return _LAND[k]


def s_pin(sc, ctx):
    region = sc.get("region", [-170, -58, 180, 84])
    lay, P = land(region)
    pins = sc["pins"]
    for k in range(len(pins)):
        ctx.events.append((.5 + k * .6, "ping"))

    def frame(t, im):
        if sc.get("title"):
            title(im, ctx, sc["title"], t)
        im.paste(lay, (0, 0), lay)
        for k, p in enumerate(pins):
            t0 = .5 + k * .6
            a = ease(prog(t, t0, t0 + .4))
            x, y = P(p["lon"], p["lat"])
            ph = (t - t0) % 1.6 / 1.6
            if t > t0:
                r = 14 + 60 * ph
                ImageDraw.Draw(im, "RGBA").ellipse((x - r, y - r, x + r, y + r), outline=ctx.acc + (int(200 * (1 - ph)),), width=3)
            r2 = 11 * (1 + .4 * (1 - a))
            ImageDraw.Draw(im, "RGBA").ellipse((x - r2, y - r2, x + r2, y + r2), fill=ctx.acc + (int(255 * a),), outline=(255, 255, 255, int(255 * a)), width=2)
            if p.get("label"):
                lx = x + 28 if x < W * .7 else x - 28
                T(im, p["label"], lx, y - 4, 30, INK, a, "l" if x < W * .7 else "r", MONT, 3)
    return .7, ctx.idx, frame


def s_cards(sc, ctx):
    cs = sc["cards"]
    hl = sc.get("hl", -1)
    cw_, chh = 420, 560
    gap = 70
    tot = len(cs) * cw_ + (len(cs) - 1) * gap
    x0 = (W - tot) / 2
    ctx.events.append((.5, "whoosh"))

    def frame(t, im):
        if sc.get("title"):
            title(im, ctx, sc["title"], t)
        dd = ImageDraw.Draw(im, "RGBA")
        for k, c in enumerate(cs):
            a = ease(prog(t, .3 + k * .35, .8 + k * .35))
            x, y = x0 + k * (cw_ + gap), 190 + (1 - a) * 40
            on = k == hl
            dd.rounded_rectangle((x, y, x + cw_, y + chh), 18, fill=(255, 255, 255, int(14 * a)),
                                 outline=(ctx.acc if on else (60, 92, 106)) + (int(255 * a),), width=3)
            sp, _ = sprite(c["animal"], cw_ - 90)
            blit(im, sp, x + 45, y + chh / 2 - sp.height / 2 - 20 + math.sin(t * .8 + k) * 6, a)
            T(im, c["label"].upper(), x + cw_ / 2, y + chh - 70, 32, INK, a, "mm", MONT, 4)
            T(im, f"{k + 1:02d}", x + cw_ / 2, y + chh - 30, 22, ctx.acc if on else DIM, a, "mm", MONT, 3)
    return .3, ctx.idx, frame


def s_big(sc, ctx):
    big = sc["big"]
    ctx.events.append((.6, "impact"))
    an = sc.get("animal")
    sp = sprite(an, 900)[0] if an else None

    def frame(t, im):
        if sp is not None:
            blit(im, sp, W / 2 - sp.width / 2 + math.sin(t * .5) * 20, H * .42 - sp.height / 2, .22)
        e = ease(prog(t, .2, 1.2))
        v = big * e
        s = f"{sc.get('prefix', '')}{v:,.{sc.get('dec', 0)}f}{sc.get('suffix', '')}"
        T(im, s, W / 2, 400, 230, INK, ease(prog(t, 0, .4)), "mm", ANTON)
        ln = ease(prog(t, .8, 1.4)) * 90
        ImageDraw.Draw(im).line([(W / 2 - ln, 528), (W / 2 + ln, 528)], fill=ctx.acc, width=4)
        rich(im, sc.get("caption", ""), W / 2, 580, 36, ctx.acc, ease(prog(t, .9, 1.5)), track=5, base=MUTED)
    return sc.get("depth", .5), ctx.idx, frame


def s_text(sc, ctx):
    words = []
    on = False
    for chunk in sc["text"].split("*"):
        for w in chunk.split():
            words.append((w, on))
        on = not on
    f = font(MONT, 88)
    lines, cur, wd = [], [], 0
    for w, o in words:
        l_ = f.getlength(w.upper() + " ")
        if wd + l_ > W - 360 and cur:
            lines.append(cur); cur, wd = [], 0
        cur.append((w.upper(), o, l_)); wd += l_
    lines.append(cur)
    n = 0

    def frame(t, im):
        k = 0
        y = H * .42 - (len(lines) - 1) * 62
        for ln in lines:
            tot = sum(w[2] for w in ln)
            x = W / 2 - tot / 2
            for w, o, l_ in ln:
                a = ease(prog(t, .15 + k * .12, .5 + k * .12))
                T(im, w, x, y + (1 - a) * 24, 88, ctx.acc if o else INK, a, "l", MONT)
                x += l_; k += 1
            y += 124
    return sc.get("depth", .45), ctx.idx, frame


SCENES = {"ocean": s_ocean, "depth": s_depth, "scale": s_scale, "bars": s_bars, "pin": s_pin, "cards": s_cards, "big": s_big, "text": s_text}


# ----------------------------------------------------------------------------- render
def render_scene(sc, prof, idx, chap, d, out: Path, nframes, trans=None):
    ctx = Ctx(prof, idx, chap, d)
    depth, seed, frame = SCENES[sc["kind"]](sc, ctx)
    z0, z1 = (1.0, 1.06) if idx % 2 == 0 else (1.06, 1.0)
    side = 1 if idx % 3 else -1
    cmd = [core.FF, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", FPS, "-i", "-",
           "-vf", f"{prof['grade']}{core._fade(trans)},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-threads", "3", out]
    p = subprocess.Popen([str(c) for c in cmd], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(nframes):
            t = i / FPS
            im = backdrop(t, depth, seed)
            frame(t, im)
            e = ease(t / max(d, .1))
            zz = z0 + (z1 - z0) * e
            cw_, ch_ = W / zz, H / zz
            px = (W - cw_) * (.5 + .1 * side * (e - .5))
            py = (H - ch_) * .5
            p.stdin.write(im.convert("RGB").resize((W, H), Image.BILINEAR, box=(px, py, px + cw_, py + ch_)).tobytes())
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
    shots, events, t = [], [], 0.0
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
                if mood == "reveal" and since_flash >= 6:
                    trans, since_flash = "flash", 0
                elif b.get("chapter", bi % 4 == 0):
                    trans = "dip"
            since_flash += 1
            sp = work / "shots" / f"f{bi:03d}_{si}.mp4"
            evs = render_scene(sc, prof, bi * 3 + si, chap, nf / FPS, sp, nf, trans)
            events += [(st + e, k) for e, k in evs]
            if trans:
                events.append((max(st - .1, 0), "whoosh"))
            shots.append((sp, nf / FPS))
            st += nf / FPS
        t = st
        t = max(t, tm["start"] + D) if abs(t - (tm["start"] + D)) < .1 else t
    return shots, [], events


def sfx_bank(sr):
    import numpy as np
    rng = np.random.default_rng(23)
    n = int(1.6 * sr)
    tt = np.arange(n) / sr
    ping = np.sin(2 * np.pi * 1180 * tt) * np.exp(-tt * 5.5)                     # sonar ping + soft echo
    echo = np.zeros(n, np.float32)
    k = int(.45 * sr)
    echo[k:] = ping[:n - k] * .3
    ping = (.28 * (ping + echo) / 1.0).astype(np.float32)
    m = int(.9 * sr)
    t2 = np.arange(m) / sr
    b = np.zeros(m, np.float32)
    for st in (0, .12, .27, .4, .6):                                              # rising bubbles
        i0 = int(st * sr)
        ln = int(.09 * sr)
        tb = np.arange(ln) / sr
        if i0 + ln <= m:
            b[i0:i0 + ln] += np.sin(2 * np.pi * (420 + 2600 * tb + rng.uniform(0, 300)) * tb) * np.exp(-tb * 45) * .12
    return {"ping": ping, "bubbles": b}
