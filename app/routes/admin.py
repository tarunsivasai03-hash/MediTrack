"""
Admin routes: dashboard, doctor / patient management, appointment oversight,
global search and CSV export.
"""

import csv
import io
from datetime import date, timedelta

from flask import (
    Blueprint, Response, abort, current_app, flash, redirect,
    render_template, request, url_for,
)
from flask_login import login_required
from sqlalchemy import or_

from app import clock
from app import db
from app.audit import audit
from app.decorators import role_required
from app.forms import DepartmentForm, DoctorForm, StaffForm
from app.i18n import translate
from app.models import Appointment, AuditLog, Department, Doctor, Patient, User
from app.reports import appointments_workbook, report_data, report_workbook, safe_cell
from app.services import BookingError, change_status, parse_date

admin_bp = Blueprint("admin", __name__)

STATUSES = ["pending", "confirmed", "completed", "cancelled", "no_show"]
DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _page() -> int:
    return max(request.args.get("page", 1, type=int), 1)


def _per_page() -> int:
    return current_app.config["ITEMS_PER_PAGE"]


@admin_bp.before_request
@login_required
@role_required("admin")
def _guard():
    """Every admin route requires the admin role (returning None lets the request through)."""


# ── Dashboard ────────────────────────────────────────────────────────────

@admin_bp.route("/dashboard")
def dashboard():
    stats = {
        "doctors": Doctor.query.count(),
        "patients": Patient.query.count(),
        "appointments": Appointment.query.count(),
        "today": Appointment.query.filter(
            Appointment.date == clock.today(), Appointment.status != "cancelled"
        ).count(),
        "pending": Appointment.query.filter_by(status="pending").count(),
    }
    recent = Appointment.query.order_by(Appointment.created_at.desc()).limit(6).all()
    return render_template("admin/dashboard.html", stats=stats, recent=recent)


# ── Doctors ──────────────────────────────────────────────────────────────

@admin_bp.route("/doctors")
def doctors():
    q = request.args.get("q", "").strip()
    query = Doctor.query.join(User)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(User.name.ilike(like), User.email.ilike(like),
                                 Doctor.specialization.ilike(like)))
    page = query.order_by(User.name).paginate(page=_page(), per_page=_per_page(), error_out=False)
    return render_template("admin/doctors.html", page=page, q=q)


def _department_choices():
    return [(0, "— None —")] + [(d.id, d.name) for d in Department.query.order_by(Department.name)]


def _fill_doctor(doctor: Doctor, form: DoctorForm) -> None:
    doctor.specialization = form.specialization.data.strip()
    doctor.qualification = form.qualification.data.strip()
    doctor.experience_years = form.experience_years.data
    doctor.consultation_fee = form.consultation_fee.data
    doctor.work_start = form.work_start.data
    doctor.work_end = form.work_end.data
    doctor.slot_minutes = form.slot_minutes.data
    dept = form.department_id.data
    doctor.department_id = dept if dept and db.session.get(Department, dept) else None
    doctor.available_days = ",".join(d for d in DAY_ORDER if d in form.available_days.data)


@admin_bp.route("/doctors/new", methods=["GET", "POST"])
def doctor_new():
    form = DoctorForm()
    form.department_id.choices = _department_choices()
    if request.method == "GET":
        form.available_days.data = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        form.slot_minutes.data = 30
        form.work_start.data = Doctor.work_start.default.arg
        form.work_end.data = Doctor.work_end.default.arg
    if form.validate_on_submit():
        if not form.password.data:
            form.password.errors.append(translate("A password is required for new doctors."))
        else:
            user = User(name=form.name.data.strip(), email=form.email.data.lower().strip(), role="doctor")
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.flush()
            doctor = Doctor(user_id=user.id)
            _fill_doctor(doctor, form)
            db.session.add(doctor)
            db.session.commit()
            audit("admin.doctor_add", entity_type="doctor", entity_id=doctor.id, detail=user.email)
            flash(f"{user.name} was added.", "success")
            return redirect(url_for("admin.doctors"))
    return render_template("admin/doctor_form.html", form=form, doctor=None)


@admin_bp.route("/doctors/<int:doctor_id>/edit", methods=["GET", "POST"])
def doctor_edit(doctor_id):
    doctor = db.get_or_404(Doctor, doctor_id)
    form = DoctorForm(obj=doctor, user_id=doctor.user_id)
    form.department_id.choices = _department_choices()
    if request.method == "GET":
        form.department_id.data = doctor.department_id or 0
    if request.method == "GET":
        form.name.data = doctor.user.name
        form.email.data = doctor.user.email
        form.available_days.data = doctor.get_available_days_list()
    if form.validate_on_submit():
        doctor.user.name = form.name.data.strip()
        doctor.user.email = form.email.data.lower().strip()
        if form.password.data:
            doctor.user.set_password(form.password.data)
        _fill_doctor(doctor, form)
        db.session.commit()
        audit("admin.doctor_edit", entity_type="doctor", entity_id=doctor.id, detail=doctor.user.email)
        flash("Doctor updated.", "success")
        return redirect(url_for("admin.doctors"))
    return render_template("admin/doctor_form.html", form=form, doctor=doctor)


@admin_bp.route("/doctors/<int:doctor_id>/toggle", methods=["POST"])
def doctor_toggle(doctor_id):
    doctor = db.get_or_404(Doctor, doctor_id)
    doctor.user.is_active = not doctor.user.is_active
    db.session.commit()
    audit("admin.doctor_toggle", entity_type="doctor", entity_id=doctor.id,
          detail=f"{doctor.user.email} active={doctor.user.is_active}")
    flash(f"{doctor.user.name} {'activated' if doctor.user.is_active else 'deactivated'}.", "info")
    return redirect(url_for("admin.doctors"))


# ── Patients ─────────────────────────────────────────────────────────────

@admin_bp.route("/patients")
def patients():
    q = request.args.get("q", "").strip()
    query = Patient.query.join(User, Patient.user_id == User.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(User.name.ilike(like), User.email.ilike(like), Patient.phone.ilike(like)))
    page = query.order_by(User.name).paginate(page=_page(), per_page=_per_page(), error_out=False)
    return render_template("admin/patients.html", page=page, q=q)


@admin_bp.route("/patients/<int:patient_id>/toggle", methods=["POST"])
def patient_toggle(patient_id):
    patient = db.get_or_404(Patient, patient_id)
    patient.user.is_active = not patient.user.is_active
    db.session.commit()
    audit("admin.patient_toggle", entity_type="patient", entity_id=patient.id, patient_id=patient.id,
          detail=f"active={patient.user.is_active}")
    flash(f"{patient.user.name} {'activated' if patient.user.is_active else 'deactivated'}.", "info")
    return redirect(url_for("admin.patients"))


# ── Appointments ─────────────────────────────────────────────────────────

def _appointment_query():
    """Appointments filtered by ?status= ?doctor= ?date= (shared by list and CSV)."""
    query = Appointment.query
    status = request.args.get("status", "")
    doctor_id = request.args.get("doctor", type=int)
    day = parse_date(request.args.get("date", ""))
    if status in STATUSES:
        query = query.filter(Appointment.status == status)
    if doctor_id:
        query = query.filter(Appointment.doctor_id == doctor_id)
    if day:
        query = query.filter(Appointment.date == day)
    return query.order_by(Appointment.date.desc(), Appointment.time.desc())


@admin_bp.route("/appointments")
def appointments():
    page = _appointment_query().paginate(page=_page(), per_page=_per_page(), error_out=False)
    doctors = Doctor.query.join(User).order_by(User.name).all()
    filters = {k: request.args.get(k, "") for k in ("status", "doctor", "date")}
    return render_template(
        "admin/appointments.html", page=page, doctors=doctors,
        statuses=STATUSES, filters=filters,
        active_filters={k: v for k, v in filters.items() if v},
    )


@admin_bp.route("/appointments/<int:appt_id>/status", methods=["POST"])
def appointment_status(appt_id):
    appt = db.get_or_404(Appointment, appt_id)
    try:
        change_status(appt, request.form.get("status", ""))
        flash(f"Appointment marked {appt.status}.", "success")
    except BookingError as e:
        flash(str(e), "danger")
    return redirect(url_for("admin.appointments"))


def _safe_cell(value):
    """Neutralise spreadsheet formula injection in user-supplied text."""
    return safe_cell(str(value))


@admin_bp.route("/appointments/export.csv")
def appointments_csv():
    """Download the currently filtered appointments as CSV."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ID", "Date", "Time", "Patient", "Doctor", "Specialization", "Status", "Reason"])
    for a in _appointment_query().all():
        w.writerow([_safe_cell(x) for x in (
            a.id, a.date.isoformat(), a.time.strftime("%H:%M"), a.patient.user.name,
            a.doctor.user.name, a.doctor.specialization, a.status, a.reason or "",
        )])
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=appointments-{clock.today()}.csv"},
    )


# ── Search ───────────────────────────────────────────────────────────────

@admin_bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "all")
    results = {"doctors": [], "patients": [], "appointments": []}
    if q:
        like = f"%{q}%"
        if category in ("all", "doctors"):
            results["doctors"] = Doctor.query.join(User).filter(or_(
                User.name.ilike(like), User.email.ilike(like), Doctor.specialization.ilike(like)
            )).limit(20).all()
        if category in ("all", "patients"):
            results["patients"] = Patient.query.join(User, Patient.user_id == User.id).filter(or_(
                User.name.ilike(like), User.email.ilike(like), Patient.phone.ilike(like)
            )).limit(20).all()
        if category in ("all", "appointments"):
            results["appointments"] = (
                Appointment.query.join(Patient).join(User, Patient.user_id == User.id)
                .filter(or_(User.name.ilike(like), Appointment.reason.ilike(like)))
                .order_by(Appointment.date.desc()).limit(20).all()
            )
    total = sum(len(v) for v in results.values())
    return render_template("admin/search.html", q=q, category=category, results=results, total=total)


# ── Excel export of the filtered appointment list ───────────────────────

@admin_bp.route("/appointments/export.xlsx")
def appointments_xlsx():
    data = appointments_workbook(_appointment_query().all())
    return Response(
        data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=appointments-{clock.today()}.xlsx"},
    )


# ── Reports ──────────────────────────────────────────────────────────────

def _report_range():
    end = parse_date(request.args.get("to", "")) or clock.today()
    start = parse_date(request.args.get("from", "")) or end - timedelta(days=29)
    if start > end:
        start, end = end, start
    return start, min(end, start + timedelta(days=366))


@admin_bp.route("/reports")
def reports():
    start, end = _report_range()
    data = report_data(start, end)
    return render_template("admin/reports.html", data=data, start=start.isoformat(), end=end.isoformat())


@admin_bp.route("/reports/export.xlsx")
def reports_xlsx():
    start, end = _report_range()
    return Response(
        report_workbook(report_data(start, end)),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=report-{start}-to-{end}.xlsx"},
    )


# ── Departments ──────────────────────────────────────────────────────────

@admin_bp.route("/departments", methods=["GET", "POST"])
def departments():
    form = DepartmentForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        if Department.query.filter(db.func.lower(Department.name) == name.lower()).first():
            form.name.errors.append(translate("A department with this name already exists."))
        else:
            dept = Department(name=name, description=(form.description.data or "").strip() or None)
            db.session.add(dept)
            db.session.commit()
            audit("admin.department_add", entity_type="department", entity_id=dept.id, detail=name)
            flash("Department added.", "success")
            return redirect(url_for("admin.departments"))
    return render_template("admin/departments.html", form=form,
                           departments=Department.query.order_by(Department.name).all())


@admin_bp.route("/departments/<int:dept_id>/delete", methods=["POST"])
def department_delete(dept_id):
    dept = db.get_or_404(Department, dept_id)
    if dept.doctors.count():
        flash("Move or reassign its doctors before deleting this department.", "danger")
    else:
        name = dept.name
        db.session.delete(dept)
        db.session.commit()
        audit("admin.department_delete", entity_type="department", entity_id=dept_id, detail=name)
        flash("Department deleted.", "info")
    return redirect(url_for("admin.departments"))


# ── Reception staff ──────────────────────────────────────────────────────

@admin_bp.route("/staff", methods=["GET", "POST"])
def staff():
    form = StaffForm()
    if form.validate_on_submit():
        user = User(name=form.name.data.strip(), email=form.email.data.lower().strip(), role="reception")
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        audit("admin.staff_add", entity_type="user", entity_id=user.id, detail=user.email)
        flash(f"{user.name} can now sign in as reception staff.", "success")
        return redirect(url_for("admin.staff"))
    members = User.query.filter_by(role="reception").order_by(User.name).all()
    return render_template("admin/staff.html", form=form, members=members)


@admin_bp.route("/staff/<int:user_id>/toggle", methods=["POST"])
def staff_toggle(user_id):
    user = db.get_or_404(User, user_id)
    if user.role != "reception":
        abort(404)
    user.is_active = not user.is_active
    db.session.commit()
    audit("admin.staff_toggle", entity_type="user", entity_id=user.id, detail=f"{user.email} active={user.is_active}")
    flash(f"{user.name} {'activated' if user.is_active else 'deactivated'}.", "info")
    return redirect(url_for("admin.staff"))


# ── Audit log ────────────────────────────────────────────────────────────

def _audit_query():
    q = AuditLog.query.outerjoin(User, AuditLog.user_id == User.id)
    action = request.args.get("action", "").strip()
    who = request.args.get("user", "").strip()
    patient = request.args.get("patient", "").strip()
    start, end = parse_date(request.args.get("from", "")), parse_date(request.args.get("to", ""))
    if action:
        q = q.filter(AuditLog.action.like(f"{action}%"))
    if who:
        q = q.filter(or_(User.email.ilike(f"%{who}%"), User.name.ilike(f"%{who}%")))
    if patient:
        pq = (db.session.query(Patient.id).join(User, Patient.user_id == User.id)
              .filter(User.name.ilike(f"%{patient}%")))
        q = q.filter(AuditLog.patient_id.in_(pq))
    if start:
        q = q.filter(AuditLog.created_at >= start)
    if end:
        q = q.filter(AuditLog.created_at < end + timedelta(days=1))
    return q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())


@admin_bp.route("/audit")
def audit_log():
    page = _audit_query().paginate(page=_page(), per_page=25, error_out=False)
    filters = {k: request.args.get(k, "") for k in ("action", "user", "patient", "from", "to")}
    return render_template("admin/audit.html", page=page, filters=filters,
                           active_filters={k: v for k, v in filters.items() if v})


@admin_bp.route("/audit/export.csv")
def audit_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Time (UTC)", "User", "Role", "Action", "Entity", "Entity ID", "Patient", "Detail", "IP"])
    for a in _audit_query().limit(20000).all():
        w.writerow([_safe_cell(x) for x in (
            a.created_at.strftime("%Y-%m-%d %H:%M:%S"), a.user.email if a.user else "", a.user.role if a.user else "",
            a.action, a.entity_type or "", a.entity_id or "", a.patient.user.name if a.patient else "",
            a.detail or "", a.ip or "",
        )])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=audit-{clock.today()}.csv"})
