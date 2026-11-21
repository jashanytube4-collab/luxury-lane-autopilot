"""python -m tools.factory.status  -> per-channel readiness of the 17-channel factory (reads files only, no Claude)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGES = ["profile", "style", "voice", "engine", "thumb", "topics", "scripts", "test_video", "upload"]
ENGINES_DONE = {"docu"}            # formats whose scene engine exists in core.py (update as engines are built)


def check(ch):
    k, ref = ch["key"], ch.get("ref")
    if ch["kind"] == "existing":
        return {"own": True}
    f = ROOT / "factory"
    st = {
        "profile": (f / "profiles" / f"{k}.json").exists(),
        "style": bool(ref) and (ROOT / "rocky" / "channels" / ref / "style.md").exists(),
        "voice": (f / "samples" / f"{k}.mp3").exists(),
        "engine": ch["engine"] in ENGINES_DONE,
        "thumb": (f / "thumb_templates" / f"{k}.json").exists(),
        "topics": (f / "topics" / f"{k}.yaml").exists() and (f / "topics" / f"{k}.yaml").read_text(encoding="utf-8").count("- title:") >= 10,
        "scripts": len(list((f / "scripts" / k).glob("*.json"))) >= 3 if (f / "scripts" / k).exists() else False,
        "test_video": any((f / "out" / k).glob("*/CHECKED.txt")) if (f / "out" / k).exists() else False,
        "upload": (f / "upload_ok" / f"{k}.txt").exists(),
    }
    return st


def main():
    reg = json.loads((ROOT / "factory" / "registry.json").read_text(encoding="utf-8"))
    done = 0
    for ch in reg:
        st = check(ch)
        if st.get("own"):
            print(f"{ch['name']:<18} own pipeline  ({ch.get('pipeline', '')})")
            continue
        n = sum(st.values())
        done += n == len(STAGES)
        miss = [s for s in STAGES if not st[s]]
        print(f"{ch['name']:<18} {n}/{len(STAGES)}  {ch['engine']:<7} missing: {', '.join(miss) or '-'}")
    new = [c for c in reg if c["kind"] == "new"]
    print(f"\nFULLY READY new channels: {done}/{len(new)}   (4 existing channels run on their own pipelines)")


if __name__ == "__main__":
    main()
