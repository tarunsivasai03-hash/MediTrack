"""
Brute-force protection: per-account lockout and per-IP throttling.

Attempts are stored in the database so the limits hold across worker processes
and restarts. The key is the lower-cased email as typed, whether or not the
account exists, so the response never reveals which emails are registered.
"""

import math
from datetime import datetime, timedelta

from flask import current_app

from app import db
from app.models import LoginAttempt, utcnow

IP_WINDOW_MINUTES = 10


def _now() -> datetime:
    return utcnow()


def minutes_locked(key: str, ip: str | None) -> int:
    """Minutes the caller must wait (0 = allowed to try)."""
    cfg = current_app.config
    now = _now()
    lock = timedelta(minutes=cfg["LOGIN_LOCK_MINUTES"])

    # Failures for this account since its last success, inside the lock window
    last_ok = (db.session.query(db.func.max(LoginAttempt.created_at))
               .filter(LoginAttempt.key == key, LoginAttempt.success.is_(True)).scalar())
    since = max(now - lock, last_ok) if last_ok else now - lock
    fails = (LoginAttempt.query.filter(
        LoginAttempt.key == key, LoginAttempt.success.is_(False), LoginAttempt.created_at > since,
    ).order_by(LoginAttempt.created_at.desc()).all())
    if len(fails) >= cfg["LOGIN_MAX_FAILURES"]:
        wait = fails[0].created_at + lock - now
        return max(math.ceil(wait.total_seconds() / 60), 1)

    if ip:
        ip_since = now - timedelta(minutes=IP_WINDOW_MINUTES)
        ip_fails = LoginAttempt.query.filter(
            LoginAttempt.ip == ip, LoginAttempt.success.is_(False), LoginAttempt.created_at > ip_since,
        ).count()
        if ip_fails >= cfg["LOGIN_IP_MAX_FAILURES"]:
            return IP_WINDOW_MINUTES
    return 0


def record_attempt(key: str, ip: str | None, success: bool) -> None:
    db.session.add(LoginAttempt(key=key[:130], ip=ip, success=success))
    # opportunistic housekeeping so the table stays small
    LoginAttempt.query.filter(LoginAttempt.created_at < _now() - timedelta(days=7)).delete()
    db.session.commit()
