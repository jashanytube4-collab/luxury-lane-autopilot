"""Runs on the laptop a few times a day: writes the reporter's report, and when a big story appears (score above
news.min_score) it collects as many official clips as it can for that story BEFORE the pipeline builds the video,
then publishes the new clips to GitHub so the next autopilot run can use them.

    python -m tools.news_prep

(YouTube blocks GitHub's machines, so the cutting must happen here at home. Only official channels are used, see
tools/auto_clips.py.) Prepared stories are remembered in work/news_prepped.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lane import news  # noqa: E402
from tools import reporter  # noqa: E402
from tools.auto_clips import entities_of, fetch_for  # noqa: E402

STATE = ROOT / "work" / "news_prepped.json"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def main() -> None:
    reporter.main()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    min_score = float(cfg.get("news", {}).get("min_score", 6))
    cursors = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    item = news.best(cursors, min_score, date.today())
    if item is None:
        print("no big story to prepare")
        return
    texts = [item.label] + [s["text"] for s in item.sources]
    ents = entities_of(texts)
    print(f"BIG STORY {item.score}/10: {item.label}\nnames to find clips for: {ents}")
    added = fetch_for(ents, target=120, per_video=4)
    news.mark_done(cursors, item, date.today())
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(cursors), encoding="utf-8")
    if not added:
        print("no new official clips found")
        return
    test = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, capture_output=True, text=True)
    if test.returncode != 0:
        print("tests failed, clips NOT pushed:\n" + test.stdout[-500:])
        return
    git("add", "assets/clips.yaml")
    git("commit", "-m", f"{added} official clips for the news story: {item.label[:70]}", "--", "assets/clips.yaml")
    pulled = git("pull", "--rebase", "--autostash")
    pushed = git("push")
    print("clips pushed" if pulled.returncode == 0 and pushed.returncode == 0 else
          f"push problem: {pulled.stderr[-200:]} {pushed.stderr[-200:]}")


if __name__ == "__main__":
    main()
