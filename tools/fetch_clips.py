"""Cut the hand-picked clips in assets/clips.yaml and store them in the private footage library.

Run on a home computer (YouTube blocks GitHub's machines):  python -m tools.fetch_clips
Clips already in the library are skipped, so it is safe to run again after adding new ones.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lane.hamdan import Library  # noqa: E402
from lane.media import FFMPEG  # noqa: E402

SRC = ROOT / "work" / "clipsrc"
OUT = ROOT / "work" / "clips"


def library() -> Library:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    token = os.environ.get("LIBRARY_TOKEN") or subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    return Library(cfg["source"]["library_repo"], token)


def source(yt: str) -> Path:
    for p in SRC.glob(f"{yt}.*"):
        if p.suffix == ".mp4":
            return p
    SRC.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", "-q", "--js-runtimes", "node",
                    "-f", "bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]/b[height<=1080]",
                    "--remux-video", "mp4", "-o", str(SRC / f"{yt}.%(ext)s"), "--",
                    f"https://www.youtube.com/watch?v={yt}"], check=True)
    return SRC / f"{yt}.mp4"


def cut(cid: str, spec: dict) -> Path:
    """Picture only (never the original sound), cropped, at most 1920x1080, parts joined in order."""
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{cid}.mp4"
    if out.exists():
        return out
    src = source(spec["yt"])
    vf = []
    if spec.get("crop"):
        x0, y0, x1, y1 = spec["crop"]
        vf.append(f"crop=trunc(iw*{x1 - x0:.4f}/2)*2:trunc(ih*{y1 - y0:.4f}/2)*2:iw*{x0:.4f}:ih*{y0:.4f}")
    vf += ["scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease",
           "scale=trunc(iw/2)*2:trunc(ih/2)*2", "fps=25", "setsar=1"]
    pieces = []
    for k, (a, b) in enumerate(spec["parts"]):
        piece = OUT / f"{cid}.part{k}.mp4"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", str(a), "-to", str(b), "-i", str(src), "-an",
                        "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-pix_fmt", "yuv420p", str(piece)], check=True)
        pieces.append(piece)
    lst = OUT / f"{cid}.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in pieces), encoding="utf-8")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    str(out)], check=True, cwd=OUT)
    for p in pieces + [lst]:
        p.unlink()
    return out


def main() -> None:
    clips = yaml.safe_load((ROOT / "assets" / "clips.yaml").read_text(encoding="utf-8"))["clips"]
    lib = library()
    have = lib.assets()
    for cid, spec in clips.items():
        name = f"clip-{cid}.mp4"
        path = cut(cid, spec)
        if name in have:
            print(f"{cid}: already in the library")
            continue
        ok = lib.upload(name, path)
        print(f"{cid}: {'stored' if ok else 'UPLOAD FAILED'} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
