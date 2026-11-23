"""Mind engine (Mindforge Arc): original illustrated self-improvement / psychology scenes, all drawn locally (PIL/numpy), no footage.
Style family of the competitor (dark duotone silhouettes, big serif slams, one-word captions) but original art: a procedural hero silhouette in several poses.
A beat carries  "scene": {...}  or a list (shown one after another, optional "w" weight). *word* = accent colour.
  {"kind":"slam","text":"NOT *LAZY*.","pose":"stand"}                        giant serif slam over the hero silhouette (impact SFX)
  {"kind":"hero","text":"Your brain *protects* you","pose":"think|walk|up|slump|point|reach|stand","side":"r|l","eye":true,"pose2":"up"}
  {"kind":"quote","text":"..","by":"Author"}
  {"kind":"rule","n":1,"title":"..","sub":"..","try":"Tonight: ..."}
  {"kind":"loop","title":"The habit loop","nodes":[{"label":"Cue","sub":".."},{"label":"Routine","sub":".."},{"label":"Reward","sub":".."}]}
  {"kind":"before_after","left":{"label":"BEFORE","items":[".."]},"right":{"label":"AFTER","items":[".."]}}
  {"kind":"streak","days":21,"caption":"Small days add up"}
  {"kind":"brain","text":"..","labels":["Cue","Craving","Action"]}
  {"kind":"text","text":"Stop *waiting* for motivation"}
Palette changes per chapter (orange/black, red/black, teal/black). Parallax skyline + embers behind every scene."""
import math
import subprocess
from pathlib import Path
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import core
from . import tech as T
from .tech import W, H, FPS, ease, out_back, prog, font, fit_rich, draw_rich, glow, rgb

SERIF, SANS = "Cinzel-Bold.ttf", "Montserrat-Black.ttf"
INK = (240, 238, 232)
MUTED = (150, 146, 140)
PALS = [((255, 138, 0), (38, 18, 6)), ((235, 48, 44), (40, 8, 10)), ((44, 190, 204), (6, 30, 38)), ((255, 138, 0), (34, 16, 8))]
CX, CY = W // 2, 462                                        # content centre (above subtitle zone)


class Ctx:
    def __init__(self, prof, idx, chap, d):
        self.idx, self.chap, self.d = idx, chap, d
        self.acc, self.tint = PALS[chap % len(PALS)]
        self.events = []


# ----------------------------------------------------------------------------- hero silhouette (procedural, consistent mascot)
POSES = {                                   # lean, head tilt, arm A (upper,fore), arm B, leg A, leg B   (deg from straight down, + = screen right)
    "stand": (0, 0, (10, 6), (-10, -6), (3, 1), (-4, -1)),
    "think": (2, 8, (14, 8), (30, 165), (3, 1), (-4, -1)),
    "walk": (6, 0, (-28, -10), (30, 50), (24, 8), (-22, -34)),
    "up": (-3, -12, (150, 168), (-150, -168), (6, 2), (-6, -2)),
    "slump": (20, 34, (6, 2), (-6, -2), (5, 2), (-3, 0)),
    "point": (4, 0, (88, 90), (-8, -4), (4, 1), (-5, -2)),
    "reach": (10, -8, (125, 140), (-10, -6), (14, 4), (-10, -2)),
}


def _lerp_pose(a, b, k):
    pa, pb = POSES[a], POSES[b]
    out = []
    for x, y in zip(pa, pb):
        out.append(tuple(x[i] + (y[i] - x[i]) * k for i in range(2)) if isinstance(x, tuple) else x + (y - x) * k)
    return out


def hero_img(pose, hpx, acc, flip=False, eye=False, glow_a=170):
    """RGBA of the silhouette (height ~hpx) with accent rim light + halo. Anchored: feet bottom-centre."""
    ss = 1.6
    s = hpx * ss / 1000.0
    cw, ch = int(1000 * s * 0.9), int(1060 * s)
    m = Image.new("L", (cw, ch), 0)
    d = ImageDraw.Draw(m)
    ox, oy = cw / 2, 30 * s
    lean, ht, aA, aB, lA, lB = pose

    def P(x, y):
        return (ox + x * s, oy + y * s)

    def limb(p0, ang, ln, w0, w1):
        a = math.radians(ang)
        p1 = (p0[0] + math.sin(a) * ln, p0[1] + math.cos(a) * ln)
        d.line((*P(*p0), *P(*p1)), fill=255, width=int((w0 + w1) / 2 * s))
        for p, w in ((p0, w0), (p1, w1)):
            q = P(*p)
            r = w / 2 * s
            d.ellipse((q[0] - r, q[1] - r, q[0] + r, q[1] + r), fill=255)
        return p1

    la = math.radians(lean)
    hip = (0.0, 540.0)
    sh = (hip[0] + math.sin(la) * 300, hip[1] - math.cos(la) * 300)
    # legs
    for (t1, t2), dx in ((lA, 16), (lB, -16)):
        kn = limb((hip[0] + dx, hip[1]), t1, 250, 78, 58)
        limb(kn, t2, 240, 56, 44)
        fx = kn[0] + math.sin(math.radians(t2)) * 240
        fy = kn[1] + math.cos(math.radians(t2)) * 240
        d.ellipse((P(fx - 38, fy - 8)[0], P(fx, fy - 12)[1], P(fx + 70, fy + 26)[0], P(fx, fy + 26)[1]), fill=255)
    # coat torso
    nx, ny = sh
    tw = 112
    d.polygon([P(nx - tw, ny + 8), P(nx + tw, ny + 8), P(hip[0] + 92, hip[1] + 120), P(hip[0] - 92, hip[1] + 120)], fill=255)
    d.polygon([P(nx - 120, ny + 30), P(nx - 84, ny - 14), P(nx + 84, ny - 14), P(nx + 120, ny + 30)], fill=255)
    # arms
    for (a1, a2), dx in ((aA, 104), (aB, -104)):
        el = limb((nx + dx, ny + 26), a1 + lean * 0.3, 160, 56, 46)
        limb(el, a2 + lean * 0.3, 150, 44, 34)
    # neck + head
    hx = nx + math.sin(math.radians(ht)) * 36
    hy = ny - 44 - 76
    d.rectangle((*P(nx - 26, ny - 50), *P(nx + 26, ny)), fill=255)
    hr = 74
    d.ellipse((*P(hx - hr, hy - hr), *P(hx + hr, hy + hr)), fill=255)
    d.polygon([P(hx - 50, hy + 40), P(hx + 50, hy + 40), P(hx + 30, hy + 100), P(hx - 30, hy + 100)], fill=255)
    if flip:
        m = m.transpose(Image.FLIP_LEFT_RIGHT)
    mw, mh = m.size
    out_w, out_h = int(mw / ss), int(mh / ss)
    m = m.resize((out_w, out_h), Image.LANCZOS)
    # halo + rim
    halo = m.filter(ImageFilter.GaussianBlur(hpx * 0.045))
    im = Image.new("RGBA", (out_w, out_h), acc + (0,))
    im.putalpha(halo.point(lambda v: int(v * glow_a / 255)))
    rim = ImageChops.subtract(m, ImageChops.offset(m, int(hpx * 0.012) * (-1 if flip else 1), int(hpx * 0.008)))
    body = Image.new("RGBA", (out_w, out_h), (5, 5, 7, 255))
    body.putalpha(m)
    im.alpha_composite(body)
    ry = Image.new("RGBA", (out_w, out_h), acc + (255,))
    ry.putalpha(rim.filter(ImageFilter.GaussianBlur(0.8)))
    im.alpha_composite(ry)
    if eye:
        ex = (hx + (-22 if flip else 22) * 0) * 1
        ed = ImageDraw.Draw(im)
        px, py = P(hx + (-40 if flip else 40), hy - 6)
        if flip:
            px = out_w * ss - px
        px, py = px / ss, py / ss
        r = max(hpx * 0.012, 3)
        g = glow(int(r * 22), acc, 1.0)
        im.alpha_composite(g, (int(px - g.width / 2), int(py - g.height / 2)))
        ed.ellipse((px - r, py - r * 0.7, px + r, py + r * 0.7), fill=(255, 235, 200, 255))
    return im


_HC = {}


def hero_frame(pose_a, pose_b, k, hpx, acc, flip, eye):
    kq = round(k * 12) / 12 if pose_a != pose_b else 0
    key = (pose_a, pose_b, kq, hpx, acc, flip, eye)
    if key not in _HC:
        if len(_HC) > 60:
            _HC.clear()
        _HC[key] = hero_img(_lerp_pose(pose_a, pose_b, kq), hpx, acc, flip, eye)
    return _HC[key]


# ----------------------------------------------------------------------------- text + background helpers
_NOISE = {}


def _noise(sz):
    if sz not in _NOISE:
        r = np.random.default_rng(5)
        a = r.random((sz[1], sz[0]))
        a = np.where(a < 0.06, 0.15, 1.0)
        _NOISE[sz] = Image.fromarray((a * 255).astype("uint8"))
    return _NOISE[sz]


def text_glow(im, xy, text, fname, size, fill, acc, anchor="mm", gl=0.8, distress=False, alpha=1.0, scale=1.0):
    f = font(fname, size)
    bb = ImageDraw.Draw(im).textbbox((0, 0), text, font=f)
    w, h = bb[2] - bb[0] + 120, bb[3] - bb[1] + 120
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(lay).text((60 - bb[0], 60 - bb[1]), text, font=f, fill=fill + (255,))
    if distress:
        lay.putalpha(ImageChops.multiply(lay.getchannel("A"), _noise((w, h))))
    if gl:
        g = Image.new("RGBA", (w, h), acc + (0,))
        g.putalpha(lay.getchannel("A").filter(ImageFilter.GaussianBlur(size * 0.12)).point(lambda v: int(min(255, v * gl * 1.1))))
        lay = Image.alpha_composite(g, lay)
    if alpha < 1:
        lay.putalpha(lay.getchannel("A").point(lambda v: int(v * alpha)))
    if scale != 1.0:
        lay = lay.resize((max(int(w * scale), 2), max(int(h * scale), 2)), Image.BILINEAR)
        w, h = lay.size
    x, y = xy
    px = x - w / 2 if anchor[0] == "m" else (x if anchor[0] == "l" else x - w)
    py = y - h / 2
    im.alpha_composite(lay, (int(px), int(py)))
    return (px + 60, py + 60, px + w - 60, py + h - 60)


def make_bg(ctx, variant):
    acc, tint = ctx.acc, ctx.tint
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W * (0.35 + 0.3 * (variant % 3) / 2)) / W) ** 2 + ((yy - H * 0.55) / H) ** 2)
    k = np.clip(1 - r * 1.25, 0, 1) ** 1.6
    base = np.zeros((H, W, 3), np.float32) + 6
    for c in range(3):
        base[..., c] += tint[c] * k * 1.5 + (yy / H) * 3
    for c in range(3):
        base[..., c] += acc[c] * 0.10 * np.clip(1 - np.abs(yy - H * 0.72) / 90, 0, 1)      # horizon haze
    return Image.fromarray(np.clip(base, 0, 255).astype("uint8")).convert("RGBA")


def _layer(kind, ctx, seed):
    rng = np.random.default_rng(seed)
    w = int(W * 1.5)
    im = Image.new("RGBA", (w, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    acc = ctx.acc
    if kind == "far":                                   # distant skyline, tinted
        x = 0
        while x < w:
            bw = int(rng.integers(50, 150))
            bh = int(rng.integers(90, 330))
            d.rectangle((x, H * 0.80 - bh, x + bw, H), fill=tuple(int(c * 0.22 + 8) for c in acc) + (255,))
            for _ in range(int(bh / 40)):
                if rng.random() < 0.3:
                    wx, wy = x + int(rng.integers(8, max(bw - 14, 9))), H * 0.80 - bh + int(rng.integers(8, max(bh - 10, 9)))
                    d.rectangle((wx, wy, wx + 7, wy + 9), fill=acc + (150,))
            x += bw + int(rng.integers(0, 22))
    else:                                               # near hills / ground
        pts = [(0, H)]
        for x in range(0, w + 40, 40):
            pts.append((x, H * 0.86 + 26 * math.sin(x / 210 + seed) + 14 * math.sin(x / 77)))
        pts.append((w, H))
        d.polygon(pts, fill=(4, 4, 6, 255))
    return im


def hero_pos(side, hpx):
    return (W * (0.74 if side == "r" else 0.26), 470 + hpx / 2)


# ----------------------------------------------------------------------------- scenes: each returns frame(t, im) drawing onto RGBA im
def sc_slam(sc, ctx):
    words = sc["text"].replace("*", "").split()
    hl = [("*" in w0) for w0 in sc["text"].split()]
    n = len(words)
    st = 0.0
    step = min(0.55, (ctx.d - 0.25) / max(n, 1))
    for k in range(n):
        ctx.events.append((0.0 + k * step, "impact"))
    pose, side = sc.get("pose", "stand"), sc.get("side", "r")

    def frame(t, im):
        hp = hero_frame(pose, pose, 0, 640, ctx.acc, side == "l", False)
        hx, hy = hero_pos(side, 640)
        sh = 1 + 0.004 * math.sin(t * 2)
        im.alpha_composite(hp, (int(hx - hp.width / 2), int(hy - hp.height * sh + 40)))
        k = min(int(t / step), n - 1)
        wd = words[k].upper().strip(".,!?")
        pun = words[k].strip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'*")
        sz = 230 if len(wd) <= 6 else 190 if len(wd) <= 9 else 150
        lt = t - k * step
        sc_ = 1.0 + 0.5 * max(0, 1 - lt / 0.16) ** 2
        x = W * (0.36 if side == "r" else 0.64)
        col = ctx.acc if hl[k] else INK
        text_glow(im, (x, CY), wd + pun[:1], SERIF, sz, col, ctx.acc, gl=0.9, distress=True, scale=sc_)
        if lt < 0.12:                                       # flash on the hit
            fl = Image.new("RGBA", (W, H), ctx.acc + (int(60 * (1 - lt / 0.12)),))
            im.alpha_composite(fl)
    return frame


def sc_hero(sc, ctx):
    pose, pose2, side = sc.get("pose", "stand"), sc.get("pose2", sc.get("pose", "stand")), sc.get("side", "r")
    f, ls, lh = fit_rich(sc.get("text", ""), SANS, 104, 56, 880, 520, 1.12)
    words = sum(len(x) for x in ls)
    step = min(0.2, (ctx.d - 0.3) / max(words, 1))
    for k in range(words):
        ctx.events.append((k * step, "tick"))
    eye = sc.get("eye", False)
    left_text = side == "r"

    def frame(t, im):
        k = ease((t - 0.3) / max(ctx.d * 0.6, 0.5))
        hp = hero_frame(pose, pose2, k, 700, ctx.acc, not left_text, eye)
        hx, hy = hero_pos(side, 700)
        bob = 3 * math.sin(t * 2.2)
        im.alpha_composite(hp, (int(hx - hp.width / 2), int(hy - hp.height + 50 + bob)))
        d = ImageDraw.Draw(im)
        th = len(ls) * lh
        y0 = CY - th / 2
        x0 = 110 if left_text else W - 110 - 880
        n = 0
        sp = f.getlength(" ")
        for i, ln in enumerate(ls):
            x = x0
            for wd, hl in ln:
                e = out_back((t - n * step) / 0.22)
                if t >= n * step:
                    d.text((x, y0 + i * lh + (1 - min(e, 1)) * 36), wd, font=f, fill=ctx.acc if hl else INK, stroke_width=0)
                x += f.getlength(wd) + sp
                n += 1
    return frame


def sc_quote(sc, ctx):
    f, ls, lh = fit_rich(sc["text"], SERIF, 92, 50, 1400, 430, 1.22)
    words = sum(len(x) for x in ls)
    step = min(0.16, (ctx.d - 0.4) / max(words, 1))
    ctx.events.append((0.0, "pop"))

    def frame(t, im):
        d = ImageDraw.Draw(im)
        th = len(ls) * lh
        y0 = CY - th / 2 - 20
        text_glow(im, (W / 2, y0 - 70), "“", SERIF, 230, ctx.acc, ctx.acc, gl=0.6, distress=False)
        n, sp = 0, f.getlength(" ")
        for i, ln in enumerate(ls):
            lw = sum(f.getlength(w0[0]) for w0 in ln) + sp * (len(ln) - 1)
            x = W / 2 - lw / 2
            for wd, hl in ln:
                if t >= n * step:
                    e = ease((t - n * step) / 0.15)
                    d.text((x, y0 + i * lh + (1 - e) * 14), wd, font=f, fill=ctx.acc if hl else INK)
                x += f.getlength(wd) + sp
                n += 1
        if sc.get("by"):
            a = prog(t, 0.3 + words * step * 0.6, 0.6 + words * step * 0.6)
            ln_w = int(160 * a)
            y = y0 + th + 40
            d.line((W / 2 - ln_w, y, W / 2 + ln_w, y), fill=ctx.acc, width=4)
            fb = font(SANS, 44)
            d.text((W / 2, y + 44), sc["by"].upper(), font=fb, fill=tuple(int(c * a) for c in MUTED), anchor="mm")
    return frame


def sc_rule(sc, ctx):
    ft, lt_, ltl = fit_rich(sc["title"], SANS, 110, 60, 1000, 300, 1.1)
    fs, ls_, lsl = fit_rich(sc.get("sub", ""), SANS, 62, 40, 1000, 190, 1.2)
    ctx.events.append((0.0, "impact"))
    if sc.get("try"):
        ctx.events.append((1.2, "pop"))

    def frame(t, im):
        d = ImageDraw.Draw(im)
        pop = 1 + 0.35 * max(0, 1 - t / 0.2) ** 2
        text_glow(im, (330, CY - 30), str(sc["n"]), SERIF, 520, ctx.acc, ctx.acc, gl=0.9, distress=True, scale=pop)
        text_glow(im, (330, CY - 300), "RULE", SANS, 56, MUTED, ctx.acc, gl=0, scale=1.0)
        d.line((640, 170, 640, 760), fill=ctx.acc + (255,) if False else ctx.acc, width=4)
        ty = CY - 190 if sc.get("try") else CY - 150
        for i, ln in enumerate(lt_):
            a = prog(t, 0.05 + i * 0.1, 0.3 + i * 0.1)
            draw_rich(d, 700, ty + i * ltl + (1 - a) * 30, [ln], ft, ltl, tuple(int(c * (0.35 + 0.65 * a)) for c in INK), ctx.acc)
        sy = ty + len(lt_) * ltl + 18
        a = prog(t, 0.35, 0.7)
        draw_rich(d, 700, sy, ls_, fs, lsl, tuple(int(c * (0.4 + 0.6 * a)) for c in MUTED), ctx.acc)
        if sc.get("try"):
            a = prog(t, 1.1, 1.5)
            by = 700
            d.rounded_rectangle((700, by - 20, 1810, by + 96), 16, fill=tuple(int(c * 0.30 * a + 8) for c in ctx.acc), outline=ctx.acc, width=3)
            fb = font(SANS, 50)
            d.text((730, by + 38), "TRY: " + sc["try"], font=fb, fill=tuple(int(c * (0.3 + 0.7 * a)) for c in INK), anchor="lm")
    return frame


def sc_loop(sc, ctx):
    nodes = [n if isinstance(n, dict) else {"label": n} for n in sc["nodes"]][:4]
    n = len(nodes)
    R, cx, cy = 330, W / 2, CY + 30
    pos = [(cx + R * 1.6 * math.sin(2 * math.pi * i / n), cy - R * 0.92 * math.cos(2 * math.pi * i / n)) for i in range(n)]
    for i in range(n):
        ctx.events.append((0.2 + i * 0.5, "pop"))

    def frame(t, im):
        d = ImageDraw.Draw(im)
        d.text((cx, 110), sc.get("title", "").upper(), font=font(SANS, 60), fill=MUTED, anchor="mm")
        for i in range(n):                                  # arrows between nodes
            a0 = prog(t, 0.45 + i * 0.5, 0.95 + i * 0.5)
            p0, p1 = pos[i], pos[(i + 1) % n]
            q = (p0[0] + (p1[0] - p0[0]) * a0, p0[1] + (p1[1] - p0[1]) * a0)
            d.line((*p0, *q), fill=ctx.acc, width=7)
            if a0 > 0.98:
                ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
                tip = (p1[0] - math.cos(ang) * 150, p1[1] - math.sin(ang) * 150)
                d.polygon([tip, (tip[0] - math.cos(ang - 0.45) * 40, tip[1] - math.sin(ang - 0.45) * 40),
                           (tip[0] - math.cos(ang + 0.45) * 40, tip[1] - math.sin(ang + 0.45) * 40)], fill=ctx.acc)
        if t > 0.5 + n * 0.5:                               # travelling pulse
            ph = (t * 0.7) % 1 * n
            i = int(ph)
            p0, p1 = pos[i % n], pos[(i + 1) % n]
            u = ph - i
            px, py = p0[0] + (p1[0] - p0[0]) * u, p0[1] + (p1[1] - p0[1]) * u
            g = glow(160, ctx.acc, 1.0)
            im.alpha_composite(g, (int(px - 80), int(py - 80)))
            d.ellipse((px - 11, py - 11, px + 11, py + 11), fill=(255, 240, 215))
        for i, nd in enumerate(nodes):
            e = out_back((t - i * 0.5 - 0.1) / 0.3)
            if t < i * 0.5 + 0.1:
                continue
            x, y = pos[i]
            bw, bh = 400 * min(e, 1.05), 190 * min(e, 1.05)
            d.rounded_rectangle((x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2), 26, fill=(14, 12, 12), outline=ctx.acc, width=5)
            d.text((x, y - 18), nd["label"].upper(), font=font(SANS, 66), fill=INK, anchor="mm")
            if nd.get("sub"):
                d.text((x, y + 48), nd["sub"], font=font(SANS, 36), fill=MUTED, anchor="mm")
    return frame


def sc_before_after(sc, ctx):
    L, R = sc["left"], sc["right"]
    ctx.events.append((0.1, "pop"))
    ctx.events.append((ctx.d * 0.45, "whoosh"))
    ctx.events.append((ctx.d * 0.45 + 0.1, "impact"))

    def panel(im, x0, label, items, col, t0, t, pose, flip, dim):
        d = ImageDraw.Draw(im)
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        ld.rounded_rectangle((x0, 110, x0 + 840, 800), 30, fill=(10, 9, 10, 215), outline=col + (255,), width=4)
        im.alpha_composite(lay)
        d.text((x0 + 420, 160), label.upper(), font=font(SANS, 64), fill=col, anchor="mm")
        hp = hero_frame(pose, pose, 0, 300, col, flip, False)
        im.alpha_composite(hp, (int(x0 + 680 - hp.width / 2), int(790 - hp.height)))
        for i, it in enumerate(items[:4]):
            a = prog(t, t0 + 0.25 + i * 0.22, t0 + 0.55 + i * 0.22)
            f, ls, lh = fit_rich(it, SANS, 48, 32, 400, 130, 1.15)
            y = 250 + i * 128
            d.ellipse((x0 + 40, y + 6, x0 + 66, y + 32), fill=col if a > 0.5 else (60, 56, 56))
            draw_rich(d, x0 + 84, y - 4 + (1 - a) * 18, ls[:2], f, lh, tuple(int(c * (0.25 + 0.75 * a)) for c in INK), col)

    def frame(t, im):
        grey = (150, 150, 158)
        sw = ease((t - ctx.d * 0.45) / 0.4)
        panel(im, 80, L.get("label", "BEFORE"), L["items"], grey, 0.0, t, "slump", False, True)
        panel(im, 1000, R.get("label", "AFTER"), R["items"], ctx.acc, ctx.d * 0.45, t, "up", True, False)
        if t < ctx.d * 0.45:                                     # right panel dimmed until the swap
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(ov).rounded_rectangle((1000, 110, 1840, 800), 30, fill=(4, 4, 6, int(200 - 120 * prog(t, 0, 0.4))))
            im.alpha_composite(ov)
        d = ImageDraw.Draw(im)
        d.polygon([(930, 440), (990, 460), (930, 480)], fill=ctx.acc)
    return frame


def sc_streak(sc, ctx):
    days = int(sc.get("days", 21))
    cols = 7
    rows = math.ceil(days / cols)
    cell, gap = 92, 16
    gw = cols * cell + (cols - 1) * gap
    gh = rows * cell + (rows - 1) * gap
    x0, y0 = 150, CY - gh / 2
    per = max((ctx.d - 0.5) / days, 0.04)
    for k in range(0, days, 3):
        ctx.events.append((0.2 + k * per, "tick"))

    def frame(t, im):
        d = ImageDraw.Draw(im)
        n = min(int((t - 0.15) / per) + 1, days) if t > 0.15 else 0
        for i in range(days):
            r_, c_ = divmod(i, cols)
            x, y = x0 + c_ * (cell + gap), y0 + r_ * (cell + gap)
            if i < n:
                u = ease((t - 0.15 - i * per) / 0.18)
                g = glow(int(cell * 2.4), ctx.acc, 0.5 * (1 - u * 0.6))
                im.alpha_composite(g, (int(x + cell / 2 - g.width / 2), int(y + cell / 2 - g.height / 2)))
                d.rounded_rectangle((x, y, x + cell, y + cell), 18, fill=ctx.acc)
                d.line((x + 24, y + 48, x + 40, y + 64, x + 70, y + 28), fill=(20, 10, 4), width=9, joint="curve")
            else:
                d.rounded_rectangle((x, y, x + cell, y + cell), 18, fill=(20, 18, 18), outline=(60, 56, 54), width=3)
        text_glow(im, (1330, CY - 90), "DAY", SANS, 74, MUTED, ctx.acc, gl=0)
        text_glow(im, (1330, CY + 60), str(max(n, 1)), SERIF, 300, ctx.acc, ctx.acc, gl=0.8, distress=True)
        if sc.get("caption"):
            d.text((1330, CY + 250), sc["caption"], font=font(SANS, 40), fill=INK, anchor="mm")
    return frame


def sc_brain(sc, ctx):
    rng = np.random.default_rng(3)
    nodes = [(CX - 330 + float(rng.random()) * 460, CY - 260 + float(rng.random()) * 330) for _ in range(26)]
    labels = sc.get("labels", [])
    path = [nodes[i] for i in (3, 9, 14, 20, 23)][: max(len(labels), 3) + 1]
    ctx.events.append((0.1, "pop"))
    f, ls, lh = fit_rich(sc.get("text", ""), SANS, 70, 44, 640, 440, 1.15)

    def frame(t, im):
        d = ImageDraw.Draw(im)
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        cx, cy = CX - 120, CY - 40
        ld.ellipse((cx - 400, cy - 330, cx + 330, cy + 250), fill=(14, 12, 12, 255), outline=ctx.acc + (255,), width=6)
        ld.rounded_rectangle((cx - 70, cy + 230, cx + 50, cy + 330), 20, fill=(14, 12, 12, 255), outline=ctx.acc + (255,), width=6)
        ld.ellipse((cx - 400, cy - 330, cx + 330, cy + 250), fill=(14, 12, 12, 255))
        ld.arc((cx - 400, cy - 330, cx + 330, cy + 250), 0, 360, fill=ctx.acc + (255,), width=6)
        im.alpha_composite(lay)
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                if (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 < 150 ** 2:
                    d.line((*a, *b), fill=tuple(int(c * 0.25) for c in ctx.acc), width=2)
        for a in nodes:
            d.ellipse((a[0] - 6, a[1] - 6, a[0] + 6, a[1] + 6), fill=tuple(int(c * 0.5) for c in ctx.acc))
        seg = max(len(path) - 1, 1)
        pr = min(max((t - 0.4) / max(ctx.d * 0.6, 0.6), 0), 1) * seg
        for i in range(seg):
            u = min(max(pr - i, 0), 1)
            if u <= 0:
                break
            a, b = path[i], path[i + 1]
            q = (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)
            d.line((*a, *q), fill=ctx.acc, width=8)
            g = glow(130, ctx.acc, 1.0)
            im.alpha_composite(g, (int(q[0] - 65), int(q[1] - 65)))
            if u >= 1 and i < len(labels):
                d.text((b[0], b[1] - 46), labels[i].upper(), font=font(SANS, 40), fill=INK, anchor="mm", stroke_width=5, stroke_fill=(6, 6, 8))
        for p in path[: int(pr) + 1]:
            d.ellipse((p[0] - 13, p[1] - 13, p[0] + 13, p[1] + 13), fill=(255, 235, 200))
        a = prog(t, 0.1, 0.5)
        draw_rich(d, 1180, CY - len(ls) * lh / 2 + (1 - a) * 20, ls, f, lh, tuple(int(c * (0.3 + 0.7 * a)) for c in INK), ctx.acc)
    return frame


def sc_text(sc, ctx):
    f, ls, lh = fit_rich(sc["text"], SERIF, 150, 64, W - 360, 560, 1.12)
    words = sum(len(x) for x in ls)
    step = min(0.3, (ctx.d - 0.4) / max(words, 1))
    for k in range(words):
        ctx.events.append((k * step, "pop" if k % 2 == 0 else "tick"))

    def frame(t, im):
        d = ImageDraw.Draw(im)
        y = CY - len(ls) * lh / 2
        n, sp = 0, f.getlength(" ")
        for i, ln in enumerate(ls):
            lw = sum(f.getlength(w0[0]) for w0 in ln) + sp * (len(ln) - 1)
            x = (W - lw) / 2
            for wd, hl in ln:
                if t >= n * step:
                    e = out_back((t - n * step) / 0.25)
                    d.text((x, y + i * lh + (1 - min(e, 1)) * 40), wd, font=f, fill=ctx.acc if hl else INK)
                x += f.getlength(wd) + sp
                n += 1
    return frame


SCENES = {"slam": sc_slam, "hero": sc_hero, "quote": sc_quote, "rule": sc_rule, "loop": sc_loop, "before_after": sc_before_after,
          "streak": sc_streak, "brain": sc_brain, "text": sc_text}


# ----------------------------------------------------------------------------- rendering
def _vignette():
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    a = np.clip((r - 0.55) / 0.9, 0, 1) ** 1.5 * 200
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    im.putalpha(Image.fromarray(a.astype("uint8")))
    return im


_VIG = None


def render_scene(sc, prof, idx, chap, d, out: Path, nframes, trans=None):
    global _VIG
    _VIG = _VIG or _vignette()
    ctx = Ctx(prof, idx, chap, d)
    bg = make_bg(ctx, idx)
    far, near = _layer("far", ctx, idx), _layer("near", ctx, idx + 7)
    rng = np.random.default_rng(idx)
    emb = [(float(rng.random()), float(rng.random()), 1.5 + float(rng.random()) * 3.5, 0.03 + float(rng.random()) * 0.08) for _ in range(34)]
    frame = SCENES[sc["kind"]](sc, ctx)
    z0, z1 = (1.0, 1.06) if idx % 2 == 0 else (1.06, 1.0)
    cmd = [core.FF, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", FPS, "-i", "-",
           "-vf", f"{prof['grade']}{core._fade(trans)},noise=alls=5:allf=t,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-threads", "3", out]
    p = subprocess.Popen([str(c) for c in cmd], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(nframes):
            t = i / FPS
            im = bg.copy()
            sx = int(t * 14)
            im.alpha_composite(far.crop((sx, 0, sx + W, H)))
            sx2 = int(t * 34)
            im.alpha_composite(near.crop((sx2, 0, sx2 + W, H)))
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            od = ImageDraw.Draw(ov)
            for ex, ey, er, es in emb:
                yy = (ey - t * es) % 1.0
                xx = (ex + 0.02 * math.sin(t * 1.3 + ey * 9)) % 1.0
                od.ellipse((xx * W - er, yy * H - er, xx * W + er, yy * H + er), fill=ctx.acc + (int(150 * (1 - yy) ** 0.7),))
            im.alpha_composite(ov)
            frame(t, im)
            im.alpha_composite(_VIG)
            e = ease(t / max(d, 0.1))
            zz = z0 + (z1 - z0) * e
            cw_, ch_ = W / zz, H / zz
            px, py = (W - cw_) / 2, (H - ch_) * 0.5
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


# ----------------------------------------------------------------------------- planning
def auto_scene(b, bi):
    ws = [w.strip(".,;:!?\"'") for w in b["text"].split() if len(w) > 3]
    key = " ".join(ws[:5]) or b["text"][:30]
    if bi % 2:
        return {"kind": "hero", "text": key, "pose": ["stand", "think", "walk", "point", "reach"][bi % 5], "side": "r" if bi % 4 else "l"}
    return {"kind": "text", "text": key}


def plan_shots(beats, timing, prof, work: Path, broll=()):
    shots, events, t = [], [], 0.0
    (work / "shots").mkdir(exist_ok=True)
    chap, since_flash = 0, 99
    for bi, (b, tm) in enumerate(zip(beats, timing)):
        D = tm["dur"]
        scs = b.get("scene") or auto_scene(b, bi)
        scs = scs if isinstance(scs, list) else [scs]
        ws = [float(s.get("w", 1)) for s in scs]
        mood = core.mood(b["text"])
        newchap = bi > 0 and b.get("chapter", bi % 4 == 0)
        if newchap:
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
                elif newchap:
                    trans = "dip"
            since_flash += 1
            sp = work / "shots" / f"m{bi:03d}_{si}.mp4"
            evs = render_scene(sc, prof, bi * 3 + si, chap, nf / FPS, sp, nf, trans)
            events += [(st + e, k) for e, k in evs]
            if trans:
                events.append((max(st - 0.1, 0), "whoosh"))
            shots.append((sp, nf / FPS))
            st += nf / FPS
        t = st
        t = max(t, tm["start"] + D) if abs(t - (tm["start"] + D)) < 0.1 else t
    return shots, [], events
