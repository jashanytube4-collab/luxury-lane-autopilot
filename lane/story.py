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
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from . import audio, commons, qa
from .config import ASSETS_DIR
from .beats import POP_STYLE, align, make_card, pop_event
from .longform import (ASS_HEADER, FPS, LW, EpisodeResult, Item, Material, Overlay, Shot, Visuals, _chapter_shots,
                       _esc, _mix_long, _punctuate, _shot, _subtitle_lines, _take, render_timeline)
from .voice import synthesize

log = logging.getLogger("lane.story")
ROOT = ASSETS_DIR.parent
_clips_file = ASSETS_DIR / "clips.yaml"
CLIPS: dict = (yaml.safe_load(_clips_file.read_text(encoding="utf-8")) or {}).get("clips", {}) if _clips_file.exists() else {}

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
    footage: str = ""          # regex on official event titles: where "him" footage for this story comes from
    ready: bool = False        # checked and approved (exact clips for every sentence): only these are published
    commons: bool = False      # this story may use outside photos (planes, places) that match its sentences


_CTA = re.compile(r"\b(comments?|subscribe|tell us (below|in)|let us know|like this video|hit the like)\b", re.I)


def clip_matches(sentence: str, limit: int = 2) -> list[str]:
    """The hand-picked clips that show what this sentence talks about (assets/clips.yaml `match`)."""
    out = []
    for cid, spec in CLIPS.items():
        rx = spec.get("match")
        if rx and re.search(rx, sentence, re.I):
            out.append(f"clip:{cid}")
            if len(out) >= limit:
                break
    return out


_STOP = set("""this that with from they them their there then than when what which while where have has had been were
will would could should about after before into over under again also just only even very much more most some such
said says told every each other another being because people world years year day days time first last never
always little great quiet quietly really still those these here""".split())


def _stem(w: str) -> str:
    w = w.lower()
    for suf in ("ing", "es", "s"):
        if len(w) > 5 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _words_of(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[A-Za-z]{3,}", text) if w.lower() not in _STOP}


def moment_matches(sentence: str, events: dict, limit: int = 2) -> list[str]:
    """Described videos (Instagram posts of him, with AI tags) that show what this sentence talks about: at least
    two of the clip's tags appear in the sentence, best matches first."""
    words = _words_of(sentence)
    if len(words) < 2:
        return []
    scored = []
    for k, e in events.items():
        tags = e.get("tags")
        if not tags or not e.get("video"):
            continue
        hit = len(words & {_stem(t) for t in tags for t in t.split()})
        if hit >= 2:
            scored.append((hit, e.get("likes", 0), k))
    scored.sort(reverse=True)
    return [f"key:{k}" for _, _, k in scored[:limit]]


def sentence_beats(sections: list[dict]) -> None:
    """Every story gets sentence-level pictures automatically: a section without hand-made beats is split into its
    sentences, each showing the clip of exactly what it says (when there is one) and otherwise the section's own
    footage. The request to comment/subscribe is kept only in the last section (asked once, at the end)."""
    last = len(sections) - 1
    for i, sec in enumerate(sections):
        if sec.get("beats"):
            beats = sec["beats"]
        else:
            sents = [x for x in re.split(r"(?<=[.!?])\s+", str(sec.get("say", "")).strip()) if x]
            beats = [{"say": x, "show": clip_matches(x)} for x in sents]
        if i != last:
            beats = [b for b in beats if not _CTA.search(str(b["say"]))] or beats[:1]
        sec["beats"] = beats
        sec["say"] = " ".join(str(b["say"]).strip() for b in beats)


def bank() -> list[Story]:
    out = []
    for p in sorted(STORY_DIR.glob("*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        sentence_beats(d["sections"])
        out.append(Story(d["id"], d["title"], d.get("thumbnail", {}), d.get("mood", "elegant"),
                         d.get("description", ""), d.get("tags", []), d["sections"], p, d.get("footage", ""),
                         bool(d.get("ready", False)), bool(d.get("commons", False))))
    return out


def next_story(done: list[str], ready_only: bool = False) -> Story | None:
    return next((s for s in bank() if s.id not in done and (s.ready or not ready_only)), None)


@dataclass
class Gathered:
    visuals: list[list[Visuals]]                 # per section
    spare: list[Visuals]
    his_photos: list[Path]                        # for the thumbnail
    beat_visuals: dict = field(default_factory=dict)   # section index -> [visuals of each beat]
    credits: list[str] = field(default_factory=list)
    clip_credits: list[str] = field(default_factory=list)
    event_keys: list[str] = field(default_factory=list)
    thumb_scene: Path | None = None               # a chosen frame for the thumbnail (story.thumbnail.scene)
    sources: dict = field(default_factory=dict)   # id(Visuals) -> where the footage came from (quality gate)
    gate_warning: str = ""                        # set when the footage was thinner than the gate (video still made)


class QualityGateError(Exception):
    """The story does not have enough distinct, varied real footage to be a premium video."""


def audit(story: Story, got: Gathered, cfg: dict) -> None:
    """Quality gate, before anything is rendered: an 8-minute story needs a new picture every few seconds, from many
    different sources, with no single source carrying the video. Otherwise it is stopped (never padded with repeats)."""
    g = cfg.get("story", {}).get("gate", {})
    min_pieces, min_sources = int(g.get("min_clips", 100)), int(g.get("min_sources", 10))
    max_share = float(g.get("max_source_share", 0.25))
    uniq = {id(v): v for per in got.beat_visuals.values() for b in per for v in b}
    uniq.update({id(v): v for sec in got.visuals for v in sec})
    count: dict[str, int] = {}
    for key, v in uniq.items():
        n = len(v.scenes) + len(v.photos)
        label = got.sources.get(key, "library")
        count[label] = count.get(label, 0) + n
    total = sum(count.values())
    log.info("quality gate '%s': %d distinct pieces from %d sources", story.id, total, len(count))
    problems = []
    if total < min_pieces:
        problems.append(f"only {total} distinct clips/photos (need {min_pieces}+)")
    if len(count) < min_sources:
        problems.append(f"only {len(count)} different sources (need {min_sources}+)")
    top = max(count.items(), key=lambda kv: kv[1], default=("", 0))
    if total and top[1] / total > max_share:
        problems.append(f"one source ('{top[0][:50]}') is {top[1] * 100 // total}% of the footage (max {int(max_share * 100)}%)")
    if problems and not g.get("block", False):     # the video still goes out; the report says it was thin
        got.gate_warning = f"'{story.id}' footage below the premium bar: " + "; ".join(problems)
        log.warning(got.gate_warning)
        return
    if problems:
        raise QualityGateError(f"'{story.id}' is not premium yet: " + "; ".join(problems)
                               + ". Add more real clips from more sources, then it will be made.")


class StoryMaker:
    """Gathers pictures for a story from the official library and Wikimedia Commons, then renders it."""

    def __init__(self, studio, events: dict, usage: dict, cfg: dict) -> None:
        self.studio, self.events, self.usage, self.cfg = studio, events, usage, cfg
        self._his: dict[str, Visuals] = {}

    # ---- pictures ---------------------------------------------------------------------------------------
    def _event_visuals(self, key: str, only_him: bool) -> Visuals | None:
        if (key, only_him) in self._his:
            return self._his[(key, only_him)]
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
        self._his[(key, only_him)] = vis
        return vis

    def _clip(self, cid: str, work: Path) -> Visuals | None:
        """A hand-picked clip of an exact moment (assets/clips.yaml), from the private library."""
        spec = CLIPS.get(cid)
        if not spec:
            log.warning("unknown clip %r", cid)
            return None
        dest = work / "clips" / f"{cid}.mp4"
        local = ROOT / "work" / "clips" / f"{cid}.mp4"
        if not dest.exists():
            if local.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(local, dest)
            else:
                lib = getattr(self.studio.hamdan, "library", None)
                if not (lib and lib.fetch(f"clip-{cid}.mp4", dest)):
                    log.warning("clip %r is not in the footage library (run tools/fetch_clips.py)", cid)
                    return None
        from .longform import _scene_list, probe
        scenes = _scene_list(dest)
        if not scenes:          # a short hand-picked clip whose shots are all brief: it was chosen, so use all of it
            dur = probe(dest)["duration"]
            scenes = [(0.05, dur - 0.05)] if dur > 0.8 else []
        return Visuals(dest, scenes, []) if scenes else None

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
        his_pool = self._events_matching(story.footage or None)
        if len(his_pool) < 6:                 # too few matching events: any footage of him
            his_pool += [k for k in self._events_matching(None) if k not in his_pool]
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
                out.sources[id(vis)] = f"event:{k}"
                if only_him:
                    out.his_photos += vis.photos
                got.append(vis)
            return got

        seen_tok: dict[str, list[Visuals]] = {}

        def resolve(tokens: list) -> list[Visuals]:
            vis: list[Visuals] = []
            for tok in tokens:
                tok = str(tok)
                if tok.startswith("card:"):
                    continue                     # designed cards are drawn at render time
                if tok in seen_tok and tok != "him":   # same subject again: the unused rest of the same pictures
                    vis += seen_tok[tok]
                    continue
                before = len(vis)
                vis += resolve_one(tok)
                seen_tok[tok] = vis[before:]
            return vis

        def resolve_one(tok: str) -> list[Visuals]:
            vis: list[Visuals] = []
            if tok.startswith("video:"):     # official VIDEO of matching events (moving footage, not photos)
                keys = [k for k in self._events_matching(tok[6:]) if self.events[k].get("video")]
                vis += [v for v in take_events(keys, 2, True) if v.video]
            elif tok.startswith("chart:"):      # an animated chart / counter drawn by us (lane/charts.py)
                from . import charts
                made = charts.render(tok[6:], work / "charts")
                if made:
                    vis.append(Visuals(made[0], [(0.05, made[1] - 0.05)], []))
                    out.sources[id(vis[-1])] = "chart"
            elif tok.startswith("clip:"):
                v = self._clip(tok[5:], work)
                if v:
                    vis.append(v)
                    out.clip_credits.append(CLIPS[tok[5:]].get("credit", ""))
                    out.sources[id(v)] = CLIPS[tok[5:]].get("credit") or f"clip:{tok[5:]}"
            elif tok.startswith(("cat:", "search:")):
                if not (self.cfg.get("story", {}).get("commons", False) or story.commons):
                    return vis               # outside photos of other people and places are switched off
                pics = commons.fetch(tok if tok.startswith("cat:") else tok[7:], cache, limit=10)
                if pics:
                    vis.append(Visuals(None, [], [p.path for p in pics]))
                    out.credits += [p.credit for p in pics]
            elif tok.startswith("key:"):
                if tok[4:] in self.events:          # a hand-pinned event: the scenes with him (no title graphics)
                    vis += take_events([tok[4:]], 1, True) or take_events([tok[4:]], 1, False)
            elif tok.startswith("him:"):
                vis += take_events(self._events_matching(tok[4:]), 2, True)
            elif tok.startswith("event:"):
                vis += take_events(self._events_matching(tok[6:]), 2, False)
            else:
                vis += take_events(his_pool, 2, True)
            return vis

        for i, sec in enumerate(story.sections):
            if sec.get("beats"):
                for b in sec["beats"]:
                    if not b.get("show"):
                        b["show"] = moment_matches(str(b["say"]), self.events)
                per = [resolve(b.get("show") or []) for b in sec["beats"]]
                out.beat_visuals[i] = per
                own = resolve(sec.get("show") or []) if sec.get("show") else []
                vis = (list({id(v): v for v in own + [v for b in per for v in b]}.values())
                       or take_events(his_pool, 2, True))
            else:
                vis = resolve(sec.get("show") or ["him"]) or take_events(his_pool, 2, True)
            out.visuals.append(vis)
        out.spare = []       # no random filler: short sections borrow from the rest of this story (see produce_story)
        out.thumb_scene = self.thumb_scene(story, work)
        self._top_up(story, out, his_pool, take_events)
        audit(story, out, self.cfg)
        return out

    def _top_up(self, story: Story, out: Gathered, his_pool: list[str], take_events) -> None:
        """Automatic: when the story is short of distinct footage, bring in more unused events from the library
        (the story's own topic first), spread over its sections, until the quality gate is met or nothing is left."""
        g = self.cfg.get("story", {}).get("gate", {})
        need = int(g.get("min_clips", 100))
        def pieces() -> int:
            u = {id(v): v for per in out.beat_visuals.values() for b in per for v in b}
            u.update({id(v): v for sec in out.visuals for v in sec})
            return sum(len(v.scenes) + len(v.photos) for v in u.values())
        i = 0
        while pieces() < need and out.visuals and i < 60:
            extra = take_events(his_pool, 1, True)
            if not extra:
                break
            out.visuals[i % len(out.visuals)] += extra
            i += 1
        if i:
            log.info("quality gate top-up for '%s': %d more events added", story.id, i)

    def thumb_scene(self, story: Story, work: Path) -> Path | None:
        """story.thumbnail.scene = "clip:<id>@<seconds>": that frame of the clip becomes the thumbnail picture."""
        tok = str(story.thumbnail.get("scene", ""))
        if not tok.startswith("clip:"):
            return None
        cid, _, at = tok[5:].partition("@")
        v = self._clip(cid, work)
        if not v:
            return None
        frame = work / "thumb_scene.jpg"
        from .media import FFMPEG
        subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", at or "0.5", "-i", str(v.video), "-frames:v", "1",
                        "-q:v", "2", str(frame)], check=False, timeout=60)
        return frame if frame.exists() else None


# ---- render -----------------------------------------------------------------------------------------------
def produce_story(story: Story, got: Gathered, cfg: dict, work: Path, seed: int) -> EpisodeResult:
    rng = random.Random(seed)
    work.mkdir(parents=True, exist_ok=True)
    scfg = cfg.get("story", {})
    vcfg = dict(cfg["voice"])
    vcfg["edge_voice"] = scfg.get("voice", "en-US-AvaMultilingualNeural")
    vcfg["edge_rate"] = scfg.get("voice_rate", "+8%")

    kcfg = {**vcfg, "engine": "kokoro", "kokoro_voice": scfg.get("kokoro_voice", "af_heart"),
            "kokoro_speed": float(scfg.get("kokoro_speed", 0.95))}

    def narrate(edge_rate: int | None, kokoro_speed: float, tag: str):
        if edge_rate is not None:
            ecfg = {**vcfg, "edge_rate": f"{edge_rate:+d}%"}
            vs = [synthesize(sec["say"].strip(), ecfg, work / f"v{tag}_{i:02d}.wav")
                  for i, sec in enumerate(story.sections)]
            if all(v.engine == "edge" for v in vs):
                return vs, "edge"
            # the online voice failed for part of the story: one narrator for the whole episode, never two
            log.warning("storyteller voice unavailable for part of the story; narrating all of it with the backup voice")
        vs = [synthesize(sec["say"].strip(), {**kcfg, "kokoro_speed": kokoro_speed}, work / f"k{tag}_{i:02d}.wav")
              for i, sec in enumerate(story.sections)]
        return vs, "kokoro"

    edge_rate = int(str(vcfg["edge_rate"]).replace("%", "") or 0)
    voices, engine = narrate(edge_rate, kcfg["kokoro_speed"], "")
    # the episode must reach the minimum length (8 minutes): if the narration came out short (a faster backup
    # voice, a quick read), narrate it again just slow enough to get there
    lf = cfg.get("longform", {})
    need = float(lf.get("min_minutes", 8.0)) * 60 + 6
    end_card = float(scfg.get("end_card", 0.0))
    total = sum(v.duration for v in voices) + 0.45 * len(voices) + 0.6 + end_card
    if total < need:
        ratio = total / need
        log.info("narration %.0fs is short of %.0fs; slowing it down to %.0f%%", total, need, ratio * 100)
        if engine == "edge":
            voices, engine = narrate(int((1 + edge_rate / 100) * ratio * 100 - 100) - 1, kcfg["kokoro_speed"] * ratio, "s")
        else:
            voices, engine = narrate(None, kcfg["kokoro_speed"] * ratio * 0.99, "s")
    material = Material()
    item_cache: dict[int, list[Item]] = {}

    def items_of(v: Visuals) -> list[Item]:
        if id(v) not in item_cache:
            item_cache[id(v)] = material.items(v)
        return item_cache[id(v)]
    pools = [[it for v in vis for it in items_of(v)] for vis in got.visuals]
    beat_pools = {i: [[it for v in vis for it in items_of(v)] for vis in per] for i, per in got.beat_visuals.items()}
    for per in beat_pools.values():
        for bp in per:
            bp.sort(key=lambda it: it.kind != "video")    # moving footage first: photo-only stories perform badly
    card_bg = (got.his_photos or [None])[0]
    his_bg = list(dict.fromkeys(got.his_photos))[:40]
    rng.shuffle(his_bg)
    n_cards = 0
    for p in pools:
        rng.shuffle(p)
        p.sort(key=lambda it: it.kind != "video")       # footage first, then photos interleave
    spare = [it for v in got.spare for it in material.items(v)]

    def borrow(i: int) -> list[Item]:
        """Pictures from the other parts of this story (nearest parts first), for a part that runs short."""
        order = sorted((j for j in range(len(pools)) if j != i), key=lambda j: abs(j - i))
        return [it for j in order for it in pools[j][len(pools[j]) // 2:]]
    used: list[Item] = []
    log.info("story material: %s items per section, %d spare", [len(p) for p in pools], len(spare))

    shots: list[Shot] = []
    voice_events: list[tuple[float, Path]] = []
    sfx: list[tuple[float, np.ndarray, float]] = []
    music_plan: list[tuple[float, str]] = [(0.0, story.mood)]
    ov = Overlay()
    pops: list[tuple[float, float, str]] = []
    t = 0.0
    gap = 0.45                                           # breath between sections
    for i, (sec, voice) in enumerate(zip(story.sections, voices)):
        start = t
        length = voice.duration + gap + (0.6 if i == 0 else 0)
        pool = pools[i]
        if sec.get("beats"):   # sentence-level visuals: each beat shows its own pictures while it is spoken
            offset = 0.6 if i == 0 else 0.15
            spans = align(sec["beats"], voice.words, voice.duration)
            sec_shots = []
            for j, (beat, (a, b)) in enumerate(zip(sec["beats"], spans)):
                a = 0.0 if j == 0 else a + offset
                b = length if j == len(spans) - 1 else b + offset
                d = max(0.8, b - a)
                bshots = []
                cards = [str(t) for t in beat.get("show") or [] if str(t).startswith("card:")
                         and scfg.get("cards", False)]
                own = [it for it in beat_pools.get(i, [[]] * len(spans))[j] if it not in used]
                rest = d
                if cards:
                    title, _, sub = cards[0][5:].partition("|")
                    bg = his_bg[n_cards % len(his_bg)] if his_bg else card_bg
                    n_cards += 1
                    path = make_card(title.strip(), sub.strip(), bg, work / f"card_{i:02d}_{j:02d}.jpg")
                    cd = d if (d <= 6.0 or not own) else 4.5     # nothing exact to show: the card stays up
                    card_shot = Shot("photo", path, int(cd * FPS), move="push", strength=0.35)
                    card_shot.xfade_in = 8
                    bshots.append(card_shot)
                    rest = d - cd
                if rest > 0.4:
                    # pictures set aside for a later sentence (its exact clip) are never used up here as filler
                    later = {id(it) for (i2, per) in beat_pools.items() for j2, bp in enumerate(per)
                             if (i2, j2) > (i, j) for it in bp}
                    same = [it for it in pool if it not in used and it not in own and id(it) not in later]
                    # a sentence with its own clip never borrows another topic: it shows more of its own clip;
                    # only a sentence with nothing of its own may use the nearest parts of the story
                    reserve = same if (own or same) else [it for it in borrow(i) if id(it) not in later]
                    mine = list(beat_pools.get(i, [[]] * len(spans))[j])
                    if not (own or reserve or mine or used):   # nothing at all yet: the nearest part's pictures
                        reserve = borrow(i) or [it for p in pools for it in p]
                    fast = (2.0, 2.8) if i == 0 else (2.6, 4.0)
                    bshots += _chapter_shots(own, rest, rng, reserve, used, pace=fast, still=(3.0, 4.2),
                                             photo_every=0, again=mine)
                for p in pools:
                    p[:] = [it for it in p if it not in used]
                for per in beat_pools.values():
                    for bp in per:
                        bp[:] = [it for it in bp if it not in used]
                if beat.get("pop") and scfg.get("pops", False):
                    pops.append((start + a + 0.25, start + a + min(2.8, d), str(beat["pop"])))
                if i == 0 and j > 0:
                    bshots[0].flash_in = 4
                sec_shots += bshots
            if i == 0:
                sec_shots[0].dip_in = 8
                sfx.append((0.0, audio.impact(seed), -12.0))
            else:
                sec_shots[0].xfade_in = 10
                sfx.append((start - 0.2, audio.whoosh(seed + i, 0.5), -16.0))
        elif i == 0:   # the hook: fast montage of moving footage of him
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
                sec_shots = _chapter_shots(pool, length, rng, spare or borrow(i), used)
            sec_shots[0].dip_in = 8
            sfx.append((0.0, audio.impact(seed), -12.0))
        else:
            extra = spare or borrow(i)
            sec_shots = _chapter_shots(pool, length, rng, extra, used, pace=(3.0, 4.4), still=(3.4, 4.6))
            for p in pools:                      # pictures borrowed by this part are not shown again later
                p[:] = [it for it in p if it not in used]
            sec_shots[0].xfade_in = 10
            sfx.append((start - 0.2, audio.whoosh(seed + i, 0.5), -16.0))
        shots += sec_shots
        voice_events.append((start + (0.6 if i == 0 else 0.15), voice.wav))
        if scfg.get("subtitles", True):      # subtitles: many viewers watch on mute, and non-native listeners stay
            for a2, b2, text in _subtitle_lines(_punctuate(voice.words, sec["say"]), start + (0.6 if i == 0 else 0.15)):
                ov.add(a2, b2, "Sub", rf"{{\fad(80,80)}}{_esc(text)}", 2)
        if i in (1, len(story.sections) // 2):
            music_plan.append((start, story.mood if i == 1 else _second_mood(story.mood)))
        t = start + sum(s.frames for s in sec_shots) / FPS
    # leftovers of the story's own pictures go to the closing card background
    if end_card > 0:
        hero = (got.his_photos or [Path(shots[0].src)])[0]
        shots.append(Shot("photo", hero, int(end_card * FPS), style="blur", xfade_in=18, dip_out=20))
        ov.add(t + 0.5, t + end_card - 0.5, "Small", rf"{{\an5\pos({LW // 2},{470})\fad(600,600)}}SUBSCRIBE FOR MORE ROYAL STORIES", 3)
        ov.add(t + 0.8, t + end_card - 0.5, "Big", rf"{{\an5\pos({LW // 2},{560})\fad(600,600)}}THE LUXURY LANE", 3)
        music_plan.append((t, "elegant"))
    else:                                    # the story simply ends: a short fade after the last words
        shots[-1].frames += int(1.2 * FPS)
        shots[-1].dip_out = 24
    duration = sum(s.frames for s in shots) / FPS

    mark = cfg["channel"].get("watermark", "")
    if mark:
        ov.add(0, duration, "Mark", rf"{{\an9\pos({LW - 60},{44})}}{_esc(mark.upper())}", 1)
    for a2, b2, text in pops:
        ov.ass_events.append(pop_event(a2, b2, text, _esc))
    ass = work / "captions.ass"
    header = ASS_HEADER.replace("\n[Events]", "\n" + POP_STYLE + "\n\n[Events]", 1)
    ass.write_text(header + "\n".join(ov.ass_events) + "\n", encoding="utf-8")
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
        if got.thumb_scene:
            from .thumbnail import make_scene_thumbnail
            thumb = make_scene_thumbnail(got.thumb_scene, story.thumbnail.get("text", story.title),
                                         story.thumbnail.get("tag", ""), work / "thumbnail.jpg", seed,
                                         story.thumbnail.get("scene_crop"))
        else:
            thumb = make_thumbnail(got.his_photos, story.thumbnail.get("text", story.title),
                                   story.thumbnail.get("tag", ""), work / "thumbnail.jpg", seed, inset=inset)
    except Exception as e:  # noqa: BLE001 — never lose the episode over a thumbnail
        log.warning("story thumbnail failed: %s", e)
    return EpisodeResult(out, thumb, duration, story.title, story.description, story.tags, [], music_used)


def _second_mood(mood: str) -> str:
    return {"elegant": "epic", "epic": "elegant", "arabian": "elegant", "modern": "epic"}.get(mood, "elegant")


def auto_story(brain, studio, events: dict, usage: dict, cursors: dict, today, cfg: dict,
               news=None) -> tuple[Story, object] | None:
    """A story written automatically about today's freshest topic (see lane/trends.py), or about a scored news story
    (lane/news.py) when `news` is given; None when nothing can be written."""
    from . import trends
    from .brain import write_story
    if news is not None:
        keys = [k.split(":", 1)[1] for k in moment_matches(news.label, events, limit=5)]
        keys += [k for k in sorted(events, key=lambda k: events[k].get("date", ""), reverse=True)
                 if events[k].get("video") and k not in keys][: 5 - len(keys)]
        topic = trends.Topic(key=f"news:{news.key}", label=news.label, events=keys[:5],
                             why=f"news {news.score}/10 from {', '.join(news.publishers[:4])}")
    else:
        topic = trends.pick(events, usage, cursors, today)
    if topic is None:
        return None
    sources, src_keys = [], []
    if news is not None:
        sources = [dict(s) for s in news.sources]
        src_keys = list(topic.events)
    for k in ([] if news is not None else sorted(topic.events, key=lambda k: not events[k].get("video"))[:5]):   # footage first
        ev = events[k]
        src_keys.append(k)
        if ev.get("news_id"):
            brief, text = studio.article_text(ev)
        else:                                    # his own Instagram video: what it shows and his caption
            brief, text = "", f"The video shows: {ev.get('about', '')}. His caption: {ev.get('caption', '')}"
        sources.append({"title": ev.get("title", ""), "date": ev.get("date", "")[:10], "text": text or brief})
    if not any(s["text"] for s in sources):
        trends.mark_used(cursors, topic, today)   # nothing to write from; try another topic next time
        return None
    facts = studio.facts(None, 8, random.Random(today.toordinal()))
    d = write_story(brain, topic.label, sources, facts)
    if news is not None:
        d["description"] = (d["description"] + "\n\nNews reports used: " + ", ".join(news.publishers[:6]))[:4000]
    # footage first: each part shows the official event its facts come from (the one being told), then the
    # rest of the story's own events; never generic footage or outside photos
    def source_of(fact: str) -> str:
        w = set(re.findall(r"[a-z]{4,}", fact.lower()))
        best = max(range(len(sources)), key=lambda j: len(w & set(re.findall(r"[a-z]{4,}", (
            sources[j]["title"] + " " + sources[j]["text"]).lower()))))
        return src_keys[best % len(src_keys)]
    sections = []
    for i, text in enumerate(d["parts"]):
        mine = list(dict.fromkeys(source_of(f) for f in d.get("part_facts", [[]] * len(d["parts"]))[i] if f))
        mine = mine or [src_keys[i % len(src_keys)]]   # a short part borrows from the story's other events
        sections.append({"say": text, "show": [f"key:{k}" for k in mine]})
    sentence_beats(sections)
    story = Story(f"auto-{today.isoformat()}{'-news' if news is not None else ''}", d["title"], d["thumbnail"], _mood_for(topic.label),
                  d["description"], d["tags"], sections, Path("auto"))
    return story, topic


def _mood_for(label: str) -> str:
    low = label.lower()
    if re.search(r"horse|endurance|heritage|camel|falcon|poem|poetry", low):
        return "arabian"
    if re.search(r"\bai\b|technolog|future|space|digital|\bapps?\b", low):
        return "modern"
    if re.search(r"hospital|condolen|hero|child|famil|thank|visit", low):
        return "elegant"
    return "epic"
