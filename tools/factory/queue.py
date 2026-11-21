"""Hybrid render queue for the factory.  python -m tools.factory.queue <cmd>
  add <pack.json | channel slug> [--local]        queue a script pack (path, or factory/scripts/<channel>/<slug>.json)
  status                                          show queue
  run-next [--force-fallback] [--timeout-min N]   take the oldest queued job and finish it
Flow: dispatch .github/workflows/factory_render.yml -> poll -> download artifact into factory/out/<key>/<slug>/ -> frame check.
GitHub failure/timeout -> retry once -> FALL BACK to a local render (one at a time) -> on final failure the exact reason is
stored in the job and sent by Telegram (bot token + owner id from rocky/, never printed). Never publishes anything.
States: queued, github, local, done, failed."""
import argparse
import base64
import gzip
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "factory" / "queue.json"
GH = ROOT / "models" / "gh" / "bin" / "gh.exe"
REPO = "jashanytube4-collab/luxury-lane-autopilot"
WORKFLOW = "factory_render.yml"
PY = sys.executable
LOCK = ROOT / "factory" / "queue.lock"
JOBS = []


def load():
    return json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else []


def save(jobs):
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE.write_text(json.dumps(jobs, indent=1), encoding="utf-8")


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(job, msg):
    job.setdefault("log", []).append(f"{now()} {msg}")
    print(f"[{job['channel']}/{job['slug']}] {msg}", flush=True)


def out_dir(job):
    return ROOT / "factory" / "out" / job["channel"] / job["out_slug"]


def sh(cmd, check=True, timeout=300):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(map(str, cmd))[:120]} -> {(r.stderr or r.stdout)[-500:]}")
    return r


def gh(*a, **k):
    return sh([GH, *a], **k)


def telegram(text):
    """Same bot as tools/rocky_telegram.py (files in rocky/). Never raises."""
    try:
        import requests
        tok = (ROOT / "rocky" / "telegram_token.txt").read_text().strip()
        owner = json.loads((ROOT / "rocky" / "telegram_owner.json").read_text())["id"]
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": owner, "text": text[:3500]}, timeout=30)
    except Exception as e:
        print("telegram failed:", type(e).__name__)


# ------------------------------------------------------------------ packs
def resolve_pack(a, b=None):
    if b:
        for p in (ROOT / "factory" / "scripts" / a / f"{b}.json", ROOT / "factory" / "scripts" / f"{b}.json"):
            if p.exists():
                return p
        raise SystemExit(f"no pack for {a} {b}")
    p = Path(a)
    p = p if p.is_absolute() else ROOT / p
    if not p.exists():
        raise SystemExit(f"missing {p}")
    return p


def github_ok(pack):
    """Packs that use laptop files (clip path) cannot render on GitHub."""
    return not any(v.get("t") == "clip" for b in pack["beats"] for v in b.get("v", []))


def cmd_add(args):
    p = resolve_pack(args.a, args.b)
    pack = json.loads(p.read_text(encoding="utf-8"))
    jobs = load()
    if any(j["channel"] == pack["channel"] and j["slug"] == p.stem and j["state"] != "failed" for j in jobs):
        raise SystemExit("already queued/done")
    jobs.append(dict(channel=pack["channel"], slug=p.stem, out_slug=pack.get("slug", p.stem), pack=str(p.relative_to(ROOT)),
                     state="queued", github=github_ok(pack) and not args.local, added=now(), attempts=0, log=[]))
    save(jobs)
    print("queued", pack["channel"], p.stem)


# ------------------------------------------------------------------ GitHub path
def runs():
    r = gh("run", "list", "-R", REPO, "--workflow", WORKFLOW, "--limit", "30", "--json", "databaseId,displayTitle,status,conclusion")
    return json.loads(r.stdout or "[]")


def dispatch(job):
    b64 = base64.b64encode(gzip.compress((ROOT / job["pack"]).read_bytes())).decode()
    if len(b64) > 60000:
        raise RuntimeError(f"pack too large for dispatch input ({len(b64)} chars)")
    before = {r["databaseId"] for r in runs()}
    gh("workflow", "run", WORKFLOW, "-R", REPO, "--ref", "main", "-f", f"channel={job['channel']}", "-f", f"slug={job['slug']}",
       "-f", f"pack_b64={b64}")
    for _ in range(30):
        time.sleep(4)
        new = [r for r in runs() if r["databaseId"] not in before and job["channel"] in r["displayTitle"] and job["slug"] in r["displayTitle"]]
        if new:
            return new[0]["databaseId"]
    raise RuntimeError("dispatched but run did not appear")


def github_render(job, timeout_min):
    rid = dispatch(job)
    job["run_id"] = rid
    save(JOBS)
    log(job, f"github run {rid} dispatched")
    t0 = time.time()
    while True:
        time.sleep(30)
        try:
            r = json.loads(gh("run", "view", str(rid), "-R", REPO, "--json", "status,conclusion").stdout)
        except Exception as e:
            log(job, f"poll error {str(e)[:100]}")
            r = {"status": "unknown"}
        if r["status"] == "completed":
            break
        if time.time() - t0 > timeout_min * 60:
            gh("run", "cancel", str(rid), "-R", REPO, check=False)
            raise RuntimeError(f"github timeout after {timeout_min} min")
    if r["conclusion"] != "success":
        raise RuntimeError(f"github run {rid} ended {r['conclusion']} (https://github.com/{REPO}/actions/runs/{rid})")
    o = out_dir(job)
    tmp = ROOT / "factory" / "out" / f"_dl_{job['channel']}_{job['slug']}"
    shutil.rmtree(tmp, ignore_errors=True)
    gh("run", "download", str(rid), "-R", REPO, "-D", str(tmp), timeout=1800)
    o.mkdir(parents=True, exist_ok=True)
    for f in tmp.rglob("*"):
        if f.is_file():
            shutil.copy2(f, o / f.name)
    shutil.rmtree(tmp, ignore_errors=True)
    gh("api", "-X", "DELETE", f"repos/{REPO}/actions/runs/{rid}", check=False)   # frees artifact storage
    log(job, "downloaded artifacts, github run deleted")


# ------------------------------------------------------------------ laptop path
def local_render(job):
    (out_dir(job) / "final.mp4").unlink(missing_ok=True)
    r = subprocess.run([PY, "-m", "tools.factory.run", job["pack"]], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("local render failed: " + (r.stderr or r.stdout)[-600:])
    log(job, "local render ok")


# ------------------------------------------------------------------ frame check
def probe(p, *a):
    return sh(["ffprobe", "-v", "error", *a, p]).stdout.strip()


def frame_check(job):
    """1920x1080, audio present, sampled frames (every 3 s) not black and not frozen. Writes CHECKED.txt; raises with the reason."""
    o = out_dir(job)
    f = o / "final.mp4"
    if not f.exists() or f.stat().st_size < 50_000:
        raise RuntimeError("final.mp4 missing or tiny")
    wh = probe(f, "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0")
    if wh != "1920,1080":
        raise RuntimeError(f"bad resolution {wh}")
    if not probe(f, "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0"):
        raise RuntimeError("no audio stream")
    d = float(probe(f, "-show_entries", "format=duration", "-of", "csv=p=0"))
    from PIL import Image, ImageChops, ImageStat
    fr = o / "_frames"
    shutil.rmtree(fr, ignore_errors=True)
    fr.mkdir()
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", f, "-vf", "fps=1/3,scale=320:-1", fr / "%04d.png"], timeout=900)
    imgs = sorted(fr.glob("*.png"))
    black = same = 0
    prev = None
    for p in imgs:
        im = Image.open(p).convert("L")
        if ImageStat.Stat(im).mean[0] < 8:
            black += 1
        if prev is not None and ImageStat.Stat(ImageChops.difference(im, prev)).mean[0] < 0.3:
            same += 1
        prev = im
    shutil.rmtree(fr, ignore_errors=True)
    n = len(imgs)
    if n and black / n > 0.1:
        raise RuntimeError(f"{black}/{n} sampled frames are black")
    if n > 1 and same / (n - 1) > 0.25:
        raise RuntimeError(f"{same}/{n - 1} sampled frame pairs frozen/identical")
    (o / "CHECKED.txt").write_text(f"{now()} ok {d:.1f}s {n} frames sampled, black {black}, frozen {same}\n")
    log(job, f"frame check ok ({d:.0f}s, black {black}/{n}, frozen {same})")


# ------------------------------------------------------------------ run-next
def finish_fail(job, reason):
    job["state"], job["reason"] = "failed", reason
    log(job, "FAILED: " + reason[:300])
    save(JOBS)
    telegram(f"Factory render FAILED: {job['channel']} / {job['slug']}\nReason: {reason[:900]}\nNothing was published.")


def cmd_run_next(args):
    global JOBS
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 6 * 3600:
        raise SystemExit("another run-next is active (factory/queue.lock)")
    LOCK.write_text(now())
    try:
        JOBS = load()
        job = next((j for j in JOBS if j["state"] == "queued"), None)
        if not job:
            print("nothing queued")
            return
        via = None
        if job["github"] and not args.force_fallback:
            for attempt in (1, 2):
                job["state"], job["attempts"] = "github", attempt
                try:
                    github_render(job, args.timeout_min)
                    frame_check(job)
                    via = "github"
                    break
                except Exception as e:
                    log(job, f"github attempt {attempt} failed: {str(e)[:300]}")
                    job["reason"] = str(e)[:500]
                    save(JOBS)
            if not via:
                log(job, "falling back to laptop")
        elif args.force_fallback:
            log(job, "forced fallback: laptop render")
        if not via:
            job["state"] = "local"
            save(JOBS)
            try:
                local_render(job)
                frame_check(job)
                via = "laptop"
            except Exception as e:
                finish_fail(job, f"{job.get('reason', '')} | laptop: {str(e)[:600]}".strip(" |"))
                return
        job.update(state="done", via=via, finished=now())
        job.pop("reason", None)
        save(JOBS)
        print("DONE via", via, out_dir(job) / "final.mp4")
    finally:
        LOCK.unlink(missing_ok=True)


def cmd_status(_a=None):
    jobs = load()
    if not jobs:
        print("queue empty")
    for j in jobs:
        print(f"{j['state']:<8} {j['channel']:<16} {j['slug']:<22} via={j.get('via') or ('github' if j['github'] else 'laptop')} tries={j['attempts']} {j.get('reason', '')[:80]}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("add")
    a.add_argument("a")
    a.add_argument("b", nargs="?")
    a.add_argument("--local", action="store_true")
    sp.add_parser("status")
    r = sp.add_parser("run-next")
    r.add_argument("--force-fallback", action="store_true")
    r.add_argument("--timeout-min", type=int, default=240)
    args = ap.parse_args()
    {"add": cmd_add, "status": cmd_status, "run-next": cmd_run_next}[args.cmd](args)


if __name__ == "__main__":
    main()
