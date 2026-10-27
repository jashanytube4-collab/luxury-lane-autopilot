"""Plan check: run the REAL renderer's shot planner on this laptop (no mixing, no encoding) and check the exact
timeline it would film. Catches what the pre-render gate can only guess: filler clips, a clip shown twice,
a sentence running out of pictures, a stock shot used again. All problems are listed at once.

    .venv\\Scripts\\python.exe -m tools.plan_check zz_fazza_money            (about 2-4 minutes)
"""
from __future__ import annotations

import logging
import random
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lane import story as st  # noqa: E402
from lane.longform import FPS  # noqa: E402

BAD_LOG = ("own clip again", "ran out of new footage", "re-using", "reuse", "unknown clip", "not approved",
           "not in the footage library", "not in the library")
GAP = 2.0                 # a source coming back after another source = a repeat (s)


class _Catch(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _stub_studio() -> SimpleNamespace:
    return SimpleNamespace(hamdan=SimpleNamespace(library=None), video=lambda *a, **k: None,
                           photos=lambda *a, **k: [], his_photos=lambda p: [], his_scenes=lambda v, **k: [])


def check_shots(shots: list) -> list[str]:
    """Problems in a planned shot list (renderer Shot objects, in order)."""
    problems: list[str] = []
    t = 0.0
    seen: dict[str, tuple[float, float]] = {}      # source -> (last end, total seconds)
    prev = None
    segs = []
    for s in shots:
        d = s.frames / FPS
        segs.append((Path(str(s.src)).stem, s.kind, t, t + d))
        t += d
    for name, kind, a, b in segs:
        if name == prev:
            seen[name] = (b, seen[name][1] + (b - a))
            continue
        if name in seen and a - seen[name][0] > GAP - 1e-6:
            problems.append(f"repeat: {name} shown again at {a // 60:.0f}:{a % 60:04.1f} "
                            f"(last at {seen[name][0] // 60:.0f}:{seen[name][0] % 60:04.1f})")
        seen[name] = (b, seen.get(name, (0, 0))[1] + (b - a))
        prev = name
    for name, (_, tot) in seen.items():
        limit = 6.5 if name.startswith("sb-") or name.isdigit() else 9.0
        if tot > limit + 1.5:
            problems.append(f"{name} on screen {tot:.1f}s in total (max {limit:.0f}s)")
    photos = [(n, a) for n, k, a, _ in segs if k == "photo"]
    for (n1, a1), (n2, a2) in zip(photos, photos[1:]):
        if abs(a2 - a1) < 0.1:
            continue
    return problems


def _tok(src) -> str | None:
    """Picture token of a planned shot: sb-<id> for stock, clip:<id> for a library clip (photos have none)."""
    name = Path(str(src)).stem
    if Path(str(src)).suffix.lower() != ".mp4":
        return None
    return name if name.startswith("sb-") else f"clip:{name}"


def check_beats(shots: list, marks: list, story_sections: list, tags: dict) -> tuple[list[str], list[str]]:
    """Per sentence, on the REAL timeline: only its own pictures, each one fits the words, Hamdan lines show Hamdan,
    first 30 s, holds, the ending."""
    from tools.prerender_gate import HAM, STOP, aspect_ok, canon, fmt, relevance
    fails: list[str] = []
    warns: list[str] = []
    vocab: set = set()
    for t in tags.values():
        vocab |= {canon(x) for x in t.get("tags", [])}
    vocab -= STOP
    starts, t = [], 0.0
    for sh in shots:
        starts.append(t)
        t += sh.frames / FPS
    ends = [a + sh.frames / FPS for a, sh in zip(starts, shots)]
    for i, j, beat, lo, hi in marks:
        say = str(beat.get("say", ""))
        own = {str(x) for x in (beat.get("show") or []) if not str(x).startswith("card:")}
        ref = f'{fmt(starts[lo])} "{say[:60]}"' if lo < hi else f'"{say[:60]}"'
        hamdan_on = False
        for k in range(lo, hi):
            tok = _tok(shots[k].src)
            if tok is None:
                continue
            tg = tags.get(tok, {})
            if tok not in own:
                fails.append(f"[filler] {tok} at {fmt(starts[k])} is not one of this sentence's own pictures | {ref}")
                continue
            if not tg:
                fails.append(f"[untagged] {tok} has no tag entry | {ref}")
                continue
            r = relevance(tok, say, tg, vocab)
            if r == "uncertain":
                warns.append(f"uncertain fit {tok} | {ref}")
            elif r != "ok":
                fails.append(f"[relevance] {tok}: {r} | {ref}")
            if tg.get("not_hamdan") and re.search(r"(hamdan|fazza)", say, re.I):
                fails.append(f"[wrong person] {tok} is not Hamdan but the line is about him | {ref}")
            hamdan_on = hamdan_on or tg.get("hamdan", 0) >= 0.3
        if hi > lo and re.search(r"(hamdan|fazza)", say, re.I) and not hamdan_on:
            fails.append(f"[hamdan line without Hamdan] no clip of him while the line is about him | {ref}")
    # holds
    for k, sh in enumerate(shots):
        d = sh.frames / FPS
        tok = _tok(sh.src)
        if tok and d > (6.6 if tok.startswith("sb-") else 8.6):
            fails.append(f"[held too long] {tok} {d:.1f}s at {fmt(starts[k])}")
    # first 30 s: fast cuts, native 16:9 Hamdan clips
    first = [k for k in range(len(shots)) if starts[k] < 30]
    if len(first) < 13:
        fails.append(f"[first 30 s] only {len(first)} shots (need 13+)")
    for k in first:
        tok = _tok(shots[k].src)
        d = shots[k].frames / FPS
        if d > 3.4:
            fails.append(f"[first 30 s] shot held {d:.1f}s at {fmt(starts[k])} ({tok})")
        if tok is None:
            fails.append(f"[first 30 s] a photo at {fmt(starts[k])}")
        elif tags.get(tok, {}).get("hamdan", 0) < 0.3:
            fails.append(f"[first 30 s] {tok} at {fmt(starts[k])} is not a Hamdan clip")
        else:
            from tools.storyboard import find
            f = find(tok)
            bad = aspect_ok(f) if f else ""
            if bad:
                fails.append(f"[first 30 s] {tok} {bad}")
    # no photo right after a photo
    kinds = [sh.kind for sh in shots]
    for k in range(1, len(shots)):
        if kinds[k] == "photo" and kinds[k - 1] == "photo":
            fails.append(f"[two photos in a row] at {fmt(starts[k])}")
    # ending on Hamdan
    last = next((_tok(sh.src) for sh in reversed(shots) if _tok(sh.src)), None)
    if not last or tags.get(last, {}).get("hamdan", 0) < 0.3:
        fails.append(f"[ending] last picture {last} is not Hamdan")
    return fails, warns


def check_thumbnail(got, story, seed: int, work: Path) -> list[str]:
    """Build the thumbnail exactly like the render does, then check his face: found, big, whole, eyes not cut off."""
    out: list[str] = []
    try:
        import cv2
        from lane.faceid import FaceID
        from lane.thumbnail import make_scene_thumbnail
        if not got.thumb_scene:
            return ["[thumbnail] no scene frame for the thumbnail"]
        path = make_scene_thumbnail(got.thumb_scene, story.thumbnail.get("text", story.title),
                                    story.thumbnail.get("tag", ""), work / "thumbnail.jpg", seed,
                                    story.thumbnail.get("scene_crop"))
        img = cv2.imread(str(path))
        h, w = img.shape[:2]
        row = FaceID().locate(img)
        if row is None:      # detector needs a whole face: a very tight crop or a falcon over his mouth also fails
            return [f"[thumbnail] warning: his whole face is not detected (cropped very tight or covered?): LOOK at {path}"]
        x, y, fw, fh = (float(v) for v in row[:4])
        eyes_y = [float(row[5]), float(row[7])]
        if fh / h < 0.22:
            out.append(f"[thumbnail] his face is small ({fh / h:.0%} of the height; need 22%+)")
        if y < 0.02 * h or x < 0 or x + fw > w or y + fh > h:
            out.append("[thumbnail] his face touches or leaves the picture edge (head/eyes cut off)")
        if min(eyes_y) < 0.06 * h:
            out.append("[thumbnail] his eyes are too close to the top edge")
    except Exception as e:  # noqa: BLE001
        out.append(f"[thumbnail] could not be built/checked: {e}")
    return out


def main(arg: str) -> int:
    from tools.prerender_gate import story_path
    from lane.config import load_config  # type: ignore
    path = story_path(arg)
    if not path:
        print("story not found:", arg)
        return 2
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    story = next(s for s in st.bank() if s.id == str(data["id"]))
    cfg = load_config()
    work = ROOT / "work" / "plan_check" / story.id
    work.mkdir(parents=True, exist_ok=True)
    import shutil
    from tools.prerender_gate import shots_tokens
    from tools.storyboard import find
    (work / "clips").mkdir(parents=True, exist_ok=True)
    for tok in set(shots_tokens(data)):      # stock clips live in several local folders: put them where the renderer looks
        src = find(tok) if tok.startswith("sb-") else None
        if src and not (work / "clips" / f"{tok}.mp4").exists():
            shutil.copy(src, work / "clips" / f"{tok}.mp4")
    catch = _Catch()
    logging.getLogger().addHandler(catch)
    maker = st.StoryMaker(_stub_studio(), {}, {}, cfg)
    got = maker.gather(story, random.Random(1), work)
    shots, marks = st.produce_story(story, got, cfg, work, 1, plan_only=True)
    problems = check_shots(shots)
    tags = yaml.safe_load((ROOT / "assets" / "clip_tags.yaml").read_text(encoding="utf-8")) or {}
    f2, warns = check_beats(shots, marks, story.sections, tags)
    th = check_thumbnail(got, story, 1, work)
    warns += [x for x in th if "warning" in x]
    problems += f2 + [x for x in th if "warning" not in x]
    problems += [f"renderer warning: {m}" for m in catch.lines if any(k in m.lower() for k in BAD_LOG)]
    total = sum(s.frames for s in shots) / FPS
    if problems:
        print(f"PLAN FAIL {story.id}: {len(shots)} shots, {total / 60:.1f} min, {len(problems)} problems")
        for p in problems:
            print("  FAIL", p)
        for w in warns:
            print("  warn", w)
        return 1
    for w in warns:
        print("  warn", w)
    print(f"PLAN PASS {story.id}: {len(shots)} shots, {total / 60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
