"""
WTForms form classes for MediTrack.

All user input is validated server-side through these forms.
CSRF protection is handled globally by Flask-WTF's CSRFProtect.
"""

from datetime import date

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import (
    StringField,
    PasswordField,
    SelectField,
    IntegerField,
    FloatField,
    TextAreaField,
    DateField,
    TimeField,
    BooleanField,
    SubmitField,
    HiddenField,
    SelectMultipleField,
    FieldList,
    RadioField,
    FormField,
    Form,
)
from wtforms.widgets import CheckboxInput, ListWidget

from app import clock
from app.i18n import translate
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)

from app.i18n import lazy as _l
from app.models import User

DAY_CHOICES = [(d, d) for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")]
MIN_PASSWORD = 8


# ═══════════════════════════════════════════════════════════════════════════
# Authentication Forms
# ═══════════════════════════════════════════════════════════════════════════

def _translate_errors(form) -> None:
    """Show validation messages in the visitor's language (English if untranslated)."""
    for field in form:
        field.errors = [translate(e) for e in field.errors]
        if isinstance(field, FormField):
            _translate_errors(field.form)
        elif isinstance(field, FieldList):
            for entry in field.entries:
                if isinstance(entry, FormField):
                    _translate_errors(entry.form)


class BaseForm(FlaskForm):
    """FlaskForm whose validation errors are translated."""

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators=extra_validators)
        _translate_errors(self)
        return ok



class LoginForm(BaseForm):
    """Login form with email + password."""

    email = StringField(
        _l("Email"),
        validators=[DataRequired(), Email()],
        render_kw={"placeholder": "you@example.com", "autocomplete": "email"},
    )
    password = PasswordField(
        _l("Password"),
        validators=[DataRequired()],
        render_kw={"placeholder": "Enter your password"},
    )
    remember = BooleanField(_l("Remember Me"))
    submit = SubmitField(_l("Log In"))


class RegistrationForm(BaseForm):
    """Patient self-registration form."""

    name = StringField(
        _l("Full Name"),
        validators=[DataRequired(), Length(min=2, max=120)],
        render_kw={"placeholder": "John Doe"},
    )
    email = StringField(
        _l("Email"),
        validators=[DataRequired(), Email(), Length(max=120)],
        render_kw={"placeholder": "you@example.com"},
    )
    password = PasswordField(
        _l("Password"),
        validators=[DataRequired(), Length(min=MIN_PASSWORD, message=f"Password must be at least {MIN_PASSWORD} characters.")],
        render_kw={"placeholder": f"At least {MIN_PASSWORD} characters"},
    )
    confirm_password = PasswordField(
        _l("Confirm Password"),
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
        render_kw={"placeholder": "Re-enter your password"},
    )
    age = IntegerField(
        _l("Age"),
        validators=[InputRequired(), NumberRange(min=0, max=150)],
        render_kw={"placeholder": "25"},
    )
    gender = SelectField(
        _l("Gender"),
        choices=[("", "Select Gender"), ("Male", "Male"), ("Female", "Female"), ("Other", "Other")],
        validators=[DataRequired()],
    )
    phone = StringField(
        _l("Phone Number"),
        validators=[DataRequired(), Length(min=7, max=20)],
        render_kw={"placeholder": "+91-9876543210"},
    )
    blood_group = SelectField(
        _l("Blood Group"),
        choices=[
            ("", "Select Blood Group"),
            ("A+", "A+"), ("A-", "A-"),
            ("B+", "B+"), ("B-", "B-"),
            ("AB+", "AB+"), ("AB-", "AB-"),
            ("O+", "O+"), ("O-", "O-"),
        ],
        validators=[Optional()],
    )
    address = TextAreaField(
        _l("Address"),
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "Your address", "rows": 3},
    )
    submit = SubmitField(_l("Register"))

    def validate_email(self, field):
        """Ensure email is not already registered."""
        if User.query.filter_by(email=field.data.lower().strip()).first():
            raise ValidationError("This email is already registered.")


# ═══════════════════════════════════════════════════════════════════════════
# Profile Edit Form (Patient)
# ═══════════════════════════════════════════════════════════════════════════

class PatientProfileForm(BaseForm):
    """Edit patient profile (not email/password)."""

    name = StringField("Full Name", validators=[DataRequired(), Length(min=2, max=120)])
    age = IntegerField("Age", validators=[InputRequired(), NumberRange(min=0, max=150)])
    gender = SelectField(
        "Gender",
        choices=[("Male", "Male"), ("Female", "Female"), ("Other", "Other")],
        validators=[DataRequired()],
    )
    phone = StringField("Phone Number", validators=[DataRequired(), Length(min=7, max=20)])
    blood_group = SelectField(
        "Blood Group",
        choices=[
            ("", "Select"), ("A+", "A+"), ("A-", "A-"),
            ("B+", "B+"), ("B-", "B-"),
            ("AB+", "AB+"), ("AB-", "AB-"),
            ("O+", "O+"), ("O-", "O-"),
        ],
        validators=[Optional()],
    )
    address = TextAreaField("Address", validators=[Optional(), Length(max=500)])
    allergies = TextAreaField(
        "Allergies", validators=[Optional(), Length(max=500)],
        render_kw={"rows": 2, "placeholder": "e.g. Penicillin, peanuts. Write 'None' if you have none."},
    )
    chronic_conditions = TextAreaField(
        "Chronic conditions", validators=[Optional(), Length(max=500)],
        render_kw={"rows": 2, "placeholder": "e.g. Type 2 diabetes, asthma"},
    )
    submit = SubmitField("Save Changes")


# ═══════════════════════════════════════════════════════════════════════════
# Admin — Doctor Management Forms
# ═══════════════════════════════════════════════════════════════════════════

class DoctorForm(BaseForm):
    """Add / edit a doctor (admin only). Pass user_id when editing."""

    name = StringField("Full Name", validators=[DataRequired(), Length(min=2, max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField(
        "Password",
        validators=[Optional(), Length(min=MIN_PASSWORD)],
        render_kw={"placeholder": "Leave blank to keep current"},
    )
    specialization = StringField(
        "Specialization", validators=[DataRequired(), Length(max=100)]
    )
    qualification = StringField(
        "Qualification", validators=[DataRequired(), Length(max=200)]
    )
    experience_years = IntegerField(
        "Years of Experience", validators=[NumberRange(min=0, max=60)]
    )
    consultation_fee = FloatField(
        "Consultation Fee (₹)", validators=[NumberRange(min=0)]
    )
    department_id = SelectField(
        "Department", default=0, validate_choice=False,
        coerce=lambda v: int(v) if str(v or "").isdigit() else 0,
    )
    work_start = TimeField("Work Start", validators=[DataRequired()], format="%H:%M")
    work_end = TimeField("Work End", validators=[DataRequired()], format="%H:%M")
    slot_minutes = SelectField(
        "Slot Duration (min)",
        choices=[(15, "15"), (20, "20"), (30, "30"), (45, "45"), (60, "60")],
        coerce=int,
    )
    available_days = SelectMultipleField(
        "Available Days",
        choices=DAY_CHOICES,
        validators=[DataRequired(message="Select at least one day.")],
        widget=ListWidget(prefix_label=False),
        option_widget=CheckboxInput(),
    )
    submit = SubmitField("Save Doctor")

    def __init__(self, *args, user_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._user_id = user_id

    def validate_email(self, field):
        existing = User.query.filter_by(email=field.data.lower().strip()).first()
        if existing and existing.id != self._user_id:
            raise ValidationError("This email is already registered.")

    def validate_work_end(self, field):
        if self.work_start.data and field.data and field.data <= self.work_start.data:
            raise ValidationError("End time must be after start time.")


class ChangePasswordForm(BaseForm):
    """Change own password."""

    current_password = PasswordField(_l("Current Password"), validators=[DataRequired()])
    new_password = PasswordField(
        _l("New Password"),
        validators=[DataRequired(), Length(min=MIN_PASSWORD, message=f"Password must be at least {MIN_PASSWORD} characters.")],
    )
    confirm_password = PasswordField(
        _l("Confirm New Password"),
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )
    submit = SubmitField(_l("Update Password"))


# ═══════════════════════════════════════════════════════════════════════════
# Appointment Booking Form
# ═══════════════════════════════════════════════════════════════════════════

class AppointmentForm(BaseForm):
    """Book an appointment (patient → doctor)."""

    doctor_id = SelectField("Doctor", coerce=int, validators=[DataRequired()])
    date = DateField("Appointment Date", validators=[DataRequired()], format="%Y-%m-%d")
    time = HiddenField("Time Slot", validators=[DataRequired()])
    reason = TextAreaField(
        "Reason for Visit",
        validators=[DataRequired(), Length(max=500)],
        render_kw={"placeholder": "Describe your symptoms or reason", "rows": 3},
    )
    submit = SubmitField("Book Appointment")


class RescheduleForm(BaseForm):
    """Pick a new date and slot for an existing appointment."""

    date = DateField("New Date", validators=[DataRequired()], format="%Y-%m-%d")
    time = HiddenField("Time Slot", validators=[DataRequired(message="Please choose a time slot.")])
    submit = SubmitField("Reschedule")


class TimeOffForm(BaseForm):
    """Block out a day or a time range (doctor)."""

    date = DateField("Date", validators=[DataRequired()], format="%Y-%m-%d")
    all_day = BooleanField("All day", default=True)
    start_time = TimeField("From", validators=[Optional()], format="%H:%M")
    end_time = TimeField("To", validators=[Optional()], format="%H:%M")
    reason = StringField("Reason", validators=[Optional(), Length(max=200)],
                         render_kw={"placeholder": "Holiday, conference… (optional)"})
    submit = SubmitField("Block time")

    def validate_date(self, field):
        if field.data and field.data < clock.today():
            raise ValidationError("Choose today or a future date.")

    def validate(self, extra_validators=None):
        # Done here, not in an inline validator: Optional() on the empty time fields
        # stops the chain before any inline validator would run.
        ok = super().validate(extra_validators)
        if self.all_day.data:
            return ok
        if not (self.start_time.data and self.end_time.data):
            self.end_time.errors = list(self.end_time.errors) + ["Enter both a start and end time, or tick All day."]
            return False
        if self.end_time.data <= self.start_time.data:
            self.end_time.errors = list(self.end_time.errors) + ["End time must be after start time."]
            return False
        return ok


class TwoFactorForm(BaseForm):
    """6-digit authenticator code, or a recovery code, at login or during setup."""

    code = StringField(
        _l("Authentication code"), validators=[DataRequired(), Length(max=20)],
        render_kw={"placeholder": "123456", "autocomplete": "one-time-code", "inputmode": "numeric"},
    )
    submit = SubmitField(_l("Verify"))


class DisableTwoFactorForm(BaseForm):
    password = PasswordField("Current password", validators=[DataRequired()])
    code = StringField("Authentication code", validators=[DataRequired(), Length(max=20)],
                       render_kw={"autocomplete": "one-time-code", "inputmode": "numeric"})
    submit = SubmitField("Turn off two-factor")


class ForgotPasswordForm(BaseForm):
    email = StringField(_l("Email"), validators=[DataRequired(), Email()],
                        render_kw={"placeholder": "you@example.com", "autocomplete": "email"})
    submit = SubmitField(_l("Send reset link"))


class ResetPasswordForm(BaseForm):
    new_password = PasswordField(
        _l("New Password"),
        validators=[DataRequired(), Length(min=MIN_PASSWORD, message=f"Password must be at least {MIN_PASSWORD} characters.")],
    )
    confirm_password = PasswordField(
        _l("Confirm New Password"),
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )
    submit = SubmitField(_l("Set new password"))


# ═══════════════════════════════════════════════════════════════════════════
# Medical Record Form (Doctor)
# ═══════════════════════════════════════════════════════════════════════════

class PrescriptionItemForm(Form):
    """One medicine row. Rows left completely blank are ignored."""

    medicine = StringField("Medicine", validators=[Optional(), Length(max=150)])
    dose = StringField("Dose", validators=[Optional(), Length(max=80)])
    frequency = StringField("Frequency", validators=[Optional(), Length(max=80)])
    duration = StringField("Duration", validators=[Optional(), Length(max=80)])
    instructions = StringField("Instructions", validators=[Optional(), Length(max=200)])

    def is_blank(self) -> bool:
        return not any((f.data or "").strip() for f in self)


class MedicalRecordForm(BaseForm):
    """Record treatment notes for a completed appointment."""

    MAX_ITEMS = 20

    diagnosis = TextAreaField(
        "Diagnosis", validators=[DataRequired(), Length(max=1000)],
        render_kw={"rows": 3},
    )
    treatment = TextAreaField(
        "Treatment", validators=[DataRequired(), Length(max=1000)],
        render_kw={"rows": 3},
    )
    rx = FieldList(FormField(PrescriptionItemForm), max_entries=MAX_ITEMS)
    prescription = TextAreaField(
        "Other prescription notes", validators=[Optional(), Length(max=1000)],
        render_kw={"rows": 2},
    )
    notes = TextAreaField(
        "Additional Notes", validators=[Optional(), Length(max=1000)],
        render_kw={"rows": 2},
    )
    follow_up_date = DateField(
        "Follow-up Date", validators=[Optional()], format="%Y-%m-%d"
    )
    submit = SubmitField("Save Record")

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        for entry in self.rx:
            row = entry.form
            if not row.is_blank() and not (row.medicine.data or "").strip():
                row.medicine.errors = list(row.medicine.errors) + ["Enter the medicine name."]
                ok = False
        return ok

    def filled_items(self) -> list[dict]:
        """Non-blank prescription rows, cleaned, in order."""
        return [
            {k: (f.data or "").strip() or None for k, f in ((n, getattr(e.form, n)) for n in
             ("medicine", "dose", "frequency", "duration", "instructions"))}
            for e in self.rx if not e.form.is_blank()
        ]


class UploadForm(BaseForm):
    """Attach a lab report / scan to a visit. Type and size are re-checked server-side."""

    file = FileField("File", validators=[FileRequired(message="Choose a file to upload.")])
    category = SelectField(
        "Type",
        choices=[("lab_report", "Lab report"), ("scan", "Scan / imaging"), ("other", "Other")],
    )
    description = StringField(
        "Description", validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Optional, e.g. Blood test, 12 Oct"},
    )
    submit = SubmitField("Upload")


class VitalsForm(BaseForm):
    """Log blood pressure, weight and/or blood sugar."""

    recorded_on = DateField("Date", validators=[DataRequired()], format="%Y-%m-%d")
    systolic = IntegerField("Systolic (top)", validators=[Optional(), NumberRange(min=50, max=260)],
                            render_kw={"placeholder": "120", "inputmode": "numeric"})
    diastolic = IntegerField("Diastolic (bottom)", validators=[Optional(), NumberRange(min=30, max=160)],
                             render_kw={"placeholder": "80", "inputmode": "numeric"})
    weight_kg = FloatField("Weight (kg)", validators=[Optional(), NumberRange(min=2, max=500)],
                           render_kw={"placeholder": "70.5", "inputmode": "decimal"})
    sugar_mgdl = FloatField("Blood sugar (mg/dL)", validators=[Optional(), NumberRange(min=20, max=800)],
                            render_kw={"placeholder": "95", "inputmode": "decimal"})
    notes = StringField("Notes", validators=[Optional(), Length(max=200)],
                        render_kw={"placeholder": "Optional, e.g. fasting"})
    submit = SubmitField("Save reading")

    def validate_recorded_on(self, field):
        if field.data and field.data > clock.today():
            raise ValidationError("The date can't be in the future.")

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if not ok:
            return False
        sys_, dia = self.systolic.data, self.diastolic.data
        if (sys_ is None) != (dia is None):
            self.diastolic.errors = list(self.diastolic.errors) + ["Enter both blood pressure numbers."]
            return False
        if sys_ is not None and sys_ <= dia:
            self.systolic.errors = list(self.systolic.errors) + ["Systolic must be higher than diastolic."]
            return False
        if sys_ is None and self.weight_kg.data is None and self.sugar_mgdl.data is None:
            self.notes.errors = list(self.notes.errors) + ["Enter at least one reading."]
            return False
        return True


# ═══════════════════════════════════════════════════════════════════════════
# Search Form
# ═══════════════════════════════════════════════════════════════════════════

class SearchForm(BaseForm):
    """Global search form used in admin panel."""

    query = StringField(
        "Search",
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "Search doctors, patients, appointments…"},
    )
    category = SelectField(
        "Category",
        choices=[("all", "All"), ("doctors", "Doctors"), ("patients", "Patients"), ("appointments", "Appointments")],
        validators=[Optional()],
    )
    submit = SubmitField("Search")



# ═══════════════════════════════════════════════════════════════════════════
# Front desk, billing, departments, ratings, family
# ═══════════════════════════════════════════════════════════════════════════

class StaffForm(BaseForm):
    """Add a reception account (admin only)."""

    name = StringField("Full Name", validators=[DataRequired(), Length(min=2, max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=MIN_PASSWORD)])
    submit = SubmitField("Add staff member")

    def validate_email(self, field):
        if User.query.filter_by(email=field.data.lower().strip()).first():
            raise ValidationError("This email is already registered.")


class DepartmentForm(BaseForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=80)])
    description = StringField("Description", validators=[Optional(), Length(max=300)])
    submit = SubmitField("Save department")


GENDER_CHOICES = [("", "Select gender"), ("Male", "Male"), ("Female", "Female"), ("Other", "Other")]
BLOOD_CHOICES = [("", "Blood group (optional)"), ("A+", "A+"), ("A-", "A-"), ("B+", "B+"), ("B-", "B-"),
                 ("AB+", "AB+"), ("AB-", "AB-"), ("O+", "O+"), ("O-", "O-")]


class WalkInForm(BaseForm):
    """Reception: add a same-day patient to a doctor's queue. patient_id 0 = register a new patient."""

    patient_id = SelectField("Patient", coerce=int)
    new_name = StringField("Full name", validators=[Optional(), Length(max=120)])
    new_age = IntegerField("Age", validators=[Optional(), NumberRange(min=0, max=150)])
    new_gender = SelectField("Gender", choices=GENDER_CHOICES, validators=[Optional()])
    new_phone = StringField("Phone", validators=[Optional(), Length(max=20)])
    doctor_id = SelectField("Doctor", coerce=int, validators=[DataRequired()])
    reason = StringField("Reason", validators=[Optional(), Length(max=200)])
    submit = SubmitField("Add to queue")

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if self.patient_id.data == 0:
            for f, msg in ((self.new_name, "Enter the patient's name."),
                           (self.new_gender, "Choose a gender.")):
                if not (f.data or "").strip():
                    f.errors = list(f.errors) + [msg]
                    ok = False
            if self.new_age.data is None:
                self.new_age.errors = list(self.new_age.errors) + ["Enter the age."]
                ok = False
        return ok


class ReceptionBookForm(AppointmentForm):
    """Reception books for any patient."""

    patient_id = SelectField("Patient", coerce=int, validators=[DataRequired()])


class PaymentForm(BaseForm):
    method = SelectField(
        "Payment method",
        choices=[("cash", "Cash"), ("card", "Card"), ("upi", "UPI"), ("other", "Other")],
    )
    discount = FloatField("Discount (₹)", validators=[Optional(), NumberRange(min=0)], default=0)
    notes = StringField("Notes", validators=[Optional(), Length(max=200)])
    submit = SubmitField("Record payment")


class RatingForm(BaseForm):
    stars = RadioField("Your rating", coerce=int, choices=[(5, "5"), (4, "4"), (3, "3"), (2, "2"), (1, "1")],
                       validators=[DataRequired(message="Choose 1 to 5 stars.")])
    comment = TextAreaField("Comment", validators=[Optional(), Length(max=500)],
                            render_kw={"rows": 3, "placeholder": "How was your visit? (optional)"})
    submit = SubmitField("Submit review")


class FamilyMemberForm(BaseForm):
    name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=120)])
    relationship = SelectField(
        "Relationship",
        choices=[("Child", "Child"), ("Spouse", "Spouse"), ("Parent", "Parent"),
                 ("Sibling", "Sibling"), ("Other", "Other")],
    )
    age = IntegerField("Age", validators=[InputRequired(), NumberRange(min=0, max=150)])
    gender = SelectField("Gender", choices=GENDER_CHOICES, validators=[DataRequired()])
    blood_group = SelectField("Blood group", choices=BLOOD_CHOICES, validators=[Optional()])
    phone = StringField("Phone", validators=[Optional(), Length(max=20)])
    submit = SubmitField("Save")
