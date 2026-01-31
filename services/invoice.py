"""Invoice business logic."""

from __future__ import annotations

import datetime
import logging

from extensions import db
from models import (
    DeliveryNote,
    DeliveryNoteOrder,
    Invoice,
    InvoiceItem,
    Order,
    Partner,
)
from services.numbering import generate_number

logger = logging.getLogger(__name__)


def _fallback_invoice_number() -> str:
    """Generate the next invoice number in format ``FV-YYYY-NNNN``."""
    year = datetime.datetime.now().year
    prefix = f"FV-{year}-"
    last = (
        Invoice.query.filter(Invoice.invoice_number.like(f"{prefix}%"))
        .order_by(Invoice.invoice_number.desc())
        .first()
    )
    if last and last.invoice_number:
        try:
            seq = int(last.invoice_number.split("-")[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


def generate_invoice_number(partner_id: int | None = None) -> str:
    """Generate the next invoice number using config or fallback."""
    num = generate_number("invoice", partner_id=partner_id)
    if num:
        return num
    return _fallback_invoice_number()


def build_invoice_for_partner(partner_id: int) -> Invoice:
    """Create a collective invoice for *partner_id* from unbilled delivery notes.

    Raises ``ValueError`` if no unbilled delivery notes exist.
    """
    partner = db.session.get(Partner, partner_id)
    if not partner:
        raise ValueError("Partner neexistuje.")

    query = (
        DeliveryNote.query.join(
            DeliveryNoteOrder,
            DeliveryNote.id == DeliveryNoteOrder.delivery_note_id,
        )
        .join(Order, DeliveryNoteOrder.order_id == Order.id)
        .join(Partner, Order.partner_id == Partner.id)
    )
    if partner.group_code:
        query = query.filter(Partner.group_code == partner.group_code)
    else:
        query = query.filter(Order.partner_id == partner_id)

    unbilled_notes = query.filter(DeliveryNote.invoiced.is_(False)).all()
    if not unbilled_notes:
        raise ValueError(
            "Žiadne nevyfakturované dodacie listy pre tohto partnera."
        )

    invoice_number = generate_invoice_number(partner_id=partner_id)
    invoice = Invoice(
        partner_id=partner_id,
        invoice_number=invoice_number,
        status="draft",
    )
    db.session.add(invoice)

    total = 0.0
    total_with_vat = 0.0

    for note in unbilled_notes:
        for item in note.items:
            line_total = item.line_total or (item.unit_price * item.quantity)
            item_name = (
                item.product.name
                if item.product
                else item.bundle.name if item.bundle else "Položka"
            )
            description = (
                f"Dodací list {note.id}: {item_name} ({item.quantity}x)"
            )

            vat_rate = 20.0
            if (
                item.product
                and hasattr(item.product, "vat_rate")
                and item.product.vat_rate is not None
            ):
                vat_rate = item.product.vat_rate

            vat_amount = round(line_total * vat_rate / 100, 2)
            line_total_with_vat = round(line_total + vat_amount, 2)

            invoice.items.append(
                InvoiceItem(
                    source_delivery_id=note.id,
                    description=description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    total=line_total,
                    vat_rate=vat_rate,
                    vat_amount=vat_amount,
                    total_with_vat=line_total_with_vat,
                )
            )
            total += line_total
            total_with_vat += line_total_with_vat
        note.invoiced = True

    invoice.total = round(total, 2)
    invoice.total_with_vat = round(total_with_vat, 2)
    db.session.commit()
    return invoice
