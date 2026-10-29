"""Footage and/or photos + an AI plan -> one finished, quality-checked Short."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import audio, qa, render
from .captions import build_ass
from .media import probe, scene_cuts
from .voice import Narration, synthesize

log = logging.getLogger("lane.short")

TAIL = 0.9   # seconds of picture + music after the last word


class ShortError(Exception):
    pass


@dataclass
class Result:
    path: Path
    duration: float
    voice_engine: str
    music: str
    layout: str


def _long_enough(narr: Narration, script: str, cfg: dict, work: Path, lengthen) -> tuple[Narration, str]:
    """Owner rule: a narrated Short talks for at least short.min_narration_seconds (no 5-second voice on a 15-second Short).
    A too-short script is lengthened (never rejected: the Short count must not drop)."""
    need = float(cfg["short"].get("min_narration_seconds", 0))
    limit = cfg["short"]["max_seconds"] - TAIL
    for _ in range(3):
        if narr.duration >= need or lengthen is None:
            break
        wps = max(1.5, len(script.split()) / max(narr.duration, 1.0))
        words = int((need + 1.5) * wps)
        longer = lengthen(script, words)
        if len(longer.split()) <= len(script.split()):
            break
        cand = synthesize(longer, cfg["voice"], work / "voice.wav")
        if cand.duration > limit:
            cand = synthesize(longer, cfg["voice"], work / "voice.wav", faster=1)
        if cand.duration > limit:
            break
        narr, script = cand, longer
    return narr, script


def _narrate(script: str, cfg: dict, work: Path, shorten, lengthen=None) -> tuple[Narration, str]:
    limit = cfg["short"]["max_seconds"] - TAIL
    for faster in (0, 1, 2):
        narr = synthesize(script, cfg["voice"], work / "voice.wav", faster=faster)
        if narr.duration <= limit:
            return _long_enough(narr, script, cfg, work, lengthen)
    if shorten is not None:
        words = int(len(script.split()) * limit / narr.duration * 0.95)
        script = shorten(script, words)
        narr = synthesize(script, cfg["voice"], work / "voice.wav", faster=1)
        if narr.duration <= limit:
            return _long_enough(narr, script, cfg, work, lengthen)
    raise ShortError(f"narration runs {narr.duration:.1f}s, longer than {limit:.1f}s")


def produce(video: Path | None, plan: dict, cfg: dict, work: Path, out: Path, seed: int, shorten=None,
            cuts: list[float] | None = None, lengthen=None) -> Result:
    """video: the event's footage (None for a photo-only Short); plan["shots"] mixes video scenes and photos."""
    work.mkdir(parents=True, exist_ok=True)
    s_cfg = cfg["short"]
    fps = int(s_cfg.get("fps", 30))

    narr, script = _narrate(plan["script"], cfg, work, shorten, lengthen)
    plan["script"] = script
    duration = min(s_cfg["max_seconds"], narr.duration + TAIL)   # owner rule: video length = voice length (+ short tail), no silent padding

    if cuts is None and video is not None:
        cuts = scene_cuts(video, probe(video)["fps"])
    try:
        timeline = render.make_plan(plan["shots"], layout=plan["layout"], duration=duration, video=video,
                                    cuts=cuts or [], fps=fps, seed=seed)
    except render.RenderError as e:
        raise ShortError(str(e)) from e

    # sound design: impact on the opening frame, whooshes timed so their peak lands on each cut
    sfx = [(0.0, audio.impact(seed), -9.0)]
    for i, (t, kind) in enumerate(zip(timeline.cut_times, timeline.transitions)):
        w = audio.whoosh(seed + i, 0.5 if kind != "punch" else 0.36)
        peak = 0.31 if kind != "punch" else 0.22
        sfx.append((t - peak, w, -13.0 if kind != "punch" else -16.0))
    music, music_name = audio.music_bed(duration, seed, plan.get("mood", ""))
    mix_path = work / "mix.wav"
    audio.mix(narr.wav, duration, sfx, music, cfg.get("music", {}), mix_path, work)

    # the smallest picture band decides where text goes, so captions sit inside the picture on every shot
    band = min(render.band_height(plan["layout"], s.src_w, s.src_h) for s in timeline.segments)
    if plan["layout"] == "fill":
        caption_y, hook_y = 1240, 400
    else:
        top, bottom = (1920 - band) // 2, (1920 + band) // 2
        # inside the picture's lower third; below it only when the band is too thin to hold captions
        caption_y = bottom - 170 if band >= 640 else bottom + 110
        hook_y = (170 + top) // 2 if top >= 430 else 400
    ass = work / "captions.ass"
    build_ass(ass, words=narr.words, script=script, emphasis=plan.get("emphasis", []),
              hook=plan.get("hook_text", ""), watermark=cfg["channel"].get("watermark", ""),
              duration=duration, caption_y=caption_y, hook_y=hook_y)

    try:
        render.render_video(timeline, ass, mix_path, out, work, seed)
    except render.RenderError as e:
        raise ShortError(str(e)) from e

    problems = qa.check(out, s_cfg["min_seconds"], s_cfg["max_seconds"])
    if problems:
        raise ShortError("quality check failed: " + "; ".join(problems))
    return Result(out, duration, narr.engine, music_name, plan["layout"])


def produce_moment(video: Path, shots: list[dict], cfg: dict, work: Path, out: Path, seed: int,
                   duration: float, caption: str = "", script: str = "", shorten=None) -> Result:
    """A 'moment' Short: real footage of him, vertical (see lane/moments.py). Music only, unless `script` is given:
    then a short true voice-over with captions is mixed in (original commentary, not just clips)."""
    import random

    import numpy as np
    work.mkdir(parents=True, exist_ok=True)
    fps = int(cfg["short"].get("fps", 30))
    cuts = scene_cuts(video, probe(video)["fps"])

    def build_timeline(length: float):
        try:
            tl = render.make_plan(shots, layout="fill", duration=length, video=video, cuts=cuts, fps=fps, seed=seed)
        except render.RenderError as e:
            raise ShortError(str(e)) from e
        for i in range(len(tl.transitions)):
            tl.transitions[i] = "punch"
        return tl

    timeline = build_timeline(duration)
    narr, spoken = None, ""
    if script:
        limit = duration - TAIL
        try:
            for faster in (0, 1, 2):
                narr = synthesize(script, cfg["voice"], work / "voice.wav", faster=faster)
                if narr.duration <= limit:
                    break
            else:
                if shorten is None:
                    raise ShortError("voice-over too long")
                script = shorten(script, int(len(script.split()) * limit / narr.duration * 0.92))
                narr = synthesize(script, cfg["voice"], work / "voice.wav", faster=1)
                if narr.duration > limit:
                    raise ShortError("voice-over too long")
            spoken = script
        except Exception as e:  # noqa: BLE001 - no voice available: still a good music-only moment
            log.warning("voice-over skipped: %s", e)
            raise ShortError(f"voice-over was requested but failed: {e}") from e     # never publish a silent Short that should be narrated
        if not narr.words:
            raise ShortError("voice-over has no spoken words")
        if narr.duration + TAIL < duration - 0.25:   # owner rule: video length = voice length (+ short tail)
            duration = narr.duration + TAIL
            timeline = build_timeline(duration)
    silent = work / "silence.wav"
    if narr is not None:
        silent = narr.wav
    else:
        audio.save(np.zeros(int(audio.SR * duration), np.float32), silent)
    mood = random.Random(seed).choice(["epic", "arabian", "elegant", "modern"])
    music, music_name = audio.music_bed(duration, seed, mood)
    mix_path = work / "mix.wav"
    audio.mix(silent, duration, [], music, {**cfg.get("music", {}), **({} if narr else {"duck_db": 0})}, mix_path, work)
    ass = work / "captions.ass"
    if narr is not None:
        build_ass(ass, words=narr.words, script=spoken, emphasis=[], hook=caption,
                  watermark=cfg["channel"].get("watermark", ""), duration=duration, caption_y=1240, hook_y=250)
    else:
        build_ass(ass, words=[], script="", emphasis=[], hook=caption, watermark=cfg["channel"].get("watermark", ""),
                  duration=duration, caption_y=1240, hook_y=250, hook_seconds=duration)
    try:
        render.render_video(timeline, ass, mix_path, out, work, seed)
    except render.RenderError as e:
        raise ShortError(str(e)) from e
    problems = qa.check(out, 11.5, 17.5)
    if problems:
        raise ShortError("quality check failed: " + "; ".join(problems))
    return Result(out, duration, narr.engine if narr else "none", music_name, "fill")
