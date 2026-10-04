"""Copy the official hamdan.ae YouTube videos into the private footage library (a GitHub release), so the daily
runs on GitHub's servers never need to download from YouTube (which blocks them). Resumable: videos already in the
library are skipped. Run from a normal home connection:

    python tools/build_library.py <owner/library-repo> <path-to-gh>
"""
import json
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "work" / "library"
YTDLP = str(Path(sys.executable).with_name("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"))
FORMAT = "bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080][vcodec^=av01]/bv*[height<=1080]/b[height<=1080]"


def existing(repo: str, gh: str) -> set[str]:
    out = subprocess.run([gh, "api", "--paginate", f"repos/{repo}/releases/tags/footage", "--jq", ".assets[].name"],
                         capture_output=True, text=True, check=True).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def main() -> None:
    repo, gh = sys.argv[1], sys.argv[2]
    events = json.loads((ROOT / "state" / "events.json").read_text(encoding="utf-8"))
    ids = sorted({e["video"]["ref"] for e in events.values() if e.get("video") and e["video"]["kind"] == "youtube"},
                 key=lambda i: next(e["date"] for e in events.values()
                                    if e.get("video") and e["video"].get("ref") == i), reverse=True)
    have = existing(repo, gh)
    todo = [i for i in ids if f"{i}.mp4" not in have]
    print(f"library has {len(have)} videos; {len(todo)} to add", flush=True)
    TMP.mkdir(parents=True, exist_ok=True)
    done = failed = 0
    for n, vid in enumerate(todo, 1):
        dest = TMP / f"{vid}.mp4"
        ok = dest.exists() and dest.stat().st_size > 200_000
        for attempt in range(3):
            if ok:
                break
            p = subprocess.run([YTDLP, "--no-warnings", "-q", "--js-runtimes", "node", "--no-playlist",
                                "-f", FORMAT, "--remux-video", "mp4", "-o", str(dest),
                                f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True)
            ok = p.returncode == 0 and dest.exists() and dest.stat().st_size > 200_000
            if not ok:
                print(f"  retry {vid}: {(p.stderr or '').strip().splitlines()[-1:]}", flush=True)
                time.sleep(20 * (attempt + 1))
        if not ok:
            failed += 1
            print(f"[{n}/{len(todo)}] FAILED {vid}", flush=True)
            continue
        up = subprocess.run([gh, "release", "upload", "footage", "-R", repo, str(dest), "--clobber"],
                            capture_output=True, text=True)
        if up.returncode != 0:
            failed += 1
            print(f"[{n}/{len(todo)}] upload FAILED {vid}: {up.stderr.strip()[-200:]}", flush=True)
            continue
        done += 1
        print(f"[{n}/{len(todo)}] added {vid} ({dest.stat().st_size / 1e6:.0f} MB)", flush=True)
        dest.unlink()
        time.sleep(random.uniform(2, 6))
    print(f"FINISHED: added {done}, failed {failed}, library now {len(have) + done} videos", flush=True)


if __name__ == "__main__":
    main()
