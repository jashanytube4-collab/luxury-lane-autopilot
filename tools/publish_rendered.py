"""Publish a video that a test run already rendered (and the owner approved), instead of rendering it again.

    python -m tools.publish_rendered <dir with *.mp4 + .txt> <story id> [thumbnail.jpg] [minutes from now | ISO time]

Runs on GitHub (publish.yml), where the YouTube secrets are. It never edits the plan (the autopilot may be running at
the same time): the video carries the story's own tag, so the autopilot's next run sees it on the channel and counts
the story as done.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lane.config import load_config, load_secrets  # noqa: E402
from lane.youtube import YouTube, key_tag  # noqa: E402


def main() -> None:
    folder, story_id = Path(sys.argv[1]), sys.argv[2]
    thumb = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None
    when = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else "15"
    video = next(folder.glob("*.mp4"))
    txt = video.with_suffix(".txt").read_text(encoding="utf-8")
    title, rest = txt.split("\n\n", 1)
    description, _, tags = rest.rpartition("\n\nTAGS: ")
    tags = [t.strip() for t in tags.split(",") if t.strip()]
    if thumb is None or not thumb.exists():
        thumb = video.with_suffix(".thumb.jpg")
    cfg, sec = load_config(), load_secrets()
    yt = YouTube(sec.yt_client_id, sec.yt_client_secret, sec.yt_refresh_token)
    at = (datetime.fromisoformat(when).astimezone(timezone.utc) if "T" in when
          else datetime.now(timezone.utc) + timedelta(minutes=int(when)))
    vid = yt.upload(video, title=title.strip(), description=description.strip(), tags=tags,
                    category_id=cfg["youtube"]["category_id"], publish_at=at)
    ok = yt.set_thumbnail(vid, thumb) if thumb.exists() else False
    print(f"uploaded https://youtu.be/{vid} — public at {at:%Y-%m-%d %H:%M} UTC; thumbnail {'set' if ok else 'NOT set'}")
    if key_tag(f"story:{story_id}") not in tags:
        print(f"WARNING: the video has no tag for story '{story_id}'; add it to stories_done by hand")


if __name__ == "__main__":
    main()
