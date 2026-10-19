"""
Patient routes: dashboard, doctor directory, booking, appointments, records, vitals,
billing, reviews, family members and the access log.

A patient account can act for itself and for family members it manages (see
app/patient_context.py). Every query is scoped to the acting patient, and every
single-record route checks the record belongs to someone the account manages.
"""

from datetime import date

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func

from app import clock
from app import db
from app.audit import PATIENT_VISIBLE_PREFIXES, audit
from app.decorators import role_required
from app.forms import (
    AppointmentForm, FamilyMemberForm, PatientProfileForm, RatingForm, RescheduleForm,
    UploadForm, VitalsForm,
)
from app.models import (
    Appointment, AuditLog, Department, Doctor, Invoice, MedicalRecord, Patient, Rating, User, Vital,
)
from app.patient_context import acting_patient, managed_ids, set_acting
from app.services import (
    BookingError, book_appointment, change_status, create_patient_without_login, parse_date,
    rating_summary, reschedule_appointment,
)
from app.utils import ListPage

patient_bp = Blueprint("patient", __name__)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@patient_bp.before_request
@login_required
@role_required("patient")
def _guard():
    """Every patient route requires the patient role."""


def _profile():
    """The acting patient's profile, or 404 if the account has none."""
    profile = acting_patient()
    if profile is None:
        abort(404)
    return profile


def _owned_appointment(appt_id: int) -> Appointment:
    """An appointment of the patient or of a family member they manage, else 403."""
    appt = db.get_or_404(Appointment, appt_id)
    if appt.patient_id not in managed_ids():
        abort(403)
    return appt


def _per_page() -> int:
    return current_app.config["ITEMS_PER_PAGE"]


def _page() -> int:
    return max(request.args.get("page", 1, type=int), 1)


@patient_bp.route("/dashboard")
def dashboard():
    """Summary counts, upcoming appointments, follow-ups and bills."""
    profile = acting_patient()
    upcoming, followups = [], []
    counts = {"upcoming": 0, "completed": 0, "records": 0}
    due = {"count": 0, "amount": 0.0}

    if profile:
        base = Appointment.query.filter_by(patient_id=profile.id)
        live = base.filter(Appointment.date >= clock.today(),
                           Appointment.status.in_(["pending", "confirmed"]))
        upcoming = live.order_by(Appointment.date, Appointment.time).limit(5).all()
        counts["upcoming"] = live.count()
        counts["completed"] = base.filter_by(status="completed").count()
        counts["records"] = profile.medical_records.count()

        # Follow-ups the doctor asked for that the patient hasn't booked yet
        booked_with = {a.doctor_id for a in live.all()}
        followups = [
            r for r in profile.medical_records.filter(MedicalRecord.follow_up_date >= clock.today())
            .order_by(MedicalRecord.follow_up_date).all()
            if r.doctor_id not in booked_with
        ][:3]

        unpaid = profile.invoices.filter_by(status="unpaid").all()
        due = {"count": len(unpaid), "amount": sum(i.total for i in unpaid)}

    return render_template(
        "patient/dashboard.html", profile=profile, upcoming=upcoming, counts=counts,
        followups=followups, due=due, has_vitals=bool(profile and profile.vitals.first()),
    )


# ── Family switcher & management ─────────────────────────────────────────

@patient_bp.route("/switch/<int:patient_id>", methods=["POST"])
def switch(patient_id):
    if not set_acting(patient_id):
        abort(403)
    return redirect(url_for("patient.dashboard"))


@patient_bp.route("/family")
def family():
    members = [p for p in current_user.managed_patients() if p.user_id != current_user.id]
    return render_template("patient/family.html", members=members)


def _fill_member(patient: Patient, form: FamilyMemberForm) -> None:
    patient.user.name = form.name.data.strip()
    patient.relationship_label = form.relationship.data
    patient.age = form.age.data
    patient.gender = form.gender.data
    patient.blood_group = form.blood_group.data or None
    patient.phone = (form.phone.data or "").strip() or None


@patient_bp.route("/family/new", methods=["GET", "POST"])
def family_new():
    form = FamilyMemberForm()
    if form.validate_on_submit():
        member = create_patient_without_login(
            form.name.data, form.age.data, form.gender.data, phone=form.phone.data,
            blood_group=form.blood_group.data, guardian=current_user, relationship=form.relationship.data,
        )
        db.session.commit()
        audit("family.add", entity_type="patient", entity_id=member.id, patient_id=member.id)
        set_acting(member.id)
        flash(f"{member.user.name} was added. You're now managing their care.", "success")
        return redirect(url_for("patient.dashboard"))
    return render_template("patient/family_form.html", form=form, member=None)


def _own_dependent(patient_id: int) -> Patient:
    member = db.get_or_404(Patient, patient_id)
    if member.guardian_user_id != current_user.id:
        abort(403)
    return member


@patient_bp.route("/family/<int:patient_id>/edit", methods=["GET", "POST"])
def family_edit(patient_id):
    member = _own_dependent(patient_id)
    form = FamilyMemberForm(obj=member)
    if request.method == "GET":
        form.name.data, form.relationship.data = member.user.name, member.relationship_label or "Other"
    if form.validate_on_submit():
        _fill_member(member, form)
        db.session.commit()
        audit("profile.update", entity_type="patient", entity_id=member.id, patient_id=member.id)
        flash("Details updated.", "success")
        return redirect(url_for("patient.family"))
    return render_template("patient/family_form.html", form=form, member=member)


@patient_bp.route("/family/<int:patient_id>/remove", methods=["POST"])
def family_remove(patient_id):
    """Archive a family member. Records are kept (hidden from the account, not deleted)."""
    member = _own_dependent(patient_id)
    member.user.is_active = False
    db.session.commit()
    audit("family.remove", entity_type="patient", entity_id=member.id, patient_id=member.id)
    if session.get("acting_pid") == member.id:
        session.pop("acting_pid", None)
    flash(f"{member.user.name} was removed from your family list.", "info")
    return redirect(url_for("patient.family"))


# ── Doctor directory ─────────────────────────────────────────────────────

@patient_bp.route("/doctors")
def doctors():
    """Browse doctors by name, department, fee, working day and rating."""
    _profile()
    q = request.args.get("q", "").strip()
    dept = request.args.get("department", type=int)
    max_fee = request.args.get("max_fee", type=float)
    day = request.args.get("day", "")
    min_rating = request.args.get("min_rating", type=float)
    sort = request.args.get("sort", "name")

    query = Doctor.query.join(User, Doctor.user_id == User.id).filter(User.is_active.is_(True))
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(User.name.ilike(like), Doctor.specialization.ilike(like),
                                    Doctor.qualification.ilike(like)))
    if dept:
        query = query.filter(Doctor.department_id == dept)
    if max_fee is not None:
        query = query.filter(Doctor.consultation_fee <= max_fee)
    if day in DAYS:
        query = query.filter(Doctor.available_days.like(f"%{day}%"))
    rows = query.order_by(User.name).all()

    ratings = rating_summary([d.id for d in rows])
    if min_rating:
        rows = [d for d in rows if ratings.get(d.id, (0, 0))[0] >= min_rating]
    if sort == "rating":
        rows.sort(key=lambda d: (-ratings.get(d.id, (0, 0))[0], -ratings.get(d.id, (0, 0))[1]))
    elif sort == "fee":
        rows.sort(key=lambda d: d.consultation_fee or 0)

    page = ListPage(rows, _page(), 9)
    filters = {"q": q, "department": request.args.get("department", ""), "max_fee": request.args.get("max_fee", ""),
               "day": day, "min_rating": request.args.get("min_rating", ""), "sort": sort}
    return render_template(
        "patient/doctors.html", page=page, ratings=ratings, filters=filters,
        active_filters={k: v for k, v in filters.items() if v and not (k == "sort" and v == "name")},
        departments=Department.query.order_by(Department.name).all(), days=DAYS,
    )


@patient_bp.route("/doctors/<int:doctor_id>")
def doctor_profile(doctor_id):
    _profile()
    doctor = db.get_or_404(Doctor, doctor_id)
    if not doctor.user.is_active:
        abort(404)
    summary = rating_summary([doctor.id]).get(doctor.id)
    reviews = doctor.ratings.order_by(Rating.created_at.desc()).limit(10).all()
    return render_template("patient/doctor_profile.html", doctor=doctor, summary=summary, reviews=reviews)


# ── Booking ──────────────────────────────────────────────────────────────

@patient_bp.route("/book", methods=["GET", "POST"])
def book_appointment_view():
    profile = _profile()
    doctors = (Doctor.query.join(User, Doctor.user_id == User.id).filter(User.is_active.is_(True))
               .order_by(User.name).all())

    form = AppointmentForm()
    form.doctor_id.choices = [(d.id, f"{d.user.name} — {d.specialization}") for d in doctors]

    if request.method == "GET":
        preselect = request.args.get("doctor", type=int)
        if preselect in {d.id for d in doctors}:
            form.doctor_id.data = preselect
        # Prefill from a follow-up link (?date=YYYY-MM-DD&reason=...)
        wanted = parse_date(request.args.get("date", ""))
        if wanted:
            form.date.data = max(wanted, clock.today())
        if request.args.get("reason"):
            form.reason.data = request.args["reason"][:500]

    if form.validate_on_submit():
        doctor = next((d for d in doctors if d.id == form.doctor_id.data), None)
        try:
            if doctor is None:
                raise BookingError("Please choose a valid doctor.")
            appt = book_appointment(
                profile, doctor, form.date.data, form.time.data, form.reason.data
            )
            flash(
                f"Booked with {doctor.user.name} on {appt.date.strftime('%d %b')} at "
                f"{appt.time.strftime('%I:%M %p')}. Awaiting confirmation.", "success",
            )
            return redirect(url_for("patient.appointments"))
        except BookingError as e:
            flash(str(e), "danger")

    doctor_info = {
        str(d.id): {"q": d.qualification, "fee": f"{d.consultation_fee or 0:.0f}",
                    "days": d.available_days.replace(",", ", "), "dept": d.department_id or 0}
        for d in doctors
    }
    return render_template(
        "patient/book.html", form=form, doctors=doctors, doctor_info=doctor_info,
        departments=Department.query.order_by(Department.name).all(),
        today=clock.today().isoformat(),
    )


# ── Appointments ─────────────────────────────────────────────────────────

@patient_bp.route("/appointments")
def appointments():
    profile = _profile()
    tab = request.args.get("tab", "upcoming")
    query = Appointment.query.filter_by(patient_id=profile.id)
    live = Appointment.status.in_(["pending", "confirmed"])
    if tab == "past":
        query = query.filter((Appointment.date < clock.today()) | ~live)
        query = query.order_by(Appointment.date.desc(), Appointment.time.desc())
    else:
        tab = "upcoming"
        query = query.filter(Appointment.date >= clock.today(), live)
        query = query.order_by(Appointment.date, Appointment.time)
    page = query.paginate(page=_page(), per_page=_per_page(), error_out=False)
    return render_template("patient/appointments.html", page=page, tab=tab)


@patient_bp.route("/appointments/<int:appt_id>/cancel", methods=["POST"])
def cancel_appointment(appt_id):
    appt = _owned_appointment(appt_id)
    try:
        change_status(appt, "cancelled", by_patient=True)
        flash("Appointment cancelled.", "info")
    except BookingError as e:
        flash(str(e), "danger")
    return redirect(url_for("patient.appointments"))


@patient_bp.route("/appointments/<int:appt_id>")
def appointment_detail(appt_id):
    """One visit: details, record, prescription PDF, files, bill and review."""
    appt = _owned_appointment(appt_id)
    set_acting(appt.patient_id)
    rating = appt.rating
    rating_form = RatingForm(obj=rating) if rating else RatingForm()
    if rating and request.method == "GET":
        rating_form.stars.data = rating.stars
    return render_template(
        "patient/appointment_detail.html", appt=appt, upload_form=UploadForm(), today=clock.today(),
        rating_form=rating_form,
    )


@patient_bp.route("/appointments/<int:appt_id>/rate", methods=["POST"])
def rate(appt_id):
    appt = _owned_appointment(appt_id)
    if appt.status != "completed":
        flash("You can review a doctor after the visit is completed.", "danger")
        return redirect(url_for("patient.appointment_detail", appt_id=appt.id))
    form = RatingForm()
    if form.validate_on_submit():
        r = appt.rating or Rating(appointment_id=appt.id, doctor_id=appt.doctor_id, patient_id=appt.patient_id)
        r.stars, r.comment = form.stars.data, (form.comment.data or "").strip() or None
        db.session.add(r)
        db.session.commit()
        flash("Thanks for your review!", "success")
    else:
        for errs in form.errors.values():
            for e in errs:
                flash(e, "danger")
    return redirect(url_for("patient.appointment_detail", appt_id=appt.id))


@patient_bp.route("/appointments/<int:appt_id>/reschedule", methods=["GET", "POST"])
def reschedule_appointment_view(appt_id):
    appt = _owned_appointment(appt_id)
    if appt.status not in ("pending", "confirmed") or appt.date < clock.today():
        flash("This appointment can't be rescheduled.", "danger")
        return redirect(url_for("patient.appointments"))

    form = RescheduleForm()
    if form.validate_on_submit():
        try:
            reschedule_appointment(appt, form.date.data, form.time.data)
            flash(
                f"Moved to {appt.date.strftime('%d %b')} at {appt.time.strftime('%I:%M %p')}. "
                "Awaiting confirmation.", "success",
            )
            return redirect(url_for("patient.appointments"))
        except BookingError as e:
            flash(str(e), "danger")
    return render_template("patient/reschedule.html", form=form, appt=appt, today=clock.today().isoformat())


# ── Medical records ──────────────────────────────────────────────────────

@patient_bp.route("/records")
def medical_records():
    profile = _profile()
    page = (MedicalRecord.query.filter_by(patient_id=profile.id)
            .order_by(MedicalRecord.created_at.desc())
            .paginate(page=_page(), per_page=_per_page(), error_out=False))
    return render_template("patient/records.html", page=page, today=clock.today())


@patient_bp.route("/records/<int:record_id>")
def medical_record_detail(record_id):
    record = db.get_or_404(MedicalRecord, record_id)
    if record.patient_id not in managed_ids():
        abort(403)
    return render_template("patient/record_detail.html", record=record)


# ── Vitals ───────────────────────────────────────────────────────────────

@patient_bp.route("/vitals", methods=["GET", "POST"])
def vitals():
    profile = _profile()
    form = VitalsForm()
    if request.method == "GET":
        form.recorded_on.data = clock.today()
    if form.validate_on_submit():
        db.session.add(Vital(
            patient_id=profile.id, recorded_by_id=current_user.id,
            recorded_on=form.recorded_on.data, systolic=form.systolic.data,
            diastolic=form.diastolic.data, weight_kg=form.weight_kg.data,
            sugar_mgdl=form.sugar_mgdl.data, notes=(form.notes.data or "").strip() or None,
        ))
        db.session.commit()
        audit("vitals.add", entity_type="patient", entity_id=profile.id, patient_id=profile.id)
        flash("Reading saved.", "success")
        return redirect(url_for("patient.vitals"))
    page = (profile.vitals.order_by(Vital.recorded_on.desc(), Vital.id.desc())
            .paginate(page=_page(), per_page=_per_page(), error_out=False))
    return render_template("patient/vitals.html", form=form, page=page)


@patient_bp.route("/vitals/<int:vital_id>/delete", methods=["POST"])
def vital_delete(vital_id):
    vital = db.get_or_404(Vital, vital_id)
    if vital.patient_id not in managed_ids():
        abort(403)
    if vital.recorded_by_id != current_user.id:
        flash("Readings entered by your doctor can't be removed.", "danger")
        return redirect(url_for("patient.vitals"))
    patient_id = vital.patient_id
    db.session.delete(vital)
    db.session.commit()
    audit("vitals.delete", entity_type="patient", entity_id=patient_id, patient_id=patient_id)
    flash("Reading removed.", "info")
    return redirect(url_for("patient.vitals"))


# ── Billing ──────────────────────────────────────────────────────────────

@patient_bp.route("/billing")
def billing():
    profile = _profile()
    page = (profile.invoices.order_by(Invoice.created_at.desc())
            .paginate(page=_page(), per_page=_per_page(), error_out=False))
    due = sum(i.total for i in profile.invoices.filter_by(status="unpaid"))
    return render_template("patient/billing.html", page=page, due=due)


# ── Profile ──────────────────────────────────────────────────────────────

@patient_bp.route("/profile", methods=["GET", "POST"])
def profile():
    patient = _profile()
    form = PatientProfileForm(obj=patient)
    if request.method == "GET":
        form.name.data = patient.user.name
    if form.validate_on_submit():
        patient.user.name = form.name.data.strip()
        patient.age = form.age.data
        patient.gender = form.gender.data
        patient.phone = form.phone.data.strip()
        patient.blood_group = form.blood_group.data or None
        patient.address = form.address.data.strip() if form.address.data else None
        patient.allergies = (form.allergies.data or "").strip() or None
        patient.chronic_conditions = (form.chronic_conditions.data or "").strip() or None
        db.session.commit()
        audit("profile.update", entity_type="patient", entity_id=patient.id, patient_id=patient.id)
        flash("Profile updated.", "success")
        return redirect(url_for("patient.profile"))
    return render_template("patient/profile.html", form=form, patient=patient)


# ── Access log ───────────────────────────────────────────────────────────

ACTION_LABELS = {
    "patient.view": "Opened your visit or history", "record.create": "Created a medical record",
    "record.update": "Edited a medical record", "record.pdf": "Downloaded a prescription",
    "file.upload": "Uploaded a file", "file.download": "Downloaded a file", "file.delete": "Removed a file",
    "vitals.add": "Added vitals", "vitals.delete": "Removed vitals", "invoice.paid": "Recorded a payment",
    "invoice.void": "Voided an invoice", "profile.update": "Updated profile details",
    "appointment.walk_in": "Added you to the walk-in queue",
}


@patient_bp.route("/access-log")
def access_log():
    """Who has looked at or changed this patient's health information (people other than you)."""
    profile = _profile()
    mine = {current_user.id, profile.user_id}
    query = AuditLog.query.filter(AuditLog.patient_id == profile.id)
    query = query.filter(db.or_(*[AuditLog.action.like(f"{pfx}%") for pfx in PATIENT_VISIBLE_PREFIXES]))
    query = query.filter(db.or_(AuditLog.user_id.is_(None), AuditLog.user_id.notin_(mine)))
    page = query.order_by(AuditLog.created_at.desc()).paginate(page=_page(), per_page=15, error_out=False)
    return render_template("patient/access_log.html", page=page, labels=ACTION_LABELS)
