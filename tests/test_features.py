import re
from datetime import date, time, timedelta

import pytest

from app import db
from app.models import Appointment, Doctor, DoctorTimeOff, User
from app.routes.auth import make_reset_token
from app.services import (
    BookingError, book_appointment, change_status, reschedule_appointment,
    send_due_reminders, slots_for,
)
from tests.conftest import login


def _doctor():
    return Doctor.query.first()


def _patient(email="pat@t.com"):
    return User.query.filter_by(email=email).first().patient_profile


def _outbox(app):
    return app.extensions.get("outbox", [])


# ── Doctor time off ──────────────────────────────────────────────────────

def test_all_day_time_off_blocks_every_slot(app, tomorrow):
    db.session.add(DoctorTimeOff(doctor_id=_doctor().id, date=tomorrow))
    db.session.commit()
    slots = slots_for(_doctor(), tomorrow)
    assert slots and not any(s["available"] for s in slots)
    with pytest.raises(BookingError, match="unavailable"):
        book_appointment(_patient(), _doctor(), tomorrow, "09:00", "x")


def test_partial_time_off_blocks_only_overlapping_slots(app, tomorrow):
    # 09:00-11:00 in 30-min slots; block 09:30-10:00 -> only the 09:30 slot goes
    db.session.add(DoctorTimeOff(doctor_id=_doctor().id, date=tomorrow,
                                 start_time=time(9, 30), end_time=time(10, 0)))
    db.session.commit()
    assert [s["available"] for s in slots_for(_doctor(), tomorrow)] == [True, False, True, True]


def test_time_off_overlap_edges(app):
    t = DoctorTimeOff(date=date.today(), start_time=time(9, 45), end_time=time(10, 15))
    assert t.blocks(time(9, 30), time(10, 0))        # overlaps the start
    assert t.blocks(time(10, 0), time(10, 30))       # overlaps the end
    assert not t.blocks(time(9, 0), time(9, 45))     # ends exactly when time off starts
    assert not t.blocks(time(10, 15), time(10, 45))  # starts exactly when it ends


def test_doctor_adds_and_removes_time_off_via_ui(client, app, tomorrow):
    login(client, "doc@t.com")
    r = client.post("/doctor/time-off", data={"date": tomorrow.isoformat(), "all_day": "y", "reason": "Holiday"})
    assert r.status_code == 302
    entry = DoctorTimeOff.query.one()
    assert entry.all_day and entry.reason == "Holiday"
    assert not any(s["available"] for s in slots_for(_doctor(), tomorrow))

    assert client.post(f"/doctor/time-off/{entry.id}/delete").status_code == 302
    assert DoctorTimeOff.query.count() == 0


def test_time_off_validation(client, tomorrow):
    login(client, "doc@t.com")
    past = (date.today() - timedelta(days=1)).isoformat()
    assert b"future date" in client.post("/doctor/time-off", data={"date": past, "all_day": "y"}).data
    r = client.post("/doctor/time-off", data={"date": tomorrow.isoformat(), "start_time": "11:00", "end_time": "10:00"})
    assert b"after start" in r.data
    r = client.post("/doctor/time-off", data={"date": tomorrow.isoformat()})
    assert b"both a start and end" in r.data
    assert DoctorTimeOff.query.count() == 0


def test_time_off_warns_about_existing_appointments(client, tomorrow):
    book_appointment(_patient(), _doctor(), tomorrow, "09:00", "x")
    login(client, "doc@t.com")
    r = client.post("/doctor/time-off", data={"date": tomorrow.isoformat(), "all_day": "y"}, follow_redirects=True)
    assert b"existing appointment" in r.data


def test_doctor_cannot_delete_other_doctors_time_off(client, app, tomorrow):
    other = User(name="Dr Two", email="two@t.com", role="doctor"); other.set_password("password123")
    db.session.add(other); db.session.flush()
    d2 = Doctor(user_id=other.id, specialization="ENT", qualification="MS")
    db.session.add(d2); db.session.flush()
    entry = DoctorTimeOff(doctor_id=d2.id, date=tomorrow)
    db.session.add(entry); db.session.commit()
    login(client, "doc@t.com")
    assert client.post(f"/doctor/time-off/{entry.id}/delete").status_code == 403


# ── Reschedule ───────────────────────────────────────────────────────────

def test_reschedule_moves_booking_and_resets_to_pending(app, tomorrow):
    a = book_appointment(_patient(), _doctor(), tomorrow, "09:00", "checkup")
    change_status(a, "confirmed")
    reschedule_appointment(a, tomorrow + timedelta(days=1), "10:00")
    a = db.session.get(Appointment, a.id)
    assert (a.date, a.time, a.status, a.reason) == (tomorrow + timedelta(days=1), time(10, 0), "pending", "checkup")
    # old slot is free again
    assert next(s for s in slots_for(_doctor(), tomorrow) if s["time"] == "09:00")["available"]


def test_reschedule_rules(app, tomorrow):
    a = book_appointment(_patient(), _doctor(), tomorrow, "09:00", "x")
    book_appointment(_patient("pat2@t.com"), _doctor(), tomorrow, "09:30", "y")
    with pytest.raises(BookingError, match="already your appointment"):
        reschedule_appointment(a, tomorrow, "09:00")
    with pytest.raises(BookingError, match="no longer available"):
        reschedule_appointment(a, tomorrow, "09:30")
    with pytest.raises(BookingError):
        reschedule_appointment(a, date.today() - timedelta(days=1), "09:00")
    change_status(a, "cancelled")
    with pytest.raises(BookingError, match="Only pending"):
        reschedule_appointment(a, tomorrow, "10:00")


def test_reschedule_respects_time_off(app, tomorrow):
    a = book_appointment(_patient(), _doctor(), tomorrow, "09:00", "x")
    day2 = tomorrow + timedelta(days=1)
    db.session.add(DoctorTimeOff(doctor_id=_doctor().id, date=day2))
    db.session.commit()
    with pytest.raises(BookingError, match="unavailable"):
        reschedule_appointment(a, day2, "09:00")


def test_reschedule_via_ui_and_ownership(client, app, tomorrow):
    a = book_appointment(_patient(), _doctor(), tomorrow, "09:00", "x")
    login(client, "pat2@t.com")
    assert client.get(f"/patient/appointments/{a.id}/reschedule").status_code == 403
    client.post("/logout")

    login(client, "pat@t.com")
    assert client.get(f"/patient/appointments/{a.id}/reschedule").status_code == 200
    new_day = (tomorrow + timedelta(days=1)).isoformat()
    r = client.post(f"/patient/appointments/{a.id}/reschedule", data={"date": new_day, "time": "10:30"})
    assert r.status_code == 302
    assert db.session.get(Appointment, a.id).time == time(10, 30)
    assert len(_outbox(app)) == 1 and "rescheduled" in _outbox(app)[0]["Subject"]


# ── Password reset ───────────────────────────────────────────────────────

def _token_from(app):
    body = _outbox(app)[-1].get_content()
    return re.search(r"/reset-password/(\S+)", body).group(1)


def test_forgot_password_sends_link_and_hides_unknown_emails(client, app):
    r1 = client.post("/forgot-password", data={"email": "pat@t.com"}, follow_redirects=True)
    r2 = client.post("/forgot-password", data={"email": "nobody@t.com"}, follow_redirects=True)
    assert b"reset link is on its way" in r1.data and b"reset link is on its way" in r2.data
    assert len(_outbox(app)) == 1  # nothing sent for the unknown address


def test_reset_password_flow_and_token_is_single_use(client, app):
    client.post("/forgot-password", data={"email": "pat@t.com"})
    token = _token_from(app)
    assert client.get(f"/reset-password/{token}").status_code == 200

    r = client.post(f"/reset-password/{token}", data={"new_password": "brandnew123", "confirm_password": "brandnew123"})
    assert r.status_code == 302
    assert login(client, "pat@t.com", "brandnew123").status_code == 302
    client.post("/logout")

    # the same link no longer works once the password changed
    assert client.get(f"/reset-password/{token}").status_code == 302


def test_reset_rejects_bad_expired_and_deactivated(client, app):
    assert client.get("/reset-password/garbage").status_code == 302
    user = User.query.filter_by(email="pat@t.com").first()
    token = make_reset_token(user)

    app.config["RESET_TOKEN_MAX_AGE"] = -1  # everything is already expired
    assert client.get(f"/reset-password/{token}").status_code == 302
    app.config["RESET_TOKEN_MAX_AGE"] = 3600

    user.is_active = False
    db.session.commit()
    assert client.get(f"/reset-password/{token}").status_code == 302
    client.post("/forgot-password", data={"email": "pat@t.com"})
    assert _outbox(app) == []  # no email for deactivated accounts


def test_reset_validates_new_password(client, app):
    token = make_reset_token(User.query.filter_by(email="pat@t.com").first())
    r = client.post(f"/reset-password/{token}", data={"new_password": "short", "confirm_password": "short"})
    assert r.status_code == 200 and b"at least 8" in r.data


# ── Email notifications & reminders ──────────────────────────────────────

def test_confirm_and_doctor_cancel_email_patient_but_patient_cancel_does_not(app, tomorrow):
    a = book_appointment(_patient(), _doctor(), tomorrow, "09:00", "x")
    assert _outbox(app) == []  # booking itself sends nothing
    change_status(a, "confirmed")
    change_status(a, "cancelled")  # by staff
    subjects = [m["Subject"] for m in _outbox(app)]
    assert subjects == ["Your appointment is confirmed", "Your appointment was cancelled"]
    assert _outbox(app)[0]["To"] == "pat@t.com"

    b = book_appointment(_patient(), _doctor(), tomorrow, "09:30", "y")
    change_status(b, "cancelled", by_patient=True)
    assert len(_outbox(app)) == 2


def test_reminders_only_for_confirmed_tomorrow_and_only_once(app, tomorrow):
    due = book_appointment(_patient(), _doctor(), tomorrow, "09:00", "a")
    change_status(due, "confirmed")
    book_appointment(_patient("pat2@t.com"), _doctor(), tomorrow, "09:30", "b")  # pending: no reminder
    later = book_appointment(_patient(), _doctor(), tomorrow + timedelta(days=3), "09:00", "c")
    change_status(later, "confirmed")
    _outbox(app).clear()

    assert send_due_reminders() == 1
    assert [m["Subject"] for m in _outbox(app)] == ["Reminder: appointment tomorrow"]
    assert send_due_reminders() == 0  # not sent twice


def test_reschedule_clears_reminder_flag(app, tomorrow):
    a = book_appointment(_patient(), _doctor(), tomorrow, "09:00", "a")
    change_status(a, "confirmed")
    send_due_reminders()
    assert db.session.get(Appointment, a.id).reminder_sent
    reschedule_appointment(a, tomorrow + timedelta(days=1), "09:00")
    assert not db.session.get(Appointment, a.id).reminder_sent


def test_cli_send_reminders(app, tomorrow):
    a = book_appointment(_patient(), _doctor(), tomorrow, "09:00", "a")
    change_status(a, "confirmed")
    result = app.test_cli_runner().invoke(args=["send-reminders"])
    assert "Sent 1 reminder" in result.output


def test_email_failure_never_breaks_the_request(app, tomorrow, monkeypatch):
    app.config.update(MAIL_SUPPRESS_SEND=False, MAIL_SERVER="localhost")
    monkeypatch.setattr("smtplib.SMTP", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    a = book_appointment(_patient(), _doctor(), tomorrow, "09:00", "a")
    change_status(a, "confirmed")  # must not raise
    assert db.session.get(Appointment, a.id).status == "confirmed"
