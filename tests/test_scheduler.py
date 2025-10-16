import pytz
from src.core.scheduler import Scheduler
from datetime import datetime


def _state_from_dt(dt: datetime) -> str:
    """Map a tz-aware datetime to the Phase-1 state labels used in ARCHITECTURE.md."""
    h = dt.hour
    m = dt.minute
    # PREMARKET: 07:00 - 09:29
    if h < 9 or (h == 9 and m < 30):
        return "PREMARKET"
    # PAUSED: 09:30 - 09:44
    if h == 9 and 30 <= m < 45:
        return "PAUSED"
    # PICK_WINDOW: 09:45 - 09:49
    if h == 9 and 45 <= m < 50:
        return "PICK_WINDOW"
    # ACTIVE: 09:50 - 11:59 (and 10:00-12:00 window)
    if (h == 9 and m >= 50) or (10 <= h < 12):
        return "ACTIVE"
    # AFTERNOON: 12:00 - 15:44
    if 12 <= h < 15 or (h == 15 and m < 45):
        return "AFTERNOON"
    # POWER_HOUR: 15:45 - 16:00
    if h == 15 and m >= 45:
        return "POWER_HOUR"
    return "OTHER"


def test_scheduler_emits_expected_state_sequence():
    # Use 07:00 -> 16:00 simulated trading window (deterministic, no I/O)
    sched = Scheduler(config={"start_time": "07:00", "end_time": "16:00"})
    planned = sched._iter_planned_wakes(max_lines=None)

    # Build the sequence of first-occurrence states in chronological order
    seq = []
    for dt in planned:
        state = _state_from_dt(dt)
        if not seq or seq[-1] != state:
            seq.append(state)

    # Remove any trailing non-mapped entries (e.g., exact end_time stamps mapped to OTHER)
    while seq and seq[-1] == "OTHER":
        seq.pop()

    expected = ["PREMARKET", "PAUSED", "PICK_WINDOW", "ACTIVE", "AFTERNOON", "POWER_HOUR"]
    assert seq == expected, f"unexpected state sequence: {seq}"
