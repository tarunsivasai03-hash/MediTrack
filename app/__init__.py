"""
Application factory module.

Creates and configures the Flask application using the factory pattern.
All extensions are initialized here and blueprints registered.
"""

from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

from config import config_map

# ── Extension instances (created once, bound to app in factory) ──────────
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()

# Configure Flask-Login defaults
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"

# Endpoints reachable before two-factor setup is finished (when REQUIRE_2FA is on)
_2FA_SETUP_ENDPOINTS = {
    "auth.security_settings", "auth.disable_2fa", "auth.logout", "static", "healthz",
    "pwa.service_worker", "pwa.manifest", "pwa.offline", "i18n.set_language",
}

# Blueprints: (module, attribute, url_prefix)
_BLUEPRINTS = [
    ("auth", "auth_bp", ""),
    ("admin", "admin_bp", "/admin"),
    ("doctor", "doctor_bp", "/doctor"),
    ("patient", "patient_bp", "/patient"),
    ("api", "api_bp", "/api"),
    ("files", "files_bp", "/files"),
    ("reception", "reception_bp", "/reception"),
    ("billing", "billing_bp", "/billing"),
    ("pwa", "pwa_bp", ""),
]


def create_app(config_name: str = "development") -> Flask:
    """
    Application factory.

    Args:
        config_name: One of 'development', 'production', 'testing'.

    Returns:
        Configured Flask application instance.
    """
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )

    # ── Load configuration ───────────────────────────────────────────────
    app.config.from_object(config_map.get(config_name, config_map["development"]))

    if config_name == "production":
        key = app.config.get("SECRET_KEY")
        if not key:
            raise RuntimeError("SECRET_KEY environment variable must be set in production.")
        if len(key) < 16 or key.lower() in ("change-me", "changeme", "secret"):
            raise RuntimeError("SECRET_KEY is too weak: use a long random value, e.g. `python -c \"import secrets; print(secrets.token_hex(32))\"`.")

    from app import clock
    clock.check_timezone(app.config.get("TIMEZONE"))

    if app.config.get("TRUST_PROXY"):
        # Behind nginx / a load balancer: trust X-Forwarded-For/Proto so client IPs
        # (lockout, audit log) and https URLs are right.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # ── Initialize extensions ────────────────────────────────────────────
    db.init_app(app)
    # render_as_batch lets Alembic alter SQLite tables (it has limited ALTER support)
    migrate.init_app(app, db, render_as_batch=True)
    login_manager.init_app(app)
    csrf.init_app(app)

    # ── Request hooks ────────────────────────────────────────────────────
    @app.before_request
    def account_guards():
        """Sign out deactivated accounts; push staff to set up 2FA when it is required."""
        from flask import request, redirect, url_for, flash
        from flask_login import current_user, logout_user

        if not current_user.is_authenticated:
            return None
        if not current_user.is_active or not current_user.can_login:
            logout_user()
            if request.endpoint != "static":
                flash("Your account has been deactivated. Contact the administrator.", "danger")
                return redirect(url_for("auth.login"))
        if (app.config["REQUIRE_2FA"] and current_user.role in ("admin", "doctor", "reception")
                and not current_user.totp_enabled and request.endpoint not in _2FA_SETUP_ENDPOINTS):
            flash("Please turn on two-factor login to continue.", "warning")
            return redirect(url_for("auth.security_settings"))
        return None

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        return resp

    # ── User loader for Flask-Login ──────────────────────────────────────
    from app.models import User

    @login_manager.user_loader
    def load_user(user_id: str):
        """Load the user a session belongs to, unless their password changed since it was issued."""
        import hmac

        uid, _, fingerprint = user_id.partition(":")
        if not uid.isdigit():
            return None
        user = db.session.get(User, int(uid))
        if user is None or not hmac.compare_digest(fingerprint, user.auth_fingerprint()):
            return None
        return user

    @app.context_processor
    def template_helpers():
        """Expose `nav_url(endpoint)`: the URL, or None if the route isn't built yet."""
        from flask import url_for
        from werkzeug.routing import BuildError

        def nav_url(endpoint: str):
            try:
                return url_for(endpoint)
            except BuildError:
                return None

        from app.patient_context import acting_patient, managed_patients

        import os

        def asset(filename: str) -> str:
            """Static URL with the file's modified time, so browsers fetch changed CSS/JS at once."""
            path = os.path.join(app.static_folder, filename)
            try:
                version = int(os.path.getmtime(path))
            except OSError:
                version = 0
            return url_for("static", filename=filename, v=version)

        return {
            "asset": asset,
            "nav_url": nav_url,
            "acting": acting_patient(),
            "family_members": managed_patients(),
        }

    # ── Internationalisation ─────────────────────────────────────────────
    from app import i18n
    i18n.init_app(app)

    # ── Register blueprints ──────────────────────────────────────────────
    import importlib

    for module, attr, prefix in _BLUEPRINTS:
        bp = getattr(importlib.import_module(f"app.routes.{module}"), attr)
        app.register_blueprint(bp, url_prefix=prefix or None)

    @app.route("/healthz")
    def healthz():
        """Liveness + database check for load balancers and Docker."""
        from flask import jsonify
        try:
            db.session.execute(db.text("SELECT 1"))
            return jsonify(status="ok")
        except Exception:  # noqa: BLE001
            return jsonify(status="database unavailable"), 503

    # ── CLI ──────────────────────────────────────────────────────────────
    import click

    @app.cli.command("send-reminders")
    @click.option("--days", default=1, show_default=True, help="Remind for appointments this many days ahead.")
    def send_reminders(days):
        """Email reminders for confirmed appointments. Run daily from cron / Task Scheduler."""
        from app.services import send_due_reminders

        click.echo(f"Sent {send_due_reminders(days)} reminder(s).")

    @app.cli.command("create-user")
    @click.option("--name", prompt=True)
    @click.option("--email", prompt=True)
    @click.option("--role", type=click.Choice(["admin", "reception"]), default="admin", show_default=True)
    @click.password_option(help="Prompted for if omitted.")
    def create_user(name, email, role, password):
        """Create an admin or reception account (use this instead of seeding weak passwords)."""
        from app.models import User

        email = email.lower().strip()
        if len(password) < 8:
            raise click.ClickException("Password must be at least 8 characters.")
        if User.query.filter_by(email=email).first():
            raise click.ClickException(f"{email} is already registered.")
        user = User(name=name.strip(), email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created {role} account for {email}.")

    # ── Custom error handlers ────────────────────────────────────────────
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(413)
    def too_large(e):
        from flask import flash, redirect, request, url_for
        flash("That upload is too large (5 MB maximum).", "danger")
        return redirect(request.referrer or url_for("auth.index"))

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500

    return app
