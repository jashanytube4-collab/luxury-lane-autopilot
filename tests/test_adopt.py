import random
from datetime import datetime, timedelta, timezone

from lane.schedule import adopt_uploads

TZ = timezone(timedelta(hours=4))


def _slot(h, m, typ="short", status="open"):
    return {"at": datetime(2026, 10, 22, h, m, tzinfo=TZ).isoformat(), "type": typ, "status": status}


def _up(i, h, m, short=True):
    return {"id": i, "at": datetime(2026, 10, 22, h, m, tzinfo=TZ).astimezone(timezone.utc), "short": short}


def test_lost_state_slot_is_adopted_not_refilled():
    slots = [_slot(6, 10), _slot(7, 0)]
    adopt_uploads(slots, [_up("a", 6, 12)], 31, 15, random.Random(1))
    assert slots[0]["status"] == "scheduled" and slots[0]["video_id"] == "a"
    assert slots[1]["status"] == "open"


def test_open_slot_close_to_unmatched_upload_is_skipped():
    slots = [_slot(6, 0)]
    adopt_uploads(slots, [_up("x", 6, 10, short=False)], 31, 15, random.Random(1))
    assert slots[0]["status"] == "skipped"


def test_day_never_exceeds_per_day():
    slots = [_slot(h, 0) for h in range(6, 12)]
    ups = [_up(f"u{i}", 15 + i, 30) for i in range(4)]       # 4 stray Shorts later in the day
    adopt_uploads(slots, ups, 8, 15, random.Random(1))
    assert sum(s["status"] == "open" for s in slots) == 4


def test_own_scheduled_shorts_are_counted_once():
    # 17 Shorts we made are scheduled in the plan AND visible on the channel: 13 open slots must stay open
    made = [dict(_slot(5, m), status="scheduled", video_id=f"v{m}") for m in range(17)]
    open_ = [_slot(h, 30) for h in range(8, 21)]
    ups = [_up(f"v{m}", 5, m) for m in range(17)]
    adopt_uploads(made + open_, ups, 30, 0, random.Random(1))
    assert all(s["status"] == "open" for s in open_)
