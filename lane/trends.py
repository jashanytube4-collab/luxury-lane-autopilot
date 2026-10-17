"""Finds fresh story topics automatically (used when the hand-written story bank is empty).

Two signals, both free:
  * what people are searching for right now: YouTube search suggestions for Fazza / Prince Hamdan. A phrase that
    was not in the suggestions before is a rising topic;
  * what just happened: the newest official events from hamdan.ae, preferring human moments (visits, surprises,
    honours, children, heroes) over routine government meetings.

A topic is only chosen when official articles can back it (so the script is written from real facts, never from a
rumour), and a topic rests 60 days after it was used.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import requests

log = logging.getLogger("lane.trends")

SEEDS = ["fazza", "prince hamdan", "sheikh hamdan", "fazza new", "sheikh hamdan new", "prince hamdan latest",
         "dubai crown prince", "fazza 2026", "sheikh hamdan today"]
RELEVANT = re.compile(r"fazza|hamdan|crown prince|faz3", re.I)
NOT_TOPIC = set("""fazza faz3 prince princes hamdan sheikh shaikh dubai dubais crown of the a an and with in on his him
he bin mohammed mohammad al maktoum new latest video videos today news 2024 2025 2026 2027 status edit edits song songs
ringtone shorts short whatsapp tiktok reels reel attitude poem poems poetry music dance ff gaming photo photos pics pic
channel youtube official hd 4k fan fans page live lifestyle hindi urdu tamil malayalam arabic english full mass entry
viral trending best top what who is why how when where ki ka ke hai kon prince's sheikh's fazza's real name
uae emirates emirati first second third one two wife wives brother brothers sister son sons daughter mother father
family kids children child baby twins house home palace car cars collection net worth life story love marriage
wedding girlfriend age birthday interview speech talking voice funny moments crying angry eating food rashid saeed
zayed mohamed khaled ahmed hasher hind sheikha city day visit visits meets meeting public people abu dhabi king
games world style look looks night speaking royal entry status""".split())
HUMAN = re.compile(r"visit|hospital|surpris|honou?r|hero|child|kids|student|famil|condolen|meets? .*(boy|girl|fan)|"
                   r"invite|thank|praise|brave|rescue|young|elder|wedding|birthday|ramadan|eid|horse|endurance|"
                   r"falcon|skydiv|adventure|record|first", re.I)
ROUTINE = re.compile(r"council|committee|approv|strateg|budget|resolution|decree|framework|meeting|session|"
                     r"forum|summit|exhibition|launch(es)? (the )?\w+ (plan|policy|strategy)", re.I)
REST_DAYS = 60
# searches about his private life cannot be answered from official news, so they never become automatic topics
PERSONAL = set("""wife wives wedding marriage married girlfriend death died passed son daughter brother sister
mother father family kids children baby twins house home palace car cars driving worth age birthday young old
childhood crying funny angry eating voice office foundation lottery giveaway handsome""".split())


@dataclass
class Topic:
    key: str                 # remembered so the topic rests REST_DAYS after use
    label: str               # what the story is about, in words (for the writer)
    events: list[str]        # official events that back it, newest first
    why: str                 # "trending: ..." / "news: ..." (for the run report)
    terms: list[str] = field(default_factory=list)


def suggestions(seeds: list[str] = SEEDS) -> list[str]:
    out: dict[str, None] = {}
    for s in seeds:
        for q in [s + " "] + [f"{s} {c}" for c in "abcdefghiklmnoprstuwy"]:
            try:
                r = requests.get("https://suggestqueries.google.com/complete/search",
                                 params={"client": "firefox", "ds": "yt", "q": q, "hl": "en"},
                                 headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                for x in json.loads(r.text)[1]:
                    if RELEVANT.search(x):
                        out[x.lower().strip()] = None
            except (requests.RequestException, ValueError, IndexError):
                continue
    return list(out)


def topic_terms(phrase: str) -> list[str]:
    words = re.findall(r"[a-z]+", phrase.lower())
    return [w for w in words if w not in NOT_TOPIC and len(w) > 3]


def _days(d: str, today: date) -> int:
    try:
        return (today - date.fromisoformat(d[:10])).days
    except ValueError:
        return 9999


def pick(events: dict, usage: dict, cursors: dict, today: date, phrases: list[str] | None = None) -> Topic | None:
    """The freshest topic that official events can back. Updates cursors["trend_seen"] with today's phrases."""
    seen: dict[str, str] = cursors.setdefault("trend_seen", {})
    done: dict[str, str] = cursors.setdefault("auto_topics_done", {})
    resting = {k for k, d in done.items() if _days(d, today) < REST_DAYS}
    first_run = not seen
    if phrases is None:
        phrases = suggestions()
    for p in phrases:
        seen.setdefault(p, today.isoformat())
    usable = {k: e for k, e in events.items() if e.get("news_id") and not usage.get(k, {}).get("no_hamdan")
              and (e.get("video") or len(e.get("photos", [])) >= 3)}
    cands: list[tuple[float, Topic]] = []

    # 1) rising searches that official events can explain: every specific word of the search must be in the event
    topics: dict[str, tuple[float, str]] = {}          # "india" / "uzbekistan" / "flydubai pilot" -> (heat, phrase)
    for p in phrases:
        terms = topic_terms(p)
        if not terms or len(terms) > 3 or PERSONAL & set(re.findall(r"[a-z]+", p)):
            continue
        key = " ".join(sorted(terms))
        heat = 3.0 if (not first_run and _days(seen[p], today) <= 14) else 1.0
        if heat > topics.get(key, (0.0, ""))[0]:
            topics[key] = (heat, p)
    for key, (heat, phrase) in topics.items():
        terms = key.split()
        if any(f"term:{t}" in resting for t in terms):
            continue
        rxs = [re.compile(r"\b" + re.escape(t) + r"(s|es|n)?\b", re.I) for t in terms]
        # the word must be a name in the official headline ("Uzbekistan", "Indian", "Salman"), not a common word
        match = sorted((k for k, e in usable.items()
                        if all((m := rx.search(e.get("title", ""))) and m.group(0)[0].isupper() for rx in rxs)),
                       key=lambda k: usable[k].get("date", ""), reverse=True)
        if not match or len(match) > 40:     # nothing official, or a word that is everywhere in the news
            continue
        fresh = max(0.0, 1.0 - _days(usable[match[0]].get("date", ""), today) / 120)
        score = heat * 2 + min(len(match), 5) * 0.4 + fresh * 2
        cands.append((score, Topic(f"term:{key}", f'what fans are searching for: "{phrase}"', match[:6],
                                   f"trending search: {phrase}", terms)))

    # 2) the newest human moments from the official news
    for k, e in usable.items():
        age = _days(e.get("date", ""), today)
        if age > 12 or f"event:{k}" in resting or usage.get(k, {}).get("long"):
            continue
        title = e.get("title", "")
        human = bool(HUMAN.search(title)) and not ROUTINE.search(title)
        score = (5.0 if human else 1.0) + (12 - age) * 0.3 + (0.5 if e.get("video") else 0)
        cands.append((score, Topic(f"event:{k}", f'this official moment: "{title}"', [k], f"fresh news: {title}")))

    # 3) his own newest Instagram videos (personal moments fans love most), told around the video itself
    for k, e in events.items():
        if e.get("source") != "instagram" or not e.get("about") or f"event:{k}" in resting:
            continue
        if usage.get(k, {}).get("long") or not k.startswith("ig:faz3:"):
            continue
        age = _days(e.get("date", ""), today)
        if age > 6:
            continue
        tags = set(e.get("tags") or [])
        related = sorted((k2 for k2, e2 in usable.items()
                          if len(tags & set(re.findall(r"[a-z]+", e2.get("title", "").lower()))) >= 2),
                         key=lambda k2: usable[k2].get("date", ""), reverse=True)[:4]
        score = 9.0 + (6 - age) * 0.4 + min(e.get("likes", 0) / 200000, 3.0)
        cands.append((score, Topic(f"event:{k}", f'his own new Instagram video: {e["about"]}', [k] + related,
                                   f"new on @faz3: {e['about'][:80]}")))

    if not cands:
        return None
    cands.sort(key=lambda c: -c[0])
    best = cands[0][1]
    log.info("auto story topic: %s (score %.1f; %d candidates)", best.why, cands[0][0], len(cands))
    return best


def mark_used(cursors: dict, topic: Topic, today: date) -> None:
    cursors.setdefault("auto_topics_done", {})[topic.key] = today.isoformat()
    for t in topic.terms:
        cursors["auto_topics_done"][f"term:{t}"] = today.isoformat()
