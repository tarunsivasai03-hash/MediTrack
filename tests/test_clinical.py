import io
import os
from datetime import date, time, timedelta

import pytest

from app import db
from app.models import (
    Appointment, Attachment, Doctor, MedicalRecord, Patient, PrescriptionItem, User, Vital,
)
from tests.conftest import login

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _doctor():
    return Doctor.query.first()


def _patient(email="pat@t.com"):
    return User.query.filter_by(email=email).first().patient_profile


def _visit(status="confirmed", when=None, patient=None, doctor=None, slot=time(9, 0)):
    appt = Appointment(
        patient_id=(patient or _patient()).id, doctor_id=(doctor or _doctor()).id,
        date=when or date.today(), time=slot, status=status, reason="cough",
    )
    db.session.add(appt)
    db.session.commit()
    return appt


def _second_doctor():
    u = User(name="Dr Two", email="two@t.com", role="doctor")
    u.set_password("password123")
    db.session.add(u); db.session.flush()
    d = Doctor(user_id=u.id, specialization="ENT", qualification="MS")
    db.session.add(d); db.session.commit()
    return d


def _rx(rows, **extra):
    data = {"diagnosis": "Flu", "treatment": "Rest"}
    for i, row in enumerate(rows):
        for k, v in row.items():
            data[f"rx-{i}-{k}"] = v
    data.update(extra)
    return data


def _upload(client, appt, name="report.pdf", content=PDF, category="lab_report", description=""):
    return client.post(
        f"/files/appointments/{appt.id}/upload",
        data={"file": (io.BytesIO(content), name), "category": category, "description": description},
        content_type="multipart/form-data",
    )


# ═══ Structured prescriptions ════════════════════════════════════════════

def test_record_saves_structured_prescription_and_skips_blank_rows(client):
    appt = _visit()
    login(client, "doc@t.com")
    r = client.post(f"/doctor/appointments/{appt.id}/record", data=_rx([
        {"medicine": "Amoxicillin", "dose": "500 mg", "frequency": "twice a day", "duration": "5 days", "instructions": "after meals"},
        {"medicine": "", "dose": "", "frequency": "", "duration": "", "instructions": ""},       # blank: ignored
        {"medicine": "Paracetamol", "dose": "650 mg", "frequency": "", "duration": "", "instructions": ""},
    ]))
    assert r.status_code == 302
    items = MedicalRecord.query.one().items
    assert [(i.medicine, i.dose, i.position) for i in items] == [("Amoxicillin", "500 mg", 0), ("Paracetamol", "650 mg", 1)]
    assert items[1].frequency is None


def test_prescription_row_without_medicine_name_is_rejected(client):
    appt = _visit()
    login(client, "doc@t.com")
    r = client.post(f"/doctor/appointments/{appt.id}/record", data=_rx([{"medicine": "", "dose": "5 mg"}]))
    assert r.status_code == 200 and b"Enter the medicine name" in r.data
    assert MedicalRecord.query.count() == 0 and db.session.get(Appointment, appt.id).status == "confirmed"


def test_editing_record_replaces_items_and_form_prefills(client):
    appt = _visit()
    login(client, "doc@t.com")
    client.post(f"/doctor/appointments/{appt.id}/record", data=_rx([{"medicine": "Old", "dose": "1"}]))
    page = client.get(f"/doctor/appointments/{appt.id}/record").get_data(as_text=True)
    assert 'value="Old"' in page
    client.post(f"/doctor/appointments/{appt.id}/record", data=_rx([{"medicine": "New A"}, {"medicine": "New B"}]))
    assert [i.medicine for i in MedicalRecord.query.one().items] == ["New A", "New B"]
    assert PrescriptionItem.query.count() == 2  # old row deleted, not orphaned


def test_prescription_pdf_for_patient_and_doctor_only(client, app):
    appt = _visit()
    login(client, "doc@t.com")
    client.post(f"/doctor/appointments/{appt.id}/record", data=_rx(
        [{"medicine": "Drug <b>&</b>", "dose": "5 mg"}], diagnosis="<script>alert(1)</script> & more"))
    rec = MedicalRecord.query.one()
    url = f"/files/records/{rec.id}/prescription.pdf"

    r = client.get(url)
    assert r.status_code == 200 and r.data.startswith(b"%PDF") and r.mimetype == "application/pdf"
    assert "attachment" in r.headers["Content-Disposition"]
    client.post("/logout")

    login(client, "pat@t.com")
    assert client.get(url).data.startswith(b"%PDF")
    client.post("/logout")

    login(client, "pat2@t.com")
    assert client.get(url).status_code == 403
    client.post("/logout")

    _second_doctor()
    login(client, "two@t.com")
    assert client.get(url).status_code == 403
    client.post("/logout")

    login(client, "admin@t.com")
    assert client.get(url).status_code == 403


def test_legacy_free_text_prescription_still_renders(client, app):
    appt = _visit(status="completed")
    rec = MedicalRecord(appointment_id=appt.id, patient_id=appt.patient_id, doctor_id=appt.doctor_id,
                        diagnosis="d", treatment="t", prescription="Old style: take rest")
    db.session.add(rec); db.session.commit()
    login(client, "pat@t.com")
    assert b"Old style: take rest" in client.get(f"/patient/records/{rec.id}").data
    assert client.get(f"/files/records/{rec.id}/prescription.pdf").data.startswith(b"%PDF")


# ═══ Allergies & chronic conditions ══════════════════════════════════════

def test_patient_saves_allergies_and_doctor_sees_them_on_the_visit(client):
    login(client, "pat@t.com")
    r = client.post("/patient/profile", data={
        "name": "Pat", "age": "30", "gender": "Male", "phone": "1234567",
        "allergies": "Penicillin", "chronic_conditions": "Asthma"})
    assert r.status_code == 302
    p = _patient()
    assert (p.allergies, p.chronic_conditions) == ("Penicillin", "Asthma")
    client.post("/logout")

    appt = _visit()
    login(client, "doc@t.com")
    page = client.get(f"/doctor/appointments/{appt.id}").get_data(as_text=True)
    assert "Penicillin" in page and "Asthma" in page and "alert-warning" in page


def test_visit_page_without_allergies_says_so(client):
    appt = _visit()
    login(client, "doc@t.com")
    assert b"No allergies or chronic conditions recorded" in client.get(f"/doctor/appointments/{appt.id}").data


def test_doctor_cannot_open_another_doctors_visit(client):
    other = _second_doctor()
    appt = _visit(doctor=other)
    login(client, "doc@t.com")
    assert client.get(f"/doctor/appointments/{appt.id}").status_code == 403


# ═══ Uploads ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name,content,ctype", [("r.pdf", PDF, "application/pdf"), ("s.png", PNG, "image/png"),
                                                ("p.JPG", JPG, "image/jpeg"), ("p.jpeg", JPG, "image/jpeg")])
def test_valid_uploads_are_stored_with_random_names(client, app, name, content, ctype):
    appt = _visit()
    login(client, "pat@t.com")
    assert _upload(client, appt, name, content).status_code == 302
    att = Attachment.query.one()
    assert att.content_type == ctype and att.size == len(content)
    assert att.stored_name != name and name.lower() not in att.stored_name
    assert os.path.exists(os.path.join(app.config["UPLOAD_FOLDER"], att.stored_name))


@pytest.mark.parametrize("name,content", [
    ("evil.exe", b"MZ" + b"\x00" * 20),
    ("page.html", b"<script>alert(1)</script>"),
    ("fake.pdf", b"just some text, not a pdf"),       # extension lies
    ("fake.png", PDF),                                  # wrong magic for the extension
    ("noext", PDF),
    ("empty.pdf", b""),
])
def test_bad_uploads_rejected(client, name, content):
    appt = _visit()
    login(client, "pat@t.com")
    _upload(client, appt, name, content)
    assert Attachment.query.count() == 0


def test_path_traversal_filename_is_neutralised(client, app):
    appt = _visit()
    login(client, "pat@t.com")
    _upload(client, appt, "../../evil.pdf", PDF)
    att = Attachment.query.one()
    assert ".." not in att.original_name and "/" not in att.original_name and "\\" not in att.original_name
    assert os.path.dirname(os.path.join(app.config["UPLOAD_FOLDER"], att.stored_name)) == app.config["UPLOAD_FOLDER"]


def test_oversized_upload_rejected(client, app):
    app.config["MAX_UPLOAD_BYTES"] = 1024
    appt = _visit()
    login(client, "pat@t.com")
    _upload(client, appt, "big.pdf", PDF + b"x" * 2048)
    assert Attachment.query.count() == 0


def test_request_over_hard_cap_gets_413_redirect(client, app):
    app.config["MAX_CONTENT_LENGTH"] = 2048
    appt = _visit()
    login(client, "pat@t.com")
    r = _upload(client, appt, "big.pdf", PDF + b"x" * 10000)
    assert r.status_code in (302, 413) and Attachment.query.count() == 0


def test_upload_limits_and_cancelled_visits(client, app):
    app.config["MAX_ATTACHMENTS_PER_VISIT"] = 2
    appt = _visit()
    login(client, "pat@t.com")
    for _ in range(3):
        _upload(client, appt)
    assert Attachment.query.count() == 2

    cancelled = _visit(status="cancelled", when=date.today() + timedelta(days=2))
    _upload(client, cancelled)
    assert Attachment.query.filter_by(appointment_id=cancelled.id).count() == 0


def test_only_the_two_parties_can_upload(client):
    appt = _visit()
    login(client, "pat2@t.com")
    assert _upload(client, appt).status_code == 403
    client.post("/logout")
    login(client, "admin@t.com")
    assert _upload(client, appt).status_code == 403
    client.post("/logout")
    _second_doctor()
    login(client, "two@t.com")
    assert _upload(client, appt).status_code == 403
    assert Attachment.query.count() == 0


def test_download_authorisation_and_safe_headers(client):
    appt = _visit()
    login(client, "pat@t.com")
    _upload(client, appt, "labs.pdf", PDF)
    att = Attachment.query.one()
    url = f"/files/{att.id}/download"

    r = client.get(url)
    assert r.status_code == 200 and r.data == PDF
    assert "attachment" in r.headers["Content-Disposition"] and "labs.pdf" in r.headers["Content-Disposition"]
    assert r.headers["X-Content-Type-Options"] == "nosniff" and "no-store" in r.headers["Cache-Control"]
    client.post("/logout")

    login(client, "doc@t.com")                       # the treating doctor
    assert client.get(url).status_code == 200
    client.post("/logout")

    for email in ("pat2@t.com", "admin@t.com"):
        login(client, email)
        assert client.get(url).status_code == 403, email
        client.post("/logout")

    _second_doctor()
    login(client, "two@t.com")
    assert client.get(url).status_code == 403
    client.post("/logout")
    assert "/login" in client.get(url).location       # anonymous


def test_doctor_uploads_and_only_uploader_can_delete(client, app):
    appt = _visit()
    login(client, "doc@t.com")
    _upload(client, appt, "scan.png", PNG, category="scan")
    att = Attachment.query.one()
    assert att.uploaded_by.email == "doc@t.com"
    client.post("/logout")

    login(client, "pat@t.com")                       # patient can see but not delete the doctor's file
    assert b"scan.png" in client.get(f"/patient/appointments/{appt.id}").data
    client.post(f"/files/{att.id}/delete")
    assert Attachment.query.count() == 1
    client.post("/logout")

    login(client, "doc@t.com")
    path = os.path.join(app.config["UPLOAD_FOLDER"], att.stored_name)
    assert os.path.exists(path)
    client.post(f"/files/{att.id}/delete")
    assert Attachment.query.count() == 0 and not os.path.exists(path)


# ═══ Vitals ══════════════════════════════════════════════════════════════

def _vital(client, **over):
    data = {"recorded_on": date.today().isoformat(), "systolic": "120", "diastolic": "80",
            "weight_kg": "70.5", "sugar_mgdl": "95", "notes": ""}
    data.update(over)
    return client.post("/patient/vitals", data=data)


def test_patient_logs_vitals_and_chart_api_returns_them(client):
    login(client, "pat@t.com")
    assert _vital(client).status_code == 302
    _vital(client, recorded_on=(date.today() - timedelta(days=3)).isoformat(),
           systolic="", diastolic="", sugar_mgdl="", weight_kg="71")
    v = Vital.query.order_by(Vital.id).first()
    assert (v.systolic, v.diastolic, v.weight_kg, v.sugar_mgdl) == (120, 80, 70.5, 95)

    data = client.get("/api/vitals").get_json()
    assert len(data["labels"]) == 2 and data["labels"] == sorted(data["labels"])
    assert data["systolic"] == [None, 120] and data["weight"] == [71.0, 70.5]


@pytest.mark.parametrize("over,msg", [
    ({"systolic": "80", "diastolic": "90"}, b"higher than diastolic"),
    ({"diastolic": ""}, b"both blood pressure"),
    ({"systolic": "999"}, b"between"),
    ({"weight_kg": "-5"}, b"between"),
    ({"recorded_on": (date.today() + timedelta(days=1)).isoformat()}, b"future"),
    ({"systolic": "", "diastolic": "", "weight_kg": "", "sugar_mgdl": ""}, b"at least one reading"),
])
def test_vitals_validation(client, over, msg):
    login(client, "pat@t.com")
    r = _vital(client, **over)
    assert r.status_code == 200 and msg in r.data and Vital.query.count() == 0


def test_vitals_privacy_between_patients_and_roles(client):
    login(client, "pat@t.com")
    _vital(client)
    v = Vital.query.one()
    client.post("/logout")

    login(client, "pat2@t.com")
    assert client.get("/api/vitals").get_json()["labels"] == []            # sees only their own
    assert client.post(f"/patient/vitals/{v.id}/delete").status_code == 403
    assert client.get(f"/api/patients/{v.patient_id}/vitals").status_code == 403   # patients can't use the doctor API
    client.post("/logout")

    login(client, "admin@t.com")
    assert client.get("/api/vitals").status_code == 403


def test_doctor_vitals_api_requires_a_relationship(client):
    login(client, "pat@t.com")
    _vital(client)
    pid = _patient().id
    client.post("/logout")

    login(client, "doc@t.com")
    assert client.get(f"/api/patients/{pid}/vitals").status_code == 403    # no appointment yet
    _visit()
    assert len(client.get(f"/api/patients/{pid}/vitals").get_json()["labels"]) == 1


def test_doctor_records_vitals_on_a_visit_and_patient_cannot_delete_them(client):
    appt = _visit()
    login(client, "doc@t.com")
    r = client.post(f"/doctor/appointments/{appt.id}/vitals", data={
        "recorded_on": date.today().isoformat(), "systolic": "130", "diastolic": "85"})
    assert r.status_code == 302
    v = Vital.query.one()
    assert v.recorded_by.email == "doc@t.com" and v.patient_id == appt.patient_id
    client.post("/logout")

    login(client, "pat@t.com")
    client.post(f"/patient/vitals/{v.id}/delete")
    assert Vital.query.count() == 1


def test_patient_can_delete_own_reading(client):
    login(client, "pat@t.com")
    _vital(client)
    client.post(f"/patient/vitals/{Vital.query.one().id}/delete")
    assert Vital.query.count() == 0


def test_dashboard_pages_render_with_and_without_vitals(client):
    login(client, "pat@t.com")
    assert b"Track your health" in client.get("/patient/dashboard").data
    _vital(client)
    page = client.get("/patient/dashboard").get_data(as_text=True)
    assert "vitals-chart" in page and "Track your health" not in page
    assert client.get("/patient/vitals").status_code == 200


# ═══ Follow-up booking ═══════════════════════════════════════════════════

def _record_with_followup(days=7, doctor=None):
    appt = _visit(status="completed", when=date.today() - timedelta(days=1), doctor=doctor)
    rec = MedicalRecord(appointment_id=appt.id, patient_id=appt.patient_id, doctor_id=appt.doctor_id,
                        diagnosis="Hypertension", treatment="Medication",
                        follow_up_date=date.today() + timedelta(days=days))
    db.session.add(rec); db.session.commit()
    return rec


def test_follow_up_link_prefills_booking_form(client):
    rec = _record_with_followup()
    login(client, "pat@t.com")
    page = client.get("/patient/records").get_data(as_text=True)
    assert "Book follow-up" in page and f"doctor={rec.doctor_id}" in page

    url = f"/patient/book?doctor={rec.doctor_id}&date={rec.follow_up_date.isoformat()}&reason=Follow-up: Hypertension"
    page = client.get(url).get_data(as_text=True)
    assert f'value="{rec.follow_up_date.isoformat()}"' in page
    assert "Follow-up: Hypertension" in page
    assert f'<option selected value="{rec.doctor_id}">' in page


def test_follow_up_prefill_clamps_past_dates_and_ignores_garbage(client):
    login(client, "pat@t.com")
    past = (date.today() - timedelta(days=5)).isoformat()
    page = client.get(f"/patient/book?date={past}").get_data(as_text=True)
    assert f'value="{date.today().isoformat()}"' in page
    assert client.get("/patient/book?date=garbage&doctor=abc&reason=" + "x" * 2000).status_code == 200


def test_dashboard_prompts_follow_up_until_booked(client):
    rec = _record_with_followup()
    login(client, "pat@t.com")
    assert b"Follow-ups to book" in client.get("/patient/dashboard").data

    client.post("/patient/book", data={
        "doctor_id": rec.doctor_id, "date": (date.today() + timedelta(days=2)).isoformat(),
        "time": "09:00", "reason": "Follow-up"})
    assert b"Follow-ups to book" not in client.get("/patient/dashboard").data


def test_visit_detail_page_shows_followup_button_and_pdf(client):
    rec = _record_with_followup()
    login(client, "pat@t.com")
    page = client.get(f"/patient/appointments/{rec.appointment_id}").get_data(as_text=True)
    assert "Book follow-up" in page and "Prescription PDF" in page
    client.post("/logout")
    login(client, "pat2@t.com")
    assert client.get(f"/patient/appointments/{rec.appointment_id}").status_code == 403
