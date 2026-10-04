import random
from datetime import date

from lane.schedule import day_slots, long_slot

CFG20 = {"timezone": "Asia/Dubai", "shorts_per_day": 20, "first_post_between": ["07:00", "09:00"],
         "gap_minutes": [45, 70]}
CFG10 = {"timezone": "Asia/Dubai", "shorts_per_day": 10, "first_post_between": ["08:00", "09:30"],
         "gap_minutes": [75, 105], "long_between": ["18:00", "20:00"]}


def _check(cfg, lo, hi):
    for seed in range(2000):
        slots = day_slots(date(2026, 10, 1), cfg, random.Random(seed))
        assert len(slots) == cfg["shorts_per_day"]
        hours = [s.hour for s in slots]
        assert len(set(hours)) == len(hours), "two Shorts share an hour"
        for a, b in zip(slots, slots[1:]):
            gap = (b - a).total_seconds() / 60
            assert lo <= gap <= hi + 0.01, gap


def test_twenty_a_day():
    _check(CFG20, 45, 70)


def test_ten_a_day_and_long_slot():
    _check(CFG10, 75, 105)
    for seed in range(500):
        rng = random.Random(seed)
        shorts = day_slots(date(2026, 10, 1), CFG10, rng)
        lt = long_slot(date(2026, 10, 1), CFG10, shorts, rng)
        assert all(abs((lt - s).total_seconds()) >= 25 * 60 for s in shorts)
        assert 18 <= lt.hour <= 22


def test_minutes_stay_random():
    minutes = [s.minute for seed in range(200) for s in day_slots(date(2026, 10, 1), CFG20, random.Random(seed))]
    late = [m for m in minutes if m >= 30]
    assert len(late) > len(minutes) * 0.2


def test_top_up_fills_free_hours_with_gaps():
    from datetime import datetime, timedelta, timezone
    from lane.schedule import top_up
    cfg = dict(CFG10, shorts_per_day=20, gap_minutes=[45, 70], first_post_between=["07:00", "08:30"])
    for seed in range(300):
        rng = random.Random(seed)
        existing = day_slots(date(2026, 10, 6), CFG10, rng)
        past = datetime(2026, 10, 1, tzinfo=timezone.utc)
        extra = top_up(date(2026, 10, 6), cfg, existing, rng, past)
        allt = sorted(existing + extra)
        assert len(extra) >= 4
        assert len({(t.date(), t.hour) for t in allt}) == len(allt)
        assert all((b - a) >= timedelta(minutes=45) for a, b in zip(allt, allt[1:]))
