"""Random daily posting times: every Short in its own clock hour, plus one evening slot for the long-form episode."""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _random_in(day: date, window: list[str], tz: ZoneInfo, rng: random.Random) -> datetime:
    a, b = (_hm(x) for x in window)
    start = datetime.combine(day, a, tz)
    span = int((datetime.combine(day, b, tz) - start).total_seconds() // 60)
    return start + timedelta(minutes=rng.randint(0, max(0, span)), seconds=rng.randint(0, 59))


def day_slots(day: date, cfg: dict, rng: random.Random) -> list[datetime]:
    """Short posting times (timezone-aware) for one day.

    With gaps under an hour, each gap is drawn from [max(min_gap, 60 - minute), min(max_gap, 119 - minute)], which
    forces the next Short into the very next clock hour; with gaps of an hour or more every Short lands in a new
    hour anyway. Either way no two Shorts share an hour and the minutes keep wandering.
    """
    tz = ZoneInfo(cfg["timezone"])
    n = int(cfg["shorts_per_day"])
    lo, hi = cfg["gap_minutes"]
    t = _random_in(day, cfg["first_post_between"], tz, rng)
    slots = [t]
    for _ in range(n - 1):
        if lo >= 60:
            gap = rng.uniform(lo, hi)
        else:
            m = t.minute
            g_lo, g_hi = max(lo, 60 - m), min(hi, 119 - m)
            gap = rng.uniform(g_lo, g_hi) if g_hi >= g_lo else 60 - m + rng.uniform(0, 10)
        t = t + timedelta(minutes=gap)
        slots.append(t.replace(microsecond=0))
    return slots


def top_up(day: date, cfg: dict, existing: list[datetime], rng: random.Random,
           not_before: datetime) -> list[datetime]:
    """Extra Short times for a day planned with fewer Shorts than `shorts_per_day` (e.g. after the setting was
    raised). New times go into hours that have no Short yet, at least min_gap minutes from every other Short."""
    tz = ZoneInfo(cfg["timezone"])
    need = int(cfg["shorts_per_day"]) - len(existing)
    if need <= 0:
        return []
    min_gap = timedelta(minutes=cfg["gap_minutes"][0])
    first = _hm(cfg["first_post_between"][0])
    start = datetime.combine(day, first, tz).replace(minute=0)
    taken = list(existing)
    used_hours = {(t.astimezone(tz).date(), t.astimezone(tz).hour) for t in existing}
    hours = [start + timedelta(hours=h) for h in range(22)]
    rng.shuffle(hours)
    added = []
    for h in hours:
        if len(added) >= need:
            break
        if (h.date(), h.hour) in used_hours:
            continue
        for _ in range(12):
            t = (h + timedelta(minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))).replace(microsecond=0)
            if t > not_before and all(abs(t - o) >= min_gap for o in taken):
                taken.append(t)
                added.append(t)
                used_hours.add((t.date(), t.hour))
                break
    return sorted(added)


def long_slot(day: date, cfg: dict, shorts: list[datetime], rng: random.Random) -> datetime:
    """Evening time for the episode, kept at least 25 minutes away from any Short."""
    tz = ZoneInfo(cfg["timezone"])
    a, b = (datetime.combine(day, _hm(x), tz) for x in cfg.get("long_between", ["18:00", "20:00"]))
    times = sorted(shorts)
    best, best_gap = None, timedelta(0)
    # the middle of the widest gap between two Shorts whose middle falls inside the evening window
    for prev, nxt in zip([a - timedelta(hours=3)] + times, times + [b + timedelta(hours=3)]):
        mid = prev + (nxt - prev) / 2
        if a <= mid <= b and nxt - prev > best_gap:
            best, best_gap = mid, nxt - prev
    if best is None:
        best = _random_in(day, cfg.get("long_between", ["18:00", "20:00"]), tz, rng)
    jitter = timedelta(minutes=rng.uniform(-3, 3)) if best_gap > timedelta(minutes=40) else timedelta(0)
    return min(max(best + jitter, a), b).replace(microsecond=0)


def target_days(now: datetime, cfg: dict) -> list[date]:
    tz = ZoneInfo(cfg["timezone"])
    today = now.astimezone(tz).date()
    return [today + timedelta(days=i) for i in range(int(cfg.get("days_ahead", 2)))]
