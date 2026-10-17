"""Sentence-level visuals for story episodes.

A story section can be written as `beats`, each one or two sentences with its own pictures:

    - beats:
        - say: "On the twentieth of May, two thousand and twenty-one, Fazza posted a photo that broke the internet."
          show: ["card:20 MAY 2021|THE POST THAT BROKE THE INTERNET"]
          pop: "TWO TINY FEET"
        - say: "Months later he was riding again at dawn..."
          show: ["video:endurance|horse"]

The section is narrated in one piece; the narrator's word timings say exactly when each beat is spoken, and the
screen shows that beat's pictures for exactly that time. `card:TITLE|SUBTITLE` is a designed full-screen card
(used when something has no footage, e.g. an Instagram post), `pop` is a big line of text that flashes on screen.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import cv2
import numpy as np

from .config import FONTS_DIR

log = logging.getLogger("lane.beats")

W, H = 1920, 1080
GOLD, WHITE = (247, 201, 72), (245, 245, 245)


def _tokens(text: str) -> int:
    return len([w for w in re.findall(r"[A-Za-z0-9']+", text)])


def align(beats: list[dict], words: list[dict], total: float) -> list[tuple[float, float]]:
    """(start, end) in seconds of every beat inside a narrated section, from the narrator's word timings.
    Counts are matched proportionally, so small tokenisation differences (numbers, hyphens) never drift."""
    counts = [_tokens(b["say"]) for b in beats]
    n_script, n_spoken = max(1, sum(counts)), len(words)
    spans, cum = [], 0
    for i, c in enumerate(counts):
        a_idx = min(n_spoken - 1, round(cum * n_spoken / n_script)) if n_spoken else 0
        cum += c
        b_idx = min(n_spoken - 1, max(a_idx, round(cum * n_spoken / n_script) - 1)) if n_spoken else 0
        start = 0.0 if i == 0 or not words else words[a_idx]["start"] - 0.15
        end = total if i == len(beats) - 1 or not words else words[b_idx]["end"] + 0.1
        spans.append((max(0.0, start), end))
    fixed = []          # contiguous: each beat ends where the next begins
    for i, (a, b) in enumerate(spans):
        nxt = spans[i + 1][0] if i + 1 < len(spans) else total
        fixed.append((a if i else 0.0, max(a + 0.8, nxt)))
    return fixed


def make_card(title: str, sub: str, bg: Path | None, out: Path) -> Path:
    """A designed 1920x1080 card: blurred, darkened photo of him behind a big gold title and a white subtitle."""
    from PIL import Image, ImageDraw, ImageFont
    base = np.zeros((H, W, 3), np.uint8)
    if bg is not None:
        img = cv2.imread(str(bg), cv2.IMREAD_COLOR)
        if img is not None:
            h, w = img.shape[:2]
            s = max(W / w, H / h)
            img = cv2.resize(img, (int(w * s) + 1, int(h * s) + 1))
            y0, x0 = (img.shape[0] - H) // 2, (img.shape[1] - W) // 2
            base = cv2.GaussianBlur(img[y0:y0 + H, x0:x0 + W], (0, 0), 18)
    base = (base.astype(np.float32) * 0.32).astype(np.uint8)
    yy = np.linspace(-1, 1, H)[:, None]
    xx = np.linspace(-1, 1, W)[None, :]
    vign = np.clip(1 - 0.35 * (xx ** 2 + yy ** 2), 0.5, 1)[..., None]
    base = (base * vign).astype(np.uint8)
    im = Image.fromarray(cv2.cvtColor(base, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(im)
    size = 132 if len(title) <= 16 else 104 if len(title) <= 26 else 84
    ft = ImageFont.truetype(str(FONTS_DIR / "Anton-Regular.ttf"), size)
    fs = ImageFont.truetype(str(FONTS_DIR / "Montserrat-ExtraBold.ttf"), 46)
    lines, cur = [], ""
    for w in title.upper().split():
        t = f"{cur} {w}".strip()
        if cur and ft.getbbox(t)[2] > W - 260:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    lh = int(size * 1.12)
    y = H // 2 - (lh * len(lines)) // 2 - (40 if sub else 0)
    for line in lines:
        d.text((W // 2, y), line, font=ft, fill=GOLD, anchor="mt", stroke_width=4, stroke_fill=(0, 0, 0))
        y += lh
    d.line([(W // 2 - 160, y + 18), (W // 2 + 160, y + 18)], fill=GOLD, width=4)
    if sub:
        d.text((W // 2, y + 50), sub.upper(), font=fs, fill=WHITE, anchor="mt")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, quality=94)
    return out


POP_STYLE = ("Style: Pop,Anton,96,&H0000D7FF,&H0000D7FF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,6,4,5,"
             "120,120,0,1")


def pop_event(a: float, b: float, text: str, esc) -> str:
    """A big yellow line that punches in and fades (dramatic emphasis, like the top documentary channels)."""
    from .captions import _ts
    return (f"Dialogue: 4,{_ts(a)},{_ts(b)},Pop,,0,0,0,,"
            rf"{{\an5\pos({W // 2},{H - 300})\fad(120,250)\fscx130\fscy130\t(0,180,\fscx100\fscy100)}}"
            f"{esc(text.upper())}")
