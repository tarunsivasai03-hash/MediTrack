"""
Uploads, downloads and prescription PDFs.

Access rule for everything here: the patient the visit belongs to, or the doctor
treating it. Admins deliberately have no access to clinical files.
Files are always sent as attachments (never rendered in the site's origin).
"""

import io

from flask import (
    Blueprint, abort, current_app, flash, redirect, request, send_file, send_from_directory, url_for,
)
from flask_login import current_user, login_required

from app import db
from app.audit import audit
from app.forms import UploadForm
from app.models import Appointment, Attachment, MedicalRecord
from app.pdf import build_prescription_pdf
from app.uploads import UploadError, delete_stored, save_upload, upload_dir

files_bp = Blueprint("files", __name__)


def _party(appt: Appointment) -> bool:
    """Is the current user the patient or the doctor on this appointment?"""
    if current_user.is_patient:
        # the patient themselves, or the guardian of a family member
        return appt.patient_id in {p.id for p in current_user.managed_patients()}
    if current_user.is_doctor:
        return bool(current_user.doctor_profile) and appt.doctor_id == current_user.doctor_profile.id
    return False


def visit_url(appt: Appointment) -> str:
    if current_user.is_doctor:
        return url_for("doctor.appointment_detail", appt_id=appt.id)
    return url_for("patient.appointment_detail", appt_id=appt.id)


@files_bp.route("/appointments/<int:appt_id>/upload", methods=["POST"])
@login_required
def upload(appt_id):
    appt = db.get_or_404(Appointment, appt_id)
    if not _party(appt):
        abort(403)
    back = visit_url(appt)

    if appt.status == "cancelled":
        flash("Files can't be added to a cancelled appointment.", "danger")
        return redirect(back)
    if len(appt.attachments) >= current_app.config["MAX_ATTACHMENTS_PER_VISIT"]:
        flash("This visit already has the maximum number of files.", "danger")
        return redirect(back)

    form = UploadForm()
    if not form.validate_on_submit():
        for errs in form.errors.values():
            for e in errs:
                flash(e, "danger")
        return redirect(back)

    try:
        meta = save_upload(form.file.data)
    except UploadError as e:
        flash(str(e), "danger")
        return redirect(back)

    att = Attachment(
        appointment_id=appt.id, patient_id=appt.patient_id, uploaded_by_id=current_user.id,
        category=form.category.data, description=(form.description.data or "").strip() or None, **meta,
    )
    db.session.add(att)
    db.session.commit()
    audit("file.upload", entity_type="attachment", entity_id=att.id, patient_id=appt.patient_id, detail=att.original_name)
    flash("File uploaded.", "success")
    return redirect(back)


def _authorized_attachment(att_id: int) -> Attachment:
    att = db.get_or_404(Attachment, att_id)
    if not _party(att.appointment):
        abort(403)
    return att


@files_bp.route("/<int:att_id>/download")
@login_required
def download(att_id):
    att = _authorized_attachment(att_id)
    audit("file.download", entity_type="attachment", entity_id=att.id, patient_id=att.patient_id, detail=att.original_name)
    resp = send_from_directory(
        upload_dir(), att.stored_name, as_attachment=True,
        download_name=att.original_name, mimetype=att.content_type,
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@files_bp.route("/<int:att_id>/delete", methods=["POST"])
@login_required
def delete(att_id):
    att = _authorized_attachment(att_id)
    if att.uploaded_by_id != current_user.id:
        flash("You can only remove files you uploaded.", "danger")
        return redirect(visit_url(att.appointment))
    back = visit_url(att.appointment)
    stored, patient_id, name = att.stored_name, att.patient_id, att.original_name
    db.session.delete(att)
    db.session.commit()
    audit("file.delete", entity_type="attachment", entity_id=att_id, patient_id=patient_id, detail=name)
    delete_stored(stored)
    flash("File removed.", "info")
    return redirect(back)


@files_bp.route("/records/<int:record_id>/prescription.pdf")
@login_required
def prescription_pdf(record_id):
    record = db.get_or_404(MedicalRecord, record_id)
    if not _party(record.appointment):
        abort(403)
    audit("record.pdf", entity_type="record", entity_id=record.id, patient_id=record.patient_id)
    resp = send_file(
        io.BytesIO(build_prescription_pdf(record)), mimetype="application/pdf",
        as_attachment=True,
        download_name=f"prescription-{record.appointment.date.isoformat()}.pdf",
    )
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
