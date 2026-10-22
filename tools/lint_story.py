"""Check a story file BEFORE any render: python -m tools.lint_story <story id or file> [...]
Exit code 1 = do not render. Catches the mistakes that used to show up only after a 45-minute render."""
from __future__ import annotations
import re, sys
from collections import Counter
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STORIES = ROOT / "assets" / "stories"


def lint(path: Path) -> list[str]:
    s = yaml.safe_load(path.read_text(encoding="utf-8"))
    vet = yaml.safe_load((ROOT / "assets" / "stock_vetted.yaml").read_text(encoding="utf-8")) \
        if (ROOT / "assets" / "stock_vetted.yaml").exists() else {}
    ok, bad = {str(x) for x in (vet or {}).get("approved", [])}, (vet or {}).get("rejected", {}) or {}
    beats = [b for sec in s.get("sections", []) for b in sec.get("beats", [])]
    problems: list[str] = []
    seq = []
    for i, b in enumerate(beats):
        for tok in b.get("show", []) or []:
            seq.append((i, str(tok)))
    n = Counter(t for _, t in seq)
    for t, c in n.items():
        if t.startswith("sb-") and c > 1:
            problems.append(f"{t} is used {c} times (a stock clip may appear once)")
        elif t.startswith("clip:") and c > 3:
            problems.append(f"{t} is used {c} times (max 3)")
        if t.startswith("sb-"):
            sid = t[3:]
            if sid in bad:
                problems.append(f"{t} was REJECTED: {bad[sid]}")
            elif sid not in ok:
                problems.append(f"{t} was never looked at (not approved in assets/stock_vetted.yaml)")
    # time per clip: ~2.5 words/s; a beat's seconds are split over its show items (stock max 6 s, any clip max 8 s)
    for i, b in enumerate(beats):
        shows = [str(t) for t in (b.get("show") or [])]
        if not shows:
            continue
        secs = len(re.findall(r"\w+", b.get("say", ""))) / 2.5
        per = secs / len(shows)
        if per > 6 and any(t.startswith("sb-") for t in shows):
            problems.append(f"beat {i}: stock shown ~{per:.0f}s each (max 6 s): {b.get('say','')[:50]}")
        elif per > 8:
            problems.append(f"beat {i}: one clip ~{per:.0f}s (max 8 s): {b.get('say','')[:50]}")
    # the first 30 s: ~75 words/30 s at normal pace -> the first beats; they must be moving footage, not photos
    words = 0
    for i, b in enumerate(beats):
        words += len(re.findall(r"\w+", b.get("say", "")))
        if words > 75:
            break
        for tok in b.get("show", []) or []:
            if str(tok).startswith(("search:", "cat:")):
                problems.append(f"photo '{tok}' in the first 30 s")
    # the first 30 s: only native 16:9 clips (no vertical / pillarboxed footage); probed from the local files
    words = 0
    for i, b in enumerate(beats):
        if words > 75:
            break
        words += len(re.findall(r"\w+", b.get("say", "")))
        for tok in b.get("show", []) or []:
            tok = str(tok)
            if tok.startswith(("clip:", "sb-")):
                try:
                    from tools.storyboard import find
                    f = find(tok)
                    if f is not None:
                        import subprocess
                        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                            "stream=width,height", "-of", "csv=p=0", str(f)], capture_output=True, text=True)
                        w, h = (int(x) for x in r.stdout.strip().split(",")[:2])
                        if w / h < 1.7:
                            problems.append(f"beat {i}: {tok} is {w}x{h}, not 16:9: not allowed in the first 30 s")
                except (OSError, ValueError):
                    pass
    if len(set(t for _, t in seq if t.startswith("sb-"))) + len({t for _, t in seq if t.startswith("clip:")}) < 40:
        problems.append("fewer than 40 different clips named")
    # checklist 13/14: open loop in the first beat, one CTA only at the very end, the video ends on Hamdan
    if beats:
        first = str(beats[0].get("say", "")).lower()
        if "?" not in first or not re.search(r"stay|reveal|end", first):
            problems.append("open loop missing: beat 1 must ask ONE big question and promise the answer at the end")
        ctas = [i for i, b in enumerate(beats) if re.search(r"\bsubscribe\b", str(b.get("say", "")), re.I)]
        if len(ctas) != 1 or ctas[0] != len(beats) - 1:
            problems.append(f"call-to-action must appear once, in the last beat only (found in beats {ctas})")
        last = [str(t) for t in (beats[-1].get("show") or [])]
        if not last or not (last[-1] == "him" or last[-1].startswith(("clip:", "key:", "him:"))):
            problems.append("the video must END on Hamdan footage (last show of the last beat is not him/clip/key)")
        # first 30 s: a new picture about every 2 s (>= 12 pictures in the first ~75 words)
        w, n_first = 0, 0
        for b in beats:
            w += len(re.findall(r"\w+", b.get("say", "")))
            n_first += max(1, len(b.get("show") or []))
            if w > 75:
                break
        if n_first < 12:
            problems.append(f"first 30 s has only {n_first} pictures (need 12+: a cut about every 2 s)")
        # never two photo sources back to back (retention)
        for (i1, t1), (i2, t2) in zip(seq, seq[1:]):
            if t1.startswith(("search:", "cat:")) and t2.startswith(("search:", "cat:")):
                problems.append(f"beats {i1}-{i2}: two photos back to back ({t1}, {t2})")
        # hand-pinned Hamdan events must exist in the library
        try:
            import json
            ev = json.loads((ROOT / "state" / "events.json").read_text(encoding="utf-8"))
            for _, t in seq:
                if t.startswith("key:") and t[4:] not in ev:
                    problems.append(f"{t[:60]} is not an event in state/events.json")
        except (OSError, ValueError):
            pass
    if not (s.get("thumbnail") or {}).get("scene"):
        problems.append("no thumbnail scene (thumbnail must show his face + the story subject)")
    total = sum(len(re.findall(r"\w+", b.get("say", ""))) for b in beats)
    if total < 1320:
        problems.append(f"script only {total} words (need 1,320+)")
    return problems


def lint_story_id(story_id: str) -> list[str]:
    """Problems for the story file with this id ([] when it has none or there is no file: automatic stories)."""
    for p in STORIES.glob("*.yaml"):
        try:
            if str((yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("id")) == story_id:
                return lint(p)
        except (OSError, yaml.YAMLError):
            continue
    return []


def main() -> int:
    rc = 0
    for a in sys.argv[1:]:
        p = Path(a) if Path(a).exists() else next(iter(STORIES.glob(f"*{a}*.yaml")), None)
        if not p:
            print("no such story:", a)
            rc = 1
            continue
        pr = lint(p)
        print(p.name, "OK" if not pr else "FAILED")
        for x in pr:
            print("  -", x)
        rc |= bool(pr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
