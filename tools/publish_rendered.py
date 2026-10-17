"""Publish a video that a test run already rendered (and the owner approved), instead of rendering it again.

    python -m tools.publish_rendered <dir with *.mp4 + .txt> <story id> [thumbnail.jpg] [minutes from now]

Runs on GitHub (publish.yml), where the YouTube secrets are. Marks the story as done so the autopilot moves on to
the next story, and records the video in today's plan.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lane.config import load_config, load_secrets  # noqa: E402
from lane.state import State  # noqa: E402
from lane.youtube import YouTube  # noqa: E402


def main() -> None:
    folder, story_id = Path(sys.argv[1]), sys.argv[2]
    thumb = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None
    minutes = int(sys.argv[4]) if len(sys.argv) > 4 else 15
    video = next(folder.glob("*.mp4"))
    txt = video.with_suffix(".txt").read_text(encoding="utf-8")
    title, rest = txt.split("\n\n", 1)
    description, _, tags = rest.rpartition("\n\nTAGS: ")
    tags = [t.strip() for t in tags.split(",") if t.strip()]
    if thumb is None or not thumb.exists():
        thumb = video.with_suffix(".thumb.jpg")
    cfg, sec = load_config(), load_secrets()
    yt = YouTube(sec.yt_client_id, sec.yt_client_secret, sec.yt_refresh_token)
    at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    vid = yt.upload(video, title=title.strip(), description=description.strip(), tags=tags,
                    category_id=cfg["youtube"]["category_id"], publish_at=at)
    ok = yt.set_thumbnail(vid, thumb) if thumb.exists() else False
    print(f"uploaded https://youtu.be/{vid} — public at {at:%H:%M} UTC; thumbnail {'set' if ok else 'NOT set'}")

    state = State()
    done = state.cursors.setdefault("stories_done", [])
    if story_id not in done:
        done.append(story_id)
    state.cursors.setdefault("posted_units", [])
    if f"story:{story_id}" not in state.cursors["posted_units"]:
        state.cursors["posted_units"].append(f"story:{story_id}")
    state.save()
    print(f"story '{story_id}' marked done; next story comes tomorrow")


if __name__ == "__main__":
    main()
