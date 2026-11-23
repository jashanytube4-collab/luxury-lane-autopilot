"""Tech engine: locally drawn code / chat / flow / compare / chart / stat scenes (no external footage). Used when profile engine == 'tech'.

A beat may carry  "scene": {...}  or  "scene": [{...}, {...}]  (shown one after another across the beat, optional "w" weight).
scene kinds (all text is plain; *word* = accent colour):
  {"kind":"code","lang":"python|js|json|shell|prompt","title":"file.py","lines":["..",".."],"hl":[2]}      typewriter + syntax colours + cursor
  {"kind":"terminal","title":"zsh","lines":["$ cmd","output"]}
  {"kind":"chat","title":"Assistant","msgs":[{"role":"user","text":".."},{"role":"ai","text":".."}]}
  {"kind":"flow","title":"..","nodes":["Input",{"label":"Draft","sub":"AI"},"Check","Publish"]}
  {"kind":"compare","title":"..","left":{"label":"BEFORE","value":40,"unit":"min","items":[".."]},"right":{"label":"AFTER","value":6,"unit":"min","items":[".."]}}
  {"kind":"bullets","title":"..","items":["..",".."],"check":true}
  {"kind":"bars","title":"..","bars":[{"label":"A","value":12},..],"unit":"min","best":"low|high","note":".."}
  {"kind":"stat","old":"40 min","new":"6 min","caption":".."}      (or "big":"3x")
  {"kind":"text","text":"Stop *typing* it all by hand"}
No scene -> 'text' scene from the beat text. Everything drawn with PIL, piped to ffmpeg."""
import math
import re
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import core

W, H, FPS = 1920, 1080, 30
FONTS = core.ROOT / "assets" / "fonts"
TOP, BOT, MX = 70, 860, 120                 # content safe area (subtitles live below BOT)
INK, MUTED, DIM = (233, 237, 247), (140, 150, 178), (86, 96, 124)
CORAL, MINT, AMBER = (255, 106, 106), (53, 224, 176), (255, 190, 90)
TINTS = [(34, 22, 78), (14, 52, 70), (60, 22, 68), (18, 36, 82), (16, 62, 56)]   # per-chapter background tints
_FC = {}


def font(name, size):
    k = (name, size)
    if k not in _FC:
        _FC[k] = ImageFont.truetype(str(FONTS / name), int(size))
    return _FC[k]


MONO, MONOB, HEAD, HEADB = "JetBrainsMono-Medium.ttf", "JetBrainsMono-Bold.ttf", "Montserrat-ExtraBold.ttf", "Montserrat-Black.ttf"


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def out_back(x):
    x = min(max(x, 0.0), 1.0)
    c1 = 1.5
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


def mix(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def prog(t, a, b):
    return ease((t - a) / max(b - a, 1e-6))


# ----------------------------------------------------------------------------- text helpers
def tokens(text):
    """'Stop *typing* it' -> [(word, highlighted)]"""
    out, hl, prev = [], False, " "
    for part in re.split(r"(\*)", text):
        if part == "*":
            hl = not hl
        else:
            ws = part.split()
            if part:
                pe, prev = prev, part
            if ws and out and not part[0].isspace() and not pe[-1:].isspace():     # "*Role*:" keeps the colon on the word
                out[-1] = (out[-1][0] + ws[0], out[-1][1]); ws = ws[1:]
            out += [(w, hl) for w in ws]
    return out


def wrap_tokens(toks, f, maxw):
    sp = f.getlength(" ")
    lines, cur, w = [], [], 0
    for tk in toks:
        tw = f.getlength(tk[0])
        if cur and w + sp + tw > maxw:
            lines.append(cur); cur, w = [], 0
        w += (sp if cur else 0) + tw
        cur.append(tk)
    if cur:
        lines.append(cur)
    return lines


def fit_rich(text, fname, maxsize, minsize, w, h, lead=1.28):
    """Largest font size where wrapped text fits w x h. Returns (font, lines, line_h)."""
    toks = tokens(text)
    for s in range(int(maxsize), int(minsize) - 1, -2):
        f = font(fname, s)
        ls = wrap_tokens(toks, f, w)
        if len(ls) * s * lead <= h and all(sum(f.getlength(t[0]) for t in ln) + f.getlength(" ") * (len(ln) - 1) <= w + 1 for ln in ls):
            return f, ls, s * lead
    f = font(fname, minsize)
    return f, wrap_tokens(toks, f, w), minsize * lead


def draw_rich(d, x, y, lines, f, lh, fill, acc, anchor="l", maxw=None, alpha_words=None):
    """Draw wrapped rich lines. anchor l|m. alpha_words: how many words visible (None = all)."""
    n = 0
    sp = f.getlength(" ")
    for i, ln in enumerate(lines):
        lw = sum(f.getlength(t[0]) for t in ln) + sp * (len(ln) - 1)
        cx = x - lw / 2 if anchor == "m" else x
        for wd, hl in ln:
            if alpha_words is not None and n >= alpha_words:
                return
            d.text((cx, y + i * lh), wd, font=f, fill=acc if hl else fill)
            cx += f.getlength(wd) + sp
            n += 1


def rrect(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(box, r, fill=fill, outline=outline, width=width)


def glow(size, color, strength=1.0, blur=None):
    """RGBA soft glow sprite."""
    im = Image.new("RGBA", (size, size), color + (0,))
    ImageDraw.Draw(im).ellipse((size * 0.22, size * 0.22, size * 0.78, size * 0.78), fill=color + (int(150 * strength),))
    return im.filter(ImageFilter.GaussianBlur(blur or size * 0.16))


def shadow_box(base, box, r, blur=30, alpha=150, off=14):
    lay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(lay).rounded_rectangle((box[0], box[1] + off, box[2], box[3] + off), r, fill=(0, 0, 0, alpha))
    base.alpha_composite(lay.filter(ImageFilter.GaussianBlur(blur)))


# ----------------------------------------------------------------------------- background
def make_bg(tint, variant, acc):
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    for y in range(H):
        k = y / H
        d.line((0, y, W, y), fill=mix((11, 13, 24), (6, 8, 15), k) if False else (int(9 + tint[0] * 0.5 * (1 - k)), int(11 + tint[1] * 0.5 * (1 - k)), int(20 + tint[2] * 0.6 * (1 - k))))
    im = im.convert("RGBA")
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    line = acc + (16,)
    if variant % 3 == 0:                       # dot grid
        for gx in range(60, W, 60):
            for gy in range(60, H, 60):
                ld.ellipse((gx - 2, gy - 2, gx + 2, gy + 2), fill=acc + (44,))
    elif variant % 3 == 1:                     # fine grid
        for gx in range(0, W, 96):
            ld.line((gx, 0, gx, H), fill=line, width=1)
        for gy in range(0, H, 96):
            ld.line((0, gy, W, gy), fill=line, width=1)
    else:                                      # diagonal hairlines
        for k in range(-H, W, 110):
            ld.line((k, 0, k + H, H), fill=line, width=1)
    im.alpha_composite(lay)
    vg = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vg).ellipse((-W * 0.25, -H * 0.3, W * 1.25, H * 1.3), fill=255)
    vg = vg.filter(ImageFilter.GaussianBlur(160))
    dark = Image.new("RGBA", (W, H), (3, 4, 9, 255))
    dark.putalpha(Image.eval(vg, lambda v: 175 - int(v * 175 / 255)))
    im.alpha_composite(dark)
    return im.convert("RGB")


# ----------------------------------------------------------------------------- scene base
class Ctx:
    def __init__(self, prof, idx, chap, d):
        self.acc = rgb(prof["accent"])
        self.idx, self.chap, self.d = idx, chap, d
        self.tint = TINTS[chap % len(TINTS)]
        self.events = []                      # (t_in_scene, kind)
        self.sec = MINT if self.acc != MINT else (124, 92, 255)


def window(base, box, title, acc, tag=None):
    """Chrome-style window: shadow, panel, title bar with dots. Returns content box."""
    shadow_box(base, box, 22)
    d = ImageDraw.Draw(base)
    rrect(d, box, 22, fill=(18, 23, 42, 255), outline=(60, 70, 108, 255), width=2)
    bar = (box[0], box[1], box[2], box[1] + 62)
    d.rounded_rectangle(bar, 22, fill=(21, 26, 46, 255))
    d.rectangle((box[0], box[1] + 40, box[2], box[1] + 62), fill=(21, 26, 46, 255))
    d.line((box[0], box[1] + 62, box[2], box[1] + 62), fill=(44, 52, 82, 255), width=2)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse((box[0] + 28 + i * 34, box[1] + 21, box[0] + 46 + i * 34, box[1] + 39), fill=c)
    if title:
        d.text(((box[0] + box[2]) / 2, box[1] + 31), title, font=font(MONO, 25), fill=MUTED, anchor="mm")
    if tag:
        tw = font(MONOB, 21).getlength(tag) + 28
        rrect(d, (box[2] - 30 - tw, box[1] + 15, box[2] - 30, box[1] + 47), 16, fill=mix((21, 26, 46), acc, 0.25), outline=acc, width=2)
        d.text((box[2] - 30 - tw / 2, box[1] + 31), tag, font=font(MONOB, 21), fill=acc, anchor="mm")
    return (box[0] + 40, box[1] + 62 + 30, box[2] - 40, box[3] - 28)


def heading(base, text, acc, y=TOP + 6, size=62, anchor="l"):
    d = ImageDraw.Draw(base)
    f, ls, lh = fit_rich(text, HEADB, size, 36, W - 2 * MX, 90, 1.1)
    draw_rich(d, MX if anchor == "l" else W / 2, y, ls[:1], f, lh, INK, acc, anchor)
    return y + lh


# ----------------------------------------------------------------------------- syntax highlight
KW = {"python": r"\b(def|return|import|from|for|in|if|else|elif|print|class|with|as|not|and|or|True|False|None|lambda|while)\b",
      "js": r"\b(const|let|var|function|return|await|async|import|from|if|else|for|new|true|false|null)\b",
      "json": r"(?!x)x", "shell": r"\b(git|cd|ls|npm|pip|python|curl|cat|echo|export|sudo)\b", "prompt": r"(?!x)x"}
C_KW, C_STR, C_NUM, C_COM, C_FN, C_TXT = (183, 156, 255), (126, 231, 160), (255, 184, 107), (92, 103, 132), (108, 182, 255), INK


def highlight(line, lang):
    """-> list of (text, color) segments."""
    if lang == "prompt":
        segs = []
        for m in re.finditer(r"(\[[^\]]+\])|(^[A-Z][A-Za-z ]{1,18}:)|(\"[^\"]*\")|([^\[\"]+?(?=\[|\"|$|[A-Z][A-Za-z ]{1,18}:))|(.)", line):
            t = m.group(0)
            c = MINT if m.group(1) else C_KW if m.group(2) else C_STR if m.group(3) else C_TXT
            segs.append((t, c))
        return segs or [(line, C_TXT)]
    if lang == "shell" and line.startswith("$ "):
        return [("$ ", MINT)] + highlight(line[2:], "shell")
    pat = re.compile("|".join([r"(?P<com>#.*|//.*)", r"(?P<str>\"[^\"]*\"|'[^']*')", r"(?P<num>\b\d+(?:\.\d+)?\b)", f"(?P<kw>{KW.get(lang, KW['python'])})", r"(?P<fn>\b[A-Za-z_]\w*(?=\())"]))
    segs, pos = [], 0
    for m in pat.finditer(line):
        if m.start() > pos:
            segs.append((line[pos:m.start()], C_TXT))
        k = m.lastgroup
        segs.append((m.group(0), {"com": C_COM, "str": C_STR, "num": C_NUM, "kw": C_KW, "fn": C_FN}[k]))
        pos = m.end()
    if pos < len(line):
        segs.append((line[pos:], C_TXT))
    return segs or [("", C_TXT)]


def wrap_code(lines, cols):
    out = []
    for ln in lines:
        if len(ln) <= cols:
            out.append((ln, False)); continue
        ind = len(ln) - len(ln.lstrip())
        cur = ln
        first = True
        while len(cur) > cols:
            cut = cur.rfind(" ", ind, cols)
            cut = cut if cut > ind else cols
            out.append((cur[:cut], not first)); cur = " " * (ind + 2) + cur[cut:].lstrip(); first = False
        out.append((cur, not first))
    return out


# ----------------------------------------------------------------------------- scenes: each returns (static_fn, frame_fn)
# frame_fn(t, img) draws dynamic stuff on top of a copy of the static base (RGBA). Scenes add events to ctx.events.
def scene_code(sc, ctx, bg):
    lang = sc.get("lang", "python")
    term = sc["kind"] == "terminal"
    if term:
        lang = "shell"
    raw = list(sc["lines"])
    box = (MX + 20, TOP + 20, W - MX - 20, BOT) if len(raw) > 7 else (MX + 60, TOP + 50, W - MX - 60, BOT - 20)
    base = bg.convert("RGBA")
    cb = window(base, box, sc.get("title", "terminal" if term else "main.py"), ctx.acc, tag=None if term else lang.upper())
    cw, ch = cb[2] - cb[0] - 70, cb[3] - cb[1]
    size = 64
    while size > 28:
        f = font(MONO, size)
        cols = int(cw / f.getlength("M"))
        rows = wrap_code(raw, cols)
        if len(rows) * size * 1.5 <= ch:
            break
        size -= 2
    f = font(MONO, size); lh = size * 1.5
    cols = int(cw / f.getlength("M")); rows = wrap_code(raw, cols)
    total = sum(len(r[0]) for r in rows)
    hls = set(sc.get("hl", []))
    d = ImageDraw.Draw(base)
    ln_no = 0
    meta = []
    for i, (txt, cont) in enumerate(rows):
        if not cont:
            ln_no += 1
        meta.append(ln_no if not cont else None)
    gut = f.getlength("00") + 26
    ty0 = cb[1] + max((ch - len(rows) * lh) / 2, 0)
    t_start, t_end = 0.0, max(ctx.d * 0.65, 1.2)
    for k in range(0, total, 3 if total < 120 else 5):
        ctx.events.append((t_start + (t_end - t_start) * k / max(total, 1), "tick"))

    def frame(t, im):
        d = ImageDraw.Draw(im)
        n = max(int(total * prog(t, t_start, t_end)), min(total, 14))
        used = 0
        cur = None
        for i, (txt, cont) in enumerate(rows):
            y = ty0 + i * lh
            if i in hls or (meta[i] in hls):
                d.rectangle((cb[0] - 20, y - 4, cb[2] + 20, y + lh - 8), fill=mix((14, 18, 32), ctx.acc, 0.2))
            if meta[i] and used <= n:
                d.text((cb[0], y), f"{meta[i]:>2}", font=f, fill=DIM)
            x = cb[0] + gut
            vis = txt[:max(n - used, 0)]
            pos = 0
            for seg, col in highlight(txt, lang):
                part = vis[pos:pos + len(seg)]
                if part:
                    d.text((x + pos * f.getlength("M"), y), part, font=f, fill=col)
                pos += len(seg)
            if used <= n <= used + len(txt) and (n < total or True):
                cur = (x + len(vis) * f.getlength("M"), y)
            used += len(txt)
            if used > n:
                break
        if cur and (int(t * 2.2) % 2 == 0 or n < total):
            d.rectangle((cur[0] + 2, cur[1] + 2, cur[0] + f.getlength("M") * 0.62, cur[1] + size * 1.15), fill=ctx.acc + (230,))
    return base, frame, (0.4, 0.0)


def scene_chat(sc, ctx, bg):
    base = bg.convert("RGBA")
    box = (MX + 60, TOP + 20, W - MX - 60, BOT)
    cb = window(base, box, sc.get("title", "Assistant"), ctx.acc)
    msgs = sc["msgs"][:3]
    cw = cb[2] - cb[0]
    avail = cb[3] - cb[1] - 10
    size = 52
    while True:
        f = font(MONO, size)
        lay = []
        for m in msgs:
            ls = wrap_tokens(tokens(m["text"]), f, cw * 0.72 - 56)
            lay.append((ls, len(ls) * size * 1.38 + 44))
        if sum(h for _, h in lay) + 28 * len(msgs) <= avail or size <= 24:
            break
        size -= 2
    f = font(MONO, size)
    n = len(msgs)
    seg = (ctx.d - 0.5) / n
    y = cb[1] + max((avail - sum(h for _, h in lay) - 28 * (len(msgs) - 1)) / 2, 6)
    items = []
    for i, (m, (ls, bh)) in enumerate(zip(msgs, lay)):
        user = m["role"] == "user"
        bw = min(cw * 0.78, max(sum(f.getlength(t[0]) for t in ls[0]) + f.getlength(" ") * (len(ls[0]) - 1) if len(ls) == 1 else cw * 0.72, 260) + 56)
        if len(ls) > 1:
            bw = cw * 0.72
        x0 = cb[2] - bw if user else cb[0] + 66
        items.append((m, ls, bh, (x0, y, x0 + bw, y + bh), user, 0.45 + i * seg, 0.45 + i * seg + seg * (0.55 if user else 0.8)))
        y += bh + 28
        for k in range(0, sum(len(t[0]) + 1 for l2 in ls for t in l2), 4):
            ctx.events.append((0.45 + i * seg + 0.2 + k / max(sum(len(t[0]) + 1 for l2 in ls for t in l2), 1) * seg * 0.6, "tick" if user else "tick"))

    def frame(t, im):
        d = ImageDraw.Draw(im)
        for (m, ls, bh, bx, user, a, b) in items:
            if t < a:
                continue
            e = prog(t, a, a + 0.28)
            off = int((1 - e) * 26)
            bb = (bx[0], bx[1] + off, bx[2], bx[3] + off)
            if user:
                rrect(d, bb, 26, fill=ctx.acc + (255,))
                fg, ac = (255, 255, 255), (255, 240, 150)
            else:
                rrect(d, bb, 26, fill=(26, 33, 58, 255), outline=(52, 62, 98, 255), width=2)
                d.ellipse((bb[0] - 58, bb[1] + 4, bb[0] - 10, bb[1] + 52), fill=MINT)
                d.text((bb[0] - 34, bb[1] + 28), "AI", font=font(HEADB, 20), fill=(8, 20, 20), anchor="mm")
                fg, ac = INK, MINT
            think = (not user) and t < a + 0.7
            if think:
                for k in range(3):
                    r = 9 + 4 * math.sin(t * 9 - k * 0.9)
                    d.ellipse((bb[0] + 34 + k * 34 - r, bb[1] + bh / 2 - r, bb[0] + 34 + k * 34 + r, bb[1] + bh / 2 + r), fill=MUTED)
                continue
            ta = a + (0.7 if not user else 0.1)
            tot = sum(len(tk[0]) for ln in ls for tk in ln)
            cnt = int(tot * prog(t, ta, b))
            used = 0
            for li, ln in enumerate(ls):
                x = bb[0] + 28
                for wd, hl in ln:
                    if used >= cnt:
                        break
                    shown = wd[:max(cnt - used, 0)]
                    d.text((x, bb[1] + 22 + li * size * 1.38), shown, font=f, fill=ac if hl else fg)
                    x += f.getlength(wd) + f.getlength(" ")
                    used += len(wd)
    return base, frame, (0.3, 0.0)


def scene_flow(sc, ctx, bg):
    base = bg.convert("RGBA")
    heading(base, sc.get("title", ""), ctx.acc) if sc.get("title") else None
    nodes = [({"label": n} if isinstance(n, str) else n) for n in sc["nodes"]][:6]
    n = len(nodes)
    rows = 1 if n <= 4 else 2
    per = math.ceil(n / rows)
    gw = W - 2 * MX
    nw = min(380, (gw - (per - 1) * 90) / per)
    nh = 330
    pos = []
    for i in range(n):
        r, c = divmod(i, per)
        cols = per if r < rows - 1 or n % per == 0 else n % per
        x0 = MX + (gw - (cols * nw + (cols - 1) * 90)) / 2 + c * (nw + 90)
        y0 = ((TOP + BOT) / 2 - nh / 2 + 50 if rows == 1 else TOP + 150 + r * (nh + 80))
        if rows == 1 and not sc.get("title"):
            y0 = 300
        pos.append((x0, y0, x0 + nw, y0 + nh))
    for b in pos:
        shadow_box(base, b, 24, blur=24, alpha=130, off=10)
    step = (ctx.d - 0.6) / n
    for i in range(n):
        ctx.events.append((0.35 + i * step, "pop"))

    def frame(t, im):
        d = ImageDraw.Draw(im)
        for i in range(n - 1):                       # connectors
            a, b = pos[i], pos[i + 1]
            if a[1] == b[1]:
                p0, p1 = (a[2], (a[1] + a[3]) / 2), (b[0], (b[1] + b[3]) / 2)
            else:
                p0, p1 = ((a[0] + a[2]) / 2, a[3]), ((b[0] + b[2]) / 2, b[1])
            e = prog(t, 0.35 + (i + 1) * step - 0.25, 0.35 + (i + 1) * step + 0.1)
            if e > 0:
                q = (p0[0] + (p1[0] - p0[0]) * e, p0[1] + (p1[1] - p0[1]) * e)
                d.line((p0, q), fill=ctx.acc + (255,), width=5)
                if e >= 1:
                    ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
                    d.polygon([(p1[0], p1[1]), (p1[0] - 22 * math.cos(ang - 0.45), p1[1] - 22 * math.sin(ang - 0.45)), (p1[0] - 22 * math.cos(ang + 0.45), p1[1] - 22 * math.sin(ang + 0.45))], fill=ctx.acc + (255,))
                    ph = (t * 0.9 + i * 0.3) % 1
                    px, py = p0[0] + (p1[0] - p0[0]) * ph, p0[1] + (p1[1] - p0[1]) * ph
                    d.ellipse((px - 8, py - 8, px + 8, py + 8), fill=MINT)
        for i, nd in enumerate(nodes):
            st = 0.35 + i * step
            if t < st:
                continue
            e = out_back((t - st) / 0.38)
            b = pos[i]
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            hw, hh = (b[2] - b[0]) / 2 * e, (b[3] - b[1]) / 2 * e
            active = st <= t < st + step
            rrect(d, (cx - hw, cy - hh, cx + hw, cy + hh), 24, fill=(20, 26, 48, 255),
                  outline=(MINT if active else ctx.acc) + (255,), width=4 if active else 3)
            if e > 0.9:
                d.ellipse((b[0] + 22, b[1] + 22, b[0] + 74, b[1] + 74), fill=ctx.acc)
                d.text((b[0] + 48, b[1] + 48), str(i + 1), font=font(HEADB, 28), fill=(255, 255, 255), anchor="mm")
                f, ls, lh = fit_rich(nd["label"], HEADB, 56, 28, nw - 50, 130, 1.15)
                th = len(ls) * lh
                draw_rich(d, cx, cy - th / 2 + 14, ls, f, lh, INK, MINT, "m")
                if nd.get("sub"):
                    d.text((cx, b[3] - 44), nd["sub"], font=font(MONO, 28), fill=MUTED, anchor="mm")
    return base, frame, (0.3, 0.0)


def scene_compare(sc, ctx, bg):
    base = bg.convert("RGBA")
    if sc.get("title"):
        heading(base, sc["title"], ctx.acc)
    L, R = sc["left"], sc["right"]
    pw, gap = (W - 2 * MX - 80) / 2, 80
    y0, y1 = (TOP + 165 if sc.get("title") else TOP + 80), BOT
    panels = []
    for i, (p, col) in enumerate(((L, CORAL), (R, MINT))):
        b = (MX + i * (pw + gap), y0, MX + i * (pw + gap) + pw, y1)
        shadow_box(base, b, 28, 26, 130, 12)
        d = ImageDraw.Draw(base)
        rrect(d, b, 28, fill=(16, 21, 40, 255), outline=col + (255,), width=3)
        d.rounded_rectangle((b[0] + 30, b[1] + 28, b[0] + 30 + font(MONOB, 28).getlength(p["label"]) + 36, b[1] + 76), 24, fill=mix((16, 21, 40), col, 0.22), outline=col, width=2)
        d.text((b[0] + 48, b[1] + 52), p["label"], font=font(MONOB, 28), fill=col, anchor="lm")
        panels.append((b, col, p))
    ctx.events += [(0.4, "pop"), (0.7, "pop"), (min(ctx.d - 0.6, 1.6), "impact")]
    mx = max(float(L["value"]), float(R["value"]), 1)

    def frame(t, im):
        d = ImageDraw.Draw(im)
        for i, (b, col, p) in enumerate(panels):
            e = prog(t, 0.4 + i * 0.3, 1.6 + i * 0.3)
            val = float(p["value"]) * e
            txt = f"{val:.0f}" if float(p["value"]) >= 10 or float(p["value"]) == int(float(p["value"])) else f"{val:.1f}"
            d.text(((b[0] + b[2]) / 2, b[1] + 175), txt, font=font("Anton-Regular.ttf", 170), fill=col, anchor="mm")
            d.text(((b[0] + b[2]) / 2, b[1] + 280), p.get("unit", "").upper(), font=font(HEAD, 44), fill=INK, anchor="mm")
            bx0, bx1, by = b[0] + 50, b[2] - 50, b[1] + 335
            rrect(d, (bx0, by, bx1, by + 22), 11, fill=(34, 42, 72, 255))
            wv = (bx1 - bx0) * float(p["value"]) / mx * e
            if wv > 22:
                rrect(d, (bx0, by, bx0 + wv, by + 22), 11, fill=col + (255,))
            items = p.get("items", [])[:3]
            iy = by + 52
            for k, it in enumerate(items):
                if t < 1.5 + k * 0.35:
                    continue
                a = prog(t, 1.5 + k * 0.35, 1.8 + k * 0.35)
                f, ls, lh = fit_rich(it, MONO, 50, 28, b[2] - b[0] - 150, 110, 1.25)
                x = b[0] + 54 + (1 - a) * 30
                if col == MINT:
                    d.line((x, iy + 20, x + 10, iy + 30, x + 28, iy + 8), fill=col, width=6, joint="curve")
                else:
                    d.line((x, iy + 8, x + 24, iy + 30), fill=col, width=6); d.line((x + 24, iy + 8, x, iy + 30), fill=col, width=6)
                draw_rich(d, x + 56, iy, ls, f, lh, INK if a > 0.6 else MUTED, MINT)
                iy += max(len(ls) * lh, 44) + 22
    return base, frame, (0.3, 0.0)


def scene_bullets(sc, ctx, bg):
    base = bg.convert("RGBA")
    y = heading(base, sc["title"], ctx.acc) + 26 if sc.get("title") else TOP + 40
    items = sc["items"][:5]
    n = len(items)
    avail = BOT - y
    ih = min(190, (avail - (n - 1) * 24) / n)
    y = max(y, (TOP + BOT) / 2 - (n * ih + (n - 1) * 24) / 2 + 36)
    iw = W - 2 * MX
    step = min(0.7, (ctx.d - 0.8) / n)
    boxes = []
    for i, it in enumerate(items):
        b = (MX, y + i * (ih + 20), MX + iw, y + i * (ih + 20) + ih)
        shadow_box(base, b, 22, 18, 110, 8)
        rrect(ImageDraw.Draw(base), b, 22, fill=(17, 22, 42, 255), outline=(44, 54, 88, 255), width=2)
        boxes.append(b)
        ctx.events.append((0.35 + i * step, "pop"))
    chk = sc.get("check", False)

    def frame(t, im):
        d = ImageDraw.Draw(im)
        for i, it in enumerate(items):
            st = 0.35 + i * step
            if t < st:
                continue
            e = ease((t - st) / 0.3)
            b = boxes[i]
            sx = (1 - e) * 120
            d.rounded_rectangle((b[0], b[1], b[0] + 12, b[3]), 6, fill=ctx.acc + (255,))
            r = ih * 0.30
            cx, cy = b[0] + 52 + r, (b[1] + b[3]) / 2
            if chk and t > st + 0.5:
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=MINT)
                d.line((cx - r * .45, cy + 2, cx - r * .1, cy + r * .38, cx + r * .5, cy - r * .35), fill=(8, 24, 20), width=7, joint="curve")
            else:
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ctx.acc)
                d.text((cx, cy + 2), str(i + 1), font=font(HEADB, int(r * 1.1)), fill=(255, 255, 255), anchor="mm")
            f, ls, lh = fit_rich(it, HEAD, 58, 28, iw - 2 * r - 150, ih - 24, 1.15)
            ty = (b[1] + b[3]) / 2 - len(ls) * lh / 2
            draw_rich(d, b[0] + 52 + 2 * r + 36 + sx * 0, ty + 2, ls, f, lh, tuple(int(c * e) for c in INK) if e < 1 else INK, ctx.sec)
    return base, frame, (0.3, 0.0)


def scene_bars(sc, ctx, bg):
    base = bg.convert("RGBA")
    y = heading(base, sc["title"], ctx.acc) + 40 if sc.get("title") else TOP + 40
    bars = sc["bars"][:6]
    n = len(bars)
    vals = [float(b["value"]) for b in bars]
    best = (min if sc.get("best") == "low" else max)(vals)
    mx = max(vals) * 1.0
    lw = 420
    x0, x1 = MX + lw, W - MX - 190
    rh = min(130, (BOT - y - (30 if sc.get("note") else 0)) / n)
    y = max(y, (TOP + BOT) / 2 - n * rh / 2 + 20)
    d = ImageDraw.Draw(base)
    for i, b in enumerate(bars):
        yy = y + i * rh
        f, ls, lh = fit_rich(b["label"], HEAD, 44, 22, lw - 30, rh - 10, 1.1)
        draw_rich(d, MX, yy + rh / 2 - len(ls) * lh / 2 - 6, ls, f, lh, INK, ctx.sec)
        rrect(d, (x0, yy + rh / 2 - 20, x1, yy + rh / 2 + 14), 14, fill=(26, 33, 60, 255))
    if sc.get("note"):
        d.text((MX, BOT - 8), sc["note"], font=font(MONO, 24), fill=DIM, anchor="lb")
    for i in range(n):
        ctx.events.append((0.4 + i * 0.25, "pop"))
    unit = sc.get("unit", "")

    def frame(t, im):
        d = ImageDraw.Draw(im)
        for i, b in enumerate(bars):
            e = prog(t, 0.4 + i * 0.25, 1.4 + i * 0.25)
            yy = y + i * rh
            col = MINT if vals[i] == best else ctx.acc
            wv = (x1 - x0) * vals[i] / mx * e
            if wv > 28:
                rrect(d, (x0, yy + rh / 2 - 20, x0 + wv, yy + rh / 2 + 14), 14, fill=col + (255,))
            v = vals[i] * e
            txt = (f"{v:.0f}" if vals[i] == int(vals[i]) else f"{v:.1f}") + (f" {unit}" if unit else "")
            d.text((min(x0 + wv + 18, x1 + 24), yy + rh / 2 - 3), txt, font=font(HEADB, 42), fill=col if vals[i] == best else INK, anchor="lm")
    return base, frame, (0.3, 0.0)


def scene_stat(sc, ctx, bg):
    base = bg.convert("RGBA")
    d = ImageDraw.Draw(base)
    new = str(sc.get("new", sc.get("big", "")))
    old = sc.get("old")
    cap = sc.get("caption", "")
    ctx.events += [(0.35, "impact")] if not old else [(0.3, "pop"), (1.1, "impact")]
    gl = glow(1100, ctx.acc, 0.9)
    nums = lambda s: [int(m) for m in re.findall(r"\d+", s)]
    fnew = None
    for sz in range(340, 120, -10):
        fnew = font("Anton-Regular.ttf", sz)
        if fnew.getlength(new) <= (W - 2 * MX) * (0.55 if old else 0.9):
            break
    if cap:
        cf, cls, clh = fit_rich(cap, HEAD, 54, 32, W - 2 * MX - 100, 160, 1.2)
        draw_rich(d, W / 2, 700 - len(cls) * clh / 2 + 70, cls, cf, clh, INK, ctx.sec, "m")

    def frame(t, im):
        d = ImageDraw.Draw(im)
        cy = 410
        if old:
            fo = font("Anton-Regular.ttf", max(sz - 90, 100))
            ow = fo.getlength(old); nw = fnew.getlength(new); aw = 130
            tot = ow + aw + nw
            x = (W - tot) / 2
            e = prog(t, 0.2, 0.6)
            d.text((x, cy), old, font=fo, fill=tuple(int(c * (0.4 + 0.6 * e)) for c in CORAL), anchor="lm")
            ln = prog(t, 0.8, 1.2)
            if ln > 0:
                d.line((x - 10, cy + 4, x - 10 + (ow + 20) * ln, cy + 4), fill=(255, 255, 255), width=12)
            ar = prog(t, 0.9, 1.3)
            if ar > 0:
                d.line((x + ow + 30, cy, x + ow + 30 + 70 * ar, cy), fill=ctx.acc, width=12)
                if ar > 0.9:
                    d.polygon([(x + ow + 108, cy), (x + ow + 80, cy - 26), (x + ow + 80, cy + 26)], fill=ctx.acc)
            ne = out_back((t - 1.1) / 0.45)
            if t > 1.1:
                f2 = font("Anton-Regular.ttf", max(int(sz * min(ne, 1.05)), 40))
                im.alpha_composite(gl, (int(x + ow + aw + nw / 2 - 550), int(cy - 550)))
                d = ImageDraw.Draw(im)
                d.text((x + ow + aw, cy), new, font=f2, fill=MINT, anchor="lm")
        else:
            ne = out_back((t - 0.2) / 0.5)
            im.alpha_composite(gl, (W // 2 - 550, cy - 550))
            d = ImageDraw.Draw(im)
            d.text((W / 2, cy), new, font=font("Anton-Regular.ttf", max(int(sz * min(ne, 1.04)), 40)), fill=ctx.sec if False else MINT, anchor="mm")
    return base, frame, (0.15, 0.0)


def scene_text(sc, ctx, bg):
    base = bg.convert("RGBA")
    f, ls, lh = fit_rich(sc["text"], HEADB, 120, 52, W - 2 * MX - 80, 520, 1.12)
    words = sum(len(l2) for l2 in ls)
    th = len(ls) * lh
    y = 450 - th / 2
    step = min(0.28, (ctx.d - 0.5) / max(words, 1))
    for k in range(words):
        ctx.events.append((0.2 + k * step, "pop" if k % 2 == 0 else "tick"))
    gl = glow(900, ctx.acc, 0.7)
    base.alpha_composite(gl, (W // 2 - 450, 450 - 450))

    def frame(t, im):
        d = ImageDraw.Draw(im)
        n = 0
        sp = f.getlength(" ")
        for i, ln in enumerate(ls):
            lw = sum(f.getlength(w[0]) for w in ln) + sp * (len(ln) - 1)
            x = (W - lw) / 2
            for wd, hl in ln:
                st = 0.2 + n * step
                e = out_back((t - st) / 0.28)
                if t >= st:
                    off = (1 - min(e, 1)) * 40
                    d.text((x, y + i * lh + off), wd, font=f, fill=MINT if hl else INK)
                x += f.getlength(wd) + sp
                n += 1
    return base, frame, (0.2, 0.0)


SCENES = {"code": scene_code, "terminal": scene_code, "chat": scene_chat, "flow": scene_flow, "compare": scene_compare,
          "bullets": scene_bullets, "bars": scene_bars, "stat": scene_stat, "text": scene_text}


# ----------------------------------------------------------------------------- rendering
def render_scene(sc, prof, idx, chap, d, out: Path, nframes, trans=None):
    ctx = Ctx(prof, idx, chap, d)
    bg = make_bg(ctx.tint, chap + idx // 3, ctx.acc)
    base, frame, _ = SCENES[sc["kind"]](sc, ctx, bg)
    bg4 = bg.convert("RGBA")
    blob = glow(1300, ctx.acc, 0.30)
    blob2 = glow(1000, ctx.sec, 0.20)
    z0, z1 = (1.0, 1.07) if idx % 2 == 0 else (1.07, 1.0)      # camera push-in / pull-out, alternating
    side = 1 if idx % 3 else -1
    cmd = [core.FF, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", FPS, "-i", "-",
           "-vf", f"{prof['grade']}{core._fade(trans)},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-threads", "3", out]
    p = subprocess.Popen([str(c) for c in cmd], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(nframes):
            t = i / FPS
            im = base.copy()
            frame(t, im)
            if t < 0.12:                                           # short fade-up, never near-empty
                im = Image.blend(bg4, im, 0.8 + 0.2 * ease(t / 0.12))
            ph = t * 0.35 + idx
            im.alpha_composite(blob, (int(W * (0.05 + 0.3 * (0.5 + 0.5 * math.sin(ph)))) - 350, int(H * (0.3 + 0.2 * math.cos(ph * 0.8))) - 450))
            im.alpha_composite(blob2, (int(W * (0.7 - 0.2 * (0.5 + 0.5 * math.sin(ph * 0.9)))) - 300, int(H * (0.7 + 0.15 * math.sin(ph * 0.7))) - 500))
            e = ease(t / max(d, 0.1))
            zz = z0 + (z1 - z0) * e
            cw_, ch_ = W / zz, H / zz
            px = (W - cw_) * (0.5 + 0.12 * side * (e - 0.5))
            py = (H - ch_) * 0.5
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


# ----------------------------------------------------------------------------- planning (called from core.build when engine == tech)
def auto_scene(b):
    ws = [w.strip(".,;:!?\"'") for w in b["text"].split() if len(w) > 3]
    key = " ".join(ws[:4]) or b["text"][:30]
    return {"kind": "text", "text": key}


def plan_shots(beats, timing, prof, work: Path, broll=()):
    shots, events, t = [], [], 0.0
    (work / "shots").mkdir(exist_ok=True)
    chap, since_flash = 0, 99
    total_f = 0
    since_whoosh = 99
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
                    trans = "flash"; since_flash = 0
                elif b.get("chapter", bi % 4 == 0):
                    trans = "dip"
            since_flash += 1
            sp = work / "shots" / f"t{bi:03d}_{si}.mp4"
            evs = render_scene(sc, prof, bi * 3 + si, chap, nf / FPS, sp, nf, trans)
            events += [(st + e, k) for e, k in evs]
            if trans:
                events.append((max(st - 0.1, 0), "whoosh"))
            shots.append((sp, nf / FPS))
            st += nf / FPS
        t = st
        # keep running time aligned to the voice timeline
        t = max(t, tm["start"] + D) if abs(t - (tm["start"] + D)) < 0.1 else t
    return shots, [], events


# ----------------------------------------------------------------------------- extra SFX (merged into core.synth_sfx bank)
def sfx_bank(sr):
    import numpy as np
    rng = np.random.default_rng(11)
    n = int(0.03 * sr)
    tk = rng.standard_normal(n).astype(np.float32) * np.exp(-np.arange(n) / sr * 220)
    tk = tk - np.convolve(tk, np.ones(40) / 40, "same")            # high-pass: crisp key click
    tick = 0.22 * tk / (np.abs(tk).max() + 1e-6)
    m = int(0.14 * sr)
    tt = np.arange(m) / sr
    pop = (np.sin(2 * np.pi * np.cumsum(520 + 700 * np.exp(-tt * 30)) / sr) * np.exp(-tt * 22)).astype(np.float32) * 0.32
    return {"tick": tick, "pop": pop}
