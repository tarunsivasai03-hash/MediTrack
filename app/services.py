"""
Business rules for appointments, kept out of the route handlers so they can
be tested directly and reused by the API.
"""

import secrets
import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy.exc import IntegrityError

from app import clock
from app import db
from app import notifications
from sqlalchemy import func

from app.models import Appointment, DoctorTimeOff, Patient, Rating, User
from app.utils import generate_time_slots

DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MAX_ADVANCE_DAYS = 60

# Allowed status changes: current -> set of next statuses
TRANSITIONS = {
    "pending": {"confirmed", "cancelled"},
    "confirmed": {"completed", "cancelled", "no_show"},
}


class BookingError(Exception):
    """A user-facing rule violation (message is safe to flash)."""


def parse_date(value: str):
    """Parse YYYY-MM-DD, returning None when invalid."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def doctor_works_on(doctor, target_date: date) -> bool:
    return DAY_ABBR[target_date.weekday()] in doctor.get_available_days_list()


def slots_for(doctor, target_date: date) -> list[dict]:
    """
    Slots for a day, [] if the doctor doesn't work that day.
    Past slots and slots overlapping the doctor's time off are marked unavailable.
    """
    if not doctor_works_on(doctor, target_date):
        return []
    slots = generate_time_slots(
        doctor.work_start, doctor.work_end, doctor.slot_minutes, target_date, doctor.id
    )
    time_off = DoctorTimeOff.query.filter_by(doctor_id=doctor.id, date=target_date).all()
    now = clock.now().strftime("%H:%M") if target_date == clock.today() else None
    step = timedelta(minutes=doctor.slot_minutes)

    for s in slots:
        start = datetime.strptime(s["time"], "%H:%M").time()
        end = (datetime.combine(target_date, start) + step).time()
        # a slot ending exactly at midnight wraps to 00:00; treat it as end of day
        if end <= start:
            end = time(23, 59, 59)
        if now and s["time"] <= now:
            s["available"] = False
        if any(t.blocks(start, end) for t in time_off):
            s["available"] = False
            s["blocked"] = True
    return slots


def _check_slot(doctor, target_date: date, time_str: str) -> time:
    """Shared rules for booking and rescheduling; returns the slot time or raises BookingError."""
    today = clock.today()
    if target_date < today:
        raise BookingError("You can't book a date in the past.")
    if target_date > today + timedelta(days=MAX_ADVANCE_DAYS):
        raise BookingError(f"Appointments can be booked up to {MAX_ADVANCE_DAYS} days ahead.")
    if not doctor.user.is_active:
        raise BookingError("This doctor is not currently accepting appointments.")
    if not doctor_works_on(doctor, target_date):
        raise BookingError(f"{doctor.user.name} doesn't work on {DAY_ABBR[target_date.weekday()]}s.")

    slot = next((s for s in slots_for(doctor, target_date) if s["time"] == time_str), None)
    if slot is None:
        raise BookingError("That time isn't a valid slot for this doctor.")
    if slot.get("blocked"):
        raise BookingError("The doctor is unavailable at that time.")
    if not slot["available"]:
        raise BookingError("That slot is no longer available. Please pick another.")
    return datetime.strptime(time_str, "%H:%M").time()


def _patient_clash(patient, target_date, slot_time, ignore_id=None):
    q = Appointment.query.filter(
        Appointment.patient_id == patient.id,
        Appointment.date == target_date,
        Appointment.time == slot_time,
        Appointment.status != "cancelled",
    )
    if ignore_id:
        q = q.filter(Appointment.id != ignore_id)
    return q.first()


def book_appointment(patient, doctor, target_date: date, time_str: str, reason: str) -> Appointment:
    """Validate and create a pending appointment, or raise BookingError."""
    slot_time = _check_slot(doctor, target_date, time_str)
    if _patient_clash(patient, target_date, slot_time):
        raise BookingError("You already have an appointment at that time.")

    appt = Appointment(
        patient_id=patient.id, doctor_id=doctor.id,
        date=target_date, time=slot_time, reason=reason.strip(),
    )
    db.session.add(appt)
    try:
        db.session.commit()
    except IntegrityError:  # lost a race for the same slot
        db.session.rollback()
        raise BookingError("That slot was just taken. Please pick another.")
    return appt


def reschedule_appointment(appt: Appointment, target_date: date, time_str: str) -> Appointment:
    """
    Move a live appointment to another slot with the same doctor.

    The booking keeps its id and reason, and returns to 'pending' so the doctor
    re-confirms the new time.
    """
    if appt.status not in ("pending", "confirmed"):
        raise BookingError("Only pending or confirmed appointments can be rescheduled.")
    if appt.date < clock.today():
        raise BookingError("Past appointments can't be rescheduled.")

    # Check this first: the booking's own slot reads as "taken" to _check_slot.
    if (target_date, time_str) == (appt.date, appt.time.strftime("%H:%M")):
        raise BookingError("That is already your appointment time. Pick a different slot.")
    slot_time = _check_slot(appt.doctor, target_date, time_str)
    if _patient_clash(appt.patient, target_date, slot_time, ignore_id=appt.id):
        raise BookingError("You already have an appointment at that time.")

    appt.date, appt.time, appt.status, appt.reminder_sent = target_date, slot_time, "pending", False
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise BookingError("That slot was just taken. Please pick another.")
    notifications.appointment_rescheduled(appt)
    return appt


def change_status(appt: Appointment, new_status: str, by_patient: bool = False) -> None:
    """Move an appointment along its lifecycle (emailing the patient) or raise BookingError."""
    if new_status not in TRANSITIONS.get(appt.status, set()):
        raise BookingError(f"A {appt.status} appointment can't become {new_status}.")
    if new_status == "no_show" and appt.date > clock.today():
        raise BookingError("An appointment in the future can't be marked as a no-show.")
    if new_status == "no_show" and appt.checked_in_at:
        raise BookingError("A patient who has checked in can't be marked as a no-show.")
    if new_status == "cancelled" and appt.started_at:
        raise BookingError("A consultation that has started can't be cancelled.")
    appt.status = new_status
    db.session.commit()
    if new_status == "confirmed":
        notifications.appointment_confirmed(appt)
    elif new_status == "cancelled":
        notifications.appointment_cancelled(appt, by_patient=by_patient)


def send_due_reminders(days_ahead: int = 1) -> int:
    """Email reminders for confirmed appointments `days_ahead` days away. Returns how many were sent.

    An appointment is only marked as reminded when the email was handed off, so a mail outage
    means the next run tries again instead of silently skipping the patient."""
    target = clock.today() + timedelta(days=days_ahead)
    due = Appointment.query.filter_by(date=target, status="confirmed", reminder_sent=False).all()
    sent = 0
    for appt in due:
        if notifications.appointment_reminder(appt):
            appt.reminder_sent = True
            sent += 1
    db.session.commit()
    return sent


# ── Patient accounts without a login (walk-ins, family members) ─────────────

def create_patient_without_login(name, age, gender, phone=None, blood_group=None,
                                 guardian=None, relationship=None) -> Patient:
    """
    A patient who cannot sign in: a walk-in registered by reception, or a family member
    managed by `guardian` (a User). Gets a real User row so all patient features work.
    """
    user = User(
        name=name.strip(), role="patient", can_login=False,
        email=f"patient-{uuid.uuid4().hex}@no-login.invalid",
    )
    user.set_password(secrets.token_urlsafe(24))   # unusable, nobody knows it
    db.session.add(user)
    db.session.flush()
    patient = Patient(
        user_id=user.id, age=age, gender=gender, phone=(phone or "").strip() or None,
        blood_group=blood_group or None,
        guardian_user_id=guardian.id if guardian is not None else None,
        relationship_label=(relationship or "").strip() or None,
    )
    db.session.add(patient)
    db.session.flush()
    return patient


# ── Front desk: check-in, walk-ins and the live queue ───────────────────────

def check_in(appt: Appointment) -> None:
    """Mark a patient as arrived (they join the waiting queue)."""
    if appt.date != clock.today():
        raise BookingError("Only today's appointments can be checked in.")
    if appt.status not in ("pending", "confirmed"):
        raise BookingError(f"A {appt.status} appointment can't be checked in.")
    if appt.checked_in_at:
        raise BookingError("This patient is already checked in.")
    appt.checked_in_at = clock.now()
    appt.status = "confirmed"
    db.session.commit()


def start_consultation(appt: Appointment) -> None:
    """Doctor calls the patient in."""
    if appt.date != clock.today() or appt.status not in ("pending", "confirmed"):
        raise BookingError("This appointment can't be started now.")
    if appt.started_at:
        raise BookingError("This consultation has already started.")
    now = clock.now()
    appt.started_at = now
    appt.checked_in_at = appt.checked_in_at or now
    appt.status = "confirmed"
    db.session.commit()


def create_walk_in(patient: Patient, doctor, reason: str) -> Appointment:
    """
    Register a same-day patient who has no booking and put them in the queue.

    Takes the doctor's next free slot today. If the day is already full, the visit is
    added just after the current time (queue order follows arrival, not slot time).
    """
    today = clock.today()
    if not doctor.user.is_active:
        raise BookingError("This doctor is not currently accepting patients.")
    if not doctor_works_on(doctor, today):
        raise BookingError(f"{doctor.user.name} doesn't work on {DAY_ABBR[today.weekday()]}s.")
    time_off = DoctorTimeOff.query.filter_by(doctor_id=doctor.id, date=today, start_time=None).first()
    if time_off:
        raise BookingError(f"{doctor.user.name} is on leave today.")

    now = clock.now()
    free = [s for s in slots_for(doctor, today) if s["available"]]
    candidates = (
        [datetime.strptime(free[0]["time"], "%H:%M").time()] if free else
        [(now.replace(second=0, microsecond=0) + timedelta(minutes=i)).time() for i in range(1, 180)]
    )
    for slot_time in candidates:
        taken = Appointment.query.filter(
            Appointment.doctor_id == doctor.id, Appointment.date == today,
            Appointment.time == slot_time, Appointment.status != "cancelled",
        ).first()
        if taken:
            continue
        appt = Appointment(
            patient_id=patient.id, doctor_id=doctor.id, date=today, time=slot_time,
            status="confirmed", reason=(reason or "").strip() or "Walk-in",
            is_walk_in=True, checked_in_at=now,
        )
        db.session.add(appt)
        try:
            db.session.commit()
            return appt
        except IntegrityError:
            db.session.rollback()
    raise BookingError("Couldn't find a place in the queue. Please try again.")


QUEUE_ORDER = {"in_consult": 0, "waiting": 1, "scheduled": 2, "done": 3, "no_show": 4, "cancelled": 5}


def queue_state(appt: Appointment) -> str:
    if appt.status == "cancelled":
        return "cancelled"
    if appt.status == "no_show":
        return "no_show"
    if appt.status == "completed":
        return "done"
    if appt.started_at:
        return "in_consult"
    if appt.checked_in_at:
        return "waiting"
    return "scheduled"


def todays_queue(doctor_id: int | None = None) -> list[Appointment]:
    """Today's appointments ordered for the front desk: in consultation, waiting
    (by arrival), then scheduled (by time), then finished."""
    q = Appointment.query.filter(Appointment.date == clock.today())
    if doctor_id:
        q = q.filter(Appointment.doctor_id == doctor_id)
    rows = q.all()
    rows.sort(key=lambda a: (
        QUEUE_ORDER[queue_state(a)],
        a.checked_in_at or datetime.max if queue_state(a) == "waiting" else datetime.min,
        a.time,
    ))
    return rows


# ── Ratings ─────────────────────────────────────────────────────────────────

def rating_summary(doctor_ids=None) -> dict[int, tuple[float, int]]:
    """{doctor_id: (average stars, number of reviews)} for doctors that have reviews."""
    q = db.session.query(Rating.doctor_id, func.avg(Rating.stars), func.count(Rating.id)).group_by(Rating.doctor_id)
    if doctor_ids is not None:
        q = q.filter(Rating.doctor_id.in_(list(doctor_ids)))
    return {d: (round(float(avg), 1), n) for d, avg, n in q.all()}
