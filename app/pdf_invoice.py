"""Invoice / receipt PDF (reportlab). The same layout is a receipt once the invoice is paid."""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.pdf import GREEN, LIGHT, _p


def _money(x: float) -> str:
    return f"Rs. {x:,.2f}"      # the built-in PDF fonts have no rupee glyph


def build_invoice_pdf(inv) -> bytes:
    appt, doctor, patient = inv.appointment, inv.doctor, inv.patient
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["BodyText"], fontSize=10, leading=14)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, textColor=colors.HexColor("#5f7a63"))
    title = ParagraphStyle("title", parent=base["Title"], textColor=GREEN, fontSize=20, alignment=0, spaceAfter=0)
    big = ParagraphStyle("big", parent=body, fontSize=14, fontName="Helvetica-Bold", textColor=GREEN)

    is_paid = inv.status == "paid"
    heading = "RECEIPT" if is_paid else ("VOID INVOICE" if inv.status == "void" else "INVOICE")

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=f"{heading} {inv.number}", author="MediTrack")

    story = [Paragraph("MediTrack", title), _p("Hospital Management System", small), Spacer(1, 6 * mm),
             Paragraph(f"{heading} {inv.number}", big), Spacer(1, 3 * mm)]

    meta = [
        [_p("Patient", small), _p(patient.user.name, body), _p("Date", small), _p(appt.date.strftime("%d %b %Y"), body)],
        [_p("Doctor", small), _p(f"{doctor.user.name} ({doctor.specialization})", body),
         _p("Status", small), _p(inv.status.upper(), body)],
    ]
    if is_paid:
        meta.append([_p("Paid on", small), _p(inv.paid_at.strftime("%d %b %Y %H:%M"), body),
                     _p("Method", small), _p((inv.method or "").capitalize(), body)])
    t = Table(meta, colWidths=[24 * mm, 66 * mm, 24 * mm, 60 * mm])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LIGHT), ("INNERGRID", (0, 0), (-1, -1), 0.3, LIGHT),
                           ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EFF6FF")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [t, Spacer(1, 6 * mm)]

    head = ParagraphStyle("th", parent=body, textColor=colors.white, fontName="Helvetica-Bold")
    rows = [[_p("Description", head), _p("Amount", head)],
            [_p(f"Consultation - {doctor.user.name}", body), _p(_money(inv.amount), body)]]
    if inv.discount:
        rows.append([_p("Discount", body), _p("- " + _money(inv.discount), body)])
    rows.append([_p("Total", big), _p(_money(inv.total), big)])
    it = Table(rows, colWidths=[126 * mm, 48 * mm])
    it.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN), ("GRID", (0, 0), (-1, -1), 0.3, LIGHT),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#DBEAFE")),
    ]))
    story.append(it)

    if inv.notes:
        story += [Spacer(1, 4 * mm), _p("Notes: " + inv.notes, small)]
    story += [Spacer(1, 12 * mm),
              _p("Thank you. This is a computer-generated document.", small)]
    doc.build(story)
    return buf.getvalue()
