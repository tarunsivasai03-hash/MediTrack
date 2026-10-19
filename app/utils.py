"""
Utility functions shared across the application.

Includes slot generation logic used by the booking system and API.
"""

from datetime import datetime, date, time, timedelta
from urllib.parse import urlparse, urljoin

from flask import request

from app.models import Appointment


def generate_time_slots(
    work_start: time,
    work_end: time,
    slot_minutes: int,
    target_date: date,
    doctor_id: int,
) -> list[dict]:
    """
    Generate available time slots for a doctor on a given date.

    Builds every possible slot between work_start and work_end
    (inclusive start, exclusive end), then marks slots that are
    already booked (non-cancelled) as unavailable.

    Args:
        work_start:   Doctor's shift start time.
        work_end:     Doctor's shift end time.
        slot_minutes: Duration of each slot in minutes.
        target_date:  The calendar date to check.
        doctor_id:    Doctor PK for querying existing appointments.

    Returns:
        List of dicts: [{"time": "09:00", "available": True}, ...]
    """
    # Fetch booked (non-cancelled) slots for this doctor on this date
    booked = {
        appt.time
        for appt in Appointment.query.filter(
            Appointment.doctor_id == doctor_id,
            Appointment.date == target_date,
            Appointment.status != "cancelled",
        ).all()
    }

    slots = []
    current = datetime.combine(target_date, work_start)
    end = datetime.combine(target_date, work_end)
    delta = timedelta(minutes=slot_minutes)

    while current + delta <= end:
        slot_time = current.time()
        slots.append(
            {
                "time": slot_time.strftime("%H:%M"),
                "available": slot_time not in booked,
            }
        )
        current += delta

    return slots


def is_safe_redirect(target: str | None) -> bool:
    """True only for same-host http(s) targets, to prevent open redirects via ?next=."""
    if not target:
        return False
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return test.scheme in ("http", "https") and ref.netloc == test.netloc


class ListPage:
    """Pagination over an in-memory list, with the same attributes the templates use
    for Flask-SQLAlchemy's Pagination (items, page, pages, has_prev, ...)."""

    def __init__(self, items: list, page: int, per_page: int):
        self.total = len(items)
        self.per_page = per_page
        self.pages = max((self.total + per_page - 1) // per_page, 1)
        self.page = min(max(page, 1), self.pages)
        start = (self.page - 1) * per_page
        self.items = items[start:start + per_page]

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def prev_num(self) -> int:
        return self.page - 1

    @property
    def next_num(self) -> int:
        return self.page + 1

    def iter_pages(self, left_edge=1, right_edge=1, left_current=1, right_current=2):
        last = 0
        for n in range(1, self.pages + 1):
            if (n <= left_edge or self.page - left_current <= n <= self.page + right_current
                    or n > self.pages - right_edge):
                if last + 1 != n:
                    yield None
                yield n
                last = n
