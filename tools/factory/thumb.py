"""Profile-driven thumbnail generator (laptop only, no Claude).
make_thumb(channel, text, query, out=None) -> Path
 - 1280x720, one big subject photo (free Wikimedia/Wikipedia photo of the topic), 2-4 HUGE words (Anton), channel colour from
   rocky/channels/new_channels.json (fallback: profile accent), dark vignette, outline for phone legibility.
 - Never repeats: every thumbnail of a channel is stored (perceptual hash + photo id + layout) in factory/thumbs/<channel>/index.json;
   a new one is re-rolled (other layout / photo / mirror) until it is visibly different from all earlier ones.
CLI: python -m tools.factory.thumb <channel> "TEXT WORDS" "photo query" """
import hashlib
import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from . import assets

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "assets" / "fonts"
TW, TH = 1280, 720
LAYOUTS = ["left", "right", "bottom", "top"]


def channel_colour(channel):
    try:
        for c in json.loads((ROOT / "rocky" / "channels" / "new_channels.json").read_text(encoding="utf-8")):
            if c.get("handle") == channel:
                return c["colour"]
    except Exception:  # noqa: BLE001
        pass
    try:
        return json.loads((ROOT / "factory" / "profiles" / f"{channel}.json").read_text(encoding="utf-8")).get("accent", "#e8b04a")
    except Exception:  # noqa: BLE001
        return "#e8b04a"


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def phash(im):
    """64-bit difference hash of the whole thumbnail (layout + colours + subject placement all move it)."""
    g = im.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
    return bits


def _ham(a, b):
    return bin(a ^ b).count("1")


def _cover(im, w, h, focus=(0.5, 0.4)):
    s = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    x = int((im.width - w) * focus[0]); y = int((im.height - h) * focus[1])
    return im.crop((x, y, x + w, y + h))


def _fit_text(d, words, font_path, box_w, box_h, max_size=330):
    """Largest Anton size where the stacked words fit the box."""
    size = max_size
    while size > 60:
        f = ImageFont.truetype(str(font_path), size)
        lines_h = sum(d.textbbox((0, 0), w, font=f)[3] + size * 0.04 for w in words)
        if max(d.textlength(w, font=f) for w in words) <= box_w and lines_h <= box_h:
            return f, size
        size -= 8
    return ImageFont.truetype(str(font_path), 60), 60


def render(channel, text, photo_path, layout, mirror, colour, zoom=1.0):
    acc = _rgb(colour)
    base = Image.open(photo_path).convert("RGB")
    if mirror:
        base = ImageOps.mirror(base)
    # subject photo: fills the frame, punchy, subject biased to the side opposite the text
    fx = {"left": 0.8, "right": 0.2, "bottom": 0.5, "top": 0.5}[layout]
    fy = {"bottom": 0.15, "top": 0.8}.get(layout, 0.35)
    if zoom > 1:
        base = base.crop((0, 0, int(base.width / zoom), int(base.height / zoom)))
    im = _cover(base, TW, TH, (fx, fy))
    im = ImageEnhance.Contrast(im).enhance(1.22)
    im = ImageEnhance.Color(im).enhance(1.15)
    im = ImageEnhance.Brightness(im).enhance(0.92)
    # dark gradient on the text side + colour tint + vignette
    shade = Image.new("L", (TW, TH), 0)
    sd = ImageDraw.Draw(shade)
    for i in range(TW if layout in ("left", "right") else TH):
        if layout == "left":
            a = max(0, 235 - int(i * 235 / (TW * 0.78)))
            sd.line((i, 0, i, TH), fill=a)
        elif layout == "right":
            a = max(0, 235 - int((TW - i) * 235 / (TW * 0.78)))
            sd.line((i, 0, i, TH), fill=a)
        elif layout == "bottom":
            a = max(0, 235 - int((TH - i) * 235 / (TH * 0.72)))
            sd.line((0, i, TW, i), fill=a)
        else:
            a = max(0, 235 - int(i * 235 / (TH * 0.72)))
            sd.line((0, i, TW, i), fill=a)
    tint = Image.new("RGB", (TW, TH), tuple(int(c * 0.18) for c in acc))
    im = Image.composite(tint, im, shade)
    im = ImageChops_multiply_vignette(im)
    d = ImageDraw.Draw(im)
    words = [w for w in text.upper().split()][:4]
    if layout in ("left", "right"):
        box = (60 if layout == "left" else TW * 0.42, 40, TW * 0.58 if layout == "left" else TW - 60, TH - 40)
    elif layout == "bottom":
        box = (60, TH * 0.42, TW - 60, TH - 40)
    else:
        box = (60, 40, TW - 60, TH * 0.58)
    bw, bh = box[2] - box[0], box[3] - box[1]
    if layout in ("bottom", "top") and len(words) > 1:      # wide boxes: two words per line
        lines = [" ".join(words[i:i + 2]) for i in range(0, len(words), 2)]
    else:
        lines = words
    f, size = _fit_text(d, lines, FONTS / "Anton-Regular.ttf", bw, bh)
    lh = [d.textbbox((0, 0), w, font=f)[3] + size * 0.04 for w in lines]
    y = box[1] + (bh - sum(lh)) / 2
    for i, (w, h) in enumerate(zip(lines, lh)):
        tw = d.textlength(w, font=f)
        x = box[0] if layout == "left" else (box[2] - tw if layout == "right" else box[0] + (bw - tw) / 2)
        last = i == len(lines) - 1
        col = colour if last else "#ffffff"
        d.text((x + 6, y + 8), w, font=f, fill=(0, 0, 0))                                   # hard shadow
        d.text((x, y), w, font=f, fill=col, stroke_width=max(6, size // 22), stroke_fill=(0, 0, 0))
        d.text((x, y), w, font=f, fill=col)
        y += h
    # accent bar so every thumbnail of the channel feels like one family
    d.rectangle((0, TH - 14, TW, TH), fill=colour)
    return im


def ImageChops_multiply_vignette(im):
    v = Image.new("L", (TW, TH), 0)
    ImageDraw.Draw(v).ellipse((-TW * 0.25, -TH * 0.3, TW * 1.25, TH * 1.3), fill=255)
    v = v.filter(ImageFilter.GaussianBlur(120))
    black = Image.new("RGB", (TW, TH), (0, 0, 0))
    return Image.composite(im, black, v.point(lambda x: 90 + int(x * 0.65)))


def make_thumb(channel, text, query, out=None, photo=None):
    colour = channel_colour(channel)
    tdir = ROOT / "factory" / "thumbs" / channel
    tdir.mkdir(parents=True, exist_ok=True)
    idxf = tdir / "index.json"
    hist = json.loads(idxf.read_text(encoding="utf-8")) if idxf.exists() else []
    seen_h = [h["hash"] for h in hist]
    seen_p = {h["photo"] for h in hist}
    seen_l = [h["layout"] for h in hist][-3:]
    pics = ([{"id": "given", "path": str(photo)}] if photo else assets.search_images(query, 8)) or []
    if not pics:
        raise RuntimeError(f"no free photo found for '{query}'")
    cands = [p for p in pics if p["id"] not in seen_p] or pics
    layouts = [l for l in LAYOUTS if l not in seen_l] + [l for l in LAYOUTS if l in seen_l]
    best = None
    for p in cands:
        for lay in layouts:
            for mirror in (False, True):
                for zoom in (1.0, 1.35):
                    im = render(channel, text, p["path"], lay, mirror, colour, zoom)
                    h = phash(im)
                    dist = min([_ham(h, x) for x in seen_h] or [64])
                    if best is None or dist > best[0]:
                        best = (dist, im, h, p, lay)
                    if dist >= 14:
                        break
                if best and best[0] >= 14:
                    break
            if best and best[0] >= 14:
                break
        if best and best[0] >= 14:
            break
    dist, im, h, p, lay = best
    out = Path(out) if out else tdir / f"{hashlib.md5((text + query).encode()).hexdigest()[:8]}.jpg"
    im.save(out, quality=92)
    hist.append(dict(hash=h, photo=p["id"], layout=lay, text=text, file=str(out), credit=p.get("credit", "")))
    idxf.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    print(f"thumb {out} layout={lay} min-hamming-to-previous={dist}")
    return out


if __name__ == "__main__":
    make_thumb(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
