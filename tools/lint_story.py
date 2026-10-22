"""Check a story file BEFORE any render: python -m tools.lint_story <story id or file> [...]
Exit code 1 = do not render. Catches the mistakes that used to show up only after a 45-minute render."""
from __future__ import annotations
import re, sys
from collections import Counter
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
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
    # the first 30 s: ~75 words/30 s at normal pace -> the first beats; they must be moving footage, not photos
    words = 0
    for i, b in enumerate(beats):
        words += len(re.findall(r"\w+", b.get("say", "")))
        if words > 75:
            break
        for tok in b.get("show", []) or []:
            if str(tok).startswith(("search:", "cat:")):
                problems.append(f"photo '{tok}' in the first 30 s")
    if len(set(t for _, t in seq if t.startswith("sb-"))) + len({t for _, t in seq if t.startswith("clip:")}) < 40:
        problems.append("fewer than 40 different clips named")
    if not (s.get("thumbnail") or {}).get("scene"):
        problems.append("no thumbnail scene (thumbnail must show his face + the story subject)")
    total = sum(len(re.findall(r"\w+", b.get("say", ""))) for b in beats)
    if total < 1320:
        problems.append(f"script only {total} words (need 1,320+)")
    return problems


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
