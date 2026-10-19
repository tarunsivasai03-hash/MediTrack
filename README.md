# MediTrack — Hospital & Clinic Management System

**MediTrack** is a comprehensive hospital and clinic management system built with **Flask**. It brings appointment scheduling, patient care, front-desk operations, billing, reporting, and security into one unified application.

The system supports four user roles — **Patient, Doctor, Reception, and Admin** — with role-based access control to keep workflows organized and sensitive medical information protected.

## Key Features

| Module | Features |
|---|---|
| **Appointment Management** | Interactive slot picker, appointment rescheduling, doctor availability and time off, doctor directory with department, consultation fee, day, and rating filters, and family member management under one account. |
| **Front Desk & Queue** | Walk-in registration, patient check-in, automatically refreshing live queue, reception-managed bookings, and no-show tracking. |
| **Clinical Management** | Structured prescriptions with PDF generation, lab report and scan uploads, vital-sign charts, allergy visibility for doctors, and follow-up appointment booking. |
| **Billing & Payments** | Automatic invoice generation when a visit is completed, payment recording, discounts, and downloadable PDF receipts. |
| **Reports & Analytics** | Doctor-wise revenue, peak hours and weekdays, no-show rates, department-wise breakdowns, and Excel/CSV exports. |
| **Security & Privacy** | Audit logs, patient-facing medical data access history, login lockout, IP-based throttling, TOTP-based two-factor authentication, recovery codes, session invalidation after password changes, and security headers. |
| **User Experience** | Dark mode, English/Hindi/Spanish localization, installable Progressive Web App (PWA), and a mobile-first responsive interface. |

---

## Getting Started

### 1. Prerequisites

- Python 3.11 or 3.12
- pip
- Git (recommended)
- SQLite for local development

### 2. Set Up the Development Environment

Clone the repository and navigate to the project directory:

```bash
git clone <repository-url>
cd <repository-directory>
```

Create and activate a virtual environment.

**Windows:**

```bash
python -m venv venv
venv\Scripts\activate
```

**macOS/Linux:**

```bash
python3 -m venv venv
source venv/bin/activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

Initialize or update the database:

```bash
flask --app run db upgrade
```

Load the demo users and departments:

```bash
python seed.py
```

Start the development server:

```bash
python run.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

> **Development only:** `run.py` enables Flask debug mode and automatic reloading. Never expose this configuration to a production environment.

## Demo Accounts

The following accounts are created by `seed.py` for local testing.

| Role | Email | Password |
|---|---|---|
| Admin | `admin@hms.com` | `admin123` |
| Reception | `desk@hms.com` | `reception123` |
| Doctor | `priya@hms.com` | `doctor123` |
| Doctor | `rajesh@hms.com` | `doctor123` |
| Doctor | `anita@hms.com` | `doctor123` |
| Patient | `amit@hms.com` | `patient123` |
| Patient | `sneha@hms.com` | `patient123` |

These credentials are public demo credentials. **Never use seeded demo accounts in a real deployment.**

The `@hms.com` addresses are sample data and are not associated with the MediTrack brand.

### Create an Initial Admin Account

For a real deployment, create the first administrator through the command line instead of seeding demo accounts:

```bash
flask --app run create-user \
  --role admin \
  --name "Your Name" \
  --email you@example.com
```

---

## Production Deployment with Docker & PostgreSQL

MediTrack supports a containerized production setup using Docker Compose, PostgreSQL 16, Gunicorn, and a scheduled reminder service.

### 1. Configure Environment Variables

```bash
cp .env.example .env
```

Update `.env` with secure values for `SECRET_KEY`, `POSTGRES_PASSWORD`, and `TIMEZONE`, along with any required email settings.

Generate a cryptographically secure application secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Start the Application

```bash
docker compose up -d --build
```

Create the initial administrator:

```bash
docker compose exec web flask create-user \
  --role admin \
  --name "Your Name" \
  --email you@example.com
```

### Deployment Architecture

- **Web:** Runs the Flask application using Gunicorn on port 8000 and applies database migrations at startup.
- **Database:** PostgreSQL 16 stores application data.
- **Scheduler:** Runs the daily appointment reminder command.
- **Uploads volume:** Stores uploaded clinical documents.
- **Database volume:** Persists PostgreSQL data.

Back up **both the database and uploaded files**. Test your restoration process regularly.

### Production Security Checklist

- Set a strong, unique `SECRET_KEY`.
- Configure a secure PostgreSQL password.
- Place a TLS-enabled reverse proxy, such as Nginx or Caddy, in front of the application.
- Set `TRUST_PROXY=1` only when the application is behind a correctly configured trusted reverse proxy.
- Keep `SESSION_COOKIE_SECURE=1` in production.
- Set `REQUIRE_2FA=1` to require two-factor authentication for administrators, doctors, and reception staff.
- Avoid exposing the database port or internal application services publicly.
- Configure persistent storage and reliable backups.
- Verify the deployment using the `/healthz` endpoint.

For local testing over plain HTTP, `SESSION_COOKIE_SECURE=0` can be used when necessary. Do not use this setting for public production traffic.

### Health Check

```http
GET /healthz
```

This endpoint is intended for application liveness/readiness checks. Verify its actual database-readiness behavior before relying on it for deployment orchestration.

---

## Configuration

MediTrack reads configuration from environment variables or a `.env` file. Refer to `.env.example` for the available settings.

| Variable | Description |
|---|---|
| `SECRET_KEY` | Application secret used to sign sessions and password-reset tokens. Required in production. Development can generate and persist a local secret under `instance/.secret_key`. |
| `DATABASE_URL` | Database connection URL. Defaults to SQLite under `instance/`. PostgreSQL URLs are supported, and legacy `postgres://` URLs are rewritten. |
| `TIMEZONE` | Hospital time zone used for appointment slots, check-ins, date calculations, and reminders. Defaults to `Asia/Kolkata`. Invalid time-zone names prevent startup. |
| `MAIL_SERVER` | SMTP server hostname. If unset, email messages are written to server logs in the configured development workflow. |
| `MAIL_PORT` | SMTP server port. |
| `MAIL_USE_TLS` | Enables SMTP TLS according to the application's mail configuration. |
| `MAIL_USERNAME` | SMTP authentication username. |
| `MAIL_PASSWORD` | SMTP authentication password. |
| `MAIL_FROM` | Sender address for outgoing emails. |
| `UPLOAD_FOLDER` | Storage location for uploaded lab reports and scans. Defaults to `instance/uploads`. |
| `TRUST_PROXY` | Enables trusted-proxy handling when configured behind a reverse proxy. |
| `SESSION_COOKIE_SECURE` | Controls whether session cookies require HTTPS. Production default: `1`. |
| `REQUIRE_2FA` | Set to `1` to require two-factor authentication setup for staff roles. |
| `PORT` | Development server port. Defaults to `5000`. |

### Time Zone Handling

MediTrack uses the hospital's configured time zone rather than relying on the host machine's clock. This helps ensure appointment slots, the definition of “today,” check-ins, and reminders remain consistent when containers run in UTC.

---

## Email Notifications & Appointment Reminders

MediTrack supports email notifications for:

- Appointment confirmations
- Appointment cancellations initiated by staff
- Appointment rescheduling
- Reminders sent one day before an appointment
- Password-reset instructions

Notifications concerning a family member are sent to the account holder.

A failed email send does not prevent the associated page from completing its operation.

### Schedule Daily Reminders

Run the following command to send reminders for confirmed appointments scheduled for the next day:

```bash
flask --app run send-reminders
```

The Docker `scheduler` service is configured to run this command daily.

An appointment is marked as reminded only after the email has been handed off successfully. If delivery cannot be handed off because the mail server is unavailable, a subsequent run can retry.

> Successful SMTP handoff does not guarantee delivery to the recipient's inbox.

---

## Accounts, Permissions & Security

MediTrack implements role-based permissions and several account security controls.

### Password & Session Security

- Password changes and resets invalidate other active sessions and remember-me logins.
- Password-reset links can only be used once.
- Five failed sign-in attempts trigger a 15-minute account lockout.
- More than 30 failed attempts from an IP within 10 minutes trigger IP-based throttling.
- TOTP-based two-factor authentication supports one-time recovery codes.

### Role-Based Access Control

| Role | Access |
|---|---|
| **Patient** | Manage appointments, view personal clinical information and billing records, and review the audit history of access to their data. |
| **Doctor** | Access clinical records only for patients with whom they have an appointment, subject to the application's permission rules. |
| **Reception** | Manage appointments, walk-ins, check-ins, and queues. Access is limited to names, phone numbers, and schedules, without access to clinical records, uploaded files, vitals, allergies, or doctor management. |
| **Admin** | Manage user accounts, oversee billing, view reports, and access audit logs. Administrators do not have access to clinical files by design. |

These permissions are intended to limit access to sensitive information according to each role's responsibilities.

---

## Project Structure

```text
MediTrack/
├── app/
│   ├── routes/
│   │   ├── auth.py
│   │   ├── admin.py
│   │   ├── doctor.py
│   │   ├── patient.py
│   │   ├── reception.py
│   │   ├── billing.py
│   │   ├── files.py
│   │   ├── api.py
│   │   └── pwa.py
│   ├── models.py
│   ├── services.py
│   ├── billing.py
│   ├── reports.py
│   ├── clock.py
│   ├── i18n.py
│   ├── translations*.py
│   ├── pdf.py
│   └── pdf_invoice.py
├── templates/
│   └── Role-specific Jinja templates
├── static/
│   ├── CSS
│   ├── JavaScript
│   ├── icons
│   └── service worker
├── migrations/
│   └── Alembic database migrations
├── tests/
│   └── Pytest test suite
├── seed.py
├── run.py
├── wsgi.py
├── config.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

### Core Components

- `models.py` — Database models and relationships.
- `services.py` — Appointment booking and queue-management rules.
- `billing.py` — Invoice generation and payment processing.
- `reports.py` — Report aggregation and Excel exports.
- `clock.py` — Hospital-local time handling.
- `i18n.py` and `translations*.py` — Localization and translation catalogues.
- `pdf.py` and `pdf_invoice.py` — Prescription and invoice PDF generation.
- `routes/` — Application endpoints organized by role and functional area.

---

## Database Migrations

MediTrack uses Flask-Migrate and Alembic to manage database schema changes.

### Generate a Migration

```bash
flask --app run db migrate -m "describe the change"
```

Review the generated migration in `migrations/versions/` before applying it.

### Apply Migrations

```bash
flask --app run db upgrade
```

Avoid applying unreviewed generated migrations directly to production.

---

## Testing & Continuous Integration

Run the test suite locally:

```bash
python -m pytest
```

The local test suite uses an in-memory database and takes approximately three minutes.

### Continuous Integration

The GitHub Actions workflow at `.github/workflows/ci.yml`:

- Runs tests on Python 3.11 and 3.12.
- Applies migrations against PostgreSQL.
- Checks model and migration consistency using `flask db check`.
- Exercises migration downgrade and upgrade paths.
- Builds the Docker image.

The Docker/PostgreSQL workflow is exercised separately from the local test suite. Before relying on a production deployment, run the containerized application and verify its health endpoint.

```bash
docker compose up --build
```

---

## Known Limitations & Future Improvements

The following limitations should be considered before deploying MediTrack in a production healthcare environment.

1. **Uploaded file scanning:** File uploads are validated by type, content, and size, stored under randomized filenames, and served as downloads. However, uploads are not virus-scanned.
2. **Email performance:** Emails are sent during web requests, so a slow mail server can delay the response. A background job queue could improve reliability and responsiveness.
3. **Incomplete localization:** Navigation, authentication, dashboards, account security, error pages, and server messages are translated into English, Hindi, and Spanish. Some doctor, admin, and reception screens remain partially English.
4. **Offline functionality:** The service worker caches public static assets and an offline page only. It does not cache application pages, API responses, or clinical documents.
5. **Online payment integration:** The system records payments made through cash, card, UPI, or other methods. It does not directly integrate with a payment gateway.
6. **Account lockout behavior:** Failed sign-ins are counted per email address. This can allow someone to intentionally lock another person's account for 15 minutes. Consider introducing progressive delays, carefully designed rate limits, and abuse-resistant account recovery.
7. **Healthcare compliance:** Before real-world deployment, assess applicable privacy, medical-record retention, consent, data-protection, and healthcare regulations. Security features alone do not establish regulatory compliance.
8. **Operational resilience:** Production deployments should validate backup restoration, upload persistence, monitoring, log retention, incident response, and disaster recovery.

---

## Contributing

Contributions are welcome.

1. Create a feature branch.
2. Make your changes and add appropriate tests.
3. Run the test suite.
4. Review any database migrations.
5. Submit a pull request describing the change and its impact.

Please avoid committing secrets, production patient data, or real credentials.

---

## License

Add your chosen license and its corresponding `LICENSE` file before distributing the project publicly.

---

**MediTrack — Simplifying hospital operations, connecting care teams, and improving the patient experience.**
