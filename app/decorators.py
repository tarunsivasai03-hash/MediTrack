"""
Access-control decorators for MediTrack.

Provides @role_required to restrict routes by user role and
@ownership_required helpers for data-level authorization.
"""

from functools import wraps
from flask import abort, flash, redirect, url_for
from flask_login import current_user


def role_required(*roles: str):
    """
    Decorator that restricts a view to users with one of the given roles.

    Usage:
        @role_required('admin')
        @role_required('admin', 'doctor')

    Returns 403 Forbidden if the user's role is not in the allowed set.
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("Please log in to access this page.", "warning")
                return redirect(url_for("auth.login"))
            if current_user.role not in roles:
                abort(403)
            if not current_user.is_active:
                flash("Your account has been deactivated. Contact the administrator.", "danger")
                return redirect(url_for("auth.login"))
            return f(*args, **kwargs)

        return decorated_function

    return decorator
