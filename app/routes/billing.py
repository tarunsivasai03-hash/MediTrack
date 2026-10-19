"""
Billing: staff (admin, reception) manage invoices; patients, their guardians and the
treating doctor can download a PDF.
"""

import io
from datetime import date

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from app import db
from app.audit import audit
from app.billing import BillingError, mark_paid, void_invoice
from app.decorators import role_required
from app.forms import PaymentForm
from app.models import Doctor, Invoice, Patient, User
from app.pdf_invoice import build_invoice_pdf
from app.services import parse_date

billing_bp = Blueprint("billing", __name__)


def _can_see(inv: Invoice) -> bool:
    if current_user.role in ("admin", "reception"):
        return True
    if current_user.is_doctor:
        return bool(current_user.doctor_profile) and inv.doctor_id == current_user.doctor_profile.id
    if current_user.is_patient:
        return inv.patient_id in {p.id for p in current_user.managed_patients()}
    return False


@billing_bp.route("/<int:inv_id>/pdf")
@login_required
def pdf(inv_id):
    inv = db.get_or_404(Invoice, inv_id)
    if not _can_see(inv):
        abort(403)
    resp = send_file(io.BytesIO(build_invoice_pdf(inv)), mimetype="application/pdf", as_attachment=True,
                     download_name=f"{'receipt' if inv.status == 'paid' else 'invoice'}-{inv.number}.pdf")
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


# ── Staff-only screens ───────────────────────────────────────────────────

@billing_bp.before_request
def _staff_guard():
    """PDF downloads are open to the parties; everything else is staff only."""
    if request.endpoint == "billing.pdf":
        return None
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next=request.path))
    if current_user.role not in ("admin", "reception"):
        abort(403)
    return None


@billing_bp.route("/")
def index():
    query = Invoice.query.join(Patient, Invoice.patient_id == Patient.id).join(User, Patient.user_id == User.id)
    status = request.args.get("status", "")
    doctor_id = request.args.get("doctor", type=int)
    q = request.args.get("q", "").strip()
    start, end = parse_date(request.args.get("from", "")), parse_date(request.args.get("to", ""))
    if status in ("unpaid", "paid", "void"):
        query = query.filter(Invoice.status == status)
    if doctor_id:
        query = query.filter(Invoice.doctor_id == doctor_id)
    if q:
        query = query.filter(or_(User.name.ilike(f"%{q}%"), Invoice.number.ilike(f"%{q}%")))
    if start:
        query = query.filter(func.date(Invoice.created_at) >= start.isoformat())
    if end:
        query = query.filter(func.date(Invoice.created_at) <= end.isoformat())

    totals = {
        s: float(query.filter(Invoice.status == s).with_entities(func.coalesce(func.sum(Invoice.amount - Invoice.discount), 0)).scalar())
        for s in ("unpaid", "paid")
    }
    page = query.order_by(Invoice.created_at.desc()).paginate(
        page=max(request.args.get("page", 1, type=int), 1),
        per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False,
    )
    doctors = Doctor.query.join(User, Doctor.user_id == User.id).order_by(User.name).all()
    filters = {"status": status, "doctor": request.args.get("doctor", ""), "q": q,
               "from": request.args.get("from", ""), "to": request.args.get("to", "")}
    return render_template("billing/index.html", page=page, totals=totals, doctors=doctors, filters=filters,
                           active_filters={k: v for k, v in filters.items() if v})


@billing_bp.route("/<int:inv_id>", methods=["GET", "POST"])
def detail(inv_id):
    inv = db.get_or_404(Invoice, inv_id)
    form = PaymentForm()
    if request.method == "GET":
        form.discount.data = inv.discount or 0
    if form.validate_on_submit():
        try:
            mark_paid(inv, form.method.data, current_user, discount=form.discount.data or 0,
                      notes=(form.notes.data or "").strip() or None)
            audit("invoice.paid", entity_type="invoice", entity_id=inv.id, patient_id=inv.patient_id,
                  detail=f"{inv.number} {inv.method} {inv.total:.2f}")
            flash(f"{inv.number} marked as paid.", "success")
            return redirect(url_for("billing.detail", inv_id=inv.id))
        except BillingError as e:
            flash(str(e), "danger")
    return render_template("billing/detail.html", inv=inv, form=form)


@billing_bp.route("/<int:inv_id>/void", methods=["POST"])
def void(inv_id):
    inv = db.get_or_404(Invoice, inv_id)
    try:
        void_invoice(inv)
        audit("invoice.void", entity_type="invoice", entity_id=inv.id, patient_id=inv.patient_id, detail=inv.number)
        flash(f"{inv.number} voided.", "info")
    except BillingError as e:
        flash(str(e), "danger")
    return redirect(url_for("billing.detail", inv_id=inv.id))
