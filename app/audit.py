"""
Audit trail: who accessed or changed which sensitive record, and when.

`audit()` never raises: a logging problem must not break a clinical workflow,
but it is logged loudly so it gets noticed.
"""

from flask import current_app, has_request_context, request
from flask_login import current_user

from app import db
from app.models import AuditLog

# Actions a patient sees in their own "who accessed my data" page
PATIENT_VISIBLE_PREFIXES = ("record.", "patient.", "file.", "vitals.", "invoice.", "profile.")


def client_ip() -> str | None:
    return request.remote_addr if has_request_context() else None


def audit(action: str, *, entity_type=None, entity_id=None, patient_id=None,
          detail=None, user=None) -> None:
    """Record an event. `patient_id` is whose data was touched (enables the patient access log)."""
    try:
        actor = user
        if actor is None and has_request_context() and current_user.is_authenticated:
            actor = current_user
        db.session.add(AuditLog(
            user_id=actor.id if actor is not None else None,
            action=action, entity_type=entity_type, entity_id=entity_id,
            patient_id=patient_id, detail=(detail or "")[:300] or None, ip=client_ip(),
        ))
        db.session.commit()
    except Exception:  # noqa: BLE001 - deliberately broad, see module docstring
        db.session.rollback()
        current_app.logger.exception("Failed to write audit log entry %s", action)
