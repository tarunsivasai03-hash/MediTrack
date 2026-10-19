from datetime import date, time, timedelta

import pytest

from app import db
from app.models import Appointment, Doctor, MedicalRecord, Patient, User
from app.services import BookingError, book_appointment, change_status, slots_for
from tests.conftest import login


def _doctor():
    return Doctor.query.first()


def _patient(email="pat@t.com"):
    return User.query.filter_by(email=email).first().patient_profile


# ── Auth & access control ────────────────────────────────────────────────

def test_login_redirects_by_role(client):
    r = login(client, "admin@t.com")
    assert r.status_code == 302 and r.location.endswith("/admin/dashboard")


def test_bad_password_rejected(client):
    r = login(client, "admin@t.com", "wrong")
    assert r.status_code == 200 and b"Invalid email or password" in r.data


@pytest.mark.parametrize("target", ["https://evil.com", "//evil.com", "javascript:alert(1)"])
def test_open_redirect_blocked(client, target):
    r = client.post(f"/login?next={target}", data={"email": "pat@t.com", "password": "password123"})
    assert r.status_code == 302 and "evil" not in r.location and "javascript" not in r.location


def test_local_next_is_honoured(client):
    r = client.post("/login?next=/patient/profile", data={"email": "pat@t.com", "password": "password123"})
    assert r.location.endswith("/patient/profile")


def test_roles_are_isolated(client):
    login(client, "pat@t.com")
    assert client.get("/admin/dashboard").status_code == 403
    assert client.get("/doctor/dashboard").status_code == 403
    assert client.get("/api/stats").status_code == 403


def test_anonymous_redirected_to_login(client):
    for url in ("/admin/doctors", "/patient/book", "/doctor/appointments"):
        assert "/login" in client.get(url).location


def test_deactivated_user_is_logged_out_mid_session(client, app):
    login(client, "pat@t.com")
    assert client.get("/patient/dashboard").status_code == 200
    User.query.filter_by(email="pat@t.com").first().is_active = False
    db.session.commit()
    r = client.get("/patient/dashboard")
    assert r.status_code == 302 and "/login" in r.location


def test_logout_requires_post(client):
    login(client, "pat@t.com")
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 302


def test_change_password(client):
    login(client, "pat@t.com")
    r = client.post("/account/password", data={
        "current_password": "password123", "new_password": "newpassword1", "confirm_password": "newpassword1"})
    assert r.status_code == 302
    client.post("/logout")
    assert login(client, "pat@t.com", "newpassword1").status_code == 302


def test_change_password_wrong_current(client):
    login(client, "pat@t.com")
    r = client.post("/account/password", data={
        "current_password": "nope", "new_password": "newpassword1", "confirm_password": "newpassword1"})
    assert r.status_code == 200 and b"incorrect" in r.data


def test_registration_allows_age_zero(client):
    r = client.post("/register", data={
        "name": "Baby", "email": "baby@t.com", "password": "password123", "confirm_password": "password123",
        "age": "0", "gender": "Male", "phone": "1234567"})
    assert r.status_code == 302
    assert User.query.filter_by(email="baby@t.com").first() is not None


# ── Booking rules ────────────────────────────────────────────────────────

def test_slots_generated(app, tomorrow):
    slots = slots_for(_doctor(), tomorrow)
    assert [s["time"] for s in slots] == ["09:00", "09:30", "10:00", "10:30"]


def test_book_and_prevent_double_booking(app, tomorrow):
    book_appointment(_patient(), _doctor(), tomorrow, "09:00", "checkup")
    with pytest.raises(BookingError):
        book_appointment(_patient("pat2@t.com"), _doctor(), tomorrow, "09:00", "same slot")


def test_cancelled_slot_can_be_rebooked(app, tomorrow):
    a = book_appointment(_patient(), _doctor(), tomorrow, "09:00", "x")
    change_status(a, "cancelled")
    b = book_appointment(_patient("pat2@t.com"), _doctor(), tomorrow, "09:00", "y")
    assert b.id != a.id and b.status == "pending"


def test_patient_cannot_book_overlapping_slots(app):
    d2 = User(name="Dr B", email="b@t.com", role="doctor"); d2.set_password("x" * 8)
    db.session.add(d2); db.session.flush()
    db.session.add(Doctor(user_id=d2.id, specialization="ENT", qualification="MS",
                          work_start=time(9, 0), work_end=time(11, 0), available_days="Mon,Tue,Wed,Thu,Fri,Sat,Sun"))
    db.session.commit()
    day = date.today() + timedelta(days=1)
    book_appointment(_patient(), Doctor.query.first(), day, "09:00", "a")
    with pytest.raises(BookingError, match="already have"):
        book_appointment(_patient(), Doctor.query.filter_by(specialization="ENT").first(), day, "09:00", "b")


@pytest.mark.parametrize("delta,slot", [(-1, "09:00"), (365, "09:00"), (1, "09:15"), (1, "23:00")])
def test_invalid_bookings_rejected(app, delta, slot):
    with pytest.raises(BookingError):
        book_appointment(_patient(), _doctor(), date.today() + timedelta(days=delta), slot, "x")


def test_doctor_day_off_rejected(app, tomorrow):
    doc = _doctor()
    doc.available_days = "Mon"
    day = tomorrow
    while day.weekday() == 0:
        day += timedelta(days=1)
    with pytest.raises(BookingError):
        book_appointment(_patient(), doc, day, "09:00", "x")


def test_status_transitions(app, tomorrow):
    a = book_appointment(_patient(), _doctor(), tomorrow, "09:00", "x")
    with pytest.raises(BookingError):
        change_status(a, "completed")  # must be confirmed first
    change_status(a, "confirmed")
    change_status(a, "completed")
    with pytest.raises(BookingError):
        change_status(a, "cancelled")  # terminal


# ── End-to-end flows ─────────────────────────────────────────────────────

def test_patient_books_via_form_and_api(client, tomorrow):
    login(client, "pat@t.com")
    doc = _doctor()
    api = client.get(f"/api/doctors/{doc.id}/slots?date={tomorrow.isoformat()}").get_json()
    assert api["works"] and len(api["slots"]) == 4
    r = client.post("/patient/book", data={
        "doctor_id": doc.id, "date": tomorrow.isoformat(), "time": "09:30", "reason": "headache"})
    assert r.status_code == 302 and r.location.endswith("/patient/appointments")
    api = client.get(f"/api/doctors/{doc.id}/slots?date={tomorrow.isoformat()}").get_json()
    assert [s["available"] for s in api["slots"]] == [True, False, True, True]


def test_slots_api_validates_input(client):
    login(client, "pat@t.com")
    doc = _doctor()
    assert client.get(f"/api/doctors/{doc.id}/slots?date=garbage").status_code == 400
    assert client.get("/api/doctors/9999/slots?date=2030-01-01").status_code == 404


def test_patient_cannot_cancel_someone_elses_appointment(client, tomorrow):
    a = book_appointment(_patient("pat2@t.com"), _doctor(), tomorrow, "09:00", "x")
    login(client, "pat@t.com")
    assert client.post(f"/patient/appointments/{a.id}/cancel").status_code == 403
    assert db.session.get(Appointment, a.id).status == "pending"


def test_doctor_workflow_creates_record_visible_to_patient_only(client):
    # Appointment today at a past time isn't bookable via API, so create directly.
    appt = Appointment(patient_id=_patient().id, doctor_id=_doctor().id,
                       date=date.today(), time=time(9, 0), status="confirmed", reason="cough")
    db.session.add(appt); db.session.commit()

    login(client, "doc@t.com")
    r = client.post(f"/doctor/appointments/{appt.id}/record", data={
        "diagnosis": "Cold", "treatment": "Rest", "prescription": "Fluids"})
    assert r.status_code == 302
    rec = MedicalRecord.query.one()
    assert db.session.get(Appointment, appt.id).status == "completed"
    client.post("/logout")

    login(client, "pat@t.com")
    assert client.get(f"/patient/records/{rec.id}").status_code == 200
    client.post("/logout")

    login(client, "pat2@t.com")
    assert client.get(f"/patient/records/{rec.id}").status_code == 403


def test_doctor_cannot_complete_future_or_unconfirmed(client, tomorrow):
    a = book_appointment(_patient(), _doctor(), tomorrow, "09:00", "x")
    login(client, "doc@t.com")
    r = client.post(f"/doctor/appointments/{a.id}/record", data={"diagnosis": "d", "treatment": "t"})
    assert r.status_code == 302 and MedicalRecord.query.count() == 0


def test_doctor_sees_only_own_patients(client, tomorrow):
    book_appointment(_patient(), _doctor(), tomorrow, "09:00", "x")
    login(client, "doc@t.com")
    assert client.get(f"/doctor/patients/{_patient().id}").status_code == 200
    assert client.get(f"/doctor/patients/{_patient('pat2@t.com').id}").status_code == 403


# ── Admin ────────────────────────────────────────────────────────────────

def _doctor_form(**over):
    data = {"name": "Dr New", "email": "new@t.com", "password": "password123",
            "specialization": "Skin", "qualification": "MD", "experience_years": "0",
            "consultation_fee": "0", "work_start": "09:00", "work_end": "13:00",
            "slot_minutes": "30", "available_days": ["Mon", "Wed"]}
    data.update(over)
    return data


def test_admin_adds_and_edits_doctor(client):
    login(client, "admin@t.com")
    r = client.post("/admin/doctors/new", data=_doctor_form())
    assert r.status_code == 302
    doc = User.query.filter_by(email="new@t.com").first().doctor_profile
    assert doc.available_days == "Mon,Wed" and doc.experience_years == 0  # zero is accepted

    r = client.post(f"/admin/doctors/{doc.id}/edit", data=_doctor_form(specialization="Derm", password=""))
    assert r.status_code == 302 and db.session.get(Doctor, doc.id).specialization == "Derm"


def test_admin_doctor_validation(client):
    login(client, "admin@t.com")
    assert b"already registered" in client.post("/admin/doctors/new", data=_doctor_form(email="doc@t.com")).data
    assert b"after start" in client.post("/admin/doctors/new", data=_doctor_form(work_end="08:00")).data
    assert b"password is required" in client.post("/admin/doctors/new", data=_doctor_form(password="")).data


def test_admin_deactivates_doctor_hides_from_booking(client):
    login(client, "admin@t.com")
    client.post(f"/admin/doctors/{_doctor().id}/toggle")
    client.post("/logout")
    login(client, "pat@t.com")
    assert b"Dr. Who" not in client.get("/patient/book").data


def test_admin_lists_search_and_csv(client, tomorrow):
    book_appointment(_patient(), _doctor(), tomorrow, "09:00", "=cmd|' /C calc'!A0")
    login(client, "admin@t.com")
    for url in ("/admin/doctors", "/admin/patients", "/admin/appointments?status=pending",
                "/admin/search?q=pat", "/admin/dashboard"):
        assert client.get(url).status_code == 200, url
    stats = client.get("/api/stats").get_json()
    assert stats["status"]["pending"] == 1 and sum(stats["daily"]["values"]) == 1
    csv_body = client.get("/admin/appointments/export.csv").get_data(as_text=True)
    assert "'=cmd" in csv_body  # formula injection neutralised


def test_error_pages(client):
    assert client.get("/nope").status_code == 404
