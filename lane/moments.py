"""'Moment' Shorts: what the most-viewed Fazza Shorts actually are (27M, 11M, 10M, 5.8M views...).

Not narrated news — one real 5-15 second moment of him (arriving, greeting people, with his father, driving),
vertical, centred on him, set to music, short enough that people watch it loop. Titles are simple and warm
("Sheikh Hamdan ❤️ #fazza #faz3"), and never repeat.
"""
from __future__ import annotations

import random
import re

EMOJI = ["❤️", "🤍", "👑", "🇦🇪", "🦅", "✨", "🔥", "💙", "😍", "🥀", "💫", "🫡"]
HASHTAGS = [
    "#fazza #faz3 #sheikhhamdan #dubai #shorts",
    "#sheikhhamdan #fazza #royals #dubaicrownprince #shorts",
    "#faz3 #fazza #dubai #princehamdan #ytshorts",
    "#fazza #sheikhhamdan #crownprince #uae #shorts",
    "#princehamdan #faz3 #royalfamily #dubai #shorts",
    "#fazza #faz3 #hamdan #dubaiprince #trending",
]
GENERAL = [
    "Sheikh Hamdan", "Fazza", "Prince Hamdan", "Sheikh Hamdan Bin Mohammed", "The Crown Prince of Dubai",
    "Fazza's Simplicity", "Prince Hamdan's Humble Side", "Fazza Style", "Dubai's Crown Prince",
    "The Most Loved Prince", "Fazza Moments", "Sheikh Hamdan's Charm", "Royal Vibes", "Fazza Respect",
    "When Fazza Arrives", "Prince Hamdan in Real Life", "Fazza Never Changes", "The Prince Everyone Loves",
    "Fazza's Smile", "This Is Why Everyone Loves Fazza", "A Prince Like No Other", "Fazza's Royal Entry",
    "Sheikh Hamdan Fazza", "Crown Prince Sheikh Hamdan", "Fazza in Dubai", "Pure Class: Fazza",
    "The Real Fazza", "Fazza Up Close", "Prince of Dubai", "Fazza's Calm Power",
]
CONTEXT = [   # (pattern on the official event title, titles that fit that moment)
    (r"mohammed bin rashid|his highness sheikh mohammed|father",
     ["Sheikh Hamdan With His Father", "Father & Son: Sheikh Mohammed & Fazza", "Fazza and His Father",
      "Like Father, Like Son", "Sheikh Mohammed & Sheikh Hamdan"]),
    (r"\beid\b|ramadan|iftar", ["Fazza on Eid", "Sheikh Hamdan's Ramadan", "Eid With Fazza"]),
    (r"horse|endurance|equestrian|race|cup|camel",
     ["Fazza & His Horses", "The Horseman Prince", "Fazza at the Races"]),
    (r"student|children|kids|school|youth|graduat",
     ["Fazza With the Kids", "Sheikh Hamdan With Students", "The Prince Who Inspires Kids"]),
    (r"army|military|police|defence|armed forces|cadet|officer",
     ["Fazza in Command", "The Defence Minister Prince", "Sheikh Hamdan With His Soldiers"]),
    (r"president|king|prime minister|receives|meets|visit",
     ["Fazza Meets World Leaders", "Sheikh Hamdan's Royal Welcome", "Fazza on the World Stage"]),
    (r"elder|citizen|people|majlis|condolence|well-wishers",
     ["Fazza With His People", "The Humble Crown Prince", "Fazza's Respect for Elders"]),
    (r"sport|run|ride|fitness|cycling|football|padel|games",
     ["Fazza the Athlete", "The Fittest Prince", "Fazza Never Stops"]),
]


def moment_runs(scenes: list) -> list[dict]:
    """Merge back-to-back scenes of him (sorted [(a, b, Sighting)]) into continuous moments 4-13 s long."""
    runs: list[dict] = []
    for a, b, s in scenes:
        last = runs[-1] if runs else None
        if last and a - last["b"] < 0.5 and b - last["a"] <= 13.0:
            n = last["n"] + 1
            last.update(b=b, size=(last["size"] * last["n"] + s.size) / n, cx=(last["cx"] * last["n"] + s.cx) / n, n=n)
        else:
            runs.append({"a": a, "b": b, "size": s.size, "cx": s.cx, "n": 1})
    return [r for r in runs if r["b"] - r["a"] >= 4.0]


def moment_title(event_title: str, recent: list[str], rng: random.Random) -> str:
    """A short, warm title in the style of the top Fazza Shorts; never one used recently."""
    low = event_title.lower()
    pool = [t for pat, ts in CONTEXT if re.search(pat, low) for t in ts]
    seen = {r.lower() for r in recent}
    for _ in range(200):
        base = rng.choice(pool) if pool and rng.random() < 0.6 else rng.choice(GENERAL)
        title = f"{base} {rng.choice(EMOJI)} {rng.choice(HASHTAGS)}"
        if title.lower() not in seen and f"{base} " not in " ".join(r for r in recent[-12:]):
            return title[:100]
    return f"{rng.choice(GENERAL)} {rng.choice(EMOJI)}{rng.choice(EMOJI)} {rng.choice(HASHTAGS)}"[:100]


def moment_description(ev: dict, rng: random.Random) -> str:
    lines = [
        "A real moment of H.H. Sheikh Hamdan bin Mohammed bin Rashid Al Maktoum (Fazza), Crown Prince of Dubai.",
        "Fazza, Crown Prince of Dubai, as his fans love him.",
        "Sheikh Hamdan bin Mohammed — Fazza — in a real moment from an official event.",
    ]
    when = f" ({ev['date'][:10]})" if ev.get("date") else ""
    return f"{rng.choice(lines)}\nFrom: {ev.get('title', '')[:120]}{when}\n\nSubscribe for a new Fazza moment every hour."


def moment_tags(ev: dict) -> list[str]:
    return ["Fazza", "Sheikh Hamdan", "Prince Hamdan", "faz3", "Fazza shorts", "Sheikh Hamdan status",
            "Dubai crown prince", "Fazza edit", "Hamdan bin Mohammed", "Dubai prince"]
