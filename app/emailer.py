"""
Minimal email sender built on smtplib (no extra dependency).

* MAIL_SERVER set        -> sends over SMTP.
* MAIL_SUPPRESS_SEND     -> appends to app.extensions["outbox"] (used by tests).
* otherwise (dev default) -> logs the message so links can be copied from the console.

Sending never raises: a mail outage must not break booking or login flows.
"""

import smtplib
from email.message import EmailMessage

from flask import current_app


def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns True if handed off (or logged), False on failure."""
    cfg = current_app.config
    msg = EmailMessage()
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    if cfg["MAIL_SUPPRESS_SEND"]:
        current_app.extensions.setdefault("outbox", []).append(msg)
        return True

    if not cfg["MAIL_SERVER"]:
        current_app.logger.warning("EMAIL (not sent, MAIL_SERVER unset)\nTo: %s\nSubject: %s\n\n%s", to, subject, body)
        return True

    try:
        with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=10) as smtp:
            if cfg["MAIL_USE_TLS"]:
                smtp.starttls()
            if cfg["MAIL_USERNAME"]:
                smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        current_app.logger.exception("Failed to send email to %s", to)
        return False
