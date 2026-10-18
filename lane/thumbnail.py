"""Episode thumbnails in the style of the most-clicked Prince Hamdan videos.

What the top Fazza thumbnails (1-23M views) have in common, and what this module builds:
  * his face BIG (about a third of the height), sharp, warm, cut out from a blurred, darkened background with a soft
    rim light, looking into the frame;
  * 2-4 huge condensed words (Anton) in white + yellow with a thick black outline: a curiosity gap, not a summary;
  * a small red badge ("RARE FOOTAGE"), and in some layouts a framed second photo of him from another moment.
Everything is real official photography; nothing is generated or faked.
"""
from __future__ import annotations

import logging
import random
import re
from pathlib import Path

import cv2
import numpy as np

from .config import ASSETS_DIR, FONTS_DIR

log = logging.getLogger("lane.thumbnail")

W, H = 1280, 720
FACE_H = 0.36 * H                  # target face height on the thumbnail
SEG_MODEL = ASSETS_DIR / "seg" / "u2netp.onnx"
YELLOW, WHITE, RED = (255, 214, 0), (255, 255, 255), (226, 30, 36)      # RGB (Pillow)


# ---- choosing the photo -------------------------------------------------------------------------------------
def _face_quality(img: np.ndarray, row: np.ndarray) -> float:
    """Big, sharp, frontal face of him = a thumbnail people click."""
    x, y, fw, fh = row[:4]
    h, w = img.shape[:2]
    x0, y0 = int(max(0, x)), int(max(0, y))
    face = img[y0:int(min(h, y + fh)), x0:int(min(w, x + fw))]
    if face.size == 0:
        return 0.0
    g = cv2.cvtColor(cv2.resize(face, (128, 128)), cv2.COLOR_BGR2GRAY)
    sharp = min(1.0, cv2.Laplacian(g, cv2.CV_64F).var() / 250.0)
    eyes = abs(row[4] - row[6]) / max(fw, 1)                     # ~0.40 frontal, smaller when turned away
    frontal = min(1.0, eyes / 0.38)
    size = min(1.0, fh / 330.0)                                   # enough pixels to stay crisp at FACE_H
    room = 1.0 if y > 0.6 * fh else 0.6                           # head not cut off at the top
    below = (h - (y + fh)) / max(fh, 1)                           # body below the face fills the frame; a photo
    room *= 1.0 if below >= 1.6 else 0.85                         # (the missing part fades to dark)
    return (1.2 * size + 0.8 * sharp + 0.7 * frontal) * room


SMILE_MODEL = ASSETS_DIR / "seg" / "emotion-ferplus-8.onnx"     # FER+ facial expression model (ONNX model zoo, MIT)
_smile_net = None


def smile(img: np.ndarray, row: np.ndarray) -> float:
    """0..1: how much he smiles in this photo. A warm smile gets far more clicks than a serious face."""
    global _smile_net
    if not SMILE_MODEL.exists():
        return 0.0
    if _smile_net is None:
        _smile_net = cv2.dnn.readNetFromONNX(str(SMILE_MODEL))
    x, y, fw, fh = [int(v) for v in row[:4]]
    m = int(0.1 * fh)
    f = img[max(0, y - m):y + fh + m, max(0, x - m):x + fw + m]
    if f.size == 0:
        return 0.0
    g = cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (64, 64)).astype(np.float32)
    _smile_net.setInput(g.reshape(1, 1, 64, 64))
    o = _smile_net.forward()[0]
    e = np.exp(o - o.max())
    return float(e[1] / e.sum())              # FER+ classes: neutral, happiness, surprise, ...


def pick_photos(paths: list[Path], face) -> list[tuple[Path, np.ndarray, float]]:
    """[(path, his face row, quality)] best first, for photos where he is found. Smiling photos come first."""
    out = []
    for p in paths:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            continue
        row = face.locate(img)
        if row is not None:
            q = _face_quality(img, row)
            sm = smile(img, row) * min(1.0, row[3] / 220.0) if q >= 1.0 else 0.0   # (big, clear faces only)
            out.append((p, row, q + 3.0 * sm + (1.0 if sm >= 0.8 else 0.0)))      # a real broad smile wins
    out.sort(key=lambda r: -r[2])
    return out


# ---- cut-out ------------------------------------------------------------------------------------------------
_net = None


def _saliency(img: np.ndarray) -> np.ndarray | None:
    global _net
    try:
        if _net is None:
            _net = cv2.dnn.readNetFromONNX(str(SEG_MODEL))
        blob = cv2.dnn.blobFromImage(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), 1 / 255.0, (320, 320))
        mean = np.array([0.485, 0.456, 0.406], np.float32).reshape(1, 3, 1, 1)
        std = np.array([0.229, 0.224, 0.225], np.float32).reshape(1, 3, 1, 1)
        _net.setInput(((blob - mean) / std).astype(np.float32))
        m = _net.forward()[0, 0]
    except Exception as e:  # noqa: BLE001 — no model: the spotlight fallback still looks good
        log.warning("cut-out model unavailable: %s", e)
        return None
    m = (m - m.min()) / (m.max() - m.min() + 1e-9)
    return cv2.resize(m, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)


def _person_prior(shape: tuple[int, int], row: np.ndarray) -> np.ndarray:
    """Soft silhouette where his head, headdress and shoulders can be (keeps other people out of the cut-out)."""
    h, w = shape
    x, y, fw, fh = row[:4]
    cx = x + fw / 2
    m = np.zeros((h, w), np.float32)
    cv2.ellipse(m, (int(cx), int(y + fh * 0.42)), (int(fw * 0.95), int(fh * 0.95)), 0, 0, 360, 1.0, -1)
    top = y + fh * 0.75
    pts = np.array([[cx - fw * 1.05, top], [cx + fw * 1.05, top], [cx + fw * 2.6, h], [cx - fw * 2.6, h]], np.int32)
    cv2.fillPoly(m, [pts], 1.0)
    return cv2.GaussianBlur(m, (0, 0), max(2.0, fw * 0.18))


def subject_alpha(img: np.ndarray, row: np.ndarray) -> np.ndarray:
    prior = _person_prior(img.shape[:2], row)
    sal = _saliency(img)
    x, y, fw, fh = (int(v) for v in row[:4])
    if sal is not None:
        cut = sal * prior
        face = cut[max(0, y):y + fh, max(0, x):x + fw]
        body = cut[min(img.shape[0] - 1, y + fh):, max(0, x - fw // 2):x + fw + fw // 2]
        if face.size and face.mean() > 0.55 and (not body.size or body.mean() > 0.35):
            a = np.clip((cut - 0.25) / 0.5, 0, 1)
            return cv2.GaussianBlur(a, (0, 0), 1.6)
    return prior * 0.92    # the model missed him: a soft spotlight instead of a hard cut


# ---- picture ------------------------------------------------------------------------------------------------
def _place(img: np.ndarray, row: np.ndarray, face_cx: float, face_top: float) -> tuple[np.ndarray, np.ndarray]:
    """Scale/flip/crop the photo so his face is FACE_H tall at (face_cx, face_top) looking into the frame."""
    x, y, fw, fh = row[:4]
    eye_mid = (row[4] + row[6]) / 2
    looks_right = (row[8] - eye_mid) / max(fw, 1) > 0.05
    if looks_right == (face_cx > 0.5):           # facing out of the frame: mirror the photo
        img = cv2.flip(img, 1)
        row = row.copy()
        w0 = img.shape[1]
        row[0] = w0 - x - fw
        for i in (4, 6, 8, 10, 12):
            row[i] = w0 - row[i]
        x = row[0]
    s = min(2.0, FACE_H / fh)
    img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    row = row.copy()
    row[:14] *= s
    ox = int(face_cx * W - (row[0] + row[2] / 2))
    oy = int(face_top * H - row[1])
    pad = int(max(0, ox, oy, W - ox - img.shape[1], H - oy - img.shape[0])) + 2
    big = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REFLECT_101)
    inside = cv2.copyMakeBorder(np.ones(img.shape[:2], np.float32), pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    sx, sy = pad - ox, pad - oy
    crop = big[sy:sy + H, sx:sx + W]
    global _VALID
    _VALID = cv2.GaussianBlur(inside[sy:sy + H, sx:sx + W], (0, 0), 25)   # where the real photo is (soft edge)
    row[0] += ox
    row[1] += oy
    row[4:14:2] += ox
    row[5:14:2] += oy
    return crop, row


def _grade(img: np.ndarray, sat: float, contrast: float, warm: float) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * sat, 0, 255)
    img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
    img = (img - 128) * contrast + 128
    img[..., 2] += warm
    img[..., 0] -= warm * 0.8
    return np.clip(img, 0, 255).astype(np.uint8)


_VALID = None


def _compose(img: np.ndarray, row: np.ndarray, rng: random.Random, face_cx: float) -> np.ndarray:
    valid = _VALID[..., None] if _VALID is not None and _VALID.shape == img.shape[:2] else 1.0
    alpha = subject_alpha(img, row)[..., None] * valid      # beyond the photo's edge he simply fades out
    # background: same photo, out of focus, darker, darkest behind the text
    bg = cv2.GaussianBlur(img, (0, 0), 10)
    bg = _grade(bg, 1.15, 1.05, rng.uniform(6, 14)).astype(np.float32) * 0.62
    bg = bg * valid + cv2.GaussianBlur(bg, (0, 0), 30) * 0.35 * (1 - valid)   # past the photo's edge: dark fade
    xx = np.linspace(0, 1, W, dtype=np.float32)[None, :, None]
    side = xx if face_cx > 0.5 else 1 - xx
    bg *= np.clip(0.30 + 1.1 * side, 0.30, 1.0)
    yy = np.linspace(-1, 1, H, dtype=np.float32)[:, None, None]
    bg *= 1 - 0.35 * np.clip((xx * 2 - 1) ** 2 + yy ** 2 - 0.4, 0, 1)        # vignette
    # rim light behind him so he separates from the background
    a1 = alpha[..., 0]
    halo = cv2.GaussianBlur(cv2.dilate(a1, np.ones((15, 15), np.uint8)), (0, 0), 14) - a1
    glow = np.clip(halo, 0, 1)[..., None] * np.array([150, 215, 255], np.float32)    # warm gold (BGR)
    bg = np.clip(bg + glow * 0.55, 0, 255)
    # subject: crisp, punchy, warm
    sharp = cv2.addWeighted(img, 1.7, cv2.GaussianBlur(img, (0, 0), 2.2), -0.7, 0)
    lab = cv2.cvtColor(sharp, cv2.COLOR_BGR2LAB)
    lab[..., 0] = cv2.createCLAHE(2.0, (8, 8)).apply(lab[..., 0])
    subj = _grade(cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), 1.18, 1.08, 8).astype(np.float32)
    return np.clip(bg * (1 - alpha) + subj * alpha, 0, 255).astype(np.uint8)


def _inset(canvas: np.ndarray, photo: Path, face, box: tuple[int, int, int, int], angle: float) -> np.ndarray:
    """A second real photo of him in a white frame (a classic high-CTR element)."""
    img = cv2.imread(str(photo), cv2.IMREAD_COLOR)
    if img is None:
        return canvas
    x0, y0, bw, bh = box
    row = face.locate(img)
    h, w = img.shape[:2]
    s = max(bw / w, bh / h)
    if row is not None:
        s = max(s, min(2.0, bh * 0.45 / row[3]))
    img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    cx = (row[0] + row[2] / 2) * s if row is not None else img.shape[1] / 2
    cy = (row[1] + row[3] * 0.9) * s if row is not None else img.shape[0] / 2
    sx = int(np.clip(cx - bw / 2, 0, img.shape[1] - bw))
    sy = int(np.clip(cy - bh / 2, 0, img.shape[0] - bh))
    tile = _grade(img[sy:sy + bh, sx:sx + bw], 1.12, 1.05, 6)
    border = 8
    tile = cv2.copyMakeBorder(tile, border, border, border, border, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    th, tw = tile.shape[:2]
    m = cv2.getRotationMatrix2D((tw / 2, th / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(th * sin + tw * cos), int(th * cos + tw * sin)
    m[0, 2] += nw / 2 - tw / 2
    m[1, 2] += nh / 2 - th / 2
    rot = cv2.warpAffine(tile, m, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    a = cv2.warpAffine(np.full((th, tw), 1.0, np.float32), m, (nw, nh), flags=cv2.INTER_LINEAR)
    out = canvas.astype(np.float32)
    shadow = cv2.GaussianBlur(a, (0, 0), 9)[..., None] * 0.6
    px, py = x0, y0
    ph, pw = min(nh, H - py - 8), min(nw, W - px - 8)
    region = out[py + 8:py + 8 + ph, px + 8:px + 8 + pw]
    region *= 1 - shadow[:region.shape[0], :region.shape[1]]
    reg = out[py:py + ph, px:px + pw]
    aa = a[:ph, :pw, None]
    reg[:] = reg * (1 - aa) + rot[:ph, :pw].astype(np.float32) * aa
    return np.clip(out, 0, 255).astype(np.uint8)


# ---- words --------------------------------------------------------------------------------------------------
def _font(size: int, name: str = "Anton-Regular.ttf"):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONTS_DIR / name), size)


def _lines(words: list[str]) -> list[str]:
    if len(words) <= 2:
        return words if sum(map(len, words)) > 9 else [" ".join(words)]
    if len(words) == 3:
        return [words[0], " ".join(words[1:])] if len(words[0]) > len(words[2]) else [" ".join(words[:2]), words[2]]
    return [" ".join(words[:2]), " ".join(words[2:])]


def _draw_words(canvas: np.ndarray, text: str, tag: str, area: tuple[int, int, int, int], seed: int) -> np.ndarray:
    from PIL import Image, ImageDraw, ImageFilter
    words = re.sub(r"\s+", " ", re.sub(r"[\"“”‘’]|(?<![A-Z])'|'(?![A-Z])", "", text.upper())).strip().split()[:4]
    lines = _lines(words) or ["FAZZA"]
    x0, y0, aw, ah = area
    # biggest size where every line fits the text area
    size = 230
    while size > 60:
        f = _font(size)
        widths = [f.getbbox(l)[2] for l in lines]
        line_h = int(size * 1.08)
        if max(widths) <= aw and line_h * len(lines) <= ah:
            break
        size -= 6
    f = _font(size)
    line_h = int(size * 1.08)
    img = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    hi = len(lines) - 1 if seed % 2 == 0 else max(range(len(lines)), key=lambda i: len(lines[i]))
    top = y0 + (ah - line_h * len(lines)) // 2
    stroke = max(5, size // 18)
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ds = ImageDraw.Draw(shadow)
    for i, line in enumerate(lines):
        y = top + i * line_h
        ds.text((x0 + 8, y + 10), line, font=f, fill=(0, 0, 0, 200), stroke_width=stroke, stroke_fill=(0, 0, 0, 200))
        d.text((x0, y), line, font=f, fill=YELLOW if i == hi else WHITE, stroke_width=stroke, stroke_fill=(0, 0, 0))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(8)))
    img = Image.alpha_composite(img, layer)
    if tag:
        bf = _font(42, "Montserrat-Black.ttf")
        tw = bf.getbbox(tag)[2]
        bx, by = x0, max(24, top - 86)
        badge = Image.new("RGBA", img.size, (0, 0, 0, 0))
        db = ImageDraw.Draw(badge)
        db.rounded_rectangle((bx, by, bx + tw + 40, by + 64), radius=9, fill=RED + (255,))
        db.text((bx + 20, by + 8), tag, font=bf, fill=WHITE)
        img = Image.alpha_composite(img, badge)
    return cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)


# ---- entry point --------------------------------------------------------------------------------------------
class AnyFace:
    """Largest face in a photo (for a second person on the thumbnail: his father, mother, a family member)."""

    def __init__(self, face) -> None:
        self.face = face

    def locate(self, img: np.ndarray) -> np.ndarray | None:
        rows = [r for r, _ in self.face.faces(img)]
        return max(rows, key=lambda r: r[2] * r[3]) if rows else None


def make_thumbnail(photos: list[Path], text: str, tag: str, out: Path, seed: int, face=None,
                   inset: list[Path] | None = None) -> Path:
    """photos: candidate photos of him (best is picked automatically). inset: photos of a second person the story is
    about (the clearest face becomes a framed picture beside him); without it a second photo of him may be used."""
    if face is None:
        from .faceid import FaceID
        face = FaceID()
    ranked = pick_photos(photos, face)
    if not ranked:
        raise ValueError("no photo with Prince Hamdan's face for the thumbnail")
    rng = random.Random(seed)
    hero, row, _ = ranked[0]
    img = cv2.imread(str(hero), cv2.IMREAD_COLOR)
    face_cx = 0.70
    crop, row = _place(img, row, face_cx, 0.15)
    canvas = _compose(crop, row, rng, face_cx)
    text_left = 52
    text_w = int(row[0] - row[2] * 0.25) - text_left - 10
    text_w = max(460, min(text_w, 660))
    other, other_face = None, face
    if inset:
        any_face = AnyFace(face)
        ranked_other = pick_photos(inset, any_face)
        if ranked_other:
            other, other_face = ranked_other[0][0], any_face
    if other is not None:
        box = (40, 36, 440, 290) if inset else (36, 30, 330, 220)
        canvas = _inset(canvas, other, other_face, box, rng.choice([-4.0, 3.5]))
        canvas = _draw_words(canvas, text, tag if inset else "", (text_left, 390 if inset else 300, text_w,
                                                                  310 if inset else 390), seed)
        layout = "inset"
    else:
        canvas = _draw_words(canvas, text, tag, (text_left, 120, text_w, 520), seed)
        layout = "words"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
    log.info("thumbnail %s (%s, from %s)", out.name, layout, Path(hero).name)
    return out


def make_scene_thumbnail(photo: Path, text: str, tag: str, out: Path, seed: int,
                         crop: list[float] | None = None) -> Path:
    """A whole real moment as the picture (him with the person the story is about, e.g. at a hospital bedside):
    nothing is cut out, the words sit in the darkened top-left corner. crop: [x0, y0, x1, y1] as fractions."""
    img = cv2.imread(str(photo), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"thumbnail scene unreadable: {photo}")
    h, w = img.shape[:2]
    if crop:
        x0, y0, x1, y1 = crop
        img = img[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]
        h, w = img.shape[:2]
    s = max(W / w, H / h)                                   # cover 1280x720, centred
    img = cv2.resize(img, (max(W, round(w * s)), max(H, round(h * s))), interpolation=cv2.INTER_LANCZOS4)
    oy, ox = (img.shape[0] - H) // 2, (img.shape[1] - W) // 2
    img = img[oy:oy + H, ox:ox + W]
    sharp = cv2.addWeighted(img, 1.6, cv2.GaussianBlur(img, (0, 0), 2.0), -0.6, 0)
    lab = cv2.cvtColor(sharp, cv2.COLOR_BGR2LAB)
    lab[..., 0] = cv2.createCLAHE(1.8, (8, 8)).apply(lab[..., 0])
    canvas = _grade(cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), 1.15, 1.08, 8).astype(np.float32)
    xx = np.linspace(0, 1, W, dtype=np.float32)[None, :]
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    shade = np.clip(1.25 - 1.6 * xx, 0, 1) * np.clip(1.15 - 1.5 * yy, 0, 1)     # darkest behind the words
    canvas *= (1 - 0.72 * shade)[..., None]
    vig = 1 - 0.30 * np.clip(((xx - 0.5) * 2) ** 2 + ((yy - 0.5) * 2) ** 2 - 0.5, 0, 1)
    canvas = np.clip(canvas * vig[..., None], 0, 255).astype(np.uint8)
    canvas = _draw_words(canvas, text, tag, (44, 120, 560, 300), seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
    log.info("thumbnail %s (scene, from %s)", out.name, Path(photo).name)
    return out
