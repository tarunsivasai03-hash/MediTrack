"""Printable prescription PDF (reportlab)."""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

GREEN = colors.HexColor("#1D4ED8")      # brand blue (name kept for imports)
LIGHT = colors.HexColor("#BFDBFE")


def _p(text, style) -> Paragraph:
    """Paragraph from untrusted text: escaped, newlines kept."""
    return Paragraph(escape(text or "").replace("\n", "<br/>"), style)


def build_prescription_pdf(record) -> bytes:
    appt, doctor, patient = record.appointment, record.doctor, record.patient
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["BodyText"], fontSize=10, leading=14)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, textColor=colors.HexColor("#5f7a63"))
    h = ParagraphStyle("h", parent=base["Heading3"], textColor=GREEN, spaceBefore=10, spaceAfter=3)
    title = ParagraphStyle("title", parent=base["Title"], textColor=GREEN, fontSize=20, alignment=0, spaceAfter=0)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Prescription - {patient.user.name}", author="MediTrack",
    )

    story = [Paragraph("MediTrack", title), _p("Hospital Management System", small), Spacer(1, 4 * mm)]

    info = [
        [_p("Patient", small), _p(patient.user.name, body), _p("Date", small), _p(appt.date.strftime("%d %b %Y"), body)],
        [_p("Age / Gender", small), _p(f"{patient.age if patient.age is not None else '-'} / {patient.gender or '-'}", body),
         _p("Doctor", small), _p(doctor.user.name, body)],
        [_p("Allergies", small), _p(patient.allergies or "None recorded", body),
         _p("Speciality", small), _p(f"{doctor.specialization} ({doctor.qualification})", body)],
    ]
    t = Table(info, colWidths=[28 * mm, 58 * mm, 24 * mm, 64 * mm])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, LIGHT), ("INNERGRID", (0, 0), (-1, -1), 0.3, LIGHT),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EFF6FF")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [t, Paragraph("Diagnosis", h), _p(record.diagnosis, body)]

    story.append(Paragraph("Medicines", h))
    if record.items:
        head = [_p(x, ParagraphStyle("th", parent=body, textColor=colors.white, fontName="Helvetica-Bold"))
                for x in ("#", "Medicine", "Dose", "Frequency", "Duration")]
        rows = [head]
        for i, it in enumerate(record.items, 1):
            med = escape(it.medicine)
            if it.instructions:
                med += f"<br/><font size=8 color='#5f7a63'>{escape(it.instructions)}</font>"
            rows.append([_p(str(i), body), Paragraph(med, body), _p(it.dose, body),
                         _p(it.frequency, body), _p(it.duration, body)])
        mt = Table(rows, colWidths=[8 * mm, 62 * mm, 26 * mm, 46 * mm, 32 * mm], repeatRows=1)
        mt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), GREEN), ("GRID", (0, 0), (-1, -1), 0.3, LIGHT),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EFF6FF")]),
        ]))
        story.append(mt)
    elif not record.prescription:
        story.append(_p("No medicines prescribed.", body))
    if record.prescription:
        story += [Spacer(1, 2 * mm), _p(record.prescription, body)]

    if record.treatment:
        story += [Paragraph("Treatment / advice", h), _p(record.treatment, body)]
    if record.follow_up_date:
        story += [Paragraph("Follow-up", h), _p(record.follow_up_date.strftime("%d %B %Y"), body)]

    story += [Spacer(1, 10 * mm),
              _p("This prescription was generated electronically by MediTrack and is valid without a signature "
                 "only when verified in the system.", small)]
    doc.build(story)
    return buf.getvalue()
