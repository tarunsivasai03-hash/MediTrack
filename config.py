"""
Application configuration module.

Loads settings from environment variables with sensible defaults
for development. Supports SQLite (dev) and PostgreSQL (prod).
"""

import os
import secrets

basedir = os.path.abspath(os.path.dirname(__file__))


def _database_url() -> str:
    """DATABASE_URL, defaulting to a local SQLite file. Heroku-style postgres:// URLs are
    rewritten because SQLAlchemy only accepts postgresql://."""
    url = os.environ.get("DATABASE_URL") or f"sqlite:///{os.path.join(basedir, 'instance', 'hms.db')}"
    return "postgresql://" + url[len("postgres://"):] if url.startswith("postgres://") else url


def _dev_secret_key() -> str:
    """SECRET_KEY for local development: random, and kept in instance/.secret_key so sessions
    survive restarts. Never a fixed string that could be guessed from the source code."""
    path = os.path.join(basedir, "instance", ".secret_key")
    try:
        with open(path, encoding="ascii") as fh:
            key = fh.read().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="ascii") as fh:
            fh.write(key)
    except OSError:
        pass            # read-only disk: fall back to a key that lasts for this process
    return key


class Config:
    """Base configuration shared across all environments."""

    # Security. Production must set SECRET_KEY (it refuses to start without one);
    # development generates its own (see _dev_secret_key).
    SECRET_KEY = os.environ.get("SECRET_KEY")

    # Database — defaults to SQLite; set DATABASE_URL for PostgreSQL
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Session cookie hardening
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_DURATION = 86400  # 1 day in seconds

    # WTForms CSRF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # 1 hour

    # The hospital's time zone: "today", slots and check-ins follow it, not the server clock
    # (containers run in UTC). Empty = the server's local time.
    TIMEZONE = os.environ.get("TIMEZONE", "Asia/Kolkata")

    # Pagination
    ITEMS_PER_PAGE = 10

    # Uploads (lab reports / scans)
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(basedir, "instance", "uploads"))
    MAX_UPLOAD_BYTES = 5 * 1024 * 1024
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024          # hard cap on any request body
    MAX_ATTACHMENTS_PER_VISIT = 10

    # Email — leave MAIL_SERVER unset in development to log messages instead of sending
    MAIL_SERVER = os.environ.get("MAIL_SERVER")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "1") == "1"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_FROM = os.environ.get("MAIL_FROM", "MediTrack <no-reply@meditrack.local>")
    MAIL_SUPPRESS_SEND = False   # tests set True and read app.extensions["outbox"]

    # Brute-force protection (see app/security.py)
    LOGIN_MAX_FAILURES = 5          # failed attempts per account before lockout
    LOGIN_LOCK_MINUTES = 15
    LOGIN_IP_MAX_FAILURES = 30      # failed attempts per IP per 10 minutes

    # Force admins and doctors to enable two-factor login before using the app
    REQUIRE_2FA = os.environ.get("REQUIRE_2FA", "0") == "1"
    TRUST_PROXY = os.environ.get("TRUST_PROXY", "0") == "1"   # behind nginx / a load balancer

    # Password-reset links stay valid for this many seconds
    RESET_TOKEN_MAX_AGE = 3600


class DevelopmentConfig(Config):
    """Development-specific settings."""

    DEBUG = True
    SECRET_KEY = os.environ.get("SECRET_KEY") or _dev_secret_key()


class ProductionConfig(Config):
    """Production-specific settings."""

    # No fallback: refuse to start with a guessable key.
    SECRET_KEY = os.environ.get("SECRET_KEY")

    DEBUG = False
    # Secure cookies need https (a TLS proxy). Set SESSION_COOKIE_SECURE=0 only to try the
    # container over plain http on localhost.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") == "1"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE


class TestingConfig(Config):
    """Testing-specific settings."""

    TESTING = True
    SECRET_KEY = "test-only-secret-key"
    TIMEZONE = None            # tests build dates from the machine clock
    MAIL_SUPPRESS_SEND = True
    UPLOAD_FOLDER = os.path.join(basedir, "instance", "test_uploads")
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


# Map environment names to config classes
config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
