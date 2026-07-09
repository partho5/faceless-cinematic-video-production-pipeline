from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta, time

def parse_tz_offset(tz_str: str) -> timezone:
    """Parse GMT offset strings (e.g. GMT, GMT+6, GMT-5, GMT+5:30) and return a timezone object."""
    if tz_str == "GMT":
        return timezone.utc
    match = re.match(r"^GMT([+-])(\d+)(?::(\d+))?$", tz_str)
    if not match:
        return timezone.utc
    sign, hours_str, mins_str = match.groups()
    hours = int(hours_str)
    mins = int(mins_str) if mins_str else 0
    delta = timedelta(hours=hours, minutes=mins)
    if sign == "-":
        delta = -delta
    return timezone(delta)


def calculate_next_slot(ref_time: datetime, slots: list[dict]) -> datetime:
    """Calculate the next available slot after ref_time (timezone-aware datetime, in UTC).

    Each slot in `slots` is a dict with keys:
    - days: list of weekdays (e.g. ["Sat", "Sun"])
    - hour: 1-12
    - minute: 0-59
    - am_pm: "AM" or "PM"
    - timezone: offset string (e.g. "GMT+6")

    Returns the next slot as a timezone-aware UTC datetime.
    """
    utc_dt, _, _ = calculate_next_slot_with_tz(ref_time, slots)
    return utc_dt


def calculate_next_slot_with_tz(
    ref_time: datetime, slots: list[dict]
) -> tuple[datetime, timezone, str]:
    """Like calculate_next_slot but also returns the winning slot's timezone.

    Returns:
        (utc_datetime, local_timezone_object, tz_str)
        e.g. (datetime(..., tzinfo=utc), timezone(+06:00), "GMT+6")
    """
    if not ref_time.tzinfo:
        ref_time = ref_time.replace(tzinfo=timezone.utc)

    # list of (utc_datetime, local_tz, tz_str)
    candidates: list[tuple[datetime, timezone, str]] = []

    for slot in slots:
        tz = parse_tz_offset(slot["timezone"])
        tz_str = slot["timezone"]
        ref_local = ref_time.astimezone(tz)

        # Parse time
        h = slot["hour"] % 12
        if slot["am_pm"].upper() == "PM":
            h += 12
        m = slot["minute"]

        # Search up to 15 days ahead
        for days_ahead in range(16):
            test_date = ref_local.date() + timedelta(days=days_ahead)
            weekday_name = test_date.strftime("%a")  # "Mon", "Tue", etc.
            if weekday_name in slot["days"]:
                candidate_local = datetime.combine(test_date, time(h, m), tzinfo=tz)
                if candidate_local > ref_local:
                    candidates.append(
                        (candidate_local.astimezone(timezone.utc), tz, tz_str)
                    )
                    break  # found earliest for this slot

    if not candidates:
        # Fallback to ref_time + 1 day, UTC
        return ref_time + timedelta(days=1), timezone.utc, "GMT"

    # Pick winner = earliest UTC time
    winner = min(candidates, key=lambda x: x[0])
    return winner
