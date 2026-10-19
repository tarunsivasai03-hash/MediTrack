"""
Reception (front desk): today's queue, walk-ins, booking for patients, check-in.

Receptionists see only contact and scheduling details. They have no access to
medical records, files, vitals, allergies or doctor management.
"""

from datetime import date

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, url_for,
)
from flask_login import login_required
from sqlalchemy import or_

from app import clock
from app import db
from app.audit import audit
from app.decorators import role_required
from app.forms import ReceptionBookForm, WalkInForm
from app.models import Appointment, Doctor, Invoice, Patient, User
from app.services import (
    BookingError, book_appointment, change_status, check_in, create_patient_without_login,
    create_walk_in, parse_date, queue_state, todays_queue,
)

reception_bp = Blueprint("reception", __name__)

STATUSES = ["pending", "confirmed", "completed", "cancelled", "no_show"]


@reception_bp.before_request
@login_required
@role_required("reception")
def _guard():
    """Every reception route requires the reception role."""


def _active_doctors():
    return (Doctor.query.join(User, Doctor.user_id == User.id)
            .filter(User.is_active.is_(True)).order_by(User.name).all())


def _patient_choices(first=None):
    rows = (Patient.query.join(User, Patient.user_id == User.id)
            .filter(User.is_active.is_(True)).order_by(User.name).all())
    choices = [(p.id, f"{p.user.name} — {p.phone or 'no phone'}") for p in rows]
    return ([first] if first else []) + choices


# ── Dashboard & queue ────────────────────────────────────────────────────

@reception_bp.route("/dashboard")
def dashboard():
    rows = todays_queue()
    counts = {k: 0 for k in ("scheduled", "waiting", "in_consult", "done", "no_show")}
    for a in rows:
        st = queue_state(a)
        if st in counts:
            counts[st] += 1
    unpaid = Invoice.query.filter_by(status="unpaid").count()
    return render_template("reception/dashboard.html", counts=counts, total=len(rows), unpaid=unpaid)


@reception_bp.route("/queue")
def queue():
    doctors = _active_doctors()
    return render_template("reception/queue.html", doctors=doctors)


@reception_bp.route("/appointments/<int:appt_id>/check-in", methods=["POST"])
def check_in_view(appt_id):
    appt = db.get_or_404(Appointment, appt_id)
    try:
        check_in(appt)
        flash(f"{appt.patient.user.name} checked in.", "success")
    except BookingError as e:
        flash(str(e), "danger")
    return redirect(request.referrer or url_for("reception.queue"))


@reception_bp.route("/appointments/<int:appt_id>/status", methods=["POST"])
def appointment_status(appt_id):
    appt = db.get_or_404(Appointment, appt_id)
    new_status = request.form.get("status", "")
    if new_status == "completed":      # only doctors complete visits
        flash("Only the doctor can complete a visit.", "danger")
        return redirect(request.referrer or url_for("reception.appointments"))
    try:
        change_status(appt, new_status)
        flash(f"Appointment marked {appt.status.replace('_', ' ')}.", "success")
    except BookingError as e:
        flash(str(e), "danger")
    return redirect(request.referrer or url_for("reception.appointments"))


# ── Walk-ins ─────────────────────────────────────────────────────────────

@reception_bp.route("/walk-in", methods=["GET", "POST"])
def walk_in():
    doctors = _active_doctors()
    form = WalkInForm()
    form.patient_id.choices = _patient_choices((0, "➕ New patient (not registered yet)"))
    form.doctor_id.choices = [(d.id, f"{d.user.name} — {d.specialization}") for d in doctors]

    if form.validate_on_submit():
        doctor = next((d for d in doctors if d.id == form.doctor_id.data), None)
        try:
            if doctor is None:
                raise BookingError("Please choose a valid doctor.")
            if form.patient_id.data == 0:
                patient = create_patient_without_login(
                    form.new_name.data, form.new_age.data, form.new_gender.data, phone=form.new_phone.data,
                )
            else:
                patient = db.session.get(Patient, form.patient_id.data)
                if patient is None:
                    raise BookingError("Please choose a valid patient.")
            appt = create_walk_in(patient, doctor, form.reason.data)
            audit("appointment.walk_in", entity_type="appointment", entity_id=appt.id, patient_id=patient.id)
            flash(f"{patient.user.name} added to {doctor.user.name}'s queue.", "success")
            return redirect(url_for("reception.queue"))
        except BookingError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return render_template("reception/walk_in.html", form=form)


# ── Booking for patients ─────────────────────────────────────────────────

@reception_bp.route("/book", methods=["GET", "POST"])
def book():
    doctors = _active_doctors()
    form = ReceptionBookForm()
    form.patient_id.choices = _patient_choices()
    form.doctor_id.choices = [(d.id, f"{d.user.name} — {d.specialization}") for d in doctors]

    if form.validate_on_submit():
        doctor = next((d for d in doctors if d.id == form.doctor_id.data), None)
        patient = db.session.get(Patient, form.patient_id.data)
        try:
            if doctor is None or patient is None:
                raise BookingError("Please choose a valid patient and doctor.")
            appt = book_appointment(patient, doctor, form.date.data, form.time.data, form.reason.data)
            change_status(appt, "confirmed")      # staff bookings need no separate confirmation
            flash(f"Booked {patient.user.name} with {doctor.user.name} on "
                  f"{appt.date.strftime('%d %b')} at {appt.time.strftime('%I:%M %p')}.", "success")
            return redirect(url_for("reception.appointments"))
        except BookingError as e:
            flash(str(e), "danger")
    return render_template("reception/book.html", form=form, today=clock.today().isoformat())


# ── Appointment list ─────────────────────────────────────────────────────

@reception_bp.route("/appointments")
def appointments():
    query = Appointment.query
    status = request.args.get("status", "")
    day = parse_date(request.args.get("date", "")) or (clock.today() if "date" not in request.args else None)
    doctor_id = request.args.get("doctor", type=int)
    if status in STATUSES:
        query = query.filter(Appointment.status == status)
    if day:
        query = query.filter(Appointment.date == day)
    if doctor_id:
        query = query.filter(Appointment.doctor_id == doctor_id)
    page = query.order_by(Appointment.date, Appointment.time).paginate(
        page=max(request.args.get("page", 1, type=int), 1),
        per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False,
    )
    filters = {"status": status, "date": day.isoformat() if day else "", "doctor": request.args.get("doctor", "")}
    return render_template(
        "reception/appointments.html", page=page, statuses=STATUSES, doctors=_active_doctors(),
        filters=filters, active_filters={k: v for k, v in filters.items() if v},
        today=clock.today(), state_of=queue_state,
    )


# ── Patients (contact details only) ──────────────────────────────────────

@reception_bp.route("/patients")
def patients():
    q = request.args.get("q", "").strip()
    query = Patient.query.join(User, Patient.user_id == User.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(User.name.ilike(like), Patient.phone.ilike(like)))
    page = query.order_by(User.name).paginate(
        page=max(request.args.get("page", 1, type=int), 1),
        per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False,
    )
    return render_template("reception/patients.html", page=page, q=q)
