"""Words for the picture posts.

* Real quotes of Prince Hamdan, collected automatically from the English news of the Government of Dubai Media
  Office (mediaoffice.ae): only text inside quotation marks that the article attributes to him. Shown with
  "— Sheikh Hamdan bin Mohammed".
* Fan lines (assets/fazza_lines.yaml): short lines about him in the style of fan pages. Never attributed to him.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import random
import re
from datetime import date

import requests
import yaml

from .config import ASSETS_DIR

log = logging.getLogger("lane.quotes")

NEWS_API = "https://www.mediaoffice.ae/api/feature/news/list"
NEWS_SOURCE = "{359D464C-9043-4B0A-8784-F63812DE41BD}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
           "Content-Type": "application/json; charset=utf-8"}
SAID = re.compile(r"said|added|noted|stated|affirmed|emphasi[sz]ed|wrote|highlighted|stressed|explained|remarked",
                  re.I)
REST_DAYS = 10
PEOPLE = re.compile(r"Hamdan bin Mohammed|Sheikh Hamdan|Mohammed bin Rashid|Sheikh Mohammed|Maktoum bin Mohammed|"
                    r"Ahmed bin Mohammed|Mansour bin Mohammed|Mohamed bin Zayed|Khaled bin Mohamed|Saif bin Zayed|"
                    r"Mansour bin Zayed|Abdullah bin Zayed|Hamdan bin Zayed|Theyab bin Mohamed|Ahmed bin Saeed|"
                    r"Mattar Al Tayer|Abdulla Al Basti|Omar Sultan Al Olama|Mohammad Al Gergawi|Helal Al Marri|"
                    r"[A-Z][a-z]+ (?:bin|bint) [A-Z][a-z]+|(?:Dr\.?|Mr\.?|Ms\.?|Mrs\.?) [A-Z][a-z]+")


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _best_sentence(q: str) -> str:
    """The most quotable sentence of a quote (short enough for a picture)."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", q)
             if 30 <= len(p.strip()) <= 150 and p.strip()[0].isupper() and "-it " not in p]
    if not parts:
        return q if len(q) <= 150 else ""
    keywords = re.compile(r"\b(we|our|us|people|nation|future|dubai|proud|together|dream|world|life)\b", re.I)
    return max(parts, key=lambda p: (len(keywords.findall(p)), -abs(len(p) - 90)))


def harvest(cursors: dict, pages: int = 3) -> int:
    """Add new real quotes from the Media Office news to cursors["quotes"]. Returns how many were added."""
    bank: list[dict] = cursors.setdefault("quotes", [])
    known = {q["id"] for q in bank}
    if not bank:
        pages = max(pages, 30)          # first run: read further back to start with a good collection
    added = 0
    for page in range(1, pages + 1):
        try:
            r = requests.post(NEWS_API, headers=HEADERS, timeout=40, data=json.dumps({
                "sourceID": NEWS_SOURCE, "pageNo": page, "pageSize": 30, "culture": "en", "itemID": "all",
                "SubCategory": "Category", "newsDate": "", "newsKeyword": ""}))
            data = json.loads(json.loads(r.text)) if r.text.startswith('"') else r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("Media Office news unavailable: %s", e)
            break
        items = data.get("Items") or []
        if not items:
            break
        for it in items:
            text = _plain(it.get("FullText", ""))
            # his full name contains his father's ("Hamdan bin Mohammed bin Rashid"): shorten it before attributing
            text = re.sub(r"Hamdan bin Mohammed bin Rashid Al Maktoum|Hamdan bin Mohammed bin Rashid",
                          "Hamdan bin Mohammed", text)
            if "Hamdan bin Mohammed" not in text:
                continue
            for m in re.finditer(r"[“\"]([^”\"]{40,400})[”\"]", text):
                before, after = text[max(0, m.start() - 180): m.start()], text[m.end(): m.end() + 80]
                if not SAID.search(before + after):
                    continue
                # the last person named before the quote must be Prince Hamdan himself (articles often quote his
                # father or other leaders, who are also "His Highness")
                wide = text[max(0, m.start() - 600): m.start()]
                names = PEOPLE.findall(wide)
                if not names or not re.search(r"Hamdan bin Mohammed|Sheikh Hamdan|Hamdan", names[-1]):
                    continue
                line = _best_sentence(m.group(1).strip())
                qid = hashlib.md5(line.lower().encode()).hexdigest()[:12]
                if not line or qid in known:
                    continue
                bank.append({"id": qid, "text": line, "title": _plain(it.get("NewsTitle", ""))[:120],
                             "link": "https://www.mediaoffice.ae" + it.get("DetailPageLink", ""), "used": ""})
                known.add(qid)
                added += 1
    return added


def fan_lines() -> list[str]:
    return [str(x) for x in yaml.safe_load((ASSETS_DIR / "fazza_lines.yaml").read_text(encoding="utf-8"))["lines"]]


def pick(cursors: dict, today: date, rng: random.Random) -> dict:
    """{"text", "by" ('' for fan lines), "link", "id"} — rested at least REST_DAYS since last used."""
    used: dict = cursors.setdefault("lines_used", {})

    def rested(key: str) -> bool:
        d = used.get(key, "")
        return not d or (today - date.fromisoformat(d)).days >= REST_DAYS

    real = [q for q in cursors.get("quotes", []) if rested("q:" + q["id"])]
    fans = [line for line in fan_lines() if rested("f:" + hashlib.md5(line.encode()).hexdigest()[:12])]
    if real and (rng.random() < 0.5 or not fans):
        q = rng.choice(real)
        used["q:" + q["id"]] = today.isoformat()
        return {"text": q["text"], "by": "Sheikh Hamdan bin Mohammed", "link": q.get("link", ""), "id": q["id"]}
    pool = fans or fan_lines()
    line = rng.choice(pool)
    fid = hashlib.md5(line.encode()).hexdigest()[:12]
    used["f:" + fid] = today.isoformat()
    return {"text": line, "by": "", "link": "", "id": fid}
