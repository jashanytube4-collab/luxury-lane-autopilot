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
    """Merge back-to-back scenes of him (sorted [(a, b, Sighting)]) into continuous moments 4-17 s long."""
    runs: list[dict] = []
    for a, b, s in scenes:
        last = runs[-1] if runs else None
        if last and a - last["b"] < 0.5 and b - last["a"] <= 17.0:
            n = last["n"] + 1
            last.update(b=b, size=(last["size"] * last["n"] + s.size) / n, cx=(last["cx"] * last["n"] + s.cx) / n, n=n)
        else:
            runs.append({"a": a, "b": b, "size": s.size, "cx": s.cx, "n": 1})
    return [r for r in runs if r["b"] - r["a"] >= 4.0]


POETIC = [   # the style of this channel's own best Shorts (25K-90K views): one poetic line about him and Dubai
    "A prince by title, a leader by action.", "From the sands of Dubai rises Hamdan's inspiration.",
    "Dubai's dreams, Hamdan's dedication.", "The mark of a great leader is the lives he inspires.",
    "Dubai's vision comes alive through Hamdan.", "Dubai's skyline reflects Hamdan's ambition.",
    "From the skies to the seas, Fazza does it all.", "The calmest kings rule the strongest.",
    "One city, one prince, endless possibilities.", "Hamdan's vision keeps Dubai moving forward.",
    "His energy speaks before he does.", "Every moment reflects a purpose.", "Dubai's pride, Hamdan's legacy.",
    "A leader who inspires without saying a word.", "Where innovation meets tradition: Dubai and Hamdan.",
    "Writing history through action.", "Some men carry respect without asking for it.",
    "Dubai's future shines through Hamdan's vision.", "Dubai grows, Hamdan inspires.",
    "Where royalty meets humanity.", "The world sees royalty; we see Fazza's heart.",
    "Strength, humility and elegance in one man.", "A prince's true legacy is written in smiles.",
    "Royal in name, humble in heart.", "Born to lead, raised to serve.", "Grace is his strongest weapon.",
    "Silence, focus, greatness.", "A crown worn with kindness.", "When Fazza walks in, everything changes.",
    "His calm is louder than any crowd.", "The heart of Dubai beats in him.", "Respect follows him everywhere.",
    "Not every prince is a legend. This one is.", "Every step, a lesson in leadership.",
    "A smile that carries a nation.", "Loyalty, faith and vision.", "The desert made him strong.",
    "The future of Dubai has a face.", "He leads, the city follows.", "Royalty that touches hearts.",
    "Elegance in every move.", "A man of honour, a prince of the people.", "Quiet power, endless respect.",
    "Dubai's heartbeat has a name.", "The poet who became a leader.", "Fazza: a name the world remembers.",
    "Leading from the front, always.", "The prince the whole Gulf admires.", "Dignity never goes out of style.",
    "He doesn't seek attention; he earns respect.", "One look, a thousand stories.",
    "The kind of presence money can't buy.", "Wisdom beyond his years.", "Built by the desert, loved by the world.",
    "Calm mind, strong heart, clear vision.", "A gentle soul with an iron will.",
    "Hamdan: tradition and tomorrow in one man.", "Where he goes, respect follows.",
    "A leader is measured by his kindness.", "Fazza's heart is bigger than his crown.",
]
SHORT_TAGS = ["#youtubeshorts #youtube", "#youtubeshorts #youtube #fazza", "#youtube #youtubeshorts",
              "#youtubeshorts #ytshorts", "#fazza #youtubeshorts #youtube", "#princehamdan #youtubeshorts #youtube"]


def moment_title(event_title: str, recent: list[str], rng: random.Random) -> str:
    """A poetic one-line title in the style of this channel's own best Shorts; not used in the last 4 days."""
    from .quotes import fan_lines
    pool = POETIC + [x for x in fan_lines() if len(x) <= 70]
    seen = " ".join(r.lower() for r in recent[-120:])
    fresh = [x for x in pool if x.lower().rstrip(".") not in seen] or pool
    line = rng.choice(fresh)
    emoji = rng.choice(["", "", " 👑", " ✨", " 🤍", " ❤️"])
    return f"{line}{emoji} {rng.choice(SHORT_TAGS)}"[:100]


def moment_description(ev: dict, rng: random.Random) -> str:
    lines = [
        "A real moment of H.H. Sheikh Hamdan bin Mohammed bin Rashid Al Maktoum (Fazza), Crown Prince of Dubai.",
        "Fazza, Crown Prince of Dubai, as his fans love him.",
        "Sheikh Hamdan bin Mohammed — Fazza — in a real moment from an official event.",
    ]
    when = f" ({ev['date'][:10]})" if ev.get("date") else ""
    origin = (f"Original video: Fazza (@faz3) on Instagram {ev.get('permalink', '')}".strip()
              if ev.get("source") == "instagram" else f"From: {ev.get('title', '')[:120]}{when}")
    return f"{rng.choice(lines)}\n{origin}\n\nSubscribe for a new Fazza moment every hour."


def moment_tags(ev: dict) -> list[str]:
    return ["Fazza", "Sheikh Hamdan", "Prince Hamdan", "faz3", "Fazza shorts", "Sheikh Hamdan status",
            "Dubai crown prince", "Fazza edit", "Hamdan bin Mohammed", "Dubai prince"]


GENERAL_CAPTIONS = [   # one story line on screen: makes the clip our own edit. Always true of a clip of him,
    "CALM. HUMBLE. FAZZA.", "THIS IS WHY MILLIONS LOVE HIM", "THE CROWN PRINCE, UP CLOSE",   # whoever else is in it
    "REAL MOMENTS. REAL FAZZA.", "PURE CLASS", "THE MOST LOVED PRINCE", "WATCH HOW HE CARRIES HIMSELF",
    "THE FUTURE RULER OF DUBAI", "NO ONE CARRIES IT LIKE HIM", "QUIET POWER", "THE PRINCE OF DUBAI",
    "SIMPLE. STRONG. FAZZA.", "LEADING WITHOUT SAYING A WORD", "CROWN PRINCE ENERGY", "ALWAYS RESPECTFUL",
    "THE MAN BEHIND THE TITLE", "FAZZA, AS HIS FANS SEE HIM", "THE PRESENCE OF A LEADER", "STILL SO HUMBLE",
    "DUBAI'S FAVOURITE PRINCE"]


def moment_caption(event_title: str, rng: random.Random) -> str:
    return rng.choice(GENERAL_CAPTIONS)
