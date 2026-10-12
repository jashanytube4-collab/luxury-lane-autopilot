"""Story episodes in the style of the most-watched Prince Hamdan / Dubai royal family channels (BUZZ STORY style).

One personal story per episode (his wife, his brother, his mother, his children...), about 8-10 minutes, told by a
warm storyteller voice with a curiosity-driven hook, questions to the audience and an emotional ending. Pictures
change every 3-4 seconds: our official footage and photos of him, plus freely licensed photos (Wikimedia Commons)
of the family members and places the story is about. Nothing is shown twice.

Scripts live in assets/stories/*.yaml (written and fact-checked in advance; see assets/stories/README.md):

    id: wife
    title: "Why Prince Hamdan NEVER Shows His Wife in Public"
    thumbnail: {text: "WHY HE HIDES HER", tag: "THE TRUTH", inset: "cat:Some Commons category"}
    mood: elegant                      # music: elegant | epic | arabian | modern
    description: "..."
    tags: [...]
    sections:
      - say: "Narration for this part..."
        show: [him, "him:wedding|family", "cat:Mohammed bin Rashid Al Maktoum", "search:Dubai Creek 1960s"]

`show` tokens: him (footage/photos where face recognition finds him), him:<regex on event titles>,
event:<regex> (any official footage of matching events), cat:<Commons category>, search:<Commons search words>.
"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from . import audio, commons, qa
from .config import ASSETS_DIR
from .longform import (ASS_HEADER, FPS, LW, EpisodeResult, Item, Material, Overlay, Shot, Visuals, _chapter_shots,
                       _esc, _mix_long, _shot, _take, render_timeline)
from .voice import synthesize

log = logging.getLogger("lane.story")

STORY_DIR = ASSETS_DIR / "stories"


@dataclass
class Story:
    id: str
    title: str
    thumbnail: dict
    mood: str
    description: str
    tags: list[str]
    sections: list[dict]
    path: Path


def bank() -> list[Story]:
    out = []
    for p in sorted(STORY_DIR.glob("*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        out.append(Story(d["id"], d["title"], d.get("thumbnail", {}), d.get("mood", "elegant"),
                         d.get("description", ""), d.get("tags", []), d["sections"], p))
    return out


def next_story(done: list[str]) -> Story | None:
    return next((s for s in bank() if s.id not in done), None)


@dataclass
class Gathered:
    visuals: list[list[Visuals]]                 # per section
    spare: list[Visuals]
    his_photos: list[Path]                        # for the thumbnail
    credits: list[str] = field(default_factory=list)
    event_keys: list[str] = field(default_factory=list)


class StoryMaker:
    """Gathers pictures for a story from the official library and Wikimedia Commons, then renders it."""

    def __init__(self, studio, events: dict, usage: dict, cfg: dict) -> None:
        self.studio, self.events, self.usage, self.cfg = studio, events, usage, cfg
        self._his: dict[str, Visuals] = {}

    # ---- pictures ---------------------------------------------------------------------------------------
    def _event_visuals(self, key: str, only_him: bool) -> Visuals | None:
        if key in self._his:
            return self._his[key]
        ev = self.events[key]
        video = self.studio.video(key, ev)
        scenes = []
        if video:
            if only_him:
                scenes = sorted((a, b) for a, b, _ in self.studio.his_scenes(video))
            else:
                from .longform import _scene_list
                scenes = _scene_list(video)
        photos = self.studio.photos(key, ev, limit=12)
        if only_him:
            photos = [p for p, _ in self.studio.his_photos(photos)]
        vis = Visuals(video if scenes else None, scenes, photos) if (scenes or photos) else None
        self._his[key] = vis
        return vis

    def _events_matching(self, pattern: str | None) -> list[str]:
        rx = re.compile(pattern, re.I) if pattern else None
        keys = [k for k, e in self.events.items()
                if not self.usage.get(k, {}).get("no_hamdan") and (e.get("video") or len(e.get("photos", [])) >= 3)
                and (rx is None or rx.search(e.get("title", "")))]
        keys.sort(key=lambda k: (not self.usage.get(k, {}).get("long"), bool(self.events[k].get("video")),
                                 self.events[k].get("date", "")), reverse=True)
        return keys

    def gather(self, story: Story, rng: random.Random, work: Path) -> Gathered:
        cache = work / "commons"
        his_pool = self._events_matching(None)
        rng.shuffle(his_pool)
        his_pool.sort(key=lambda k: (bool(self.usage.get(k, {}).get("long")), not self.events[k].get("video")))
        out = Gathered([], [], [])
        taken: set[str] = set()

        def take_events(keys: list[str], n: int, only_him: bool) -> list[Visuals]:
            got = []
            for k in keys:
                if len(got) >= n:
                    break
                if k in taken:
                    continue
                vis = self._event_visuals(k, only_him)
                if vis is None:
                    if only_him:
                        self.usage.setdefault(k, {})["no_hamdan"] = True
                    continue
                taken.add(k)
                out.event_keys.append(k)
                if only_him:
                    out.his_photos += vis.photos
                got.append(vis)
            return got

        for sec in story.sections:
            vis: list[Visuals] = []
            for tok in sec.get("show") or ["him"]:
                tok = str(tok)
                if tok.startswith(("cat:", "search:")):
                    pics = commons.fetch(tok if tok.startswith("cat:") else tok[7:], cache, limit=10)
                    if pics:
                        vis.append(Visuals(None, [], [p.path for p in pics]))
                        out.credits += [p.credit for p in pics]
                elif tok.startswith("him:"):
                    vis += take_events(self._events_matching(tok[4:]), 2, True)
                elif tok.startswith("event:"):
                    vis += take_events(self._events_matching(tok[6:]), 2, False)
                else:
                    vis += take_events(his_pool, 2, True)
            if not vis:
                vis = take_events(his_pool, 2, True)
            out.visuals.append(vis)
        out.spare = take_events(his_pool, 8, True)       # footage of him for any section that runs short
        return out


# ---- render -----------------------------------------------------------------------------------------------
def produce_story(story: Story, got: Gathered, cfg: dict, work: Path, seed: int) -> EpisodeResult:
    rng = random.Random(seed)
    work.mkdir(parents=True, exist_ok=True)
    scfg = cfg.get("story", {})
    vcfg = dict(cfg["voice"])
    vcfg["edge_voice"] = scfg.get("voice", "en-US-AvaMultilingualNeural")
    vcfg["edge_rate"] = scfg.get("voice_rate", "+8%")

    voices = [synthesize(sec["say"].strip(), vcfg, work / f"v_{i:02d}.wav") for i, sec in enumerate(story.sections)]
    material = Material()
    pools = [[it for v in vis for it in material.items(v)] for vis in got.visuals]
    for p in pools:
        rng.shuffle(p)
        p.sort(key=lambda it: it.kind != "video")       # footage first, then photos interleave
    spare = [it for v in got.spare for it in material.items(v)]
    used: list[Item] = []
    log.info("story material: %s items per section, %d spare", [len(p) for p in pools], len(spare))

    shots: list[Shot] = []
    voice_events: list[tuple[float, Path]] = []
    sfx: list[tuple[float, np.ndarray, float]] = []
    music_plan: list[tuple[float, str]] = [(0.0, story.mood)]
    ov = Overlay()
    t = 0.0
    gap = 0.45                                           # breath between sections
    for i, (sec, voice) in enumerate(zip(story.sections, voices)):
        start = t
        length = voice.duration + gap + (0.6 if i == 0 else 0)
        pool = pools[i]
        if i == 0:   # the hook: fast montage of moving footage of him
            sec_shots, k = [], 0
            while sum(s.frames for s in sec_shots) / FPS < length - 0.1:
                it = _take(pool, "video") or _take(spare, "video") or _take(pool) or _take(spare)
                if it is None:
                    break
                used.append(it)
                s = _shot(it, min(it.most, 2.4), rng)
                s.start += 0.1 if it.kind == "video" else 0
                s.move, s.strength = rng.choice(["push", "pull"]), 1.0
                s.flash_in = 4 if k % 2 else 0
                sec_shots.append(s)
                k += 1
            if sec_shots:
                over = sum(s.frames for s in sec_shots) - int(length * FPS)
                sec_shots[-1].frames = max(FPS, sec_shots[-1].frames - over)
            else:
                sec_shots = _chapter_shots(pool, length, rng, spare, used)
            sec_shots[0].dip_in = 8
            sfx.append((0.0, audio.impact(seed), -12.0))
        else:
            sec_shots = _chapter_shots(pool, length, rng, spare, used, pace=(3.0, 4.4), still=(3.4, 4.6))
            sec_shots[0].xfade_in = 10
            sfx.append((start - 0.2, audio.whoosh(seed + i, 0.5), -16.0))
        shots += sec_shots
        voice_events.append((start + (0.6 if i == 0 else 0.15), voice.wav))
        if i in (1, len(story.sections) // 2):
            music_plan.append((start, story.mood if i == 1 else _second_mood(story.mood)))
        t = start + sum(s.frames for s in sec_shots) / FPS
    # leftovers of the story's own pictures go to the closing card background
    end_card = 8.0
    hero = (got.his_photos or [Path(shots[0].src)])[0]
    shots.append(Shot("photo", hero, int(end_card * FPS), style="blur", xfade_in=18, dip_out=20))
    ov.add(t + 0.5, t + end_card - 0.5, "Small", rf"{{\an5\pos({LW // 2},{470})\fad(600,600)}}SUBSCRIBE FOR MORE ROYAL STORIES", 3)
    ov.add(t + 0.8, t + end_card - 0.5, "Big", rf"{{\an5\pos({LW // 2},{560})\fad(600,600)}}THE LUXURY LANE", 3)
    music_plan.append((t, "elegant"))
    duration = sum(s.frames for s in shots) / FPS

    mark = cfg["channel"].get("watermark", "")
    if mark:
        ov.add(0, duration, "Mark", rf"{{\an9\pos({LW - 60},{44})}}{_esc(mark.upper())}", 1)
    ass = work / "captions.ass"
    ass.write_text(ASS_HEADER + "\n".join(ov.ass_events) + "\n", encoding="utf-8")
    music_used = _mix_long(work, duration, voice_events, sfx, music_plan, cfg, seed)
    out = work / "episode.mp4"
    render_timeline(shots, ass, work / "mix.wav", out, work, seed)
    lf = cfg.get("longform", {})
    problems = qa.check_long(out, float(lf.get("min_minutes", 8.0)) * 60 - 20, float(lf.get("max_minutes", 13)) * 60)
    if problems:
        raise RuntimeError("quality check failed: " + "; ".join(problems))

    thumb = None
    try:
        from .thumbnail import make_thumbnail
        inset = None
        tok = str(story.thumbnail.get("inset", ""))
        if tok.startswith(("cat:", "search:")):
            pics = commons.fetch(tok if tok.startswith("cat:") else tok[7:], work / "commons", limit=6)
            inset = [p.path for p in pics]
        thumb = make_thumbnail(got.his_photos, story.thumbnail.get("text", story.title), story.thumbnail.get("tag", ""),
                               work / "thumbnail.jpg", seed, inset=inset)
    except Exception as e:  # noqa: BLE001 — never lose the episode over a thumbnail
        log.warning("story thumbnail failed: %s", e)
    return EpisodeResult(out, thumb, duration, story.title, story.description, story.tags, [], music_used)


def _second_mood(mood: str) -> str:
    return {"elegant": "epic", "epic": "elegant", "arabian": "elegant", "modern": "epic"}.get(mood, "elegant")
