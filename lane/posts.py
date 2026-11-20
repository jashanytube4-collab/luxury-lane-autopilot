"""Picture posts: 10-15 a day, published as short picture Shorts (YouTube has no API for its Posts tab).

Each one: four photos of Prince Hamdan (face-checked, never reused), slow camera moves, a short quote on screen —
his real words with his name, or a fan line without it — a "SUBSCRIBE FOR MORE FAZZA" ending and music.
A random 12-17 seconds long.
"""
from __future__ import annotations

import hashlib
import logging
import random
from datetime import date
from pathlib import Path

import numpy as np

from . import audio, qa, quotes, render
from .captions import GOLD, H, W, _esc

log = logging.getLogger("lane.posts")

HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Quote,Montserrat ExtraBold,66,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0.5,0,1,5,3,5,90,90,0,1
Style: By,Cinzel,40,{GOLD},{GOLD},&H00000000,&H80000000,-1,0,0,0,100,100,6,0,1,2.5,2,5,90,90,0,1
Style: Sub,Montserrat Black,86,{GOLD},{GOLD},&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,6,4,5,90,90,0,1
Style: Mark,Cinzel,30,&H70FFFFFF,&H70FFFFFF,&HB0000000,&HB0000000,-1,0,0,0,100,100,7,0,1,1.5,0,8,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
EMOJI = ["🤍", "❤️", "👑", "🇦🇪", "✨", "💫"]
TAGS = "#fazza #faz3 #sheikhhamdan #princehamdan #shorts"


def _wrap(text: str, width: int = 22) -> str:
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return r"\N".join(_esc(x) for x in lines)


def _ass(path: Path, quote: dict, duration: float, end_card: float, watermark: str) -> None:
    ev = []

    def add(a, b, style, text, layer=1):
        from .captions import _ts
        ev.append(f"Dialogue: {layer},{_ts(a)},{_ts(b)},{style},,0,0,0,,{text}")

    q_end = duration - end_card
    text = quote["text"] if not quote["by"] else f"“{quote['text']}”"
    add(0.5, q_end, "Quote", rf"{{\an5\pos({W // 2},1420)\fad(400,300)}}{_wrap(text)}", 2)
    if quote["by"]:
        lines = _wrap(text).count(r"\N") + 1
        add(1.0, q_end, "By", rf"{{\an5\pos({W // 2},{1420 + lines * 40 + 60})\fad(400,300)}}— "
                              rf"{_esc(quote['by'].upper())}", 2)
    add(q_end + 0.1, duration, "Sub", rf"{{\an5\pos({W // 2},1420)\fad(250,0)\fscx120\fscy120"
                                       rf"\t(0,300,\fscx100\fscy100)}}SUBSCRIBE\NFOR MORE\NFAZZA", 3)
    if watermark:
        add(0, duration, "Mark", rf"{{\an8\pos({W // 2},118)}}{_esc(watermark.upper())}", 1)
    path.write_text(HEADER + "\n".join(ev) + "\n", encoding="utf-8")


def _photo_pool(studio, events: dict, usage: dict, rng: random.Random, need: int = 4) -> list[tuple[str, int, Path, float]]:
    """[(event key, photo index, path, focus x)] — unused photos of him, from 1-3 events, newest events first."""
    keys = [k for k, e in events.items() if len(e.get("photos", [])) >= 2 and not usage.get(k, {}).get("no_hamdan")]
    keys.sort(key=lambda k: events[k].get("date", ""), reverse=True)
    recent, older = keys[:150], keys[150:]
    rng.shuffle(recent)
    rng.shuffle(older)
    picked: list[tuple[str, int, Path, float]] = []
    for k in recent + older:
        if len(picked) >= need:
            break
        used = set(usage.get(k, {}).get("post_photos", []))
        paths = studio.photos(k, events[k], limit=12)
        found = studio.his_photos([p for i, p in enumerate(paths) if i not in used])
        for p, s in found[:2]:
            idx = paths.index(p)
            picked.append((k, idx, p, s.cx))
            if len(picked) >= need:
                break
    return picked


def make_post(studio, events: dict, usage: dict, cursors: dict, today: date, cfg: dict, work: Path,
              seed: int) -> dict | None:
    rng = random.Random(seed)
    photos = _photo_pool(studio, events, usage, rng)
    if len(photos) < 3:
        return None
    quote = quotes.pick(cursors, today, rng)
    duration = round(rng.uniform(12.0, 17.0), 2)
    end_card = 2.4
    each = (duration) / len(photos)
    shots = [{"kind": "photo", "path": str(p), "seconds": each, "focus_x": fx} for _, _, p, fx in photos]
    work.mkdir(parents=True, exist_ok=True)
    fps = int(cfg["short"].get("fps", 30))
    plan = render.make_plan(shots, layout="fill", duration=duration, video=None, cuts=[], fps=fps, seed=seed)
    for i in range(len(plan.transitions)):
        plan.transitions[i] = rng.choice(["flash", "punch"])
    silent = work / "silence.wav"
    audio.save(np.zeros(int(audio.SR * duration), np.float32), silent)
    music, music_name = audio.music_bed(duration, seed, rng.choice(["elegant", "arabian", "epic"]))
    mix = work / "mix.wav"
    audio.mix(silent, duration, [], music, {**cfg.get("music", {}), "duck_db": 0}, mix, work)
    ass = work / "captions.ass"
    _ass(ass, quote, duration, end_card, cfg["channel"].get("watermark", ""))
    out = work / "post.mp4"
    render.render_video(plan, ass, mix, out, work, seed)
    problems = qa.check(out, 11.5, 17.5)
    if problems:
        raise render.RenderError("quality check failed: " + "; ".join(problems))
    emoji = rng.choice(EMOJI)
    if quote["by"]:
        short = quote["text"] if len(quote["text"]) <= 58 else quote["text"][:55].rsplit(" ", 1)[0] + "…"
        title = f"“{short}” — Fazza {emoji}"
        desc = (f"“{quote['text']}”\n— H.H. Sheikh Hamdan bin Mohammed bin Rashid Al Maktoum (Fazza), Crown Prince "
                f"of Dubai.\nSource: {quote['link']}\n\nSubscribe for more Fazza.")
    else:
        title = f"{quote['text']} {emoji}"
        desc = f"{quote['text']}\n\nPrince Hamdan — Fazza, Crown Prince of Dubai.\nSubscribe for more Fazza."
    title = f"{title} {TAGS}" if len(title) + len(TAGS) < 99 else f"{title} #fazza #shorts"
    unit = "post:" + hashlib.md5("|".join(f"{k}:{i}" for k, i, _, _ in photos).encode()).hexdigest()[:10]
    return {"path": out, "title": title[:100], "description": desc, "unit": unit, "music": music_name,
            "photos": [(k, i) for k, i, _, _ in photos], "work": work,
            "tags": ["Fazza", "Fazza quotes", "Sheikh Hamdan", "Prince Hamdan", "faz3", "Sheikh Hamdan quotes",
                     "Crown Prince of Dubai", "Fazza status", "Dubai prince", "Hamdan bin Mohammed"]}
