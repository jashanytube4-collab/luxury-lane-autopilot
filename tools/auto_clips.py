"""Finds and cuts many real clips for a story by itself, then stores them in the private footage library.

    python -m tools.auto_clips <story-id> [--target 60] [--per-video 6] [--dry]
    python -m tools.auto_clips --next [--target 60]      # the next story waiting in the queue

Run on the home computer (YouTube blocks GitHub's machines). It reads ONLY the official YouTube channels
(config source.official_youtube: Dubai Media Office, Sheikh Hamdan, Sheikh Mohammed), picks the videos whose titles match
the story's main names and places, downloads the picture only (never the sound), splits each video into its shots,
keeps short 3-6 second pieces,
and registers each piece in assets/clips.yaml with a `match` on the name, so the story picks it for the sentences that
talk about it. Every clip is credited to its channel in the video description. Commit assets/clips.yaml afterwards.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lane.longform import _scene_list  # noqa: E402
from lane.media import FFMPEG  # noqa: E402
from lane.story import bank  # noqa: E402
from tools.fetch_clips import OUT, SRC, library  # noqa: E402

CLIPS_FILE = ROOT / "assets" / "clips.yaml"
SKIP = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
        "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "down",
        "middle", "east", "minister", "defence", "defense", "uncle", "emirates", "stay", "today"}
GENERIC = {"fazza", "hamdan", "prince", "sheikh", "crown", "dubai", "the", "today", "stay", "like", "this", "that",
           "but", "and", "when", "then", "what", "every", "some", "his", "her", "they", "she", "was", "for", "now",
           "one", "and", "all", "not", "yes", "had", "who", "how", "why", "from", "with", "there", "here", "after"}


def story_text(story) -> list[str]:
    out = []
    for sec in story.sections:
        out.append(str(sec.get("say", "")))
        out += [str(b.get("say", "")) for b in sec.get("beats", []) or []]
    return out


def entities(story, top: int = 14) -> list[str]:
    """The story's main names and places: capitalised phrases that are not at the start of a sentence."""
    count: Counter = Counter()
    for text in story_text(story):
        for sent in re.split(r"(?<=[.!?])\s+", text):
            for m in re.finditer(r"(?<=[a-z,;] )((?:[A-Z][a-zA-Z'-]+)(?: (?:of |al-|bin )?[A-Z][a-zA-Z'-]+){0,2})", sent):
                ent = re.sub(r"'s$", "", m.group(1).strip())
                if (len(ent) > 3 and not any(w.lower() in GENERIC | SKIP for w in ent.split())):
                    count[ent] += 1
    return [e for e, _ in count.most_common(top)]


OFFICIAL = {"Dubai Media Office": "UCJ-AxG2x-5kgN_MMfBYa2Cg",
            "Sheikh Hamdan": "UCuwxptOLQd-hd0Ujt33-W1Q",
            "Sheikh Mohammed": "UCVfsCXH49iiSF8o1ctgH7xA"}


def channel_videos(channel_id: str, n: int = 400) -> list[dict]:
    """Recent uploads of one official channel (title, id, duration)."""
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", "-q", "--js-runtimes", "node", "--flat-playlist",
                        "--playlist-end", str(n), "-J", f"https://www.youtube.com/channel/{channel_id}/videos"],
                       capture_output=True, text=True, timeout=300)
    try:
        return [e for e in json.loads(r.stdout).get("entries", []) if e and e.get("id")]
    except json.JSONDecodeError:
        return []


def title_words(title: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z]{4,}", title) if w.lower() not in GENERIC | SKIP][:3]


def download(vid: str) -> Path | None:
    SRC.mkdir(parents=True, exist_ok=True)
    for p in SRC.glob(f"{vid}.mp4"):
        return p
    subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", "-q", "--js-runtimes", "node",
                    "-f", "bv*[height<=720][vcodec^=avc1]/bv*[height<=720]/b[height<=720]", "--remux-video", "mp4",
                    "-o", str(SRC / f"{vid}.%(ext)s"), "--", f"https://www.youtube.com/watch?v={vid}"],
                   capture_output=True, timeout=600)
    p = SRC / f"{vid}.mp4"
    return p if p.exists() else None


def cut_piece(src: Path, a: float, b: float, dest: Path) -> bool:
    OUT.mkdir(parents=True, exist_ok=True)
    vf = ("scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease,"
          "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=25,setsar=1")
    r = subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", str(src), "-an", "-vf", vf,
                        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", str(dest)])
    return r.returncode == 0 and dest.exists()


def pick_scenes(scenes: list[tuple[float, float]], n: int) -> list[tuple[float, float]]:
    good = [(a, min(b, a + 6.0)) for a, b in scenes if b - a >= 3.0]
    if len(good) <= n:
        return good
    step = len(good) / n
    return [good[int(i * step)] for i in range(n)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("story", nargs="?")
    ap.add_argument("--next", action="store_true")
    ap.add_argument("--target", type=int, default=120)
    ap.add_argument("--per-video", type=int, default=4)
    ap.add_argument("--dry", action="store_true", help="only show what would be searched")
    a = ap.parse_args()
    cursors = json.loads((ROOT / "state" / "cursors.json").read_text(encoding="utf-8"))
    done = set(cursors.get("stories_done", [])) | set(cursors.get("stories_blocked", []))
    story = next((s for s in bank() if (s.id == a.story if a.story else (a.next and s.id not in done))), None)
    if story is None:
        sys.exit("story not found")
    ents = entities(story)
    print(f"story {story.id}: searching for {ents}")
    if a.dry:
        return
    doc = yaml.safe_load(CLIPS_FILE.read_text(encoding="utf-8"))
    clips = doc["clips"]
    before = set(clips)
    used_yt = {s.get("yt") for s in clips.values()}
    lib, have = library(), None
    have = lib.assets()
    new = 0
    pool = []          # (score, channel name, entry, match regex) - videos whose title matches the story first
    for name, cid_ in OFFICIAL.items():
        for e in channel_videos(cid_):
            dur, title = e.get("duration") or 0, e.get("title") or ""
            if e["id"] in used_yt or not (20 <= dur <= 1500):
                continue
            hit = next((x for x in ents if x.lower() in title.lower()), None)
            words = title_words(title)
            if hit:
                pool.append((2, name, e, re.escape(hit)))
            elif len(words) >= 2:
                pool.append((1, name, e, "|".join(re.escape(w) for w in words)))
    pool.sort(key=lambda t: -t[0])
    print(f"  {len(pool)} official videos to choose from")
    for _, channel, hit, match in pool:
        if new >= a.target:
            break
        vid = hit["id"]
        src = download(vid)
        if not src:
            continue
        try:
            scenes = pick_scenes(_scene_list(src), a.per_video)
        except Exception as e:  # noqa: BLE001
            print(f"  {vid}: scenes failed ({e})")
            continue
        made = 0
        for k, (x, y) in enumerate(scenes):
            cid = f"auto-{vid.lower().replace('_', 'x')}-{k}"
            dest = OUT / f"{cid}.mp4"
            if cut_piece(src, x, y, dest) and lib.upload(f"clip-{cid}.mp4", dest):
                clips[cid] = {"match": match, "yt": vid, "parts": [[round(x, 2), round(y, 2)]],
                              "credit": f"{channel} (official YouTube channel)"}
                made += 1
                new += 1
        used_yt.add(vid)
        print(f"  {channel}: {vid} '{(hit.get('title') or '')[:50]}' -> {made} clips (total {new})")
    added = {k: v for k, v in clips.items() if k.startswith("auto-") and k not in before}
    if added:       # appended as text so the file's comments stay
        text = yaml.safe_dump(added, sort_keys=False, allow_unicode=True, width=200)
        with CLIPS_FILE.open("a", encoding="utf-8") as f:
            f.write("".join(f"  {line}\n" for line in text.splitlines()))
    print(f"DONE: {new} new clips added to assets/clips.yaml (target {a.target})")


if __name__ == "__main__":
    main()
