# MediTrack

A hospital and clinic management system built with Flask. Four roles share one app: **patient**, **doctor**, **reception** and **admin**.

| Area | What it does |
|---|---|
| Booking | Slot picker, rescheduling, doctor time off, doctor directory (department / fee / day / rating filters), family members under one login |
| Front desk | Walk-ins, check-in, **live queue** (auto-refreshing), booking on behalf of patients, no-show tracking |
| Clinical | Structured prescriptions + PDF, lab report / scan uploads, vitals charts, allergies shown to doctors, follow-up booking |
| Money | Invoices created when a visit completes, payments with discount, receipts (PDF) |
| Reports | Revenue per doctor, busiest hours / weekdays, no-show rate, department breakdown, Excel + CSV export |
| Security | Audit log (plus a patient-facing "who accessed my data" page), login lockout and IP throttling, TOTP two-factor login with recovery codes, sessions ended on password change, security headers |
| Experience | Dark mode, English / हिन्दी / Español, installable app (PWA), mobile-first layout |

## Quick start (development, SQLite)

```bash
python -m venv venv
venv\Scripts\activate                 # Windows. On macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
flask --app run db upgrade            # create / update the database
python seed.py                        # demo users and departments
python run.py                         # http://localhost:5000
```

`run.py` runs with debug and auto-reload on in development. For anything else use gunicorn (`wsgi:app`) or Docker, below.

### Demo logins

Created by `seed.py`. **These passwords are public. Never seed a real deployment.**

| Role | Email | Password |
|---|---|---|
| Admin | admin@hms.com | `admin123` |
| Reception | desk@hms.com | `reception123` |
| Doctor | priya@hms.com, rajesh@hms.com, anita@hms.com | `doctor123` |
| Patient | amit@hms.com, sneha@hms.com | `patient123` |

The `@hms.com` addresses are just the demo data; they are not tied to the app's name.

For real accounts, create the first admin from the command line instead of seeding:

```bash
flask --app run create-user --role admin --name "Your Name" --email you@example.com
```

## Production with Docker + PostgreSQL

```bash
cp .env.example .env            # set SECRET_KEY, POSTGRES_PASSWORD and TIMEZONE
docker compose up -d --build
docker compose exec web flask create-user --role admin --name "Your Name" --email you@example.com
```

- `web` runs gunicorn on port 8000 and applies migrations on start; `db` is PostgreSQL 16; `scheduler` sends reminder emails daily.
- Production **refuses to start** without a `SECRET_KEY`, or with a weak one (under 16 characters, or a placeholder such as `change-me`). Generate one with `python -c "import secrets; print(secrets.token_hex(32))"`.
- Uploads live in the `uploads` volume, the database in `db_data`. **Back up both.**
- Put a TLS proxy (nginx, Caddy, a cloud load balancer) in front of port 8000 and keep `TRUST_PROXY=1`.
  Cookies are `Secure` in production; to try the container over plain http on localhost set `SESSION_COOKIE_SECURE=0`.
- `GET /healthz` is the liveness / readiness check.
- Set `REQUIRE_2FA=1` to make admins, doctors and reception set up two-factor login before using the app.

CI (`.github/workflows/ci.yml`) runs the tests on Python 3.11 and 3.12, applies every migration on PostgreSQL,
checks that the models match the migrations (`flask db check`), tests downgrade and upgrade, and builds the Docker image.

> The Docker and PostgreSQL setup is exercised by CI, not by the local test suite. Run `docker compose up --build` once and check `/healthz` before relying on it.

## Configuration

Set these as environment variables or in a `.env` file (see `.env.example`).

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Required in production; signs sessions and password-reset links. In development one is generated and kept in `instance/.secret_key`. |
| `DATABASE_URL` | `postgresql://user:pass@host/db` (a `postgres://` URL is rewritten). Defaults to SQLite in `instance/`. |
| `TIMEZONE` | The hospital's time zone, e.g. `Asia/Kolkata` (the default). Slots, "today", check-ins and reminders follow it, **not** the server clock, because containers run in UTC. An invalid name stops the app at start-up. |
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM` | SMTP. With `MAIL_SERVER` empty, emails are written to the server log instead (that is where reset links appear in development). |
| `UPLOAD_FOLDER` | Where lab reports and scans are stored (default `instance/uploads`). |
| `TRUST_PROXY` | `1` behind a reverse proxy so client IPs and https are detected correctly. |
| `SESSION_COOKIE_SECURE` | Production default `1`. Set `0` only to try the container over plain http. |
| `REQUIRE_2FA` | `1` to force two-factor setup for staff roles. |
| `PORT` | Port for `python run.py` (default 5000). |

## Email and reminders

Emails: appointment confirmed, cancelled by staff, rescheduled, a day-before reminder, and password reset.
Messages about a family member go to the account holder. A failed send never breaks a page.

Reminders are sent by a command, so schedule it daily (the Docker `scheduler` service does this):

```bash
flask --app run send-reminders          # confirmed appointments tomorrow
```

An appointment is marked as reminded only after its email was handed off, so if the mail server is down the next run tries again.

## Accounts and security

- **Passwords:** changing or resetting a password signs out every other device and remember-me login. Reset links work once.
- **Lockout:** 5 failed sign-ins lock an account for 15 minutes; an IP is throttled after 30 failures in 10 minutes.
- **Two-factor:** an authenticator app (TOTP) with one-time recovery codes. Codes cannot be reused.
- **Reception** sees names, phones and schedules only: no medical records, files, vitals or allergies, and no doctor management.
- **Admin** has no access to clinical files either (by design). Admins manage people, billing oversight, reports and the audit log.
- **Doctors** see only patients they have an appointment with.

## Project layout

```
app/                 application package
  routes/            one module per area: auth, admin, doctor, patient, reception, billing, files, api, pwa
  models.py          database models          services.py   booking and queue rules
  billing.py         invoices and payments    reports.py    report data + Excel export
  clock.py           hospital-local time      i18n.py       translations (catalogue in translations*.py)
  pdf.py, pdf_invoice.py   prescription / invoice PDFs
templates/           Jinja templates, one folder per role
static/              CSS (single design system), JavaScript, icons, service worker
migrations/          Alembic migrations
tests/               pytest suite
seed.py, run.py, wsgi.py, config.py
```

## Database changes

```bash
flask --app run db migrate -m "describe the change"   # review the generated file in migrations/versions/
flask --app run db upgrade
```

## Tests

```bash
python -m pytest            # about 3 minutes; uses an in-memory database
```

## Known limitations

- Uploaded files are validated by type, content and size, stored under random names and always sent as downloads, but they are **not virus-scanned**.
- Emails are sent during the web request, so a slow mail server can slow the page that triggered them.
- Translations cover navigation, sign-in, dashboards, account security, error pages and server messages. Some doctor, admin and reception screens are still partly English; missing text falls back to English.
- The service worker caches only public static files and an offline page. It never stores pages, API responses or documents.
- Online payment is not integrated: staff record payments (`cash`, `card`, `upi`, `other`).
- Failed sign-ins are counted per email address, so someone can deliberately lock another person's account for 15 minutes.
#   M e d i T r a c k  
 