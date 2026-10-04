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


def _narrate(script: str, cfg: dict, work: Path, shorten) -> tuple[Narration, str]:
    limit = cfg["short"]["max_seconds"] - TAIL
    for faster in (0, 1, 2):
        narr = synthesize(script, cfg["voice"], work / "voice.wav", faster=faster)
        if narr.duration <= limit:
            return narr, script
    if shorten is not None:
        words = int(len(script.split()) * limit / narr.duration * 0.95)
        script = shorten(script, words)
        narr = synthesize(script, cfg["voice"], work / "voice.wav", faster=1)
        if narr.duration <= limit:
            return narr, script
    raise ShortError(f"narration runs {narr.duration:.1f}s, longer than {limit:.1f}s")


def produce(video: Path | None, plan: dict, cfg: dict, work: Path, out: Path, seed: int, shorten=None,
            cuts: list[float] | None = None) -> Result:
    """video: the event's footage (None for a photo-only Short); plan["shots"] mixes video scenes and photos."""
    work.mkdir(parents=True, exist_ok=True)
    s_cfg = cfg["short"]
    fps = int(s_cfg.get("fps", 30))

    narr, script = _narrate(plan["script"], cfg, work, shorten)
    plan["script"] = script
    duration = min(s_cfg["max_seconds"], max(s_cfg["min_seconds"], narr.duration + TAIL))

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
