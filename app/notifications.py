"""Email notifications for appointment events and account recovery."""

from flask import url_for

from app.emailer import send_email


def _when(appt) -> str:
    return f"{appt.date.strftime('%A, %d %B %Y')} at {appt.time.strftime('%I:%M %p')}"


def _recipient(patient):
    """Who to email for this patient: themselves, or their guardian for family members.
    None for walk-ins without an account."""
    user = patient.user
    if not user.can_login:
        user = patient.guardian
    return user if user is not None and user.is_active and user.can_login else None


def _notify(appt, subject: str, intro: str) -> bool:
    """Email the patient (or guardian). True when handed off, or when there is nobody to email."""
    user = _recipient(appt.patient)
    if user is None:
        return True
    body = (
        f"Hello {user.name},\n\n{('This is about ' + appt.patient.user.name + '. ') if appt.patient.user_id != user.id else ''}{intro}\n\n"
        f"Doctor: {appt.doctor.user.name} ({appt.doctor.specialization})\n"
        f"When:   {_when(appt)}\n\n"
        "Please arrive 10 minutes early. To change or cancel, sign in to your account.\n\n— MediTrack"
    )
    return send_email(user.email, subject, body)


def appointment_confirmed(appt) -> None:
    _notify(appt, "Your appointment is confirmed", "Your appointment has been confirmed.")


def appointment_cancelled(appt, by_patient: bool = False) -> None:
    if by_patient:
        return  # they just did it themselves
    _notify(appt, "Your appointment was cancelled",
            "Unfortunately your appointment was cancelled. You can book another time from your account.")


def appointment_rescheduled(appt) -> None:
    _notify(appt, "Your appointment was rescheduled",
            "Your appointment has been moved and is awaiting confirmation from the doctor.")


def appointment_reminder(appt) -> bool:
    return _notify(appt, "Reminder: appointment tomorrow", "This is a reminder of your appointment tomorrow.")


def password_reset(user, token: str, max_age_minutes: int) -> None:
    link = url_for("auth.reset_password", token=token, _external=True)
    body = (
        f"Hello {user.name},\n\nWe received a request to reset your password. "
        f"Open this link within {max_age_minutes} minutes:\n\n{link}\n\n"
        "If you didn't ask for this, you can ignore this email; your password stays the same.\n\n— MediTrack"
    )
    send_email(user.email, "Reset your MediTrack password", body)
