"""PDF generation for delivery notes and invoices."""

from __future__ import annotations

import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from config_models import AppConfig
from models import DeliveryNote, Invoice

# ---------------------------------------------------------------------------
# Font registration — DejaVu Sans for proper Slovak diacritics
# ---------------------------------------------------------------------------
_FONT_DIR = "/usr/share/fonts/truetype/dejavu"
_FONT_REGULAR = os.path.join(_FONT_DIR, "DejaVuSans.ttf")
_FONT_BOLD = os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")

if os.path.exists(_FONT_REGULAR):
    pdfmetrics.registerFont(TTFont("DejaVuSans", _FONT_REGULAR))
if os.path.exists(_FONT_BOLD):
    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", _FONT_BOLD))

PDF_FONT = "DejaVuSans" if os.path.exists(_FONT_REGULAR) else "Helvetica"
PDF_FONT_BOLD = (
    "DejaVuSans-Bold" if os.path.exists(_FONT_BOLD) else "Helvetica-Bold"
)

_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"
)


# ---------------------------------------------------------------------------
# Delivery note PDF
# ---------------------------------------------------------------------------


def generate_delivery_pdf(delivery: DeliveryNote, app_cfg: AppConfig) -> str:
    """Generate a PDF for a delivery note and return the file path."""
    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    filename = os.path.join(_OUTPUT_DIR, f"delivery_{delivery.id}.pdf")
    pdf = canvas.Canvas(filename, pagesize=A4)

    pdf.setFont(PDF_FONT_BOLD, 14)
    pdf.drawString(20 * mm, 285 * mm, f"Dodací list {delivery.id}")
    pdf.setFont(PDF_FONT, 10)

    partner_name = (
        delivery.primary_order.partner.name if delivery.primary_order else ""
    )
    pdf.drawString(20 * mm, 278 * mm, f"Partner: {partner_name}")
    pdf.drawString(20 * mm, 272 * mm, f"Dátum: {delivery.created_at.date()}")
    pdf.drawString(
        20 * mm,
        266 * mm,
        f"Ceny: {'Áno' if delivery.show_prices else 'Nie'}",
    )
    pdf.drawString(
        20 * mm,
        260 * mm,
        f"Plán: {delivery.planned_delivery_datetime or ''} | "
        f"Skutočnosť: {delivery.actual_delivery_datetime or ''}",
    )

    y = 250
    pdf.setFont(PDF_FONT_BOLD, 10)
    pdf.drawString(20 * mm, y * mm, "Položky")
    y -= 6
    pdf.setFont(PDF_FONT, 9)

    for item in delivery.items:
        name = (
            item.product.name
            if item.product
            else item.bundle.name if item.bundle else "Položka"
        )
        line = f"{name} - {item.quantity}x"
        if delivery.show_prices:
            line += f" | {item.unit_price:.2f} {app_cfg.base_currency}"
            line += f" | {item.line_total:.2f} {app_cfg.base_currency}"
        pdf.drawString(25 * mm, y * mm, line)
        y -= 5
        for component in item.components:
            comp_line = (
                f"  - {component.product.name}: {component.quantity}x"
            )
            pdf.drawString(30 * mm, y * mm, comp_line)
            y -= 4
        if y < 20:
            pdf.showPage()
            y = 280

    pdf.save()
    return filename


# ---------------------------------------------------------------------------
# Invoice PDF
# ---------------------------------------------------------------------------


def generate_invoice_pdf(invoice: Invoice, app_cfg: AppConfig) -> str:
    """Generate a PDF for an invoice and return the file path."""
    os.makedirs(_OUTPUT_DIR, exist_ok=True)

    display_number = invoice.invoice_number or str(invoice.id)
    filename = os.path.join(_OUTPUT_DIR, f"invoice_{invoice.id}.pdf")
    pdf = canvas.Canvas(filename, pagesize=A4)

    pdf.setFont(PDF_FONT_BOLD, 14)
    pdf.drawString(
        20 * mm, 285 * mm, f"Zúčtovacia faktúra {display_number}"
    )
    pdf.setFont(PDF_FONT, 10)
    pdf.drawString(20 * mm, 278 * mm, f"Partner: {invoice.partner.name}")
    pdf.drawString(20 * mm, 272 * mm, f"Dátum: {invoice.created_at.date()}")

    # Partner identification details
    partner = invoice.partner
    y = 264
    if partner.ico:
        pdf.drawString(20 * mm, y * mm, f"IČO: {partner.ico}")
        y -= 5
    if partner.dic:
        pdf.drawString(20 * mm, y * mm, f"DIČ: {partner.dic}")
        y -= 5
    if partner.ic_dph:
        pdf.drawString(20 * mm, y * mm, f"IČ DPH: {partner.ic_dph}")
        y -= 5

    y -= 3
    pdf.setFont(PDF_FONT_BOLD, 10)
    pdf.drawString(20 * mm, y * mm, "Položky")
    y -= 6
    pdf.setFont(PDF_FONT, 9)

    for item in invoice.items:
        line = (
            f"{item.description} | {item.quantity}x | "
            f"{item.unit_price:.2f} {app_cfg.base_currency}"
        )
        if item.vat_rate is not None:
            line += f" | DPH {item.vat_rate:.0f}%"
        if item.total_with_vat is not None:
            line += (
                f" | s DPH: {item.total_with_vat:.2f} {app_cfg.base_currency}"
            )
        pdf.drawString(25 * mm, y * mm, line)
        y -= 5
        if y < 20:
            pdf.showPage()
            y = 280

    y -= 3
    pdf.setFont(PDF_FONT_BOLD, 11)
    pdf.drawString(
        20 * mm,
        max(y - 5, 15) * mm,
        f"Spolu bez DPH: {invoice.total:.2f} {app_cfg.base_currency}",
    )
    if invoice.total_with_vat is not None:
        pdf.drawString(
            20 * mm,
            max(y - 12, 15) * mm,
            f"Spolu s DPH: {invoice.total_with_vat:.2f} {app_cfg.base_currency}",
        )

    pdf.save()
    return filename
