"""Which patient is a signed-in patient account currently acting for (self or a family member)."""

from flask import session
from flask_login import current_user

SESSION_KEY = "acting_pid"


def managed_patients() -> list:
    """Active profiles this account may act for: its own, then its family members."""
    if not current_user.is_authenticated or not current_user.is_patient:
        return []
    return [p for p in current_user.managed_patients() if p.user.is_active]


def managed_ids() -> set[int]:
    return {p.id for p in managed_patients()}


def acting_patient():
    """The profile selected in the family switcher (defaults to the account's own)."""
    managed = managed_patients()
    chosen = session.get(SESSION_KEY)
    for p in managed:
        if p.id == chosen:
            return p
    return managed[0] if managed else None


def set_acting(patient_id: int) -> bool:
    if patient_id in managed_ids():
        session[SESSION_KEY] = patient_id
        return True
    return False
