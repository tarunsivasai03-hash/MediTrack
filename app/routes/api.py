"""
JSON endpoints consumed by the frontend.

GET /api/doctors/<id>/slots?date=YYYY-MM-DD   available slots (any logged-in user)
GET /api/stats                                  admin chart data
"""

from collections import Counter
from datetime import date, timedelta

from datetime import datetime

from flask import Blueprint, jsonify, request, url_for
from flask import abort
from flask_login import current_user, login_required
from sqlalchemy import func

from app import clock
from app import db
from app.decorators import role_required
from app.models import Appointment, Doctor, Patient, User, Vital
from app.patient_context import acting_patient
from app.services import (
    MAX_ADVANCE_DAYS, doctor_works_on, parse_date, queue_state, slots_for, todays_queue,
)

api_bp = Blueprint("api", __name__)


@api_bp.errorhandler(403)
def _forbidden(e):
    return jsonify(error="forbidden"), 403


@api_bp.route("/doctors/<int:doctor_id>/slots")
@login_required
def doctor_slots(doctor_id):
    doctor = db.session.get(Doctor, doctor_id)
    if doctor is None or not doctor.user.is_active:
        return jsonify(error="Doctor not found."), 404

    target = parse_date(request.args.get("date", ""))
    if target is None:
        return jsonify(error="Use a date in YYYY-MM-DD format."), 400
    if target < clock.today() or target > clock.today() + timedelta(days=MAX_ADVANCE_DAYS):
        return jsonify(error=f"Choose a date within the next {MAX_ADVANCE_DAYS} days."), 400

    works = doctor_works_on(doctor, target)
    return jsonify(
        date=target.isoformat(),
        works=works,
        days=doctor.get_available_days_list(),
        slots=slots_for(doctor, target),
    )


def _vitals_payload(patient_id: int, days: int) -> dict:
    """Readings oldest -> newest, as parallel arrays (null where a value wasn't recorded)."""
    since = clock.today() - timedelta(days=days)
    rows = (Vital.query.filter(Vital.patient_id == patient_id, Vital.recorded_on >= since)
            .order_by(Vital.recorded_on, Vital.id).all())
    return {
        "labels": [r.recorded_on.isoformat() for r in rows],
        "systolic": [r.systolic for r in rows],
        "diastolic": [r.diastolic for r in rows],
        "weight": [r.weight_kg for r in rows],
        "sugar": [r.sugar_mgdl for r in rows],
    }


def _days_arg() -> int:
    return min(max(request.args.get("days", 90, type=int), 7), 730)


@api_bp.route("/vitals")
@login_required
@role_required("patient")
def my_vitals():
    """Readings of the patient (or family member) the account is currently acting for."""
    profile = acting_patient()
    if profile is None:
        abort(404)
    return jsonify(_vitals_payload(profile.id, _days_arg()))


@api_bp.route("/patients/<int:patient_id>/vitals")
@login_required
@role_required("doctor")
def patient_vitals(patient_id):
    """A patient's readings, for doctors who have an appointment with them."""
    doctor = current_user.doctor_profile
    seen = doctor and Appointment.query.filter_by(doctor_id=doctor.id, patient_id=patient_id).first()
    if not seen:
        abort(403)
    return jsonify(_vitals_payload(patient_id, _days_arg()))


def _queue_actions(appt, state: str) -> list[dict]:
    """Buttons the current role may press on a queue row."""
    acts = []
    if current_user.is_reception:
        if state == "scheduled":
            acts.append({"label": "Check in", "url": url_for("reception.check_in_view", appt_id=appt.id), "style": "btn-primary"})
            if appt.time < clock.now().time():
                acts.append({"label": "No-show", "url": url_for("reception.appointment_status", appt_id=appt.id),
                             "style": "btn-secondary", "data": {"status": "no_show"}, "confirm": "Mark as no-show?"})
        if state in ("scheduled", "waiting"):
            acts.append({"label": "Cancel", "url": url_for("reception.appointment_status", appt_id=appt.id),
                         "style": "btn-danger", "data": {"status": "cancelled"}, "confirm": "Cancel this appointment?"})
    elif current_user.is_doctor:
        if state in ("scheduled", "waiting"):
            acts.append({"label": "Start", "url": url_for("doctor.start", appt_id=appt.id), "style": "btn-primary"})
        if state in ("waiting", "in_consult"):
            acts.append({"label": "Complete", "url": url_for("doctor.record", appt_id=appt.id),
                         "style": "btn-success", "method": "GET"})
    return acts


@api_bp.route("/queue")
@login_required
@role_required("reception", "doctor", "admin")
def queue():
    """Today's queue (doctors see only their own). Polled by the live queue screens."""
    doctor_id = request.args.get("doctor", type=int)
    if current_user.is_doctor:
        profile = current_user.doctor_profile
        if profile is None:
            abort(404)
        doctor_id = profile.id
    now = clock.now()
    rows = []
    for a in todays_queue(doctor_id):
        state = queue_state(a)
        wait = int((now - a.checked_in_at).total_seconds() // 60) if state == "waiting" else None
        rows.append({
            "id": a.id, "time": a.time.strftime("%I:%M %p"), "patient": a.patient.user.name,
            "doctor": a.doctor.user.name, "doctor_id": a.doctor_id, "state": state,
            "walk_in": a.is_walk_in, "reason": a.reason or "", "wait_minutes": wait,
            "actions": _queue_actions(a, state),
        })
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    # waiting position per doctor (1 = next)
    pos: dict[int, int] = {}
    for r in rows:
        if r["state"] == "waiting":
            pos[r["doctor_id"]] = pos.get(r["doctor_id"], 0) + 1
            r["position"] = pos[r["doctor_id"]]
    return jsonify(rows=rows, counts=counts, updated=now.strftime("%I:%M:%S %p"))


@api_bp.route("/stats")
@login_required
@role_required("admin")
def stats():
    """Counts by status, appointments in the next 7 days, and busiest doctors."""
    by_status = dict(
        db.session.query(Appointment.status, func.count(Appointment.id))
        .group_by(Appointment.status).all()
    )
    start = clock.today()
    per_day = Counter(
        d for (d,) in db.session.query(Appointment.date).filter(
            Appointment.date >= start, Appointment.date <= start + timedelta(days=6),
            Appointment.status != "cancelled"
        )
    )
    days = [start + timedelta(days=i) for i in range(7)]

    top = (
        db.session.query(User.name, func.count(Appointment.id))
        .join(Doctor, Doctor.user_id == User.id)
        .join(Appointment, Appointment.doctor_id == Doctor.id)
        .filter(Appointment.status != "cancelled")
        .group_by(User.name).order_by(func.count(Appointment.id).desc()).limit(5).all()
    )
    return jsonify(
        status={s: by_status.get(s, 0) for s in ("pending", "confirmed", "completed", "cancelled", "no_show")},
        daily={"labels": [d.strftime("%a %d") for d in days], "values": [per_day.get(d, 0) for d in days]},
        top_doctors={"labels": [n for n, _ in top], "values": [c for _, c in top]},
    )
