"""Daily news reporter: finds fresh stories where Prince Hamdan (Fazza) is personally involved, and writes a short report.

    python -m tools.reporter            # today's report -> rocky/reports/YYYY-MM-DD.md (and prints it)

It reads only the headlines of public news feeds (Google News RSS, no account, no key) and never opens an article link.
A story is worth a video when it is recent, Hamdan is in it, it is a human moment (visits, help, rescue, family,
condolences, emotions, poems...) rather than an official launch/meeting/inauguration, and at least two different
publishers carry it. Rumours are marked as such. The report lists, for each story, the headlines and publishers, so the
script can be written from facts confirmed by several sources.
"""
from __future__ import annotations

import html
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
QUERIES = ["Sheikh Hamdan", "Fazza Dubai Crown Prince", "Prince Hamdan", "Hamdan bin Mohammed", "Sheikh Mohammed bin Rashid family",
           "فزاع حمدان بن محمد"]
HUMAN = re.compile(r"visit|hospital|bedside|rescue|saves?|helps?|met |meets|surprise|condolence|mourn|dies|death|passed away|"
                   r"funeral|accident|crash|born|birth|baby|wedding|married|marriage|divorce|poem|poet|emotional|tears|"
                   r"hug|kiss|gift|donat|adopt|child|children|mother|brother|sister|wife|daughter|son |family|"
                   r"horse|race|rides?|drives?|hunt|falcon|holiday|vacation|swim|dive|skydiv|cars?|lifestyle|"
                   r"زيارة|عزاء|وفاة|مولود|قصيدة", re.I)
OFFICIAL = re.compile(r"inaugurat|launch|approve|decree|appoint|MoU|agreement|signs?|sign |strategy|budget|law |"
                      r"summit|forum|chairs|council|committee|unveil|initiative|programme|program |plan |"
                      r"opens?|opening|ceremony of|awards? ceremony", re.I)
RUMOUR = re.compile(r"reportedly|rumou?r|alleged|sources say|claims?|unverified|viral|secret", re.I)


def fetch(query: str) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={quote(query)}+when:2d&hl=en&gl=US&ceid=US:en"
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(r.content)
    except Exception as e:  # noqa: BLE001
        print(f"feed failed for {query!r}: {e}", file=sys.stderr)
        return []
    out = []
    for it in root.iter("item"):
        title = html.unescape(it.findtext("title") or "")
        src = it.find("source")
        publisher = (src.text if src is not None else "") or title.rsplit(" - ", 1)[-1]
        title = re.sub(r"\s+-\s+[^-]+$", "", title)
        try:
            when = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:  # noqa: BLE001
            continue
        out.append({"title": title.strip(), "publisher": publisher.strip(), "when": when})
    return out


def words(t: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z]{4,}", t)} - {"sheikh", "hamdan", "prince", "dubai", "crown", "fazza",
                                                                  "with", "that", "from", "after", "this", "says", "news"}


def cluster(items: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for it in sorted(items, key=lambda x: x["when"], reverse=True):
        w = words(it["title"])
        for g in groups:
            gw = words(g[0]["title"])
            if w and gw and len(w & gw) / min(len(w), len(gw)) >= 0.4:
                g.append(it)
                break
        else:
            groups.append([it])
    return groups


def main() -> None:
    now = datetime.now(timezone.utc)
    seen, items = set(), []
    for q in QUERIES:
        for it in fetch(q):
            key = (it["title"].lower(), it["publisher"])
            if key in seen or now - it["when"] > timedelta(hours=60):
                continue
            seen.add(key)
            items.append(it)
    mine = [it for it in items if re.search(r"hamdan|fazza|فزاع|حمدان", it["title"], re.I)
            or re.search(r"Sheikh Mohammed", it["title"])]
    good, skipped = [], []
    for g in cluster(mine):
        pubs = {x["publisher"] for x in g}
        text = " ".join(x["title"] for x in g)
        human, official = bool(HUMAN.search(text)), bool(OFFICIAL.search(text))
        row = (g, pubs, human, official)
        (good if human and not official and len(pubs) >= 2 else skipped).append(row)
    day = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# Reporter — {day}", "", f"Checked {len(items)} fresh headlines ({len(mine)} about Hamdan / the family).", ""]
    if not good:
        lines.append("No strong video story today (needs: recent, Hamdan in it, a human moment, 2+ publishers).")
    for g, pubs, _, _ in good:
        rumour = " — RUMOUR: label it as one" if RUMOUR.search(" ".join(x["title"] for x in g)) else ""
        lines += [f"## {g[0]['title']}", f"- {len(pubs)} publishers: {', '.join(sorted(pubs))}{rumour}",
                  f"- first seen {min(x['when'] for x in g).astimezone().strftime('%d %b %H:%M')}"]
        lines += [f"- “{x['title']}” ({x['publisher']})" for x in g[:5]]
        lines.append("")
    near = [s for s in skipped if s[2] and not s[3]]
    if near:
        lines += ["## Watching (human moment, only one publisher so far)", ""]
        lines += [f"- {s[0][0]['title']} ({s[0][0]['publisher']})" for s in near[:6]]
        lines.append("")
    lines += ["## Skipped as official/launch news", ""] + [f"- {s[0][0]['title']}" for s in skipped if s[3]][:6]
    text = "\n".join(lines) + "\n"
    out = ROOT / "rocky" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{day}.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
