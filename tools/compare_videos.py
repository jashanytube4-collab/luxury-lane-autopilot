"""Compare every new video with the ones before it, WITHOUT Claude (runs from 'Compute Always On.bat').
Reads Studio (long + Shorts lists), stores a history, and writes rocky/reports/compare-DATE.md:
  - each long video aged 2+ days: views/day and revenue per 1,000 views vs the median of the 10 long videos before it
  - Shorts: views per Short by type (narrated / moment / montage / remix) and the best + worst
  - verdict lines (BETTER / WORSE / SAME) and a one-line summary appended to rocky/progress.md
Rocky reads the report and decides what to keep or undo (rules 30c, 45, 51)."""
import json
import re
import statistics as st
import sys
from datetime import date, datetime
from pathlib import Path

from tools.daily_metrics import read

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "rocky" / "video_history.json"
REPORTS = ROOT / "rocky" / "reports"


def age_days(s: str) -> int | None:
    for fmt in ("%b %d, %Y", "%d %b %Y"):
        try:
            return max(0, (date.today() - datetime.strptime(s.strip(), fmt).date()).days)
        except ValueError:
            pass
    return None


def kinds() -> dict[str, str]:
    """Short video id -> narrated / moment / montage / remix, from the day plans."""
    out = {}
    for f in (ROOT / "state" / "days").glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for s in d.get("slots", []) if isinstance(d, dict) else []:
            if isinstance(s, dict) and s.get("video_id"):
                out[s["video_id"]] = s.get("kind") or "other"
    return out


def per_day(r: dict) -> float:
    a = age_days(r["date"])
    return r["views"] / max(1, a) if a is not None else 0.0


def main() -> int:
    if (REPORTS / f"compare-{date.today().isoformat()}.md").exists() and "--force" not in sys.argv:
        return 0
    try:
        longs, shorts = read("long"), read("short")
    except Exception as e:  # noqa: BLE001
        print("compare failed:", e)
        return 1
    if not longs:
        print("no rows read (Studio signed out or busy)")
        return 1
    today = date.today().isoformat()
    hist = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else {}
    for r in longs + shorts:
        h = hist.setdefault(r["id"], {"title": r["title"], "date": r["date"], "snaps": {}})
        h["snaps"][today] = {"views": r["views"], "rev": r["rev"]}
    HIST.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")

    lines = [f"# Video comparison {today}", "", "## Long videos (views/day since publish vs median of the 10 before it)"]
    verdicts = []
    for i, r in enumerate(longs[:6]):
        a = age_days(r["date"])
        base = [per_day(x) for x in longs[i + 1:i + 11] if (age_days(x["date"]) or 0) >= 2]
        if a is None or a < 2 or len(base) < 3:
            lines.append(f"- {r['title']} ({r['date']}): too new to judge ({r['views']} views)")
            continue
        m, v = st.median(base), per_day(r)
        v_ = "BETTER" if v > 1.2 * m else "WORSE" if v < 0.8 * m else "SAME"
        rpm = r["rev"] / r["views"] * 1000 if r["views"] else 0
        lines.append(f"- **{v_}** {r['title']} ({r['date']}): {r['views']} views, {v:.0f}/day vs usual {m:.0f}/day, "
                     f"Rs{r['rev']:.0f} (Rs{rpm:.0f} per 1,000 views)")
        verdicts.append(v_)

    k = kinds()
    groups: dict[str, list[int]] = {}
    for r in shorts:
        if (age_days(r["date"]) or 0) >= 2:
            groups.setdefault(k.get(r["id"], "other"), []).append(r["views"])
    lines += ["", "## Shorts (2+ days old) by type"]
    for kind, vs in sorted(groups.items(), key=lambda x: -st.mean(x[1])):
        lines.append(f"- {kind}: {len(vs)} Shorts, average {st.mean(vs):.0f} views, median {st.median(vs):.0f}, best {max(vs)}")
    old = sorted((r for r in shorts if (age_days(r["date"]) or 0) >= 2), key=lambda r: -r["views"])
    if old:
        lines += ["", "Best 3: " + "; ".join(f"{r['views']} {r['title'][:40]}" for r in old[:3]),
                  "Worst 3: " + "; ".join(f"{r['views']} {r['title'][:40]}" for r in old[-3:])]
    lines += ["", "Next step (Rocky): copy what the BETTER videos did (topic, title style, thumbnail, hook) into the next one; "
              "undo what the WORSE ones changed; write LEARN lines in rocky/progress.md."]
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"compare-{today}.md").write_text("\n".join(lines), encoding="utf-8")
    best = max(groups, key=lambda x: st.mean(groups[x])) if groups else "n/a"
    with (ROOT / "rocky" / "progress.md").open("a", encoding="utf-8") as f:
        f.write(f"\n- {today} COMPARE: new long videos {verdicts.count('BETTER')} better / {verdicts.count('SAME')} same / "
                f"{verdicts.count('WORSE')} worse than usual; best Short type: {best}. Details rocky/reports/compare-{today}.md\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
