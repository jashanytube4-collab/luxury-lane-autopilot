"""Daily long-form documentary episode (8-12 min, 1920x1080) built from several official events.

Structure: cold open (hook narration over a fast montage) -> title sequence -> 5-7 chapters (each: chapter card,
footage intercut with photos, lower third, narration written from the official article) -> outro + end card.
Everything is rendered here: camera moves, crossfades, dips, light leaks, colour grade, subtitles, music beds
with ducking, sound effects, and a thumbnail.
"""
from __future__ import annotations

import logging
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import audio, qa
from .captions import _esc, _ts
from .media import FFMPEG, iter_frames, probe, run, scene_cuts
from .render import Look, RenderError, blurred_backdrop, camera, ease, finish_encoder, paste_feathered, \
    start_encoder, window
from .voice import synthesize

log = logging.getLogger("lane.longform")

LW, LH = 1920, 1080
FPS = 30
GOLD = "&H0048C9F7&"

THEMES = {
    "Speed, Sport and Endurance": ["endurance", "race", "horse", "equestrian", "marathon", "cycl", "fitness", "sport",
                                   "football", "camel", "falcon", "hunting", "polo", "swim", "ride", "champion"],
    "Wings, Rails and the Road Ahead": ["airport", "air taxi", "rail", "metro", "aviation", "airline", "emirates",
                                        "transport", "road", "loop", "tunnel", "autonomous", "mobility", "port"],
    "Space, Science and the Future": ["space", "satellite", "asteroid", "mars", "moon", "artificial intelligence",
                                      " ai ", "technology", "future", "innovation", "digital", "robot", "gaming",
                                      "science", "research"],
    "Leading Dubai": ["executive council", "approves", "strategy", "budget", "directs", "decree", "law", "plan",
                      "government", "agenda", "policy"],
    "Dubai and the World": ["receives", "meets", "president", "king", "prime minister", "emir", "delegation",
                            "summit", "visit to", "arrives in", "official visit", "minister"],
    "Heritage, Culture and Poetry": ["heritage", "culture", "poetry", "poem", "ramadan", "eid", "festival", "hatta",
                                     "national day", "flag", "art", "museum", "majlis"],
    "For the People of Dubai": ["students", "graduat", "youth", "health", "hospital", "education", "school",
                                "citizens", "emirati", "housing", "volunteer", "family", "community", "care"],
    "Building the Impossible": ["project", "jebel ali", "expo", "trade", "market", "investment", "economy",
                                "billion", "development", "tower", "district", "city", "infrastructure"],
}
THEME_MOOD = {"Speed, Sport and Endurance": "arabian", "Wings, Rails and the Road Ahead": "modern",
              "Space, Science and the Future": "modern", "Leading Dubai": "epic", "Dubai and the World": "epic",
              "Heritage, Culture and Poetry": "arabian", "For the People of Dubai": "elegant",
              "Building the Impossible": "epic"}


class LongformError(Exception):
    pass


# ---- episode selection ----------------------------------------------------------------------------------------
def choose_episode(events: dict[str, dict], used_long: set[str], day_index: int, chapters: int = 6
                   ) -> tuple[str, list[str]]:
    """Pick today's theme (rotating) and the events for it: never reused in another episode, at least half with
    video when possible, ordered chronologically so the episode tells a story."""
    pool = {k: e for k, e in events.items() if k not in used_long and e.get("news_id")
            and len(e.get("photos", [])) >= (2 if e.get("video") else 4)}
    names = list(THEMES)
    for shift in range(len(names) + 1):
        if shift < len(names):
            theme = names[(day_index + shift) % len(names)]
            kws = THEMES[theme]
            cands = [k for k, e in pool.items() if any(w in f" {e['title'].lower()} " for w in kws)]
        else:
            theme = "A Week in the Life of the Crown Prince"
            cands = list(pool)
        with_video = sorted((k for k in cands if pool[k].get("video")), key=lambda k: pool[k]["date"], reverse=True)
        photos_only = sorted((k for k in cands if not pool[k].get("video")), key=lambda k: pool[k]["date"],
                             reverse=True)
        if len(with_video) + len(photos_only) < chapters:
            continue
        n_video = min(len(with_video), max(chapters // 2, chapters - len(photos_only)))
        rng = random.Random(day_index)
        chosen = with_video[:n_video * 2]
        rng.shuffle(chosen)
        chosen = chosen[:n_video] + photos_only[:chapters - n_video]
        chosen.sort(key=lambda k: pool[k]["date"])
        return theme, chosen
    raise LongformError("not enough unused events for an episode")


# ---- timeline -----------------------------------------------------------------------------------------------
@dataclass
class Shot:
    kind: str                  # video | photo
    src: Path
    frames: int
    start: float = 0.0         # video only
    focus_x: float = 0.5
    move: str = "push"
    strength: float = 0.7
    xfade_in: int = 0          # frames blended with the previous shot
    dip_in: int = 0            # fade up from black
    dip_out: int = 0           # fade down to black
    flash_in: int = 0
    style: str = "normal"      # normal | blur (title/end-card background)
    dims: list = field(default_factory=list)      # [(from_frame, to_frame, amount)] darkening for title cards
    leak_at: int = -1          # frame index (within shot) where a light leak starts


@dataclass
class Visuals:
    video: Path | None
    scenes: list[tuple[float, float]]
    photos: list[Path]


def _scene_list(video: Path) -> list[tuple[float, float]]:
    info = probe(video)
    cuts = scene_cuts(video, info["fps"])
    edges = [0.0] + [c for c in cuts if 0.4 < c < info["duration"] - 0.4] + [info["duration"]]
    scenes = []
    for a, b in zip(edges, edges[1:]):
        a, b = a + 0.12, b - 0.12
        if b - a < 1.6:
            continue
        n = max(1, int((b - a) // 6.0))
        step = (b - a) / n
        scenes += [(a + i * step, a + (i + 1) * step) for i in range(n)]
    return scenes


def _chapter_shots(vis: Visuals, seconds: float, rng: random.Random) -> list[Shot]:
    """Fill `seconds` with footage scenes intercut with photos (documentary rhythm, no immediate repeats)."""
    shots: list[Shot] = []
    moves = ["push", "pull", "drift_l", "drift_r"]
    scenes = list(vis.scenes)
    photos = list(vis.photos)
    si = pi = 0
    total = 0.0
    pattern = (["v", "v", "p"] if scenes and photos else ["v"] if scenes else ["p"])
    k = 0
    while total < seconds - 0.2:
        kind = pattern[k % len(pattern)]
        k += 1
        if kind == "v":
            a, b = scenes[si % len(scenes)]
            si += 1
            dur = min(b - a, rng.uniform(3.6, 5.8))
            shot = Shot("video", vis.video, int(dur * FPS), start=a, move=rng.choice(moves), strength=0.55)
        else:
            dur = rng.uniform(4.4, 5.6)
            shot = Shot("photo", photos[pi % len(photos)], int(dur * FPS), move=rng.choice(moves), strength=0.9)
            pi += 1
        if shots:
            shot.xfade_in = 12 if (kind == "p" or shots[-1].kind == "photo" or rng.random() < 0.35) else 0
        shots.append(shot)
        total += shot.frames / FPS
    overshoot = int((total - seconds) * FPS)
    if overshoot > 0 and shots[-1].frames - overshoot > FPS:
        shots[-1].frames -= overshoot
    return shots


# ---- frame production ----------------------------------------------------------------------------------------
def _layout(w: int, h: int) -> str:
    return "fill" if w / h >= 1.45 else "pillar"


def _work_dims(w: int, h: int) -> tuple[int, int]:
    if _layout(w, h) == "fill":
        if w / h >= LW / LH:
            return int(round(LH * w / h / 2)) * 2, LH
        return LW, int(round(LW * h / w / 2)) * 2
    return int(round(LH * w / h / 2)) * 2, LH


def _source_frames(shot: Shot, n: int):
    """Yield n working frames (uncomposed) for a shot."""
    if shot.kind == "photo":
        img = cv2.imread(str(shot.src), cv2.IMREAD_COLOR)
        if img is None:
            raise RenderError(f"cannot read {shot.src}")
        h, w = img.shape[:2]
        dw, dh = _work_dims(w, h)
        img = cv2.resize(img, (dw, dh), interpolation=cv2.INTER_AREA if dw < w else cv2.INTER_CUBIC)
        for _ in range(n):
            yield img
        return
    info = probe(shot.src)
    dw, dh = _work_dims(info["width"], info["height"])
    last, count = None, 0
    for f in iter_frames(shot.src, dw, dh, start=shot.start, duration=n / FPS + 0.3,
                         vf_prefix=f"fps={FPS},scale={dw}:{dh}:flags=lanczos,"):
        if count >= n:
            break
        last = f
        count += 1
        yield f
    if last is None:
        raise RenderError(f"could not decode {shot.src} at {shot.start:.1f}s")
    while count < n:
        count += 1
        yield last


def _compose(src: np.ndarray, shot: Shot, p: float, gi: int) -> np.ndarray:
    zoom, drift = camera(shot.move, p, shot.strength)
    sh, sw = src.shape[:2]
    if shot.style == "blur":
        small = cv2.resize(src, (192, int(192 * sh / sw)), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), 4)
        img = window(small, LW, LH, 1.05 + 0.05 * p, small.shape[1] / 2, small.shape[0] / 2)
        return cv2.convertScaleAbs(img, alpha=0.38, beta=0)
    if _layout(sw, sh) == "fill":
        return window(src, LW, LH, zoom, sw / 2 + drift * LW, sh / 2)
    bg = blurred_backdrop(src, LW, LH, shot.focus_x, 1.08 + 0.03 * p, darken=0.45)
    fg = window(src, sw, sh, zoom, sw / 2 + drift * sw, sh / 2)
    return paste_feathered(bg, fg, (LW - sw) // 2, 0, feather=36)


def _leak(t: float, seed: int) -> np.ndarray:
    """Warm light-leak layer (BGR uint8) for progress t in [0,1]."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:54, 0:96].astype(np.float32)
    out = np.zeros((54, 96, 3), np.float32)
    for k in range(2):
        cx = (-20 + 136 * t + rng.uniform(-10, 10)) if k == 0 else (110 - 120 * t)
        cy = rng.uniform(5, 50)
        r = rng.uniform(18, 30)
        g = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))
        col = np.array([40, 140, 255] if k == 0 else [90, 190, 255], np.float32)
        out += g[:, :, None] * col
    env = math.sin(math.pi * t)
    return np.clip(cv2.resize(out * env, (LW, LH), interpolation=cv2.INTER_LINEAR), 0, 255).astype(np.uint8)


def render_timeline(shots: list[Shot], ass: Path, mix: Path, out: Path, work: Path, seed: int) -> None:
    total = sum(s.frames for s in shots)
    enc, errlog = start_encoder(work, ass, mix, out, (LW, LH), FPS, total / FPS, crf=20)
    look = Look("wide", seed, (LW, LH))
    gi = 0
    tail: list[np.ndarray] = []     # extra frames of the previous shot, blended into the next shot's start
    try:
        for si, shot in enumerate(shots):
            nxt = shots[si + 1] if si + 1 < len(shots) else None
            extra = nxt.xfade_in if nxt else 0
            n = shot.frames + extra
            new_tail = []
            for fi, frame in enumerate(_source_frames(shot, n)):
                img = _compose(frame, shot, fi / max(1, n - 1), gi)
                if fi >= shot.frames:          # overlap frames for the next crossfade
                    new_tail.append(img)
                    continue
                if fi < shot.xfade_in and tail:
                    a = ease((fi + 1) / (shot.xfade_in + 1))
                    img = cv2.addWeighted(tail[min(fi, len(tail) - 1)], 1 - a, img, a, 0)
                for a0, a1, amt in shot.dims:
                    if a0 <= fi < a1:
                        ramp = max(0.0, min(1.0, (fi - a0) / 9, (a1 - fi) / 12))
                        small = cv2.resize(img, (LW // 8, LH // 8), interpolation=cv2.INTER_AREA)
                        soft = cv2.resize(cv2.GaussianBlur(small, (0, 0), 2.2), (LW, LH),
                                          interpolation=cv2.INTER_LINEAR)
                        img = cv2.addWeighted(img, 1 - 0.75 * ramp, soft, 0.75 * ramp, 0)
                        img = cv2.convertScaleAbs(img, alpha=1 - amt * ramp, beta=0)
                if shot.leak_at >= 0 and shot.leak_at <= fi < shot.leak_at + 36:
                    img = cv2.addWeighted(img, 1.0, _leak((fi - shot.leak_at) / 36, seed + si), 0.38, 0)
                img = look.apply(img, gi)
                fade = 1.0
                if shot.dip_in and fi < shot.dip_in:
                    fade = min(fade, (fi + 1) / shot.dip_in)
                left = shot.frames - 1 - fi
                if shot.dip_out and left < shot.dip_out:
                    fade = min(fade, left / shot.dip_out)
                if fade < 1.0:
                    img = cv2.convertScaleAbs(img, alpha=ease(max(0.0, fade)), beta=0)
                if shot.flash_in and fi < shot.flash_in:
                    f = 0.7 * (1 - fi / shot.flash_in)
                    img = cv2.addWeighted(img, 1 - f, np.full_like(img, 255), f, 0)
                enc.stdin.write(np.ascontiguousarray(img).tobytes())
                gi += 1
            tail = new_tail
    except (BrokenPipeError, OSError) as e:
        enc.kill()
        errlog.close()
        raise RenderError(f"encoder stopped: {(work / 'ffmpeg_encode.log').read_text(errors='ignore')[-800:]} {e}")
    finish_encoder(enc, errlog, work)


# ---- titles and subtitles (ASS) ---------------------------------------------------------------------------------
ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {LW}
PlayResY: {LH}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Montserrat ExtraBold,46,&H00F2F2F2,&H00F2F2F2,&H00101010,&H96000000,0,0,0,0,100,100,0.5,0,1,2.6,1.6,2,240,240,58,1
Style: Big,Cinzel,104,&H0048C9F7,&H0048C9F7,&H00140E08,&H90000000,-1,0,0,0,100,100,4,0,1,3,5,5,140,140,0,1
Style: Small,Cinzel,34,&H00E8E8E8,&H00E8E8E8,&H00000000,&H80000000,-1,0,0,0,100,100,12,0,1,1.2,2,5,0,0,0,1
Style: LT1,Cinzel,30,&H0048C9F7,&H0048C9F7,&H00000000,&H80000000,-1,0,0,0,100,100,6,0,1,1.2,2,7,0,0,0,1
Style: LT2,Montserrat ExtraBold,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0.5,0,1,2,2,7,0,0,0,1
Style: Plate,Cinzel,10,&H00000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Bar,Cinzel,10,&H0048C9F7,&H0048C9F7,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Mark,Cinzel,26,&H70FFFFFF,&H70FFFFFF,&HB0000000,&HB0000000,-1,0,0,0,100,100,6,0,1,1.2,0,9,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _wrap(text: str, width: int) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return r"\N".join(lines)


def _punctuate(words: list[dict], script: str) -> list[dict]:
    """Give spoken words back their punctuation from the script (voice timings come without it)."""
    tokens = script.split()
    norm = lambda s: re.sub(r"[^\w]", "", s.lower())  # noqa: E731
    out, j = [], 0
    for w in words:
        txt = w["text"]
        for k in range(j, min(j + 4, len(tokens))):
            if norm(tokens[k]) == norm(w["text"]):
                txt, j = tokens[k], k + 1
                break
        out.append({**w, "text": txt})
    return out


def _subtitle_lines(words: list[dict], offset: float, max_chars: int = 44) -> list[tuple[float, float, str]]:
    lines, cur, start = [], [], None
    for i, w in enumerate(words):
        if start is None:
            start = w["start"]
        cur.append(w)
        text = " ".join(x["text"] for x in cur)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 9
        sentence_end = text.endswith((".", "!", "?"))
        if len(text) >= max_chars or gap > 0.45 or sentence_end or nxt is None:
            lines.append((offset + start, offset + w["end"] + 0.25, text))
            cur, start = [], None
    # never overlap: each line ends when the next begins
    fixed = []
    for j, (a, b, t) in enumerate(lines):
        if j + 1 < len(lines):
            b = min(b, lines[j + 1][0] - 0.02)
        fixed.append((a, b, t))
    return fixed


@dataclass
class Overlay:
    ass_events: list[str] = field(default_factory=list)

    def add(self, a: float, b: float, style: str, text: str, layer: int = 1) -> None:
        if b - a > 0.02:
            self.ass_events.append(f"Dialogue: {layer},{_ts(a)},{_ts(b)},{style},,0,0,0,,{text}")


def _title_block(ov: Overlay, a: float, b: float, small: str, big: str, small_y: int, big_y: int) -> None:
    ov.add(a, b, "Small", rf"{{\an5\pos({LW // 2},{small_y})\fad(500,500)\fsp18\t(0,1600,\fsp12)}}{_esc(small.upper())}", 3)
    ov.add(a + 0.25, b, "Big", rf"{{\an5\pos({LW // 2},{big_y})\fad(600,500)\fscx108\fscy108\t(0,1400,0.6,\fscx100\fscy100)"
                             rf"\blur6\t(0,700,\blur0)}}{_wrap(_esc(big.upper()), 24)}", 3)
    lines = _wrap(big.upper(), 24).count(r"\N") + 1
    bar_y = big_y + lines * 62 + 26
    ov.add(a + 0.6, b, "Bar", rf"{{\an5\pos({LW // 2},{bar_y})\fad(0,500)\fscx0\t(0,700,0.5,\fscx100)\p1}}"
                              r"m -160 0 l 160 0 l 160 4 l -160 4{\p0}", 3)


def _short_title(title: str, limit: int = 58) -> str:
    if len(title) <= limit:
        return title
    return title[:limit].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


def _lower_third(ov: Overlay, a: float, place: str, title: str) -> None:
    b = a + 6.0
    x, y = 120, 742
    title = _short_title(title)
    plate_w = 70 + 21 * max(len(title), len(place) * 1.3)
    ov.add(a, b, "Plate", rf"{{\an7\pos({x - 24},{y - 20})\fad(300,500)\1a&H78&\blur16\p1}}"
                          rf"m 0 0 l {plate_w:.0f} 0 l {plate_w:.0f} 152 l 0 152{{\p0}}", 3)
    ov.add(a, b, "Bar", rf"{{\an7\pos({x},{y})\fad(200,400)\fscx0\t(0,450,0.5,\fscx100)\p1}}m 0 0 l 6 0 l 6 112 l 0 112{{\p0}}", 4)
    ov.add(a + 0.2, b, "LT1", rf"{{\an7\move({x - 30},{y + 4},{x + 26},{y + 4},0,450)\fad(250,400)}}{_esc(place.upper())}", 4)
    ov.add(a + 0.35, b, "LT2", rf"{{\an7\move({x - 30},{y + 50},{x + 26},{y + 50},0,500)\fad(250,400)}}"
                               rf"{_esc(title)}", 4)


# ---- the episode --------------------------------------------------------------------------------------------
@dataclass
class EpisodeResult:
    path: Path
    thumbnail: Path | None
    duration: float
    title: str
    description: str
    tags: list[str]
    chapters: list[tuple[float, str]]
    music: list[str]


def produce_episode(*, theme: str, chapters_in: list[dict], package: dict, cfg: dict, work: Path, seed: int
                    ) -> EpisodeResult:
    """chapters_in: [{"event_title", "date", "chapter_title", "place_line", "narration", "visuals": Visuals}]."""
    rng = random.Random(seed)
    work.mkdir(parents=True, exist_ok=True)
    vcfg = dict(cfg["voice"])
    vcfg["edge_rate"] = cfg.get("longform", {}).get("voice_rate", "-2%")

    # 1) narration
    hook = synthesize(package["hook"], vcfg, work / "v_hook.wav")
    chap_voice = [synthesize(c["narration"], vcfg, work / f"v_ch{i}.wav") for i, c in enumerate(chapters_in)]
    outro = synthesize(package["outro"], vcfg, work / "v_outro.wav")

    # 2) timeline (seconds)
    all_vis = [c["visuals"] for c in chapters_in]
    shots: list[Shot] = []
    voice_events: list[tuple[float, Path]] = []
    sfx: list[tuple[float, np.ndarray, float]] = []
    sub_events: list[tuple[float, list[dict]]] = []
    ov = Overlay()
    chapter_marks: list[tuple[float, str]] = []
    music_plan: list[tuple[float, str]] = []
    t = 0.0

    # cold open: fast montage of the strongest moments across chapters
    cold = hook.duration + 1.2
    montage = []
    for v in all_vis:
        if v.video and v.scenes:
            a, b = v.scenes[min(1, len(v.scenes) - 1)]
            montage.append(("video", v.video, a))
        for p in v.photos[:1]:
            montage.append(("photo", p, 0.0))
    rng.shuffle(montage)
    per = 2.4
    k = 0
    while sum(s.frames for s in shots) / FPS < cold - 0.1:
        kind, src, a = montage[k % len(montage)]
        k += 1
        s = Shot(kind, src, int(per * FPS), start=a, move=rng.choice(["push", "pull"]), strength=1.0)
        s.flash_in = 4 if k > 1 and k % 2 == 0 else 0
        shots.append(s)
    cold_frames = int(cold * FPS)
    over = sum(s.frames for s in shots) - cold_frames
    shots[-1].frames = max(FPS, shots[-1].frames - over)
    shots[0].dip_in = 8
    voice_events.append((0.35, hook.wav))
    sub_events.append((0.35, _punctuate(hook.words, package["hook"])))
    sfx.append((0.0, audio.impact(seed), -12.0))
    music_plan.append((0.0, "epic"))
    t = sum(s.frames for s in shots) / FPS

    # title sequence
    title_len = 5.5
    hero = next((v.photos[0] for v in all_vis if v.photos), None) or montage[0][1]
    title_shot = Shot("photo" if hero.suffix.lower() in (".jpg", ".jpeg", ".png") else "video", hero,
                      int(title_len * FPS), style="blur", flash_in=6, dip_out=10)
    shots.append(title_shot)
    _title_block(ov, t + 0.3, t + title_len - 0.3, "The Luxury Lane presents", package["episode_title"], 430, 540)
    sfx.append((t - 0.05, audio.impact(seed + 1), -8.0))
    t += title_len

    # chapters — if the narration came out short, add footage "breathers" so the episode still runs 8+ minutes
    card = 3.6
    lf = cfg.get("longform", {})
    planned = (t + title_len + sum(card - 0.6 + v.duration + 1.6 for v in chap_voice) + outro.duration + 1.4
               + 9.0)
    want = max(float(lf.get("min_minutes", 7.5)) * 60 + 20, float(lf.get("target_minutes", 9.5)) * 60 - 30)
    pad = min(24.0, max(0.0, (want - planned) / max(1, len(chap_voice))))
    if pad:
        log.info("narration is short; adding %.1fs of footage per chapter", pad)
    for i, (c, voice) in enumerate(zip(chapters_in, chap_voice)):
        start = t
        body = card - 0.6 + voice.duration + 1.6 + pad
        vis = c["visuals"]
        card_len = card + 0.9
        card_shot = Shot("photo", vis.photos[0], int(card_len * FPS), move="push", strength=0.8, dip_in=12,
                         dims=[(0, int(card * FPS), 0.6)], leak_at=2)
        rest = Visuals(vis.video, vis.scenes, vis.photos[1:] + vis.photos[:1])
        cshots = [card_shot] + _chapter_shots(rest, body - card_len, rng)
        cshots[1].xfade_in = 14
        cshots[-1].dip_out = 12
        shots += cshots
        _title_block(ov, start + 0.35, start + card - 0.15, f"Chapter {i + 1:02d}", c["chapter_title"], 440, 540)
        sfx.append((start - 0.25, audio.whoosh(seed + 10 + i, 0.6), -12.0))
        voice_events.append((start + card - 0.6, voice.wav))
        sub_events.append((start + card - 0.6, _punctuate(voice.words, c["narration"])))
        _lower_third(ov, start + card + 0.8, c["place_line"] or c["date"], c["event_title"])
        chapter_marks.append((start, c["chapter_title"]))
        if i == 0 or i == len(chapters_in) // 2:
            music_plan.append((start, c.get("mood") or "epic"))
        t = start + sum(s.frames for s in cshots) / FPS

    # outro + end card
    end_card = 9.0
    oshots = []
    photos = [p for v in all_vis for p in v.photos] or [hero]
    o_len = outro.duration + 1.4
    k = 0
    while sum(s.frames for s in oshots) / FPS < o_len - 0.1:
        s = Shot("photo", photos[(k * 3) % len(photos)], int(4.6 * FPS), move=rng.choice(["push", "drift_l", "drift_r"]),
                 strength=0.9, xfade_in=14 if k else 0)
        oshots.append(s)
        k += 1
    oshots[0].dip_in = 12
    voice_events.append((t + 0.4, outro.wav))
    sub_events.append((t + 0.4, _punctuate(outro.words, package["outro"])))
    music_plan.append((t, "elegant"))
    shots += oshots
    t += sum(s.frames for s in oshots) / FPS
    end = Shot("photo", hero, int(end_card * FPS), style="blur", xfade_in=18, dip_out=20)
    shots.append(end)
    ov.add(t + 0.5, t + end_card - 0.5, "Small", rf"{{\an5\pos({LW // 2},{470})\fad(600,600)}}MORE STORIES OF FAZZA", 3)
    ov.add(t + 0.8, t + end_card - 0.5, "Big", rf"{{\an5\pos({LW // 2},{560})\fad(600,600)}}THE LUXURY LANE", 3)
    t += end_card
    duration = sum(s.frames for s in shots) / FPS

    # 3) overlays: subtitles + brand mark
    for off, words in sub_events:
        for a, b, text in _subtitle_lines(words, off):
            ov.add(a, b, "Sub", rf"{{\fad(80,80)}}{_esc(text)}", 2)
    mark = cfg["channel"].get("watermark", "")
    if mark:
        ov.add(0, duration, "Mark", rf"{{\an9\pos({LW - 60},{44})}}{_esc(mark.upper())}", 1)
    ass = work / "captions.ass"
    ass.write_text(ASS_HEADER + "\n".join(ov.ass_events) + "\n", encoding="utf-8")

    # 4) sound: voice track, music beds (crossfaded by section), effects, ducking, loudness
    music_used = _mix_long(work, duration, voice_events, sfx, music_plan, cfg, seed)

    # 5) picture
    out = work / "episode.mp4"
    render_timeline(shots, ass, work / "mix.wav", out, work, seed)
    problems = qa.check_long(out, min(6.0, float(lf.get("min_minutes", 7.5))) * 60,
                             float(lf.get("max_minutes", 13)) * 60)
    if problems:
        raise LongformError("quality check failed: " + "; ".join(problems))

    thumb = None
    try:
        thumb = make_thumbnail(hero, package.get("thumbnail_text") or package["episode_title"], work)
    except Exception as e:  # noqa: BLE001 — a missing thumbnail must not lose the episode
        log.warning("thumbnail failed: %s", e)
    return EpisodeResult(out, thumb, duration, package["youtube_title"], package["description"], package["tags"],
                         chapter_marks, music_used)


def _music_section(length: float, mood: str, seed: int) -> tuple[np.ndarray, list[str]]:
    """`length` seconds of music for one section: tracks of the right mood chained with 3 s crossfades,
    each loudness-matched, so a long section never falls back to the synth pad."""
    sr = audio.SR
    rng = random.Random(seed)
    credits = audio.music_credits()
    tracks = [p for p in audio.MUSIC_DIR.glob("*.mp3") if credits.get(p.name, {}).get("mood") == mood] \
        or list(audio.MUSIC_DIR.glob("*.mp3"))
    if not tracks:
        return audio.ambient_pad(length, seed), ["generated-pad"]
    rng.shuffle(tracks)
    need, out, names, xf = int(length * sr), np.zeros(0, np.float32), [], int(3 * sr)
    k = 0
    while len(out) < need and k < 12:
        tr = tracks[k % len(tracks)]
        k += 1
        sig = audio.load(tr)
        if len(sig) < sr * 20:
            continue
        sig = sig / (np.sqrt(np.mean(sig ** 2)) + 1e-9) * 0.1
        names.append(tr.name)
        if not len(out):
            start = rng.randint(0, max(0, len(sig) - need)) if len(sig) > need else 0
            out = sig[start:]
            continue
        ramp = np.linspace(0, 1, xf, dtype=np.float32)
        out[-xf:] = out[-xf:] * (1 - ramp) + sig[:xf] * ramp
        out = np.concatenate([out, sig[xf:]])
    if len(out) < need:
        out = np.pad(out, (0, need - len(out)))
    return out[:need], names


def _mix_long(work: Path, duration: float, voice_events, sfx, music_plan, cfg: dict, seed: int) -> list[str]:
    sr = audio.SR
    n = int(duration * sr)
    voice = np.zeros(n, np.float32)
    for at, wav in voice_events:
        polished = wav.with_name(wav.stem + "_p.wav")
        audio.polish_voice(wav, polished)
        sig = audio.load(polished)
        i = int(at * sr)
        seg = sig[: max(0, n - i)]
        voice[i:i + len(seg)] += seg
    music = np.zeros(n, np.float32)
    used: list[str] = []
    bounds = [t for t, _ in music_plan] + [duration]
    xf = int(2.0 * sr)
    for j, (a, mood) in enumerate(music_plan):
        b = bounds[j + 1]
        length = b - a + 2.0
        sig, names = _music_section(length, mood, seed + 31 * j)
        used += names
        i0 = int(a * sr)
        seg = sig[: max(0, min(len(sig), n - i0))].copy()
        if j > 0:
            seg[:xf] *= np.linspace(0, 1, min(xf, len(seg)))[: len(seg[:xf])]
        tail = int((b - a) * sr)
        if tail < len(seg):
            fade = seg[tail:tail + xf]
            seg[tail:tail + len(fade)] *= np.linspace(1, 0, len(fade))
            seg[tail + len(fade):] = 0
        music[i0:i0 + len(seg)] += seg
    mcfg = cfg.get("music", {})
    music *= audio._duck_gain(voice, mcfg.get("volume_db", -21) + 20 - 2, mcfg.get("duck_db", -9) - 2)
    fade_out = int(3 * sr)
    music[-fade_out:] *= np.linspace(1, 0, fade_out)
    fx = np.zeros(n, np.float32)
    for at, sig, gain_db in sfx:
        i = int(max(0.0, at) * sr)
        seg = sig[: max(0, n - i)]
        fx[i:i + len(seg)] += seg * audio.db(gain_db)
    total = voice + music + fx
    raw = work / "mix_raw.wav"
    audio.save(total / max(1.0, float(np.abs(total).max())), raw)
    audio.loudnorm(raw, work / "mix.wav")
    return used


def make_thumbnail(photo: Path, text: str, work: Path) -> Path:
    """1280x720: graded hero photo, dark gradient, big two-line title (gold last word), brand tag."""
    img = cv2.imread(str(photo), cv2.IMREAD_COLOR)
    if img is None:
        raise RenderError("thumbnail photo unreadable")
    h, w = img.shape[:2]
    scale = max(1280 / w, 720 / h)
    img = cv2.resize(img, (int(w * scale) + 1, int(h * scale) + 1), interpolation=cv2.INTER_AREA)
    y0 = max(0, (img.shape[0] - 720) // 3)
    x0 = max(0, (img.shape[1] - 1280) // 2)
    img = img[y0:y0 + 720, x0:x0 + 1280]
    from .render import grade_lut
    img = cv2.LUT(img, grade_lut())
    xx = np.linspace(0, 1, 1280, dtype=np.float32)[None, :, None]
    grad = np.clip(1.0 - 0.78 * (1 - xx) ** 1.6, 0.22, 1.0)
    img = (img.astype(np.float32) * grad).astype(np.uint8)
    base = work / "thumb_base.png"
    cv2.imwrite(str(base), img)
    words = re.sub(r"\s+", " ", text.upper()).strip().split()[:5]
    first, last = " ".join(words[:-1]), words[-1] if words else ""
    lines = _wrap(first, 12) if first else ""
    body = (lines + (r"\N" if lines else "") + rf"{{\c{GOLD}}}{_esc(last)}") if last else lines
    ass = work / "thumb.ass"
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: T,Montserrat Black,104,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,0,0,0,0,100,100,1,0,1,6,6,4,70,40,70,1
Style: B,Cinzel,30,&H0048C9F7,&H0048C9F7,&H00000000,&H80000000,-1,0,0,0,100,100,8,0,1,1.5,2,7,70,40,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:05.00,B,,0,0,0,,THE LUXURY LANE
Dialogue: 0,0:00:00.00,0:00:05.00,T,,0,0,0,,{body}
""", encoding="utf-8")
    fonts = work / "fonts"
    out = work / "thumbnail.jpg"
    run([FFMPEG, "-y", "-v", "error", "-i", base.name, "-vf", f"ass={ass.name}:fontsdir={fonts.name}",
         "-frames:v", "1", "-q:v", "2", out.name], cwd=work)
    return out
