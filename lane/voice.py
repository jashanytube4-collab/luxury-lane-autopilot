"""Narration: Microsoft Edge neural voices (free), with Kokoro (open source, runs locally) as automatic fallback.

Both engines return exact per-word timings, which drive the animated captions.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .media import FFMPEG, run

log = logging.getLogger("lane.voice")

_kokoro_pipeline = None


@dataclass
class Narration:
    wav: Path              # 48 kHz mono
    words: list[dict]      # [{"text", "start", "end"}] in seconds
    duration: float        # end of the last spoken word
    engine: str


def _clean_word(w: str) -> str:
    return re.sub(r"^[^\w$£€%']+|[^\w$£€%']+$", "", w)


async def _edge_stream(text: str, voice: str, rate: str, mp3: Path) -> list[dict]:
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    words = []
    with open(mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append({"text": chunk["text"], "start": start, "end": start + chunk["duration"] / 1e7})
    return words


def _edge(text: str, cfg: dict, out: Path, rate: str) -> Narration:
    mp3 = out.with_suffix(".mp3")
    last: Exception | None = None
    for _ in range(3):
        try:
            words = asyncio.run(_edge_stream(text, cfg["edge_voice"], rate, mp3))
            if words and mp3.stat().st_size > 5000:
                break
            raise RuntimeError("edge-tts returned no audio")
        except Exception as e:  # network hiccups, service changes
            last = e
            log.warning("edge-tts attempt failed: %s", e)
    else:
        raise RuntimeError(f"edge-tts failed: {last}")
    run([FFMPEG, "-y", "-v", "error", "-i", str(mp3), "-ar", "48000", "-ac", "1", str(out)])
    words = [{**w, "text": _clean_word(w["text"])} for w in words if _clean_word(w["text"])]
    return Narration(out, words, words[-1]["end"], "edge")


def _kokoro(text: str, cfg: dict, out: Path, speed: float) -> Narration:
    global _kokoro_pipeline
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    if _kokoro_pipeline is None:
        _kokoro_pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    chunks, words, offset = [], [], 0.0
    for r in _kokoro_pipeline(text, voice=cfg["kokoro_voice"], speed=speed):
        audio = r.audio.numpy() if hasattr(r.audio, "numpy") else np.asarray(r.audio)
        glue = False
        for tk in r.tokens or []:
            if tk.start_ts is None or tk.end_ts is None:
                continue
            w = _clean_word(tk.text)
            if not w:
                glue = not tk.whitespace
                continue
            if glue and words:  # e.g. "do" + "n't"
                words[-1]["text"] += w
                words[-1]["end"] = offset + tk.end_ts
            else:
                words.append({"text": w, "start": offset + tk.start_ts, "end": offset + tk.end_ts})
            glue = not tk.whitespace
        offset += len(audio) / 24000
        chunks.append(audio)
    raw = out.with_suffix(".24k.wav")
    sf.write(raw, np.concatenate(chunks), 24000)
    run([FFMPEG, "-y", "-v", "error", "-i", str(raw), "-ar", "48000", "-ac", "1", str(out)])
    return Narration(out, words, words[-1]["end"], "kokoro")


def synthesize(text: str, cfg: dict, out: Path, *, faster: int = 0) -> Narration:
    """faster = 0, 1, 2 steps of extra speed, used when a script runs long."""
    out.parent.mkdir(parents=True, exist_ok=True)
    base = int(str(cfg.get("edge_rate", "+0%")).replace("%", "") or 0)
    rate = f"{base + 7 * faster:+d}%"
    if cfg.get("engine", "edge") == "edge":
        try:
            return _edge(text, cfg, out, rate)
        except Exception as e:
            log.error("Edge voice failed, switching to Kokoro: %s", e)
    return _kokoro(text, cfg, out, float(cfg.get("kokoro_speed", 1.05)) + 0.07 * faster)
