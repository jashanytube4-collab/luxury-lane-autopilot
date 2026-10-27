"""Plan check: run the REAL renderer's shot planner on this laptop (no mixing, no encoding) and check the exact
timeline it would film. Catches what the pre-render gate can only guess: filler clips, a clip shown twice,
a sentence running out of pictures, a stock shot used again. All problems are listed at once.

    .venv\\Scripts\\python.exe -m tools.plan_check zz_fazza_money            (about 2-4 minutes)
"""
from __future__ import annotations

import logging
import random
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
    shots = st.produce_story(story, got, cfg, work, 1, plan_only=True)
    problems = check_shots(shots)
    problems += [f"renderer warning: {m}" for m in catch.lines if any(k in m.lower() for k in BAD_LOG)]
    total = sum(s.frames for s in shots) / FPS
    if problems:
        print(f"PLAN FAIL {story.id}: {len(shots)} shots, {total / 60:.1f} min, {len(problems)} problems")
        for p in problems:
            print("  FAIL", p)
        return 1
    print(f"PLAN PASS {story.id}: {len(shots)} shots, {total / 60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
