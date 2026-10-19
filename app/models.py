"""
Database models for MediTrack.

Models:
    User         – authentication & role management
    Doctor       – doctor profile extending User
    Patient      – patient profile extending User
    Appointment  – scheduling with unique constraint per slot
    MedicalRecord – diagnosis, treatment and prescriptions
"""

import hashlib
from datetime import datetime, date, time, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app import db


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (what the DateTime columns store).
    Replaces datetime.utcnow(), which is deprecated from Python 3.12."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    """
    Core user model for authentication.

    Roles: 'admin', 'doctor', 'patient'.
    Passwords are stored as Werkzeug hashes (pbkdf2:sha256).
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="patient")  # admin | doctor | patient
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    # Dependents (children, parents...) have a User row so all patient logic works,
    # but can never sign in themselves.
    can_login = db.Column(db.Boolean, default=True, nullable=False, server_default="1")

    # Two-factor login (TOTP). The secret is only trusted once totp_enabled is True.
    totp_secret = db.Column(db.String(64))
    totp_enabled = db.Column(db.Boolean, default=False, nullable=False, server_default="0")
    totp_last_step = db.Column(db.Integer)        # last accepted 30s step, blocks code replay
    recovery_codes = db.Column(db.Text)           # JSON list of sha256 hashes of unused codes
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    # ── Relationships ────────────────────────────────────────────────────
    doctor_profile = db.relationship(
        "Doctor", backref="user", uselist=False, cascade="all, delete-orphan"
    )
    patient_profile = db.relationship(
        "Patient", backref="user", uselist=False, cascade="all, delete-orphan",
        foreign_keys="Patient.user_id",
    )
    # Patients this account manages (family members)
    dependents = db.relationship(
        "Patient", backref="guardian", foreign_keys="Patient.guardian_user_id",
    )

    # ── Password helpers ─────────────────────────────────────────────────
    def set_password(self, password: str) -> None:
        """Hash and store password."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Verify password against stored hash."""
        return check_password_hash(self.password_hash, password)

    def auth_fingerprint(self) -> str:
        """Short digest of the current password hash. It changes whenever the password does,
        which is what signs out other devices and kills old reset links."""
        return hashlib.sha256(self.password_hash.encode()).hexdigest()[:16]

    def get_id(self) -> str:
        """What Flask-Login stores in the session and remember-me cookie: id + fingerprint."""
        return f"{self.id}:{self.auth_fingerprint()}"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_doctor(self) -> bool:
        return self.role == "doctor"

    @property
    def is_patient(self) -> bool:
        return self.role == "patient"

    @property
    def is_reception(self) -> bool:
        return self.role == "reception"

    def managed_patients(self) -> list:
        """The patient profiles this account may act for: its own, then family members."""
        own = [self.patient_profile] if self.patient_profile else []
        return own + sorted((d for d in self.dependents if d.user.is_active), key=lambda p: p.user.name)

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"


class Doctor(db.Model):
    """
    Doctor profile linked to a User with role='doctor'.

    Stores professional details and schedule parameters used
    to generate available appointment slots.
    """

    __tablename__ = "doctors"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    specialization = db.Column(db.String(100), nullable=False)
    experience_years = db.Column(db.Integer, default=0)
    qualification = db.Column(db.String(200), nullable=False)
    consultation_fee = db.Column(db.Float, default=0.0)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), index=True)

    # Schedule fields
    work_start = db.Column(db.Time, default=time(9, 0), nullable=False)
    work_end = db.Column(db.Time, default=time(17, 0), nullable=False)
    slot_minutes = db.Column(db.Integer, default=30, nullable=False)
    available_days = db.Column(
        db.String(50), default="Mon,Tue,Wed,Thu,Fri", nullable=False
    )  # Comma-separated day abbreviations

    # ── Relationships ────────────────────────────────────────────────────
    appointments = db.relationship(
        "Appointment", backref="doctor", lazy="dynamic"
    )
    medical_records = db.relationship(
        "MedicalRecord", backref="doctor", lazy="dynamic"
    )
    time_off = db.relationship(
        "DoctorTimeOff", backref="doctor", lazy="dynamic", cascade="all, delete-orphan"
    )

    def get_available_days_list(self) -> list[str]:
        """Return available days as a Python list."""
        return [d.strip() for d in self.available_days.split(",") if d.strip()]

    def __repr__(self) -> str:
        return f"<Doctor {self.user.name} — {self.specialization}>"


class Patient(db.Model):
    """
    Patient profile linked to a User with role='patient'.

    Stores demographic and contact information.
    """

    __tablename__ = "patients"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    age = db.Column(db.Integer)
    gender = db.Column(db.String(20))
    phone = db.Column(db.String(20))
    blood_group = db.Column(db.String(5))
    address = db.Column(db.Text)
    allergies = db.Column(db.Text)            # free text, shown to doctors at every visit
    chronic_conditions = db.Column(db.Text)
    # Set for family members managed by another account
    guardian_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    relationship_label = db.Column(db.String(30))   # e.g. "Child", "Spouse", "Parent"

    # ── Relationships ────────────────────────────────────────────────────
    appointments = db.relationship(
        "Appointment", backref="patient", lazy="dynamic"
    )
    medical_records = db.relationship(
        "MedicalRecord", backref="patient", lazy="dynamic"
    )
    vitals = db.relationship(
        "Vital", backref="patient", lazy="dynamic", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Patient {self.user.name}>"


class Appointment(db.Model):
    """
    Appointment between a patient and a doctor.

    A partial unique index on (doctor_id, date, time) prevents double-booking
    a live slot. Status flows: pending → confirmed → completed, or
    pending/confirmed → cancelled.
    """

    __tablename__ = "appointments"
    # Unique only while the booking is live, so a cancelled slot can be rebooked.
    __table_args__ = (
        db.Index(
            "uq_doctor_slot_active", "doctor_id", "date", "time", unique=True,
            sqlite_where=db.text("status != 'cancelled'"),
            postgresql_where=db.text("status != 'cancelled'"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(
        db.Integer, db.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    doctor_id = db.Column(
        db.Integer, db.ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False
    )
    date = db.Column(db.Date, nullable=False, index=True)
    time = db.Column(db.Time, nullable=False)
    status = db.Column(
        db.String(20), default="pending", nullable=False
    )  # pending | confirmed | completed | cancelled | no_show
    reason = db.Column(db.Text)
    reminder_sent = db.Column(db.Boolean, default=False, nullable=False, server_default="0")
    is_walk_in = db.Column(db.Boolean, default=False, nullable=False, server_default="0")
    checked_in_at = db.Column(db.DateTime)    # patient arrived (queue position = order of this)
    started_at = db.Column(db.DateTime)       # doctor began the consultation
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    # ── Relationship ─────────────────────────────────────────────────────
    medical_record = db.relationship(
        "MedicalRecord", backref="appointment", uselist=False, cascade="all, delete-orphan"
    )
    attachments = db.relationship(
        "Attachment", backref="appointment", cascade="all, delete-orphan",
        order_by="Attachment.created_at",
    )

    def __repr__(self) -> str:
        return f"<Appointment {self.date} {self.time} — {self.status}>"


class DoctorTimeOff(db.Model):
    """
    A period a doctor is unavailable: a whole day (start/end NULL) or a time range.

    Slots overlapping it are hidden from booking. Existing appointments are
    not touched automatically; the doctor is warned instead.
    """

    __tablename__ = "doctor_time_off"

    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(
        db.Integer, db.ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date = db.Column(db.Date, nullable=False, index=True)
    start_time = db.Column(db.Time)   # NULL with end_time NULL = all day
    end_time = db.Column(db.Time)
    reason = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @property
    def all_day(self) -> bool:
        return self.start_time is None

    def blocks(self, slot_start: time, slot_end: time) -> bool:
        """True if the slot [slot_start, slot_end) overlaps this time off."""
        if self.all_day:
            return True
        return slot_start < self.end_time and slot_end > self.start_time

    def __repr__(self) -> str:
        return f"<DoctorTimeOff {self.date} doctor={self.doctor_id}>"


class MedicalRecord(db.Model):
    """
    Medical record tied to a completed appointment.

    Created by the treating doctor; viewable by the patient.
    """

    __tablename__ = "medical_records"

    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(
        db.Integer, db.ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    patient_id = db.Column(
        db.Integer, db.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    doctor_id = db.Column(
        db.Integer, db.ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False
    )
    diagnosis = db.Column(db.Text, nullable=False)
    treatment = db.Column(db.Text, nullable=False)
    prescription = db.Column(db.Text)
    notes = db.Column(db.Text)
    follow_up_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    # `prescription` (free text) is kept for older records and extra notes;
    # new prescriptions are structured rows below.
    items = db.relationship(
        "PrescriptionItem", backref="record", cascade="all, delete-orphan",
        order_by="PrescriptionItem.position",
    )

    def __repr__(self) -> str:
        return f"<MedicalRecord appt={self.appointment_id}>"


class PrescriptionItem(db.Model):
    """One medicine on a prescription: what, how much, how often, for how long."""

    __tablename__ = "prescription_items"

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(
        db.Integer, db.ForeignKey("medical_records.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    position = db.Column(db.Integer, default=0, nullable=False)
    medicine = db.Column(db.String(150), nullable=False)
    dose = db.Column(db.String(80))          # e.g. "500 mg"
    frequency = db.Column(db.String(80))     # e.g. "twice a day, after meals"
    duration = db.Column(db.String(80))      # e.g. "5 days"
    instructions = db.Column(db.String(200))


class Attachment(db.Model):
    """
    A lab report, scan or other file attached to a visit.

    Bytes live on disk under UPLOAD_FOLDER as `stored_name` (random, never the
    user's filename); `original_name` is kept only for display and downloads.
    """

    __tablename__ = "attachments"

    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(
        db.Integer, db.ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    patient_id = db.Column(
        db.Integer, db.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    category = db.Column(db.String(30), default="other", nullable=False)  # lab_report | scan | other
    description = db.Column(db.String(200))
    original_name = db.Column(db.String(200), nullable=False)
    stored_name = db.Column(db.String(64), nullable=False, unique=True)
    content_type = db.Column(db.String(60), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    uploaded_by = db.relationship("User")


class Vital(db.Model):
    """A health reading (blood pressure, weight, blood sugar) for trend charts."""

    __tablename__ = "vitals"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(
        db.Integer, db.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    recorded_on = db.Column(db.Date, nullable=False, index=True)
    systolic = db.Column(db.Integer)       # mmHg
    diastolic = db.Column(db.Integer)      # mmHg
    weight_kg = db.Column(db.Float)
    sugar_mgdl = db.Column(db.Float)       # blood glucose, mg/dL
    notes = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    recorded_by = db.relationship("User")


class Department(db.Model):
    """A hospital department; doctors belong to at most one."""

    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(300))

    doctors = db.relationship("Doctor", backref="department", lazy="dynamic")


class Invoice(db.Model):
    """
    Bill for a completed visit (one per appointment).

    Created automatically when the visit is completed, using the doctor's fee.
    Payment is recorded by staff; `method` is a label only (no gateway yet).
    """

    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(20), unique=True)            # INV-000042, set after insert
    appointment_id = db.Column(
        db.Integer, db.ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    patient_id = db.Column(
        db.Integer, db.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doctor_id = db.Column(
        db.Integer, db.ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount = db.Column(db.Float, nullable=False, default=0.0)
    discount = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(10), nullable=False, default="unpaid", index=True)  # unpaid | paid | void
    method = db.Column(db.String(20))                         # cash | card | upi | other
    paid_at = db.Column(db.DateTime)
    paid_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    notes = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    appointment = db.relationship("Appointment", backref=db.backref("invoice", uselist=False))
    patient = db.relationship("Patient", backref=db.backref("invoices", lazy="dynamic"))
    doctor = db.relationship("Doctor", backref=db.backref("invoices", lazy="dynamic"))
    paid_by = db.relationship("User")

    @property
    def total(self) -> float:
        return max((self.amount or 0) - (self.discount or 0), 0.0)


class Rating(db.Model):
    """A patient's 1-5 star review of a doctor after a completed visit."""

    __tablename__ = "ratings"

    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(
        db.Integer, db.ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    doctor_id = db.Column(
        db.Integer, db.ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_id = db.Column(
        db.Integer, db.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    stars = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    appointment = db.relationship("Appointment", backref=db.backref("rating", uselist=False))
    doctor = db.relationship("Doctor", backref=db.backref("ratings", lazy="dynamic"))
    patient = db.relationship("Patient")


class AuditLog(db.Model):
    """Append-only trail of access to and changes of sensitive data."""

    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)   # NULL = anonymous (e.g. failed login)
    action = db.Column(db.String(40), nullable=False, index=True)           # e.g. record.view
    entity_type = db.Column(db.String(30))
    entity_id = db.Column(db.Integer)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), index=True)  # whose data was touched
    detail = db.Column(db.String(300))
    ip = db.Column(db.String(45))

    user = db.relationship("User")
    patient = db.relationship("Patient")


class LoginAttempt(db.Model):
    """One sign-in or 2FA attempt, used for lockout and per-IP throttling."""

    __tablename__ = "login_attempts"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(130), nullable=False, index=True)   # lower-cased email, or "2fa:<id>"
    ip = db.Column(db.String(45), index=True)
    success = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
