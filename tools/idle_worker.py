"""Laptop idle worker: keeps useful NO-CLAUDE work flowing so the laptop and GitHub never sit idle.
python -m tools.idle_worker      (called by 'Compute Always On.bat' in its loop; safe to run repeatedly)

Order of work each call (one heavy job at a time; RAM is only 7.4 GB):
 1. factory script packs with no finished video yet -> queue them (max 2 waiting) and run the next job
    (GitHub render first = free, parallel, frees the laptop; automatic laptop fallback inside queue.py).
 2. otherwise return, and the caller runs the footage tagger.
Never publishes anything. Output videos land in factory/out/<channel>/<slug>/ for a frame check by Rocky.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
QUEUE = ROOT / "factory" / "queue.json"
MAX_WAITING = 2


def _queue() -> list:
    try:
        q = json.loads(QUEUE.read_text(encoding="utf-8"))
    except Exception:
        return []
    return q if isinstance(q, list) else q.get("jobs", [])


def _run(*args: str, timeout: int = 4 * 3600) -> int:
    p = subprocess.run([PY, "-X", "utf8", "-m", "tools.factory.queue", *args], cwd=ROOT, timeout=timeout)
    return p.returncode


def main() -> int:
    jobs = _queue()
    known = {(j.get("channel"), j.get("slug")) for j in jobs if isinstance(j, dict)}
    waiting = [j for j in jobs if isinstance(j, dict) and j.get("state") in ("queued", "github", "local")]
    for pack in sorted((ROOT / "factory" / "scripts").glob("*/*.json")):
        if len(waiting) >= MAX_WAITING:
            break
        ch, slug = pack.parent.name, pack.stem
        if (ch, slug) in known or (ROOT / "factory" / "out" / ch / slug / "final.mp4").exists():
            continue
        print(f"queue add {ch}/{slug}", flush=True)
        if _run("add", ch, slug, timeout=300) == 0:
            waiting.append({"channel": ch, "slug": slug})
    if waiting:
        print("run-next", flush=True)
        _run("run-next", "--timeout-min", "150")
    else:
        print("nothing to render", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
