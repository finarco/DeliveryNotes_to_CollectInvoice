"""GoPay payment gateway integration service."""

from __future__ import annotations

import logging
from typing import Optional

from flask import current_app

logger = logging.getLogger(__name__)


def _get_gopay_client():
    """Create and return a configured GoPay payments client.

    Returns None if gopay is not installed or not enabled.
    """
    cfg = current_app.config.get("GOPAY_CONFIG")
    if not cfg or not cfg.enabled:
        return None
    try:
        import gopay
    except ImportError:
        logger.warning("gopay package not installed — GoPay features disabled")
        return None
    if not cfg.goid or not cfg.client_id or not cfg.client_secret:
        logger.warning("GoPay credentials not configured")
        return None

    is_production = "gate.gopay.cz" in cfg.gateway_url
    return gopay.payments(
        {
            "goid": cfg.goid,
            "clientId": cfg.client_id,
            "clientSecret": cfg.client_secret,
            "isProductionMode": is_production,
        }
    )


def _get_embed_js_url() -> str:
    """Return the GoPay embed.js URL based on gateway configuration."""
    cfg = current_app.config.get("GOPAY_CONFIG")
    if cfg and "gate.gopay.cz" in cfg.gateway_url:
        return "https://gate.gopay.cz/gp-gw/js/embed.js"
    return "https://gw.sandbox.gopay.com/gp-gw/js/embed.js"


def create_gopay_payment(
    tenant,
    plan,
    billing_cycle: str,
    return_url: str,
    notify_url: str,
) -> tuple[Optional[int], Optional[str]]:
    """Create a GoPay payment for a subscription.

    Returns (gopay_payment_id, gw_url) on success, or (None, None) on failure.
    """
    client = _get_gopay_client()
    if not client:
        return None, None

    if billing_cycle == "yearly":
        amount = int(plan.price_yearly * 100)
        description = f"{plan.name} — ročné predplatné"
    else:
        amount = int(plan.price_monthly * 100)
        description = f"{plan.name} — mesačné predplatné"

    try:
        import gopay

        response = client.create_payment(
            {
                "payer": {
                    "default_payment_instrument": gopay.enums.PaymentInstrument.PAYMENT_CARD,
                    "allowed_payment_instruments": [
                        gopay.enums.PaymentInstrument.PAYMENT_CARD,
                        gopay.enums.PaymentInstrument.BANK_ACCOUNT,
                        gopay.enums.PaymentInstrument.APPLE_PAY,
                        gopay.enums.PaymentInstrument.GPAY,
                        gopay.enums.PaymentInstrument.PAYPAL,
                    ],
                    "allowed_swifts": [
                        "TATRSKBX",
                        "SUBASKBX",
                        "UNCRSKBX",
                        "GIBASKBX",
                    ],
                    "contact": {
                        "email": tenant.billing_email or tenant.email or "",
                    },
                },
                "amount": amount,
                "currency": gopay.enums.Currency.EUR,
                "order_number": f"T{tenant.id}-{plan.slug}-{billing_cycle}",
                "order_description": description,
                "items": [
                    {
                        "type": "ITEM",
                        "name": description,
                        "amount": amount,
                        "count": 1,
                    }
                ],
                "callback": {
                    "return_url": return_url,
                    "notification_url": notify_url,
                },
                "lang": gopay.enums.Language.SLOVAK,
            }
        )

        if response.has_succeed():
            gw_url = response.json.get("gw_url", "")
            payment_id = response.json.get("id")
            logger.info(
                "Created GoPay payment %s for tenant %s (amount=%s)",
                payment_id,
                tenant.id,
                amount,
            )
            return payment_id, gw_url
        else:
            logger.error(
                "GoPay create_payment failed for tenant %s: %s",
                tenant.id,
                response.json,
            )
            return None, None

    except Exception as e:
        logger.error("GoPay create_payment error for tenant %s: %s", tenant.id, e)
        return None, None


def get_gopay_payment_status(gopay_payment_id) -> Optional[dict]:
    """Check payment status via the GoPay API.

    Returns the full status response dict, or None on error.
    The key field is ``state`` (PAID, CANCELED, TIMEOUTED, CREATED, etc.).
    """
    client = _get_gopay_client()
    if not client:
        return None
    try:
        response = client.get_status(int(gopay_payment_id))
        if response.has_succeed():
            return response.json
        logger.error("GoPay get_status failed for %s: %s", gopay_payment_id, response.json)
        return None
    except Exception as e:
        logger.error("GoPay get_status error for %s: %s", gopay_payment_id, e)
        return None


def handle_gopay_notification(gopay_payment_id) -> bool:
    """Process a GoPay notification callback.

    1. Fetch current payment status from GoPay API
    2. If PAID: record payment + activate subscription
    3. If CANCELED/TIMEOUTED: mark payment as failed

    Returns True if processed successfully.
    """
    from extensions import db
    from models import Payment
    from services.billing import reactivate_after_payment, record_payment

    status_data = get_gopay_payment_status(gopay_payment_id)
    if not status_data:
        return False

    state = status_data.get("state", "")
    payment = Payment.query.filter_by(
        gopay_payment_id=str(gopay_payment_id)
    ).first()

    if not payment:
        logger.warning("No payment record found for GoPay ID %s", gopay_payment_id)
        return False

    if state == "PAID":
        if payment.status != "completed":
            payment.status = "completed"
            from datetime import datetime, timezone

            payment.paid_at = datetime.now(timezone.utc)
            db.session.commit()
            reactivate_after_payment(payment.tenant_id)
            logger.info(
                "GoPay payment %s PAID for tenant %s",
                gopay_payment_id,
                payment.tenant_id,
            )
    elif state in ("CANCELED", "TIMEOUTED"):
        if payment.status not in ("completed", "failed"):
            payment.status = "failed"
            db.session.commit()
            logger.info(
                "GoPay payment %s %s for tenant %s",
                gopay_payment_id,
                state,
                payment.tenant_id,
            )
    else:
        logger.info(
            "GoPay payment %s state=%s for tenant %s (no action)",
            gopay_payment_id,
            state,
            payment.tenant_id,
        )

    return True
