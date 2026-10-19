"""Animated charts and counters drawn by us (no stock footage needed), as short video clips for the story pipeline.

A story section lists them in `show` like any other picture:

    chart:bars|WHO HAS MORE?|Elon Musk=800|Dubai output in 3 years=750|$ BILLION
    chart:line|DUBAI ECONOMY|2023=1|2026=2.4|2033=4|TRILLION AED
    chart:count|DUBAI IN 3 MONTHS|63|$ BILLION

bars: grow from zero with the number counting up; line: draws itself across dotted grid lines with a glowing tip;
count: one big number counting up. Dark navy and gold, same look as the channel's thumbnails.
"""
from __future__ import annotations

import hashlib
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import ASSETS_DIR
from .media import FFMPEG

W, H, FPS = 1920, 1080, 30
FONTS = ASSETS_DIR / "fonts"
GOLD, WHITE, BLUE, DIM = (232, 184, 74), (255, 255, 255), (74, 160, 255), (150, 160, 185)


def _font(size: int, name: str = "Anton-Regular.ttf") -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _bg() -> Image.Image:
    img = Image.new("RGB", (W, H))
    px = ImageDraw.Draw(img)
    for y in range(H):
        k = y / H
        px.line([(0, y), (W, y)], fill=(int(8 + 14 * k), int(12 + 18 * k), int(30 + 28 * k)))
    return img


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if v >= 100 else (f"{v:.1f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v)}")


def _title(d: ImageDraw.ImageDraw, text: str, t: float) -> None:
    f = _font(78)
    w = d.textlength(text, font=f)
    a = _ease(t / 0.5)
    d.text(((W - w) / 2, 70 - (1 - a) * 30), text, font=f, fill=tuple(int(c * a) for c in WHITE))
    d.rectangle([W / 2 - 90, 196, W / 2 + 90, 202], fill=tuple(int(c * a) for c in GOLD))


def _draw_bars(spec: dict, t: float, dur: float) -> Image.Image:
    img = _bg()
    d = ImageDraw.Draw(img)
    _title(d, spec["title"], t)
    items = spec["items"]
    top = max(v for _, v in items) or 1
    n = len(items)
    left, base, height = 260, 920, 520
    slot = (W - 2 * left) / n
    for i, (name, val) in enumerate(items):
        g = _ease((t - 0.5 - 0.35 * i) / 1.6)
        bw = min(300, slot * 0.6)
        x0 = left + slot * i + (slot - bw) / 2
        bh = max(6, height * (val / top) * g)
        col = GOLD if i == 0 else BLUE
        d.rounded_rectangle([x0, base - bh, x0 + bw, base], 18, fill=col)
        num = f"{_fmt(val * g)}"
        f = _font(96)
        d.text((x0 + bw / 2 - d.textlength(num, font=f) / 2, base - bh - 120), num, font=f, fill=WHITE)
        lf = _font(44, "Montserrat-ExtraBold.ttf")
        for k, line in enumerate(_wrap(d, name, lf, slot - 30)):
            d.text((x0 + bw / 2 - d.textlength(line, font=lf) / 2, base + 24 + 52 * k), line, font=lf, fill=DIM)
    if spec.get("unit"):
        uf = _font(46, "Montserrat-ExtraBold.ttf")
        d.text(((W - d.textlength(spec["unit"], font=uf)) / 2, 216), spec["unit"], font=uf, fill=GOLD)
    d.line([(left - 40, base), (W - left + 40, base)], fill=(70, 80, 110), width=3)
    return img


def _wrap(d: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, width: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if d.textlength((cur + " " + w).strip(), font=f) <= width:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
    return (lines + [cur])[:3]


def _draw_line(spec: dict, t: float, dur: float) -> Image.Image:
    img = _bg()
    d = ImageDraw.Draw(img)
    _title(d, spec["title"], t)
    pts = spec["items"]
    lo, hi = 0.0, max(v for _, v in pts) * 1.15
    x0, x1, y0, y1 = 240, W - 240, 900, 300
    for k in range(6):                                   # dotted grid
        y = y0 + (y1 - y0) * k / 5
        for x in range(int(x0), int(x1), 26):
            d.line([(x, y), (x + 10, y)], fill=(60, 72, 104), width=2)
    xy = [(x0 + (x1 - x0) * i / max(1, len(pts) - 1), y0 - (y0 - y1) * ((v - lo) / (hi - lo))) for i, (_, v) in enumerate(pts)]
    prog = _ease((t - 0.4) / 2.6) * (len(pts) - 1)
    drawn = [xy[0]]
    for i in range(1, len(xy)):
        if prog >= i:
            drawn.append(xy[i])
        else:
            fr = max(0.0, prog - (i - 1))
            drawn.append((xy[i - 1][0] + (xy[i][0] - xy[i - 1][0]) * fr, xy[i - 1][1] + (xy[i][1] - xy[i - 1][1]) * fr))
            break
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).line(drawn, fill=(120, 90, 20), width=26, joint="curve")
    img = Image.fromarray(__import__("numpy").maximum(__import__("numpy").asarray(img),
                                                       __import__("numpy").asarray(glow.filter(ImageFilter.GaussianBlur(18)))))
    d = ImageDraw.Draw(img)
    d.line(drawn, fill=GOLD, width=9, joint="curve")
    f = _font(52, "Montserrat-ExtraBold.ttf")
    for i, ((name, val), (px, py)) in enumerate(zip(pts, xy)):
        if prog >= i - 0.02:
            d.ellipse([px - 15, py - 15, px + 15, py + 15], fill=WHITE, outline=GOLD, width=6)
            d.text((px - d.textlength(name, font=f) / 2, y0 + 40), name, font=f, fill=DIM)
            vf = _font(70)
            s = _fmt(val)
            d.text((px - d.textlength(s, font=vf) / 2, py - 110), s, font=vf, fill=WHITE)
    tip = drawn[-1]
    r = 16 + 5 * math.sin(t * 8)
    d.ellipse([tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r], outline=WHITE, width=4)
    if spec.get("unit"):
        uf = _font(46, "Montserrat-ExtraBold.ttf")
        d.text(((W - d.textlength(spec["unit"], font=uf)) / 2, 216), spec["unit"], font=uf, fill=GOLD)
    return img


def _draw_count(spec: dict, t: float, dur: float) -> Image.Image:
    img = _bg()
    d = ImageDraw.Draw(img)
    _title(d, spec["title"], t)
    val = spec["items"][0][1]
    g = _ease((t - 0.3) / 2.2)
    s = _fmt(val * g)
    f = _font(430)
    d.text(((W - d.textlength(s, font=f)) / 2, 250), s, font=f, fill=GOLD)
    if spec.get("unit"):
        uf = _font(86, "Montserrat-ExtraBold.ttf")
        d.text(((W - d.textlength(spec["unit"], font=uf)) / 2, 830), spec["unit"], font=uf, fill=WHITE)
    return img


def parse(token: str) -> dict:
    """'bars|TITLE|A=800|B=250|UNIT' -> {kind, title, items, unit}"""
    kind, *parts = [p.strip() for p in token.split("|")]
    title = parts[0] if parts else ""
    items, unit = [], ""
    for p in parts[1:]:
        if "=" in p:
            n, _, v = p.rpartition("=")
            try:
                items.append((n.strip(), float(v.replace(",", ""))))
                continue
            except ValueError:
                pass
        if p and not items and kind == "count":
            try:
                items.append(("", float(p.replace(",", ""))))
                continue
            except ValueError:
                pass
        unit = p
    return {"kind": kind, "title": title.upper(), "items": items, "unit": unit.upper()}


def render(token: str, outdir: Path, seconds: float = 4.4) -> tuple[Path, float] | None:
    """Draw the chart described by `token` into outdir and return (mp4 path, duration)."""
    spec = parse(token)
    draw = {"bars": _draw_bars, "line": _draw_line, "count": _draw_count}.get(spec["kind"])
    if not draw or not spec["items"]:
        return None
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"chart_{hashlib.sha1(token.encode()).hexdigest()[:10]}.mp4"
    if out.exists():
        return out, seconds
    n = int(seconds * FPS)
    ff = subprocess.Popen([FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
                           "-preset", "veryfast", str(out)], stdin=subprocess.PIPE)
    for i in range(n):
        ff.stdin.write(draw(spec, i / FPS, seconds).tobytes())
    ff.stdin.close()
    ff.wait()
    return (out, seconds) if out.exists() else None
