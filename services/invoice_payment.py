"""Invoice payment service — handles payment initiation and processing for invoices."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from extensions import db
from models import AppSetting, Invoice

logger = logging.getLogger(__name__)


def _get_tenant_setting(tenant_id: int, key: str, default: str = "") -> str:
    """Return a per-tenant AppSetting value, falling back to *default*.

    Args:
        tenant_id: The tenant whose settings are queried.
        key: The setting key to look up.
        default: Value returned when the setting is absent or empty.

    Returns:
        The stored setting value, or *default* if not found.
    """
    row = AppSetting.query.filter_by(tenant_id=tenant_id, key=key).first()
    return row.value if row and row.value else default


def get_invoice_payment_config(tenant_id: int) -> dict:
    """Return the invoice payment configuration for a tenant.

    Args:
        tenant_id: The tenant whose payment configuration is retrieved.

    Returns:
        A dict containing ``gateway``, ``iban``, ``swift``, and ``bank_name``
        keys populated from per-tenant AppSettings.
    """
    return {
        "gateway": _get_tenant_setting(tenant_id, "invoice_payment_gateway", "bank_transfer"),
        "iban": _get_tenant_setting(tenant_id, "invoice_bank_iban"),
        "swift": _get_tenant_setting(tenant_id, "invoice_bank_swift"),
        "bank_name": _get_tenant_setting(tenant_id, "invoice_bank_name"),
    }


def generate_variable_symbol(invoice: Invoice) -> str:
    """Generate a variable symbol from the invoice number.

    Extracts up to ten trailing digits from the invoice number; falls back to
    the invoice primary key when no digits are available.

    Args:
        invoice: The invoice for which the variable symbol is generated.

    Returns:
        A numeric string suitable for use as a bank variable symbol.
    """
    if invoice.variable_symbol:
        return invoice.variable_symbol
    # Extract digits from invoice number, or fall back to invoice ID.
    num = invoice.invoice_number or str(invoice.id)
    digits = "".join(c for c in num if c.isdigit())
    return digits[-10:] if digits else str(invoice.id)


def initiate_payment(invoice: Invoice) -> Optional[str]:
    """Initiate a payment for an invoice.

    Selects the appropriate gateway based on per-tenant configuration, ensures
    a variable symbol exists on the invoice, and delegates to the gateway-
    specific helper.

    Args:
        invoice: The invoice to be paid.

    Returns:
        A redirect URL for online gateways, or ``None`` for bank transfer and
        on error.
    """
    config = get_invoice_payment_config(invoice.tenant_id)
    gateway = config["gateway"]

    if not invoice.variable_symbol:
        invoice.variable_symbol = generate_variable_symbol(invoice)
        db.session.flush()

    if gateway == "bank_transfer":
        # No redirect needed for bank transfer — caller shows payment details.
        invoice.payment_status = "pending"
        invoice.payment_method = "bank_transfer"
        db.session.commit()
        return None

    if gateway == "gopay":
        return _initiate_gopay_payment(invoice, config)

    if gateway == "stripe":
        return _initiate_stripe_payment(invoice, config)

    logger.warning("Unknown invoice payment gateway '%s' for tenant %s", gateway, invoice.tenant_id)
    return None


def _initiate_gopay_payment(invoice: Invoice, config: dict) -> Optional[str]:
    """Create a GoPay payment for an invoice.

    Uses the gopay SDK v2.x API (``response.success`` / ``response.json``)
    consistent with the rest of the codebase.

    Args:
        invoice: The invoice to be paid via GoPay.
        config: Tenant payment configuration (currently unused for GoPay;
            credentials come from ``GOPAY_CONFIG``).

    Returns:
        The GoPay gateway redirect URL, or ``None`` on configuration error or
        API failure.
    """
    try:
        from flask import current_app, url_for

        gopay_cfg = current_app.config.get("GOPAY_CONFIG")
        if not gopay_cfg or not gopay_cfg.enabled:
            logger.warning("GoPay not configured for invoice payment")
            return None

        try:
            import gopay
            from gopay.enums import Currency, Language, PaymentInstrument
        except ImportError:
            logger.warning("gopay package not installed — GoPay invoice payment disabled")
            return None

        if not gopay_cfg.goid or not gopay_cfg.client_id or not gopay_cfg.client_secret:
            logger.warning("GoPay credentials incomplete — cannot initiate invoice payment")
            return None

        payments = gopay.payments(
            {
                "goid": int(gopay_cfg.goid),
                "client_id": gopay_cfg.client_id,
                "client_secret": gopay_cfg.client_secret,
                "gateway_url": gopay_cfg.gateway_url,
                "language": Language.SLOVAK,
            }
        )

        amount_cents = int(float(invoice.total_with_vat or 0) * 100)
        order_number = invoice.variable_symbol or str(invoice.id)
        description = f"Faktura {invoice.invoice_number or invoice.id}"

        response = payments.create_payment(
            {
                "payer": {
                    "default_payment_instrument": PaymentInstrument.BANK_ACCOUNT,
                    "allowed_payment_instruments": [
                        PaymentInstrument.PAYMENT_CARD,
                        PaymentInstrument.BANK_ACCOUNT,
                        PaymentInstrument.APPLE_PAY,
                        PaymentInstrument.GPAY,
                    ],
                },
                "amount": amount_cents,
                "currency": Currency.EUROS,
                "order_number": order_number,
                "order_description": description,
                "items": [
                    {
                        "type": "ITEM",
                        "name": description,
                        "amount": amount_cents,
                        "count": 1,
                    }
                ],
                "callback": {
                    "return_url": url_for(
                        "invoices.payment_return",
                        _external=True,
                    ) + f"?invoice_id={invoice.id}",
                    "notification_url": url_for(
                        "invoices.payment_notify",
                        gateway="gopay",
                        _external=True,
                    ),
                },
                "lang": Language.SLOVAK,
            }
        )

        if response.success:
            gw_url = response.json.get("gw_url", "")
            payment_id = response.json.get("id")
            invoice.gateway_payment_id = str(payment_id) if payment_id else ""
            invoice.payment_method = "gopay"
            invoice.payment_status = "pending"
            db.session.commit()
            logger.info(
                "Created GoPay payment %s for invoice %s (amount=%s cents)",
                payment_id,
                invoice.id,
                amount_cents,
            )
            return gw_url or None

        logger.error(
            "GoPay payment creation failed for invoice %s: %s",
            invoice.id,
            response.json,
        )
    except Exception as e:
        logger.error("GoPay invoice payment error for invoice %s: %s", invoice.id, e)

    return None


def _initiate_stripe_payment(invoice: Invoice, config: dict) -> Optional[str]:
    """Create a Stripe Checkout Session for an invoice.

    Args:
        invoice: The invoice to be paid via Stripe.
        config: Tenant payment configuration (currently unused for Stripe;
            credentials come from ``STRIPE_SECRET_KEY``).

    Returns:
        The Stripe Checkout Session URL, or ``None`` on configuration error or
        API failure.
    """
    try:
        import stripe
        from flask import current_app, url_for

        stripe.api_key = current_app.config.get("STRIPE_SECRET_KEY", "")
        if not stripe.api_key:
            logger.warning("Stripe not configured for invoice payment")
            return None

        amount_cents = int(float(invoice.total_with_vat or 0) * 100)
        product_name = f"Faktura {invoice.invoice_number or invoice.id}"

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "eur",
                        "unit_amount": amount_cents,
                        "product_data": {"name": product_name},
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            success_url=(
                url_for("invoices.payment_return", _external=True)
                + f"?invoice_id={invoice.id}&session_id={{CHECKOUT_SESSION_ID}}"
            ),
            cancel_url=url_for("invoices.list_invoices", _external=True),
            metadata={"invoice_id": str(invoice.id)},
        )

        invoice.gateway_payment_id = session.id
        invoice.payment_method = "stripe"
        invoice.payment_status = "pending"
        db.session.commit()
        logger.info(
            "Created Stripe session %s for invoice %s (amount=%s cents)",
            session.id,
            invoice.id,
            amount_cents,
        )
        return session.url

    except ImportError:
        logger.warning("stripe package not installed — Stripe invoice payment disabled")
    except Exception as e:
        logger.error("Stripe invoice payment error for invoice %s: %s", invoice.id, e)

    return None


def record_invoice_payment(
    invoice: Invoice,
    amount: Optional[Decimal] = None,
    method: Optional[str] = None,
) -> None:
    """Record a manual or webhook-confirmed payment against an invoice.

    Marks the invoice as ``paid`` and persists the payment timestamp, amount,
    and method. The invoice ``status`` field is also set to ``"paid"`` so
    downstream workflows (e.g. PDF generation, reporting) reflect the settled
    state.

    Args:
        invoice: The invoice being marked as paid.
        amount: The amount received. Defaults to ``invoice.total_with_vat``
            when omitted.
        method: The payment method label (e.g. ``"bank_transfer"``,
            ``"gopay"``, ``"stripe"``). Leaves the existing value unchanged
            when omitted.
    """
    invoice.payment_status = "paid"
    invoice.paid_at = datetime.now(timezone.utc)
    invoice.paid_amount = amount if amount is not None else invoice.total_with_vat
    if method:
        invoice.payment_method = method
    invoice.status = "paid"
    db.session.commit()
    logger.info("Recorded payment for invoice %s (method=%s)", invoice.id, invoice.payment_method)
