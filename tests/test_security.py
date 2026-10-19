import json
import re
from datetime import date, datetime, time, timedelta

import pyotp
import pytest

from app import db
from app.models import Appointment, AuditLog, Doctor, LoginAttempt, MedicalRecord, User
from tests.conftest import login


def _doctor():
    return Doctor.query.first()


def _patient(email="pat@t.com"):
    return User.query.filter_by(email=email).first().patient_profile


def _visit(status="confirmed"):
    a = Appointment(patient_id=_patient().id, doctor_id=_doctor().id, date=date.today(),
                    time=time(9, 0), status=status, reason="cough")
    db.session.add(a)
    db.session.commit()
    return a


def _actions():
    return [a.action for a in AuditLog.query.order_by(AuditLog.id).all()]


# ═══ Audit log ═══════════════════════════════════════════════════════════

def test_doctor_access_and_record_changes_are_audited(client):
    appt = _visit()
    login(client, "doc@t.com")
    client.get(f"/doctor/appointments/{appt.id}")
    client.post(f"/doctor/appointments/{appt.id}/record", data={"diagnosis": "Flu", "treatment": "Rest"})
    rec = MedicalRecord.query.one()
    client.get(f"/files/records/{rec.id}/prescription.pdf")

    rows = AuditLog.query.filter(AuditLog.action.in_(["patient.view", "record.create", "record.pdf"])).all()
    assert {r.action for r in rows} == {"patient.view", "record.create", "record.pdf"}
    assert all(r.patient_id == appt.patient_id and r.user.email == "doc@t.com" and r.ip for r in rows)


def test_login_and_failures_are_audited(client):
    login(client, "pat@t.com", "wrong")
    login(client, "pat@t.com")
    client.post("/logout")
    acts = _actions()
    assert acts == ["auth.login_failed", "auth.login", "auth.logout"]
    assert AuditLog.query.first().detail == "pat@t.com"


def test_patient_access_log_shows_others_but_not_self(client):
    appt = _visit()
    login(client, "doc@t.com")
    client.get(f"/doctor/appointments/{appt.id}")
    client.post("/logout")

    login(client, "pat@t.com")
    client.post("/patient/vitals", data={"recorded_on": date.today().isoformat(), "systolic": "120", "diastolic": "80"})
    page = client.get("/patient/access-log").get_data(as_text=True)
    assert "Dr. Who" in page and "Opened your visit or history" in page
    assert "Added vitals" not in page                      # their own action is not listed
    client.post("/logout")

    login(client, "pat2@t.com")
    assert "Dr. Who" not in client.get("/patient/access-log").get_data(as_text=True)   # someone else's log


def test_admin_audit_page_filters_and_csv(client):
    appt = _visit()
    login(client, "doc@t.com")
    client.get(f"/doctor/appointments/{appt.id}")
    client.post("/logout")
    login(client, "admin@t.com")

    page = client.get("/admin/audit?action=patient.").get_data(as_text=True)
    assert "patient.view" in page and "auth.login" not in page
    assert "Dr. Who" in client.get("/admin/audit?user=doc@t").get_data(as_text=True)
    assert "patient.view" in client.get("/admin/audit?patient=Pat").get_data(as_text=True)
    csv_text = client.get("/admin/audit/export.csv?action=patient.").get_data(as_text=True)
    assert csv_text.startswith("Time (UTC)") and "patient.view" in csv_text and "doc@t.com" in csv_text


def test_audit_page_is_admin_only(client):
    login(client, "doc@t.com")
    assert client.get("/admin/audit").status_code == 403
    assert client.get("/admin/audit/export.csv").status_code == 403


def test_file_actions_are_audited(client):
    import io
    appt = _visit()
    login(client, "pat@t.com")
    client.post(f"/files/appointments/{appt.id}/upload", content_type="multipart/form-data",
                data={"file": (io.BytesIO(b"%PDF-1.4 test"), "a.pdf"), "category": "other", "description": ""})
    from app.models import Attachment
    att = Attachment.query.one()
    client.get(f"/files/{att.id}/download")
    client.post(f"/files/{att.id}/delete")
    assert [a for a in _actions() if a.startswith("file.")] == ["file.upload", "file.download", "file.delete"]


# ═══ Lockout & throttling ════════════════════════════════════════════════

def test_account_locks_after_repeated_failures_even_for_correct_password(client):
    for _ in range(5):
        assert login(client, "pat@t.com", "nope").status_code == 200
    r = login(client, "pat@t.com", "password123")          # correct, but locked
    assert r.status_code == 429 and b"Too many failed attempts" in r.data
    assert "auth.locked" in _actions()


def test_lockout_does_not_reveal_whether_an_account_exists(client):
    for _ in range(5):
        login(client, "ghost@t.com", "nope")
    r = login(client, "ghost@t.com", "nope")
    assert r.status_code == 429 and b"Too many failed attempts" in r.data   # same as a real account


def test_lockout_expires_and_success_resets_the_counter(client, app):
    for _ in range(5):
        login(client, "pat@t.com", "nope")
    LoginAttempt.query.update({"created_at": datetime.utcnow() - timedelta(minutes=16)})
    db.session.commit()
    assert login(client, "pat@t.com").status_code == 302
    client.post("/logout")

    for _ in range(4):
        login(client, "pat@t.com", "nope")
    assert login(client, "pat@t.com").status_code == 302           # 4 failures < threshold
    client.post("/logout")
    for _ in range(4):
        login(client, "pat@t.com", "nope")                          # counter restarted after the success
    assert login(client, "pat@t.com").status_code == 302


def test_per_ip_throttle(client, app):
    app.config["LOGIN_IP_MAX_FAILURES"] = 6
    for i in range(6):
        login(client, f"user{i}@t.com", "nope")                    # different accounts, same IP
    assert login(client, "pat@t.com").status_code == 429


# ═══ Two-factor login ════════════════════════════════════════════════════

def _enable_2fa(client, email="doc@t.com"):
    login(client, email)
    page = client.get("/account/security").get_data(as_text=True)
    secret = re.search(r'class="secret-key">([A-Z2-7]+)<', page).group(1)
    assert "data:image/png;base64," in page                       # QR rendered locally
    r = client.post("/account/security", data={"code": pyotp.TOTP(secret).now()})
    codes = re.findall(r"<code>([0-9a-f]{4}-[0-9a-f]{4})</code>", r.get_data(as_text=True))
    assert len(codes) == 8
    client.post("/logout")
    # The setup code's time step is burned (anti-replay). Pretend time has moved on so the
    # test can use the current code to sign in.
    user = User.query.filter_by(email=email).first()
    user.totp_last_step -= 5
    db.session.commit()
    return secret, codes


def test_2fa_setup_requires_a_valid_code(client):
    login(client, "doc@t.com")
    client.get("/account/security")
    r = client.post("/account/security", data={"code": "000000"})
    assert b"didn" in r.data and not User.query.filter_by(email="doc@t.com").first().totp_enabled


def test_2fa_login_requires_the_code(client):
    secret, _ = _enable_2fa(client)
    r = login(client, "doc@t.com")
    assert r.status_code == 302 and r.location.endswith("/login/2fa")
    assert "/login" in client.get("/doctor/dashboard").location        # password alone is not enough

    bad = client.post("/login/2fa", data={"code": "123456"})
    assert bad.status_code == 200 and b"not valid" in bad.data
    assert "/login" in client.get("/doctor/dashboard").location

    ok = client.post("/login/2fa", data={"code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 302 and client.get("/doctor/dashboard").status_code == 200


def test_2fa_code_cannot_be_replayed(client):
    secret, _ = _enable_2fa(client)
    code = pyotp.TOTP(secret).now()
    login(client, "doc@t.com")
    assert client.post("/login/2fa", data={"code": code}).status_code == 302
    client.post("/logout")
    login(client, "doc@t.com")
    r = client.post("/login/2fa", data={"code": code})                 # same code again
    assert r.status_code == 200 and b"not valid" in r.data


def test_recovery_codes_work_once(client):
    _, codes = _enable_2fa(client)
    login(client, "doc@t.com")
    assert client.post("/login/2fa", data={"code": codes[0]}).status_code == 302
    client.post("/logout")
    login(client, "doc@t.com")
    assert b"not valid" in client.post("/login/2fa", data={"code": codes[0]}).data     # already used
    assert client.post("/login/2fa", data={"code": codes[1]}).status_code == 302


def test_2fa_pending_state_expires(client):
    _enable_2fa(client)
    login(client, "doc@t.com")
    with client.session_transaction() as s:
        pending = dict(s["pre2fa"])          # reassign: in-place edits of nested dicts aren't saved
        pending["ts"] -= 600
        s["pre2fa"] = pending
    r = client.get("/login/2fa")
    assert r.status_code == 302 and r.location.endswith("/login")


def test_2fa_code_attempts_are_rate_limited(client):
    _enable_2fa(client)
    login(client, "doc@t.com")
    for _ in range(5):
        client.post("/login/2fa", data={"code": "111111"})
    r = client.post("/login/2fa", data={"code": "111111"})
    assert r.status_code == 429


def test_disable_2fa_needs_password_and_code(client):
    secret, _ = _enable_2fa(client)
    login(client, "doc@t.com")
    client.post("/login/2fa", data={"code": pyotp.TOTP(secret).now()})
    client.post("/account/security/disable", data={"password": "wrong", "code": "000000"})
    assert User.query.filter_by(email="doc@t.com").first().totp_enabled
    # a fresh code is needed because the login consumed the current step
    user = User.query.filter_by(email="doc@t.com").first()
    user.totp_last_step = 0
    db.session.commit()
    client.post("/account/security/disable", data={"password": "password123", "code": pyotp.TOTP(secret).now()})
    user = User.query.filter_by(email="doc@t.com").first()
    assert not user.totp_enabled and user.totp_secret is None


def test_require_2fa_forces_staff_to_set_it_up(client, app):
    app.config["REQUIRE_2FA"] = True
    login(client, "doc@t.com")
    r = client.get("/doctor/dashboard")
    assert r.status_code == 302 and r.location.endswith("/account/security")
    assert client.get("/account/security").status_code == 200
    login(client, "pat@t.com")                                          # patients are not forced
    client.post("/logout")
    login(client, "pat@t.com")
    assert client.get("/patient/dashboard").status_code == 200


# ═══ Headers, health, PWA, languages ═════════════════════════════════════

def test_security_headers(client):
    h = client.get("/login").headers
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.get_json() == {"status": "ok"}


def test_pwa_assets(client):
    m = client.get("/manifest.webmanifest")
    assert m.mimetype == "application/manifest+json" and m.get_json()["display"] == "standalone"
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["Service-Worker-Allowed"] == "/" and "javascript" in sw.mimetype
    assert b"never stored" in sw.data or b"never" in sw.data
    assert client.get("/offline").status_code == 200
    assert "manifest" in client.get("/login").get_data(as_text=True)
    for icon in ("icon-192.png", "icon-512.png", "icon-maskable-512.png"):
        assert client.get(f"/static/img/{icon}").status_code == 200


def test_language_switch_and_fallback(client):
    assert b"Welcome back" in client.get("/login").data
    r = client.get("/lang/hi")
    assert r.status_code == 302 and "lang=hi" in r.headers["Set-Cookie"]
    page = client.get("/login").get_data(as_text=True)
    assert "वापसी पर स्वागत है" in page and 'lang="hi"' in page
    client.get("/lang/es")
    assert "Bienvenido de nuevo" in client.get("/login").get_data(as_text=True)
    client.get("/lang/xx")                                              # unknown code: cookie unchanged
    assert "Bienvenido de nuevo" in client.get("/login").get_data(as_text=True)


def test_language_redirect_cannot_leave_the_site(client):
    r = client.get("/lang/es", headers={"Referer": "https://evil.example/phish"})
    assert r.status_code == 302 and "evil.example" not in r.location


def test_untranslated_text_falls_back_to_english(client):
    client.get("/lang/hi")
    login(client, "pat@t.com")
    page = client.get("/patient/dashboard").get_data(as_text=True)
    assert "डैशबोर्ड" in page and "My Vitals" not in page and "मेरे वाइटल्स" in page


def test_every_template_string_has_a_translation(app):
    """Guards against adding _('…') strings that nobody translated."""
    import glob, io, os, re
    from app.translations import HI, ES
    root = os.path.join(os.path.dirname(__file__), "..", "templates")
    pat = [re.compile(r"\b_\(\s*'((?:[^'\\]|\\.)*)'"), re.compile(r'\b_\(\s*"((?:[^"\\]|\\.)*)"')]
    keys = set()
    for f in glob.glob(os.path.join(root, "**", "*.html"), recursive=True):
        text = io.open(f, encoding="utf-8").read()
        for p in pat:
            keys |= {m.group(1).replace("\\'", "'") for m in p.finditer(text)}
    missing = sorted(k for k in keys if k not in HI or k not in ES)
    assert not missing, missing


def test_dark_mode_hooks_present(client):
    page = client.get("/login").get_data(as_text=True)
    assert "data-theme-toggle" in page and "hms-theme" in page
    css = client.get("/static/css/style.css").get_data(as_text=True)
    assert 'data-theme="dark"' in css


# ═══ Deployment helpers ══════════════════════════════════════════════════

def test_create_user_cli(app):
    runner = app.test_cli_runner()
    r = runner.invoke(args=["create-user", "--name", "Boss", "--email", "Boss@T.com", "--role", "admin",
                            "--password", "longpassword1"], input="longpassword1\n")
    assert r.exit_code == 0 and "Created admin" in r.output
    u = User.query.filter_by(email="boss@t.com").first()
    assert u.role == "admin" and u.check_password("longpassword1")

    dup = runner.invoke(args=["create-user", "--name", "X", "--email", "boss@t.com", "--password", "longpassword1"],
                        input="longpassword1\n")
    assert dup.exit_code != 0 and "already registered" in dup.output
    short = runner.invoke(args=["create-user", "--name", "X", "--email", "x@t.com", "--password", "short"], input="short\n")
    assert short.exit_code != 0


def test_postgres_url_is_normalised(monkeypatch):
    import importlib
    import config
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/db")
    assert config._database_url() == "postgresql://u:p@h:5432/db"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    assert config._database_url() == "postgresql://u:p@h/db"
    monkeypatch.delenv("DATABASE_URL")
    assert config._database_url().startswith("sqlite:///")


def test_production_refuses_missing_secret(monkeypatch):
    from app import create_app
    monkeypatch.delenv("SECRET_KEY", raising=False)
    import config
    config.ProductionConfig.SECRET_KEY = None
    with pytest.raises(RuntimeError):
        create_app("production")
