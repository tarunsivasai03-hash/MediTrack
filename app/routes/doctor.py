"""
Doctor routes: dashboard, appointment workflow, medical records, patients.
Every query is scoped to the logged-in doctor's profile.
"""

from datetime import date, datetime, time, timedelta

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required

from app import clock
from app import db
from app.audit import audit
from app.billing import create_invoice_for
from app.decorators import role_required
from app.forms import MedicalRecordForm, TimeOffForm, UploadForm, VitalsForm
from app.models import (
    Appointment, DoctorTimeOff, MedicalRecord, Patient, PrescriptionItem, Rating, User, Vital,
)
from app.services import (
    BookingError, change_status, parse_date, queue_state, rating_summary, start_consultation, todays_queue,
)

doctor_bp = Blueprint("doctor", __name__)

STATUSES = ["pending", "confirmed", "completed", "cancelled", "no_show"]


@doctor_bp.before_request
@login_required
@role_required("doctor")
def _guard():
    """Every doctor route requires the doctor role."""


def _profile():
    profile = current_user.doctor_profile
    if profile is None:
        abort(404)
    return profile


def _own_appointment(appt_id: int) -> Appointment:
    appt = db.get_or_404(Appointment, appt_id)
    if appt.doctor_id != _profile().id:
        abort(403)
    return appt


def _page() -> int:
    return max(request.args.get("page", 1, type=int), 1)


@doctor_bp.route("/dashboard")
def dashboard():
    profile = current_user.doctor_profile
    today_appts, counts = [], {"today": 0, "pending": 0, "completed": 0}

    if profile:
        base = Appointment.query.filter_by(doctor_id=profile.id)
        today_appts = (
            base.filter(Appointment.date == clock.today(), Appointment.status != "cancelled")
            .order_by(Appointment.time).all()
        )
        counts["today"] = len(today_appts)
        counts["pending"] = base.filter_by(status="pending").count()
        counts["completed"] = base.filter_by(status="completed").count()

    rating = rating_summary([profile.id]).get(profile.id) if profile else None
    reviews = (profile.ratings.order_by(Rating.created_at.desc()).limit(3).all() if profile else [])
    return render_template(
        "doctor/dashboard.html", profile=profile, today_appts=today_appts, counts=counts,
        rating=rating, reviews=reviews,
    )


# ── Appointments ─────────────────────────────────────────────────────────

@doctor_bp.route("/appointments")
def appointments():
    profile = _profile()
    query = Appointment.query.filter_by(doctor_id=profile.id)
    status = request.args.get("status", "")
    day = parse_date(request.args.get("date", ""))
    if status in STATUSES:
        query = query.filter(Appointment.status == status)
    if day:
        query = query.filter(Appointment.date == day)
    page = query.order_by(Appointment.date.desc(), Appointment.time.desc()).paginate(
        page=_page(), per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False
    )
    filters = {"status": status, "date": request.args.get("date", "") if day else ""}
    return render_template(
        "doctor/appointments.html", page=page, statuses=STATUSES, filters=filters, today=clock.today(),
        active_filters={k: v for k, v in filters.items() if v},
    )


@doctor_bp.route("/appointments/<int:appt_id>/status", methods=["POST"])
def appointment_status(appt_id):
    appt = _own_appointment(appt_id)
    new_status = request.form.get("status", "")
    if new_status == "completed":
        # Completion goes through the record form so every visit is documented.
        return redirect(url_for("doctor.record", appt_id=appt.id))
    try:
        change_status(appt, new_status)
        flash(f"Appointment marked {appt.status}.", "success")
    except BookingError as e:
        flash(str(e), "danger")
    return redirect(url_for("doctor.appointments"))


@doctor_bp.route("/queue")
def queue():
    """Today's waiting room for this doctor (live-updating)."""
    _profile()
    return render_template("doctor/queue.html")


@doctor_bp.route("/appointments/<int:appt_id>/start", methods=["POST"])
def start(appt_id):
    appt = _own_appointment(appt_id)
    try:
        start_consultation(appt)
        return redirect(url_for("doctor.appointment_detail", appt_id=appt.id))
    except BookingError as e:
        flash(str(e), "danger")
    return redirect(request.referrer or url_for("doctor.queue"))


# ── Visit page ───────────────────────────────────────────────────────────

@doctor_bp.route("/appointments/<int:appt_id>")
def appointment_detail(appt_id):
    """Everything a doctor needs when opening a visit: allergies, vitals, files, history."""
    appt = _own_appointment(appt_id)
    patient = appt.patient
    recent_vitals = patient.vitals.order_by(Vital.recorded_on.desc(), Vital.id.desc()).limit(5).all()
    history = (MedicalRecord.query.filter_by(patient_id=patient.id, doctor_id=appt.doctor_id)
               .filter(MedicalRecord.appointment_id != appt.id)
               .order_by(MedicalRecord.created_at.desc()).limit(3).all())
    vitals_form = VitalsForm()
    vitals_form.recorded_on.data = clock.today()
    audit("patient.view", entity_type="appointment", entity_id=appt.id, patient_id=patient.id)
    return render_template(
        "doctor/appointment_detail.html", appt=appt, patient=patient, recent_vitals=recent_vitals,
        history=history, upload_form=UploadForm(), vitals_form=vitals_form,
    )


@doctor_bp.route("/appointments/<int:appt_id>/vitals", methods=["POST"])
def add_vitals(appt_id):
    appt = _own_appointment(appt_id)
    form = VitalsForm()
    if form.validate_on_submit():
        db.session.add(Vital(
            patient_id=appt.patient_id, recorded_by_id=current_user.id,
            recorded_on=form.recorded_on.data, systolic=form.systolic.data,
            diastolic=form.diastolic.data, weight_kg=form.weight_kg.data,
            sugar_mgdl=form.sugar_mgdl.data, notes=(form.notes.data or "").strip() or None,
        ))
        db.session.commit()
        audit("vitals.add", entity_type="appointment", entity_id=appt.id, patient_id=appt.patient_id)
        flash("Vitals recorded.", "success")
    else:
        for errs in form.errors.values():
            for e in errs:
                flash(e, "danger")
    return redirect(url_for("doctor.appointment_detail", appt_id=appt.id))


# ── Medical records ──────────────────────────────────────────────────────

@doctor_bp.route("/appointments/<int:appt_id>/record", methods=["GET", "POST"])
def record(appt_id):
    """Create the record (completing the visit) or edit an existing one."""
    appt = _own_appointment(appt_id)
    existing = appt.medical_record

    if existing is None:
        if appt.status != "confirmed":
            flash("Only confirmed appointments can be completed.", "danger")
            return redirect(url_for("doctor.appointments"))
        if appt.date > clock.today():
            flash("This appointment is in the future and can't be completed yet.", "danger")
            return redirect(url_for("doctor.appointments"))

    form = MedicalRecordForm(obj=existing)
    if request.method == "GET":
        # FieldList can't read the ORM rows directly, so load them into the form
        while len(form.rx):
            form.rx.pop_entry()
        for it in (existing.items if existing else []):
            form.rx.append_entry({"medicine": it.medicine, "dose": it.dose, "frequency": it.frequency,
                                  "duration": it.duration, "instructions": it.instructions})
    if len(form.rx) == 0:
        form.rx.append_entry()

    if form.validate_on_submit():
        created = existing is None
        if existing is None:
            existing = MedicalRecord(
                appointment_id=appt.id, patient_id=appt.patient_id, doctor_id=appt.doctor_id,
            )
            db.session.add(existing)
            appt.status = "completed"
        existing.diagnosis = form.diagnosis.data.strip()
        existing.treatment = form.treatment.data.strip()
        existing.prescription = (form.prescription.data or "").strip() or None
        existing.notes = (form.notes.data or "").strip() or None
        existing.follow_up_date = form.follow_up_date.data
        existing.items = [
            PrescriptionItem(position=i, **row) for i, row in enumerate(form.filled_items())
        ]
        db.session.flush()
        if created:
            create_invoice_for(appt)      # bill the consultation fee when the visit is completed
        db.session.commit()
        audit("record.create" if created else "record.update", entity_type="record",
              entity_id=existing.id, patient_id=appt.patient_id)
        flash("Medical record saved.", "success")
        return redirect(url_for("doctor.patient_detail", patient_id=appt.patient_id))

    return render_template("doctor/record_form.html", form=form, appt=appt, editing=existing is not None and existing.id is not None)


# ── Patients ─────────────────────────────────────────────────────────────

def _my_patient_ids(profile):
    return db.session.query(Appointment.patient_id).filter_by(doctor_id=profile.id)


@doctor_bp.route("/patients")
def patients():
    profile = _profile()
    q = request.args.get("q", "").strip()
    query = Patient.query.join(User, Patient.user_id == User.id).filter(Patient.id.in_(_my_patient_ids(profile)))
    if q:
        query = query.filter(User.name.ilike(f"%{q}%"))
    page = query.order_by(User.name).paginate(
        page=_page(), per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False
    )
    return render_template("doctor/patients.html", page=page, q=q)


@doctor_bp.route("/patients/<int:patient_id>")
def patient_detail(patient_id):
    profile = _profile()
    patient = db.get_or_404(Patient, patient_id)
    has_seen = Appointment.query.filter_by(doctor_id=profile.id, patient_id=patient.id).first()
    if not has_seen:
        abort(403)  # doctors only see patients they have an appointment with
    records = (MedicalRecord.query.filter_by(patient_id=patient.id, doctor_id=profile.id)
               .order_by(MedicalRecord.created_at.desc()).all())
    appts = (Appointment.query.filter_by(patient_id=patient.id, doctor_id=profile.id)
             .order_by(Appointment.date.desc(), Appointment.time.desc()).limit(10).all())
    audit("patient.view", entity_type="patient", entity_id=patient.id, patient_id=patient.id, detail="history")
    return render_template("doctor/patient_detail.html", patient=patient, records=records, appts=appts)


# ── Time off ─────────────────────────────────────────────────────────────

def _slot_end(start: time, minutes: int) -> time:
    """End of a slot starting at `start`, capped at the end of the day."""
    end = datetime.combine(clock.today(), start) + timedelta(minutes=minutes)
    return end.time() if end.date() == clock.today() else time(23, 59, 59)


@doctor_bp.route("/time-off", methods=["GET", "POST"])
def time_off():
    profile = _profile()
    form = TimeOffForm()
    if form.validate_on_submit():
        all_day = form.all_day.data
        entry = DoctorTimeOff(
            doctor_id=profile.id, date=form.date.data,
            start_time=None if all_day else form.start_time.data,
            end_time=None if all_day else form.end_time.data,
            reason=(form.reason.data or "").strip() or None,
        )
        db.session.add(entry)
        db.session.commit()

        affected = [
            a for a in Appointment.query.filter(
                Appointment.doctor_id == profile.id, Appointment.date == entry.date,
                Appointment.status.in_(["pending", "confirmed"]),
            ).all()
            if entry.blocks(a.time, _slot_end(a.time, profile.slot_minutes))
        ]
        flash("Time off added. Those slots are no longer bookable.", "success")
        if affected:
            flash(
                f"{len(affected)} existing appointment(s) fall in this period. "
                "Please cancel them or ask the patients to reschedule.", "warning",
            )
        return redirect(url_for("doctor.time_off"))

    upcoming = (
        DoctorTimeOff.query.filter(
            DoctorTimeOff.doctor_id == profile.id, DoctorTimeOff.date >= clock.today())
        .order_by(DoctorTimeOff.date, DoctorTimeOff.start_time).all()
    )
    return render_template("doctor/time_off.html", form=form, upcoming=upcoming)


@doctor_bp.route("/time-off/<int:entry_id>/delete", methods=["POST"])
def time_off_delete(entry_id):
    entry = db.get_or_404(DoctorTimeOff, entry_id)
    if entry.doctor_id != _profile().id:
        abort(403)
    db.session.delete(entry)
    db.session.commit()
    flash("Time off removed.", "info")
    return redirect(url_for("doctor.time_off"))
