"""QR code generation for PayBySquare (Slovak bank payment standard)."""

from __future__ import annotations

import io
import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def generate_pay_by_square_qr(
    amount: float,
    iban: str,
    swift: str = "",
    variable_symbol: str = "",
    beneficiary_name: str = "",
    currency: str = "EUR",
    note: str = "",
) -> Optional[bytes]:
    """Generate a PayBySquare QR code image as PNG bytes.

    Returns PNG bytes or None if dependencies are missing.
    """
    if not iban:
        return None

    try:
        from pay_by_square import generate as pbs_generate
    except ImportError:
        logger.warning("pay-by-square not installed, trying manual generation")
        return _generate_simple_qr(
            amount=amount, iban=iban, swift=swift,
            variable_symbol=variable_symbol,
            beneficiary_name=beneficiary_name,
            currency=currency, note=note,
        )

    try:
        import qrcode

        # Generate PayBySquare encoded string
        pbs_data = pbs_generate(
            amount=amount,
            iban=iban.replace(" ", ""),
            swift=swift.replace(" ", "") if swift else "",
            variable_symbol=variable_symbol,
            beneficiary_name=beneficiary_name,
            currency=currency,
            note=note or "",
        )

        # Generate QR code image
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(pbs_data)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.error("PayBySquare QR generation failed: %s", e)
        return _generate_simple_qr(
            amount=amount, iban=iban, swift=swift,
            variable_symbol=variable_symbol,
            beneficiary_name=beneficiary_name,
            currency=currency, note=note,
        )


def _generate_simple_qr(
    amount: float,
    iban: str,
    swift: str = "",
    variable_symbol: str = "",
    beneficiary_name: str = "",
    currency: str = "EUR",
    note: str = "",
) -> Optional[bytes]:
    """Fallback: generate a simple QR with payment info (not PayBySquare encoded)."""
    try:
        import qrcode
    except ImportError:
        logger.warning("qrcode library not installed")
        return None

    # EPC QR Code format (used by some EU banks)
    lines = [
        "BCD",
        "002",
        "1",
        "SCT",
        swift.replace(" ", "") if swift else "",
        beneficiary_name or "",
        iban.replace(" ", ""),
        f"{currency}{amount:.2f}",
        "",
        variable_symbol or "",
        note or "",
    ]
    data = "\n".join(lines)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def qr_to_base64(png_bytes: Optional[bytes]) -> Optional[str]:
    """Convert QR PNG bytes to a data:image/png;base64 string for HTML embedding."""
    if not png_bytes:
        return None
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def generate_invoice_qr(invoice, tenant) -> Optional[str]:
    """Generate a PayBySquare QR for an invoice, returning base64 data URI or None."""
    from models import AppSetting

    # Get bank details from tenant settings
    iban_row = AppSetting.query.filter_by(tenant_id=tenant.id, key="invoice_bank_iban").first()
    swift_row = AppSetting.query.filter_by(tenant_id=tenant.id, key="invoice_bank_swift").first()

    iban = iban_row.value if iban_row and iban_row.value else ""
    swift = swift_row.value if swift_row and swift_row.value else ""

    if not iban:
        return None

    amount = float(invoice.total_with_vat or 0)
    if amount <= 0:
        return None

    vs = invoice.variable_symbol or ""
    if not vs and invoice.invoice_number:
        vs = "".join(c for c in invoice.invoice_number if c.isdigit())[-10:]

    png_bytes = generate_pay_by_square_qr(
        amount=amount,
        iban=iban,
        swift=swift,
        variable_symbol=vs,
        beneficiary_name=tenant.name or "",
        note=f"Faktura {invoice.invoice_number or invoice.id}",
    )

    return qr_to_base64(png_bytes)
