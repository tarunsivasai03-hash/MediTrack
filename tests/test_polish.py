"""Deprecation clean-up, config safety and translated server messages."""

import warnings

import pytest
from flask import g

from app import create_app, i18n
from app.translations import TRANSLATIONS
from tests.conftest import login


# ── Translated messages ──────────────────────────────────────────────────

def _t(app, lang, text):
    with app.test_request_context():
        g.lang = lang
        return i18n.translate(text)


def test_exact_and_pattern_messages_translate(app):
    assert _t(app, "es", "Invalid email or password.") == "Correo o contraseña incorrectos."
    assert _t(app, "hi", "Welcome back, Sam!") == "वापसी पर स्वागत है, Sam!"
    assert _t(app, "es", "Too many failed attempts. Please try again in 7 minutes.").endswith("en 7 minutos.")
    assert _t(app, "es", "Too many failed attempts. Please try again in 1 minute.").endswith("en 1 minuto.")


def test_status_and_day_words_are_translated_inside_messages(app):
    assert _t(app, "es", "Appointment marked no show.") == "Cita marcada como ausente."
    assert _t(app, "es", "Dr. Who doesn't work on Mons.") == "Dr. Who no trabaja los lunes."


def test_names_inside_messages_are_left_alone(app):
    # a patient called "Admin" must not be translated as if it were a menu label
    assert _t(app, "hi", "Welcome back, Admin!") == "वापसी पर स्वागत है, Admin!"


def test_unknown_text_and_english_fall_back(app):
    assert _t(app, "hi", "Something never translated") == "Something never translated"
    assert _t(app, "en", "Welcome back, Sam!") == "Welcome back, Sam!"


def test_every_message_pattern_has_matching_placeholders():
    # a translation must use the same {placeholders} as its English key, or .format() would fail
    import re
    for lang, table in TRANSLATIONS.items():
        for key, value in table.items():
            assert set(re.findall(r"\{(\w+)\}", key)) == set(re.findall(r"\{(\w+)\}", value)), (lang, key)


def test_login_error_flash_is_translated(client):
    client.get("/lang/es")
    r = client.post("/login", data={"email": "nobody@t.com", "password": "wrong-password"}, follow_redirects=True)
    assert "Correo o contraseña incorrectos." in r.get_data(as_text=True)
    assert "Invalid email or password." not in r.get_data(as_text=True)


def test_lockout_message_is_translated(client, app):
    app.config["LOGIN_MAX_FAILURES"] = 2
    client.get("/lang/hi")
    for _ in range(3):
        r = client.post("/login", data={"email": "pat@t.com", "password": "nope-nope"})
    assert r.status_code == 429
    assert "बहुत अधिक असफल प्रयास" in r.get_data(as_text=True)


def test_form_validation_errors_are_translated(client):
    client.get("/lang/es")
    r = client.post("/register", data={
        "name": "New Person", "email": "pat@t.com", "password": "password123", "confirm_password": "different1",
        "age": 30, "gender": "Male", "phone": "1234567",
    })
    html = r.get_data(as_text=True)
    assert "Este correo ya está registrado." in html
    assert "Las contraseñas deben coincidir." in html


def test_booking_success_and_error_flashes_are_translated(client, tomorrow):
    login(client, "pat@t.com")
    client.get("/lang/es")
    data = {"doctor_id": 1, "date": tomorrow.isoformat(), "time": "09:00", "reason": "check up"}
    ok = client.post("/patient/book", data=data, follow_redirects=True).get_data(as_text=True)
    assert "Pendiente de confirmación." in ok and "Awaiting confirmation" not in ok
    again = client.post("/patient/book", data=data, follow_redirects=True).get_data(as_text=True)
    assert "Esa franja ya no está disponible" in again or "Ya tiene una cita a esa hora." in again


# ── Deprecated utcnow ────────────────────────────────────────────────────

def test_no_datetime_utcnow_left_in_app():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    hits = [str(p) for p in root.rglob("*.py") if "datetime.utcnow" in p.read_text(encoding="utf-8")
            and p.name != "translations_messages.py"]
    # the only mention allowed is the explanatory docstring in models.py
    assert [h for h in hits if not h.endswith("models.py")] == []


def test_utcnow_is_naive_utc_and_warning_free():
    from datetime import datetime, timezone
    from app.models import utcnow
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        now = utcnow()
    assert now.tzinfo is None
    assert abs((now - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()) < 5


# ── Configuration safety ─────────────────────────────────────────────────

def test_development_secret_key_is_random_and_stable(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "basedir", str(tmp_path))
    first = config._dev_secret_key()
    assert len(first) >= 32 and first != "dev-secret-key-change-in-production"
    assert config._dev_secret_key() == first          # kept on disk, so sessions survive restarts


def test_no_guessable_default_secret():
    import config
    assert config.Config.SECRET_KEY is None or config.Config.SECRET_KEY != "dev-secret-key-change-in-production"
    assert config.DevelopmentConfig.SECRET_KEY != "dev-secret-key-change-in-production"


@pytest.mark.parametrize("weak", ["change-me", "short", "secret"])
def test_production_rejects_weak_secret(monkeypatch, weak):
    import config
    monkeypatch.setattr(config.ProductionConfig, "SECRET_KEY", weak)
    with pytest.raises(RuntimeError):
        create_app("production")


def test_production_accepts_strong_secret(monkeypatch):
    import config
    monkeypatch.setattr(config.ProductionConfig, "SECRET_KEY", "x" * 40)
    monkeypatch.setattr(config.ProductionConfig, "SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")
    assert create_app("production").config["DEBUG"] is False


def test_run_py_debug_follows_config():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "run.py").read_text(encoding="utf-8")
    assert "debug=True" not in src and 'app.config["DEBUG"]' in src


# ── Appointment rules and reminder retries ───────────────────────────────

def _live_appt(**kw):
    from datetime import date, time
    from app import db
    from app.models import Appointment, Doctor, Patient
    a = Appointment(patient_id=Patient.query.first().id, doctor_id=Doctor.query.first().id,
                    date=kw.pop("when", date.today()), time=time(9, 0), status="confirmed", reason="r", **kw)
    db.session.add(a)
    db.session.commit()
    return a


def test_checked_in_patient_cannot_be_marked_no_show(app):
    from app.services import BookingError, change_status, check_in
    a = _live_appt()
    check_in(a)
    with pytest.raises(BookingError, match="checked in"):
        change_status(a, "no_show")
    assert a.status == "confirmed"


def test_not_arrived_patient_can_still_be_marked_no_show(app):
    from app.services import change_status
    a = _live_appt()
    change_status(a, "no_show")
    assert a.status == "no_show"


def test_started_consultation_cannot_be_cancelled(app):
    from app.services import BookingError, change_status, start_consultation
    a = _live_appt()
    start_consultation(a)
    with pytest.raises(BookingError, match="started"):
        change_status(a, "cancelled", by_patient=True)
    assert a.status == "confirmed"


def test_waiting_patient_can_still_cancel(app):
    from app.services import change_status, check_in
    a = _live_appt()
    check_in(a)
    change_status(a, "cancelled")
    assert a.status == "cancelled"


def test_failed_reminder_is_retried_next_run(app, tomorrow, monkeypatch):
    from app import db, emailer, notifications
    from app.models import Appointment
    from app.services import send_due_reminders
    a = _live_appt(when=tomorrow)
    real = emailer.send_email
    monkeypatch.setattr(notifications, "send_email", lambda *args, **kw: False)   # mail server down
    assert send_due_reminders() == 0
    assert db.session.get(Appointment, a.id).reminder_sent is False
    monkeypatch.setattr(notifications, "send_email", real)                       # back up
    assert send_due_reminders() == 1
    assert db.session.get(Appointment, a.id).reminder_sent is True
    assert send_due_reminders() == 0


def test_security_and_error_pages_are_translated(client):
    client.get("/lang/es")
    html = client.get("/definitely-missing").get_data(as_text=True)
    assert "Página no encontrada" in html and "Page Not Found" not in html
    login(client, "doc@t.com")
    html = client.get("/account/security").get_data(as_text=True)
    assert "Seguridad de la cuenta" in html and "Protect your account" not in html


# ── Hospital time zone ───────────────────────────────────────────────────

def test_clock_follows_configured_timezone(app):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app import clock
    app.config["TIMEZONE"] = "Pacific/Kiritimati"       # UTC+14
    ahead = clock.now()
    app.config["TIMEZONE"] = "Pacific/Pago_Pago"        # UTC-11
    behind = clock.now()
    assert 24 * 3600 < (ahead - behind).total_seconds() < 26 * 3600 + 60     # 25 hours apart
    app.config["TIMEZONE"] = "Asia/Kolkata"
    assert abs((clock.now() - datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)).total_seconds()) < 5
    assert clock.today() == clock.now().date()


def test_slots_use_the_hospital_clock_not_the_server_clock(app, monkeypatch):
    from datetime import datetime
    from app import clock
    from app.models import Doctor
    from app.services import slots_for
    monkeypatch.setattr(clock, "now", lambda: datetime(2030, 1, 7, 10, 0))      # 10:00 at the hospital
    slots = {s["time"]: s["available"] for s in slots_for(Doctor.query.first(), clock.today())}
    assert slots["09:00"] is False and slots["10:00"] is False and slots["10:30"] is True


def test_invalid_timezone_fails_at_startup(monkeypatch):
    import config
    monkeypatch.setattr(config.TestingConfig, "TIMEZONE", "Mars/Olympus")
    with pytest.raises(RuntimeError, match="TIMEZONE"):
        create_app("testing")


# ── Sessions end when the password changes ───────────────────────────────

def _get(client, path):
    """GET as a brand-new request. The test app context is shared, so Flask-Login's per-request
    user cache (g._login_user) must be cleared or it would hide a rejected session."""
    from flask import g
    g.pop("_login_user", None)
    return client.get(path)


def _second_device(app):
    from flask import g
    g.pop("_login_user", None)          # otherwise /login thinks this browser is already signed in
    c = app.test_client()
    assert login(c, "pat@t.com").status_code == 302
    assert _get(c, "/patient/dashboard").status_code == 200
    return c


def test_password_change_signs_out_other_devices_but_not_this_one(client, app):
    login(client, "pat@t.com")
    other = _second_device(app)
    assert _get(other, "/patient/dashboard").status_code == 200

    r = client.post("/account/password", data={
        "current_password": "password123", "new_password": "brand-new-pass-1", "confirm_password": "brand-new-pass-1",
    })
    assert r.status_code == 302
    assert _get(client, "/patient/dashboard").status_code == 200         # this device stays signed in
    gone = _get(other, "/patient/dashboard")
    assert gone.status_code == 302 and "/login" in gone.headers["Location"]


def test_password_reset_signs_out_existing_sessions(client, app):
    from app.models import User
    from app.routes.auth import make_reset_token
    other = _second_device(app)
    token = make_reset_token(User.query.filter_by(email="pat@t.com").one())
    client.post(f"/reset-password/{token}", data={"new_password": "reset-pass-12345", "confirm_password": "reset-pass-12345"})
    assert "/login" in _get(other, "/patient/dashboard").headers["Location"]


def test_old_style_session_cookie_is_rejected(client):
    with client.session_transaction() as s:
        s["_user_id"] = "1"                         # the old format: a bare id, no fingerprint
    assert _get(client, "/patient/dashboard").status_code == 302


def test_forged_fingerprint_is_rejected(client):
    with client.session_transaction() as s:
        s["_user_id"] = "1:0000000000000000"
    assert _get(client, "/admin/dashboard").status_code == 302


def test_reset_token_no_longer_exposes_password_hash_fragment(app):
    from itsdangerous import URLSafeTimedSerializer
    from app.models import User
    from app.routes.auth import make_reset_token
    user = User.query.filter_by(email="pat@t.com").one()
    data = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="password-reset").loads(make_reset_token(user))
    assert data["ph"] != user.password_hash[-12:] and user.password_hash[-12:] not in data["ph"]


# ── Branding ─────────────────────────────────────────────────────────────

def test_app_is_branded_meditrack(client):
    html = client.get("/login").get_data(as_text=True)
    assert "MediTrack" in html and "<title>" in html and "Hospital Management System" not in html
    manifest = client.get("/manifest.webmanifest").get_json()
    assert manifest["name"] == "MediTrack" and manifest["short_name"] == "MediTrack"


def test_no_old_name_left_in_user_facing_files():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    old = ("HMS", "Hospital Management System")
    for folder in ("templates", "static/js", "static/css"):
        for p in (root / folder).rglob("*"):
            if p.is_file() and p.suffix in (".html", ".js", ".css"):
                text = p.read_text(encoding="utf-8")
                assert not any(o in text for o in old), f"{p} still mentions the old name"
    for name in ("app/notifications.py", "app/pdf.py", "app/pdf_invoice.py", "app/twofa.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert '"HMS"' not in text and "— HMS" not in text, name
