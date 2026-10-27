"""Code-only render checker (no Claude credits): download a finished test render, run objective gates, send the
owner a Telegram report with a contact sheet. Publishing is never done here.

    .venv\\Scripts\\python.exe -m tools.render_watch <run_id>     check that run
    .venv\\Scripts\\python.exe -m tools.render_watch              check the newest successful manual run not yet checked

Gates: size/resolution/length/audio/black frames (lane.qa), silence longer than 2 s (dead air), near-duplicate
frames (the same picture reused), the first 30 s cut speed. Verdict PASS/FAIL goes to Telegram with a contact sheet
(one frame every 10 s) so the final human glance is a single image.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lane import qa  # noqa: E402
from lane.media import FFMPEG  # noqa: E402

REPO = "jashanytube4-collab/luxury-lane-autopilot"
GH = ROOT / "models" / "gh" / "bin" / "gh.exe"
SEEN = ROOT / "rocky" / "render_watch_seen.json"
STORY_ID = ""        # set from --story <id>: enables the cue checks against the gate stamp


def sh(*a: str) -> str:
    return subprocess.run(a, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def pick_run() -> str | None:
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else []
    rows = json.loads(sh(str(GH), "run", "list", "-R", REPO, "-L", "15", "--json",
                         "databaseId,event,conclusion"))
    for r in rows:
        if r["event"] == "workflow_dispatch" and r["conclusion"] == "success" and str(r["databaseId"]) not in seen:
            return str(r["databaseId"])
    return None


def gates(video: Path) -> tuple[list[str], dict]:
    problems = qa.check_long(video, 480, 1500)
    info = qa.probe(video)
    dur = info["duration"]
    out = subprocess.run([FFMPEG, "-nostats", "-i", str(video), "-af", "silencedetect=n=-38dB:d=2", "-f", "null", "-"],
                         capture_output=True, text=True, errors="replace").stderr
    gaps = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", out)]
    long_gaps = [g for g in gaps if g < dur * 0.5]  # ignore nothing; ending silence is short anyway
    if len(long_gaps) > 1:
        problems.append(f"{len(long_gaps)} stretches of >2 s without voice (dead air)")
    # shot-level repeat scan (work/dup_scan.py method): every half second, group into shots, compare each shot with all earlier ones
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(video), "-vf", "fps=2,scale=32:18,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    f = np.frombuffer(raw, np.uint8)
    f = f[: len(f) // 576 * 576].reshape(-1, 576).astype(np.int16)
    cuts_idx = [0] + [i for i in range(1, len(f)) if np.abs(f[i] - f[i - 1]).mean() > 18] + [len(f)]
    reps = []
    for a_ in range(len(cuts_idx) - 1):
        fa = f[(cuts_idx[a_] + cuts_idx[a_ + 1]) // 2]
        if fa.mean() < 14:
            continue
        for b_ in range(a_):
            fb = f[(cuts_idx[b_] + cuts_idx[b_ + 1]) // 2]
            if np.abs(fa - fb).mean() < 9:
                reps.append((cuts_idx[a_] / 2, cuts_idx[b_] / 2)); break
    dups = len(reps)
    for t_, o_ in reps[:12]:
        problems.append(f"repeated shot {int(t_ // 60)}:{int(t_ % 60):02d} looks like {int(o_ // 60)}:{int(o_ % 60):02d}")
    if dups > 0:
        problems.append(f"{dups} repeated shots in total (rule: none allowed)")
    # editing: light-leak / white-flash luma spikes and sound-effect energy at the planned cue times (from the gate stamp)
    luma = np.frombuffer(subprocess.run([FFMPEG, "-v", "error", "-i", str(video), "-vf", "fps=10,scale=16:9,format=gray",
                                         "-f", "rawvideo", "-"], capture_output=True).stdout, np.uint8)
    luma = luma[: len(luma) // 144 * 144].reshape(-1, 144).mean(axis=1)
    base = np.convolve(luma, np.ones(15) / 15, mode="same")
    flashes = int(np.sum((luma - base > 22) & (np.arange(len(luma)) > 5)))
    stamp = None
    if STORY_ID:
        sp = ROOT / "assets" / "stories" / ".gate" / f"{STORY_ID}.json"
        stamp = json.loads(sp.read_text()) if sp.exists() else None
    sfx_hit = sfx_n = 0
    if stamp:
        pcm = np.frombuffer(subprocess.run([FFMPEG, "-v", "error", "-i", str(video), "-af", "highpass=f=5000", "-ac", "1",
                                            "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout, np.int16).astype(np.float32)
        win = 16000 // 2
        env = np.array([np.sqrt(np.mean(pcm[i:i + win] ** 2)) for i in range(0, len(pcm) - win, win)]) if len(pcm) > win else np.zeros(1)
        med = float(np.median(env)) + 1e-6
        for ct in stamp.get("cues_sfx", []):
            k = int(max(0, ct) * 2)
            seg = env[max(0, k - 1): k + 4]
            sfx_n += 1
            sfx_hit += bool(len(seg) and seg.max() > 1.6 * med)
        if sfx_n and sfx_hit / sfx_n < 0.6:
            problems.append(f"sound effects barely audible: energy peak at only {sfx_hit} of {sfx_n} planned cue times")
        if flashes < max(3, len(stamp.get("cues_fx", [])) // 3):
            problems.append(f"light leaks/white flashes barely visible: {flashes} brightness spikes for {len(stamp.get('cues_fx', []))} planned")
    # opening pace: scene changes in the first 30 s
    sc = subprocess.run([FFMPEG, "-nostats", "-t", "30", "-i", str(video), "-vf", "select='gt(scene,0.3)',showinfo",
                         "-f", "null", "-"], capture_output=True, text=True, errors="replace").stderr
    cuts = len(re.findall(r"pts_time", sc))
    if cuts < 8:
        problems.append(f"first 30 s has only {cuts} cuts (want ~12+)")
    return problems, {"minutes": round(dur / 60, 1), "cuts30": cuts, "dead_air": len(long_gaps), "repeats": dups, "flashes": flashes, "sfx": f"{sfx_hit}/{sfx_n}"}


def sheet(video: Path, dest: Path) -> Path:
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(video), "-vf",
                    "fps=1/10,scale=240:-1,tile=8x6", "-frames:v", "1", str(dest)], check=False)
    return dest


def telegram(text: str, photo: Path | None = None) -> None:
    token = (ROOT / "rocky" / "telegram_token.txt").read_text().strip()
    chat = json.loads((ROOT / "rocky" / "telegram_owner.json").read_text(encoding="utf-8"))["id"]
    base = f"https://api.telegram.org/bot{token}"
    if photo and photo.exists():
        with open(photo, "rb") as f:
            requests.post(f"{base}/sendPhoto", data={"chat_id": chat, "caption": text[:1000]}, files={"photo": f}, timeout=120)
    else:
        requests.post(f"{base}/sendMessage", json={"chat_id": chat, "text": text}, timeout=60)


def main() -> None:
    global STORY_ID
    argv = sys.argv[1:]
    if "--story" in argv:
        STORY_ID = argv[argv.index("--story") + 1]
        del argv[argv.index("--story"): argv.index("--story") + 2]
    run = argv[0] if argv else pick_run()
    if not run:
        print("nothing new to check")
        return
    dest = ROOT / "work" / f"render_{run}"
    dest.mkdir(parents=True, exist_ok=True)
    if not list(dest.rglob("*.mp4")):
        subprocess.run([str(GH), "run", "download", run, "-R", REPO, "-n", "test-videos", "-D", str(dest)])
    vids = sorted(dest.rglob("*.mp4"), key=lambda p: p.stat().st_size, reverse=True)
    if not vids:
        telegram(f"Render {run}: no video file found in the download.")
        return
    video = vids[0]
    problems, stats = gates(video)
    img = sheet(video, dest / "sheet.png")
    verdict = "PASS" if not problems else "FAIL"
    msg = (f"Long video check {verdict} (run {run}): {stats['minutes']} min, {stats['cuts30']} cuts in first 30 s, "
           f"dead air {stats['dead_air']}, repeats {stats['repeats']}, flashes {stats['flashes']}, sfx peaks {stats['sfx']}." + ("" if not problems else "\nProblems: " + "; ".join(problems))
           + "\nNot published. Contact sheet attached (1 frame / 10 s).")
    telegram(msg, img)
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else []
    SEEN.write_text(json.dumps(seen + [run]))
    print(msg)


if __name__ == "__main__":
    main()
