"""Per-SECOND detailed local tagging of footage clips (no Claude credits): python -m tools.tag_seconds [--pool | <story file>] [--limit N]
A local open-source vision model (Qwen, as in tools.tag_clips) looks at one frame EVERY second of every clip and writes
a detailed description of that second to assets/clip_seconds.yaml: {token: {"dur": s, "sec": [{"t", "desc", "tags", "person", "action", "shot", "quality"}]}}.
Resumable: a clip already in the file is never looked at again. Used to match sentences to the exact moment."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "assets" / "clip_seconds.yaml"
PARTIAL = ROOT / "work" / "tag_seconds_partial.json"     # seconds done so far of the clip being tagged

PROMPT = ("This is ONE frame (one second) of a video clip. Describe in detail what is really visible. Answer JSON only: "
          '{"desc": "1-2 plain sentences: who/what, what they do, where, clothes, objects, lighting", '
          '"tags": ["8-14 lowercase single-word nouns/places/objects visible"], '
          '"person": "who is visible: none / young man in white kandura and ghutra / woman in abaya / crowd ... (never name anyone)", '
          '"action": "what is happening in 2-6 words", '
          '"shot": "close-up / medium / wide / aerial / photo-still / black-or-flash", '
          '"quality": "ok / blurry / text-overlay / letterbox / cartoon-or-chart / flash"}')
SCHEMA = {"type": "object", "properties": {k: ({"type": "array", "items": {"type": "string"}} if k == "tags" else {"type": "string"})
                                           for k in ("desc", "tags", "person", "action", "shot", "quality")},
          "required": ["desc", "tags", "person", "action", "shot", "quality"]}


def load() -> dict:
    return (yaml.safe_load(OUT.read_text(encoding="utf-8")) or {}) if OUT.exists() else {}


def single_instance():
    """Hold an OS lock for the whole run (released when the process dies). Two runs at once would double the RAM and
    overwrite each other's results in clip_seconds.yaml, so a second run just exits."""
    lock = (ROOT / "work" / "tag_seconds.lock").open("a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return lock


def main() -> None:
    if not (_lock := single_instance()):
        print("another tag_seconds run is already going - exiting", flush=True)
        return
    from lane.brain import LocalModel, image_b64, _img
    from lane.config import load_config
    from tools.prerender_gate import story_tokens, duration, CLIPS
    from tools.storyboard import find
    args = sys.argv[1:]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 10**9
    if "--limit" in args:
        del args[args.index("--limit"): args.index("--limit") + 2]
    if not args or args[0] == "--pool":
        vet = yaml.safe_load((ROOT / "assets" / "stock_vetted.yaml").read_text(encoding="utf-8"))
        toks = [f"sb-{i}" for i in vet["approved"]] + [f"clip:{c}" for c in CLIPS]
    else:
        toks = story_tokens(Path(args[0]))
    data = load()
    todo = [t for t in toks if t not in data and find(t)][:limit]
    print(f"{len(toks)} clips, {len(todo)} to tag", flush=True)
    if not todo:
        return
    import subprocess
    busy = subprocess.run(["tasklist", "/fi", "imagename eq llama-server.exe"], capture_output=True,
                          text=True).stdout if sys.platform == "win32" else ""
    if "llama-server.exe" in busy:          # the comment replier has the local AI loaded: two copies do not fit in RAM
        print("another local AI is running, try later", flush=True)
        return
    if sys.platform == "win32":             # a Deep Space plan/render (work/ds_run.py) or any ffmpeg render needs the RAM
        procs = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-CimInstance Win32_Process | ForEach-Object { $_.Name + ' ' + $_.CommandLine }"],
                               capture_output=True, text=True).stdout
        if "ds_run.py" in procs or "ffmpeg.exe" in procs.lower():
            print("a render is running, try later", flush=True)
            return
    # One frame + short prompt needs ~1K tokens; the default 12K context made the server too big for the
    # 7.5 GB laptop, so the RAM guard killed every run before a single second was tagged.
    m = LocalModel({**load_config()["ai"], "context": 3072})
    for n, tok in enumerate(todo, 1):
        p = find(tok)
        d = duration(p)
        # The RAM guard kills this run every few minutes on a busy laptop; keep each finished second of the
        # current clip so the next run carries on instead of restarting the same clip forever.
        part = json.loads(PARTIAL.read_text(encoding="utf-8")) if PARTIAL.exists() else {}
        secs = part["sec"] if part.get("tok") == tok else []
        for s in range(secs[-1]["t"] + 1 if secs else 0, max(1, int(d))):
            t = min(s + 0.5, max(d - 0.05, 0))
            try:
                out = m.chat([{"type": "text", "text": PROMPT}, _img(image_b64(p, t, 448))], schema=SCHEMA, max_tokens=260, temperature=0.1)
                j = json.loads(out[out.find("{"): out.rfind("}") + 1])
            except Exception as e:
                print("FAILED", tok, s, e, flush=True)
                continue
            secs.append({"t": s, "desc": str(j.get("desc", ""))[:300], "tags": sorted({str(x).lower().strip() for x in j.get("tags", [])}),
                         "person": str(j.get("person", ""))[:120], "action": str(j.get("action", ""))[:80],
                         "shot": str(j.get("shot", ""))[:30], "quality": str(j.get("quality", ""))[:30]})
            PARTIAL.write_text(json.dumps({"tok": tok, "sec": secs}), encoding="utf-8")
        if secs:
            data[tok] = {"dur": round(d, 1), "sec": secs}
            OUT.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=True, width=200), encoding="utf-8")
        print(f"[{n}/{len(todo)}] {tok}: {len(secs)} s", flush=True)
    m.stop()


if __name__ == "__main__":
    main()
