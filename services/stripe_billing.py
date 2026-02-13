"""Stripe payment integration service."""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _get_stripe():
    """Lazy-import and configure stripe."""
    try:
        import stripe
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        return stripe
    except ImportError:
        logger.warning("stripe package not installed — Stripe features disabled")
        return None


def create_stripe_customer(tenant) -> Optional[str]:
    """Create a Stripe customer for a tenant. Returns customer ID."""
    stripe = _get_stripe()
    if not stripe or not stripe.api_key:
        return None
    try:
        customer = stripe.Customer.create(
            name=tenant.name,
            email=tenant.billing_email or tenant.email,
            metadata={"tenant_id": str(tenant.id)},
        )
        return customer.id
    except Exception as e:
        logger.error("Failed to create Stripe customer for tenant %s: %s", tenant.id, e)
        return None


def create_stripe_subscription(subscription, plan) -> Optional[str]:
    """Create a Stripe subscription. Returns subscription ID."""
    stripe = _get_stripe()
    if not stripe or not stripe.api_key:
        return None
    if not subscription.stripe_customer_id:
        return None
    try:
        # This is a simplified version — in production, you'd create
        # Stripe Products and Prices first, then reference them here
        stripe_sub = stripe.Subscription.create(
            customer=subscription.stripe_customer_id,
            items=[{"price_data": {
                "currency": plan.currency.lower(),
                "unit_amount": int(
                    plan.price_monthly * 100 if subscription.billing_cycle == "monthly"
                    else plan.price_yearly * 100
                ),
                "recurring": {
                    "interval": "month" if subscription.billing_cycle == "monthly" else "year",
                },
                "product_data": {"name": plan.name},
            }}],
            metadata={"tenant_id": str(subscription.tenant_id)},
        )
        return stripe_sub.id
    except Exception as e:
        logger.error("Failed to create Stripe subscription: %s", e)
        return None


def cancel_stripe_subscription(subscription) -> bool:
    """Cancel a Stripe subscription at period end."""
    stripe = _get_stripe()
    if not stripe or not stripe.api_key or not subscription.stripe_subscription_id:
        return False
    try:
        stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            cancel_at_period_end=True,
        )
        return True
    except Exception as e:
        logger.error("Failed to cancel Stripe subscription: %s", e)
        return False


def handle_webhook(payload: str, sig_header: str) -> bool:
    """Process a Stripe webhook event. Returns True on success."""
    stripe = _get_stripe()
    if not stripe or not stripe.api_key:
        return False
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        logger.warning("STRIPE_WEBHOOK_SECRET not configured")
        return False

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        logger.error("Stripe webhook verification failed: %s", e)
        return False

    from extensions import db
    from models import TenantSubscription

    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if event_type == "invoice.paid":
        customer_id = data.get("customer")
        sub = TenantSubscription.query.filter_by(stripe_customer_id=customer_id).first()
        if sub:
            amount = data.get("amount_paid", 0) / 100
            from services.billing import record_payment, reactivate_after_payment
            record_payment(
                sub.tenant_id, amount, "stripe",
                stripe_payment_intent_id=data.get("payment_intent", ""),
            )
            reactivate_after_payment(sub.tenant_id)
            logger.info("Stripe invoice.paid for tenant %s", sub.tenant_id)

    elif event_type == "invoice.payment_failed":
        customer_id = data.get("customer")
        sub = TenantSubscription.query.filter_by(stripe_customer_id=customer_id).first()
        if sub and sub.status == "active":
            sub.status = "past_due"
            db.session.commit()
            logger.info("Stripe payment failed for tenant %s -> past_due", sub.tenant_id)

    elif event_type == "customer.subscription.deleted":
        stripe_sub_id = data.get("id")
        sub = TenantSubscription.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
        if sub:
            sub.status = "cancelled"
            db.session.commit()
            logger.info("Stripe subscription deleted for tenant %s", sub.tenant_id)

    return True
