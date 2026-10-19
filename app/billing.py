"""Invoices: created when a visit is completed, settled by front-desk staff."""

from app import db
from app.models import Invoice, utcnow

METHODS = ("cash", "card", "upi", "other")


class BillingError(Exception):
    """A user-facing billing rule violation."""


def create_invoice_for(appt) -> Invoice | None:
    """
    Bill the doctor's consultation fee for a completed visit. One invoice per
    appointment (calling again returns the existing one). No invoice for free visits.
    """
    if appt.invoice is not None:
        return appt.invoice
    fee = float(appt.doctor.consultation_fee or 0)
    if fee <= 0:
        return None
    inv = Invoice(appointment_id=appt.id, patient_id=appt.patient_id, doctor_id=appt.doctor_id, amount=fee)
    db.session.add(inv)
    db.session.flush()
    inv.number = f"INV-{inv.id:06d}"
    return inv


def mark_paid(inv: Invoice, method: str, user, discount: float | None = None, notes: str | None = None) -> None:
    if inv.status != "unpaid":
        raise BillingError(f"This invoice is already {inv.status}.")
    if method not in METHODS:
        raise BillingError("Choose a valid payment method.")
    if discount is not None:
        if discount < 0 or discount > inv.amount:
            raise BillingError("The discount must be between 0 and the invoice amount.")
        inv.discount = discount
    inv.status, inv.method = "paid", method
    inv.paid_at, inv.paid_by_id = utcnow(), user.id
    if notes:
        inv.notes = notes[:200]
    db.session.commit()


def void_invoice(inv: Invoice) -> None:
    if inv.status == "paid":
        raise BillingError("A paid invoice can't be voided.")
    if inv.status == "void":
        raise BillingError("This invoice is already void.")
    inv.status = "void"
    db.session.commit()
