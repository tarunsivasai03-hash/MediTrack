"""
Authentication routes: login (with lockout and optional two-factor), register,
logout, password change / reset, and two-factor setup.

Patients can self-register. Staff accounts are created by admins.
"""

import time

from flask import (
    Blueprint, current_app, render_template, redirect, url_for, flash, request, session,
)
from flask_login import login_user, logout_user, login_required, current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app import db, notifications, security, twofa
from app.audit import audit, client_ip
from app.forms import (
    ChangePasswordForm, DisableTwoFactorForm, ForgotPasswordForm, LoginForm,
    RegistrationForm, ResetPasswordForm, TwoFactorForm,
)
from app.i18n import translate
from app.models import Patient, User
from app.utils import is_safe_redirect

auth_bp = Blueprint("auth", __name__)

PRE_2FA_TTL = 300  # seconds the "password OK, code pending" state lasts


@auth_bp.route("/")
def index():
    """Redirect root to login or role-specific dashboard."""
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)
    return redirect(url_for("auth.login"))


# ── Login ────────────────────────────────────────────────────────────────

def _locked_response(template, form, minutes):
    flash(f"Too many failed attempts. Please try again in {minutes} minute{'s' if minutes != 1 else ''}.", "danger")
    return render_template(template, form=form), 429


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Email + password, with lockout; staff with 2FA continue to /login/2fa."""
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        ip = client_ip()

        wait = security.minutes_locked(email, ip)
        if wait:
            audit("auth.locked", detail=email)
            return _locked_response("auth/login.html", form, wait)

        user = User.query.filter_by(email=email).first()
        ok = user is not None and user.can_login and user.check_password(form.password.data)
        if not ok:
            security.record_attempt(email, ip, False)
            audit("auth.login_failed", detail=email)
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html", form=form)

        if not user.is_active:
            flash("Your account has been deactivated. Contact the administrator.", "danger")
            return render_template("auth/login.html", form=form)

        if user.totp_enabled:
            # Password is right but the account isn't signed in until the code is verified.
            session["pre2fa"] = {
                "uid": user.id, "ts": int(time.time()),
                "remember": bool(form.remember.data), "next": request.args.get("next"),
            }
            return redirect(url_for("auth.login_2fa"))

        security.record_attempt(email, ip, True)
        return _complete_login(user, form.remember.data, request.args.get("next"))

    return render_template("auth/login.html", form=form)


def _complete_login(user: User, remember: bool, next_page):
    session.pop("pre2fa", None)
    login_user(user, remember=remember)
    audit("auth.login", user=user)
    flash(f"Welcome back, {user.name}!", "success")
    if is_safe_redirect(next_page):
        return redirect(next_page)
    return _redirect_by_role(user)


@auth_bp.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    pending = session.get("pre2fa")
    if not pending or time.time() - pending["ts"] > PRE_2FA_TTL:
        session.pop("pre2fa", None)
        flash("Please sign in again.", "warning")
        return redirect(url_for("auth.login"))

    user = db.session.get(User, pending["uid"])
    if user is None or not user.is_active or not user.totp_enabled:
        session.pop("pre2fa", None)
        return redirect(url_for("auth.login"))

    form = TwoFactorForm()
    key, ip = f"2fa:{user.id}", client_ip()
    if form.validate_on_submit():
        wait = security.minutes_locked(key, ip)
        if wait:
            audit("auth.locked", user=user, detail="2fa")
            return _locked_response("auth/login_2fa.html", form, wait)

        code = form.code.data
        if twofa.verify_user_totp(user, code) or twofa.use_recovery_code(user, code):
            db.session.commit()
            security.record_attempt(key, ip, True)
            security.record_attempt(user.email, ip, True)
            return _complete_login(user, pending["remember"], pending["next"])

        db.session.rollback()
        security.record_attempt(key, ip, False)
        audit("auth.2fa_failed", user=user)
        flash("That code is not valid. Check your authenticator app and try again.", "danger")
    return render_template("auth/login_2fa.html", form=form)


# ── Registration / logout ────────────────────────────────────────────────

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Patient self-registration."""
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)

    form = RegistrationForm()

    if form.validate_on_submit():
        user = User(
            name=form.name.data.strip(),
            email=form.email.data.lower().strip(),
            role="patient",
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.flush()  # get user.id before creating profile

        patient = Patient(
            user_id=user.id,
            age=form.age.data,
            gender=form.gender.data,
            phone=form.phone.data.strip(),
            blood_group=form.blood_group.data or None,
            address=form.address.data.strip() if form.address.data else None,
        )
        db.session.add(patient)
        db.session.commit()

        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    """Log the user out and redirect to login."""
    audit("auth.logout")
    logout_user()
    session.clear()  # also drops pending flash messages meant for this user
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


# ── Password change ──────────────────────────────────────────────────────

@auth_bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    """Let any logged-in user change their own password."""
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append(translate("Current password is incorrect."))
        else:
            current_user.set_password(form.new_password.data)
            db.session.commit()
            # Every other session and remember-me cookie stops working now; keep this one.
            remembered = current_app.config.get("REMEMBER_COOKIE_NAME", "remember_token") in request.cookies
            login_user(current_user._get_current_object(), remember=remembered)
            audit("auth.password_changed")
            flash("Password updated.", "success")
            return redirect(url_for("auth.index"))
    return render_template("auth/change_password.html", form=form)


# ── Two-factor setup ─────────────────────────────────────────────────────

@auth_bp.route("/account/security", methods=["GET", "POST"])
@login_required
def security_settings():
    """Enable (QR + verify) or disable two-factor login."""
    if current_user.totp_enabled:
        return render_template(
            "auth/security.html", enabled=True, form=DisableTwoFactorForm(),
            codes_left=twofa.recovery_codes_left(current_user),
        )

    form = TwoFactorForm()
    secret = session.get("totp_pending")
    if request.method == "GET" or not secret:
        secret = session["totp_pending"] = twofa.new_secret()

    if form.validate_on_submit():
        step = twofa.verify_totp(secret, form.code.data, None)
        if step is None:
            form.code.errors.append(translate("That code didn't match. Check the time on your phone and try again."))
        else:
            codes, hashed = twofa.make_recovery_codes()
            current_user.totp_secret, current_user.totp_enabled = secret, True
            current_user.totp_last_step, current_user.recovery_codes = step, hashed
            db.session.commit()
            session.pop("totp_pending", None)
            audit("auth.2fa_enabled")
            return render_template("auth/security_codes.html", codes=codes)

    uri = twofa.provisioning_uri(secret, current_user.email)
    return render_template(
        "auth/security.html", enabled=False, form=form, secret=secret, qr=twofa.qr_data_uri(uri),
    )


@auth_bp.route("/account/security/disable", methods=["POST"])
@login_required
def disable_2fa():
    form = DisableTwoFactorForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.password.data):
            flash("Current password is incorrect.", "danger")
        elif not (twofa.verify_user_totp(current_user, form.code.data)
                  or twofa.use_recovery_code(current_user, form.code.data)):
            db.session.rollback()
            flash("That authentication code is not valid.", "danger")
        else:
            current_user.totp_enabled, current_user.totp_secret = False, None
            current_user.totp_last_step, current_user.recovery_codes = None, None
            db.session.commit()
            audit("auth.2fa_disabled")
            flash("Two-factor login turned off.", "info")
    else:
        flash("Enter your password and a code to turn two-factor off.", "danger")
    return redirect(url_for("auth.security_settings"))


# ── Password reset ───────────────────────────────────────────────────────

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")


def make_reset_token(user: User) -> str:
    """Signed, expiring token. It embeds a slice of the current password hash,
    so it stops working as soon as the password changes (single use)."""
    return _serializer().dumps({"uid": user.id, "ph": user.auth_fingerprint()})


def user_from_reset_token(token: str):
    try:
        data = _serializer().loads(token, max_age=current_app.config["RESET_TOKEN_MAX_AGE"])
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data.get("uid"))
    if user is None or not user.is_active or not user.can_login or user.auth_fingerprint() != data.get("ph"):
        return None
    return user


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("auth.change_password"))
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user and user.is_active and user.can_login:
            notifications.password_reset(
                user, make_reset_token(user), current_app.config["RESET_TOKEN_MAX_AGE"] // 60
            )
        # Same answer whether or not the account exists, so emails can't be probed.
        flash("If that email is registered, a reset link is on its way.", "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot_password.html", form=form)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = user_from_reset_token(token)
    if user is None:
        flash("That reset link is invalid or has expired. Please request a new one.", "danger")
        return redirect(url_for("auth.forgot_password"))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.new_password.data)
        db.session.commit()
        audit("auth.password_reset", user=user)
        flash("Password updated. Please log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", form=form)


# ── Helpers ──────────────────────────────────────────────────────────────

def _redirect_by_role(user: User):
    """Redirect authenticated user to their role-specific dashboard."""
    if user.role == "admin":
        return redirect(url_for("admin.dashboard"))
    elif user.role == "doctor":
        return redirect(url_for("doctor.dashboard"))
    elif user.role == "reception":
        return redirect(url_for("reception.dashboard"))
    else:
        return redirect(url_for("patient.dashboard"))
