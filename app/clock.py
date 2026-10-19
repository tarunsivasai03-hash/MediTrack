"""
The hospital's local time.

Slots, "today", check-in times and reminders all depend on what day and time it is *at the
hospital*, not on the server's clock (Docker and cloud hosts run in UTC). TIMEZONE names the
hospital's zone, e.g. "Asia/Kolkata". Leave it empty to use the server's own local time.
Values are naive datetimes: they are local wall-clock time, like the appointment slots.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, has_app_context

_zones: dict[str, ZoneInfo] = {}


def zone() -> ZoneInfo | None:
    name = current_app.config.get("TIMEZONE") if has_app_context() else None
    if not name:
        return None
    if name not in _zones:
        _zones[name] = ZoneInfo(name)
    return _zones[name]


def check_timezone(name: str | None) -> None:
    """Fail at start-up (not on the first booking) if TIMEZONE is not a real zone."""
    if name:
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise RuntimeError(f"TIMEZONE {name!r} is not a valid time zone name (e.g. Asia/Kolkata).") from exc


def now() -> datetime:
    """Current local date and time at the hospital (naive)."""
    return datetime.now(zone()).replace(tzinfo=None)


def today() -> date:
    return now().date()
