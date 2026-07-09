from datetime import datetime, timezone, timedelta
from vp.schedule import parse_tz_offset, calculate_next_slot

def test_parse_tz_offset():
    assert parse_tz_offset("GMT") == timezone.utc
    assert parse_tz_offset("GMT+6") == timezone(timedelta(hours=6))
    assert parse_tz_offset("GMT-5") == timezone(timedelta(hours=-5))
    assert parse_tz_offset("GMT+5:30") == timezone(timedelta(hours=5, minutes=30))
    assert parse_tz_offset("INVALID") == timezone.utc

def test_calculate_next_slot_same_day_future():
    # Thursday 12:00 UTC (18:00 GMT+6)
    ref = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    slots = [{
        "days": ["Thu"],
        "hour": 8,
        "minute": 0,
        "am_pm": "PM",
        "timezone": "GMT+6"
    }]
    # Next slot should be Thursday 8:00 PM GMT+6 = 14:00 UTC
    res = calculate_next_slot(ref, slots)
    assert res == datetime(2026, 7, 9, 14, 0, tzinfo=timezone.utc)

def test_calculate_next_slot_same_day_past():
    # Thursday 15:00 UTC (21:00 GMT+6)
    ref = datetime(2026, 7, 9, 15, 0, tzinfo=timezone.utc)
    slots = [{
        "days": ["Thu", "Sat"],
        "hour": 8,
        "minute": 0,
        "am_pm": "PM",
        "timezone": "GMT+6"
    }]
    # Next slot should be Saturday 8:00 PM GMT+6 = Saturday 14:00 UTC
    res = calculate_next_slot(ref, slots)
    assert res == datetime(2026, 7, 11, 14, 0, tzinfo=timezone.utc)

def test_calculate_next_slot_multiple_slots():
    # Thursday 12:00 UTC (18:00 GMT+6)
    ref = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    slots = [
        {
            "days": ["Thu"],
            "hour": 10,
            "minute": 0,
            "am_pm": "PM",
            "timezone": "GMT+6" # 16:00 UTC
        },
        {
            "days": ["Thu"],
            "hour": 8,
            "minute": 0,
            "am_pm": "PM",
            "timezone": "GMT+6" # 14:00 UTC (earlier)
        }
    ]
    res = calculate_next_slot(ref, slots)
    assert res == datetime(2026, 7, 9, 14, 0, tzinfo=timezone.utc)
