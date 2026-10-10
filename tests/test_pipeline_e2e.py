"""End-to-end day with fake hamdan.ae / AI / YouTube: real editing, voice, long-form render, thumbnail, scheduling
and bookkeeping. Slow (renders real videos, needs internet for the voice), so it is skipped in the daily CI
self-test:
    python -m pytest -q -m slow tests/test_pipeline_e2e.py
"""
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import lane.pipeline as pl
from lane.state import State

SOURCES = [
    "testsrc2=size=1440x1080:rate=30:duration=40",
    "life=size=1280x720:rate=30:mold=10:ratio=0.1:life_color=#F7C948:death_color=#202040,format=yuv420p",
    "mandelbrot=size=1280x720:rate=30",
]


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    vids = []
    for i, src in enumerate(SOURCES):
        p = d / f"v{i}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-t", "40", "-pix_fmt", "yuv420p",
                        "-vf", "drawbox=x='mod(t*120,iw)':y=100:w=200:h=200:color=gold@0.8:t=fill", str(p)],
                       check=True)
        vids.append(p)
    photos = []
    for i in range(6):
        p = d / f"p{i}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"gradients=size=2400x1600:seed={i + 3}:speed=0.02", "-frames:v", "1", str(p)], check=True)
        photos.append(p)
    return vids, photos


def _events():
    ev = {}
    for i in range(6):
        ev[f"event {i}"] = {
            "title": f"Hamdan bin Mohammed attends endurance race number {i}",
            "date": f"2026-09-{10 + i:02d}",
            "video": {"kind": "mp4", "ref": f"v{i % 3}"} if i < 3 else None,
            "photos": [f"p{(i + k) % 6}" for k in range(4)],
            "news_id": str(5000 + i),
        }
    return ev


class FakeHamdan:
    media = None

    def __init__(self, library=None):
        self.library = library

    def sync(self, events, full=False):
        return 0

    def article(self, nid):
        return {"brief": "Sheikh Hamdan attended the race.", "text": "شهد سمو الشيخ حمدان بن محمد السباق."}

    def download_photo(self, url, dest):
        _, photos = self.media
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(photos[int(url[1:])], dest)
        return dest

    def download_video(self, video, dest, cookies=None):
        vids, _ = self.media
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(vids[int(video["ref"][1:])], dest)
        return dest


NARRATION = ("The desert wakes before the riders do. Engines idle, horses breathe, and a crowd gathers along the "
             "track. Sheikh Hamdan arrives without ceremony and walks the line, greeting every rider by name. "
             "Today is about endurance, about the bond between horse and rider that Dubai has honoured for "
             "generations. As the flag drops, the field surges forward into the morning light.")


class FakeBrain:
    def __init__(self, cfg=None):
        self.calls = 0

    def plan_short(self, items, **kw):
        self.calls += 1
        shots = []
        for it in items[:4]:
            if it["kind"] == "video":
                shots.append({"kind": "video", "start": it["start"] + 0.1, "end": min(it["end"], it["start"] + 4.5),
                              "focus_x": 0.5})
            else:
                shots.append({"kind": "photo", "path": it["path"], "seconds": 3.2, "focus_x": 0.5})
        return {"suitable": True, "score": 9, "mood": "arabian", "layout": "frame" if self.calls % 2 else "fill",
                "hook_text": "Built for endurance", "shots": shots,
                "script": "Forty riders, one desert, and a Crown Prince who walks the line himself. "
                          "This is what endurance looks like in Dubai. Watch the start.",
                "emphasis": ["endurance"], "title": f"The Start Line {self.calls}", "description": "A test.",
                "tags": ["test"]}

    def write_chapter(self, num, **kw):
        return {"chapter_title": f"Into The Dunes {num}", "place_line": "DUBAI · 10 SEPTEMBER 2026",
                "narration": NARRATION}

    def write_episode(self, theme, chapters, title_hint=""):
        return {"episode_title": "Riders of the Dawn", "hook": NARRATION[:220], "outro": NARRATION[:160],
                "youtube_title": "Riders Of The Dawn: Fazza's Endurance World", "description": "Test episode.",
                "tags": ["endurance", "Fazza"], "thumbnail_text": "RIDERS OF THE DAWN"}

    def shorten(self, script, words):
        return " ".join(script.split()[:words])

    def close(self):
        pass


class FakeFace:
    """Synthetic test footage has no faces: pretend Prince Hamdan is on screen everywhere."""
    def __init__(self):
        from lane.faceid import Sighting
        self.s = Sighting(True, 0.3, 0.5, 0.9)

    def scan(self, video, every=0.5):
        from lane.media import probe
        d = probe(video)["duration"]
        return [(i * every, self.s) for i in range(int(d / every))]

    def photo(self, path):
        return self.s

    def find(self, img):
        return self.s


class FakeYT:
    channel_title = "Test channel"

    def __init__(self):
        self.uploads, self.thumbs = [], []

    def recent_tags(self):
        return set()

    def upload(self, path, **kw):
        assert Path(path).stat().st_size > 400_000
        self.uploads.append({"path": str(path), **kw})
        return f"VID{len(self.uploads)}"

    def set_thumbnail(self, vid, image):
        assert Path(image).exists()
        self.thumbs.append(vid)
        return True


@pytest.mark.slow
def test_full_day(tmp_path, monkeypatch, media):
    FakeHamdan.media = media
    monkeypatch.setattr(pl, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(pl, "State", lambda: State(tmp_path / "state"))
    monkeypatch.setattr(pl, "load_events", _events)
    monkeypatch.setattr(pl, "save_events", lambda ev: None)
    monkeypatch.setattr(pl, "Brain", FakeBrain)
    monkeypatch.setattr(pl, "Hamdan", FakeHamdan)
    import lane.studio as st
    monkeypatch.setattr(st, "FaceID", FakeFace)

    runner = pl.Runner(SimpleNamespace(dry_run=False, max=None))
    cfg = runner.cfg
    cfg["schedule"].update(shorts_per_day=2, days_ahead=1, one_per_hour=True, gap_minutes=[45, 70])
    cfg["longform"].update(chapters=4, target_minutes=2.5, min_minutes=1.0, max_minutes=8)
    runner.yt = FakeYT()
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    assert runner.fill_day(tomorrow) is True

    ups = runner.yt.uploads
    assert len(ups) == 3, [u["title"] for u in ups]
    long = [u for u in ups if "Riders" in u["title"]]
    assert len(long) == 1 and runner.yt.thumbs == ["VID1"]
    assert "0:00 Intro" in long[0]["description"] and "Kevin MacLeod" in long[0]["description"]
    plan = runner.state.load_day(tomorrow.isoformat())
    assert sorted(s["status"] for s in plan["slots"]) == ["scheduled"] * 3
    used_long = [k for k, u in runner.usage.items() if u.get("long")]
    assert len(used_long) == 4
    shorts = [u for u in ups if u["title"].startswith("The Start Line")]
    assert all(any(t.startswith("ll") and len(t) == 12 for t in u["tags"]) for u in shorts)

    # Running again the same day must not upload anything new.
    runner2 = pl.Runner(SimpleNamespace(dry_run=False, max=None))
    runner2.cfg = cfg
    runner2.yt = FakeYT()
    runner2.fill_day(tomorrow)
    assert not runner2.yt.uploads
