"""Billing and subscription management service."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from extensions import db
from models import AppSetting, AuditLog, Payment, SubscriptionPlan, TenantSubscription

logger = logging.getLogger(__name__)


def _get_global_setting(key: str, default: str = "") -> str:
    """Get a global (tenant_id=NULL) AppSetting value."""
    row = AppSetting.query.filter_by(tenant_id=None, key=key).first()
    return row.value if row and row.value else default


def _get_trial_days() -> int:
    """Return the configured trial duration in days."""
    val = _get_global_setting("billing_trial_days", "30")
    try:
        return int(val)
    except (ValueError, TypeError):
        return 30


def _get_grace_period_days() -> int:
    """Return the configured grace period in days."""
    val = _get_global_setting("billing_grace_period_days", "14")
    try:
        return int(val)
    except (ValueError, TypeError):
        return 14


def get_tenant_subscription(tenant_id: int) -> Optional[TenantSubscription]:
    """Return the active subscription for a tenant, or None."""
    return TenantSubscription.query.filter_by(tenant_id=tenant_id).first()


def is_tenant_active(tenant_id: int) -> bool:
    """Check if a tenant's subscription allows normal operation."""
    sub = get_tenant_subscription(tenant_id)
    if not sub:
        return True  # No subscription = allow (grace for setup)
    return sub.status in ("trial", "active", "past_due", "grace_period")


def create_trial_subscription(tenant_id: int) -> TenantSubscription:
    """Create a trial subscription for a new tenant."""
    pro_plan = SubscriptionPlan.query.filter_by(slug="pro").first()
    if not pro_plan:
        pro_plan = SubscriptionPlan.query.first()
    trial_days = _get_trial_days()
    now = datetime.now(timezone.utc)
    sub = TenantSubscription(
        tenant_id=tenant_id,
        plan_id=pro_plan.id,
        status="trial",
        billing_cycle="monthly",
        current_period_start=now,
        current_period_end=now + timedelta(days=trial_days),
        trial_ends_at=now + timedelta(days=trial_days),
        original_trial_days=trial_days,
    )
    db.session.add(sub)
    db.session.flush()
    logger.info("Created trial subscription for tenant %s (%s days)", tenant_id, trial_days)
    return sub


def extend_trial(tenant_id: int, extra_days: int, extended_by_user_id: int) -> None:
    """Extend a tenant's trial period by additional days."""
    sub = get_tenant_subscription(tenant_id)
    if not sub:
        return
    now = datetime.now(timezone.utc)
    if sub.trial_ends_at:
        trial_end = sub.trial_ends_at
        if trial_end.tzinfo is None:
            trial_end = trial_end.replace(tzinfo=timezone.utc)
        sub.trial_ends_at = trial_end + timedelta(days=extra_days)
    else:
        sub.trial_ends_at = now + timedelta(days=extra_days)
    sub.trial_extended_by_id = extended_by_user_id
    sub.trial_extended_at = now
    # If subscription was suspended, reactivate to trial
    if sub.status == "suspended":
        sub.status = "trial"
    sub.current_period_end = sub.trial_ends_at
    # Log in audit
    db.session.add(AuditLog(
        user_id=extended_by_user_id,
        tenant_id=tenant_id,
        action="extend_trial",
        entity_type="subscription",
        entity_id=sub.id,
        details=f"Extended trial by {extra_days} days",
    ))
    db.session.commit()
    logger.info("Extended trial for tenant %s by %s days", tenant_id, extra_days)


def reset_trial(tenant_id: int, extended_by_user_id: int) -> None:
    """Reset a tenant's trial to full duration from today."""
    sub = get_tenant_subscription(tenant_id)
    if not sub:
        return
    trial_days = _get_trial_days()
    now = datetime.now(timezone.utc)
    sub.trial_ends_at = now + timedelta(days=trial_days)
    sub.trial_extended_by_id = extended_by_user_id
    sub.trial_extended_at = now
    sub.status = "trial"
    sub.current_period_start = now
    sub.current_period_end = sub.trial_ends_at
    db.session.add(AuditLog(
        user_id=extended_by_user_id,
        tenant_id=tenant_id,
        action="reset_trial",
        entity_type="subscription",
        entity_id=sub.id,
        details=f"Reset trial to {trial_days} days from today",
    ))
    db.session.commit()
    logger.info("Reset trial for tenant %s to %s days", tenant_id, trial_days)


def get_trial_days_remaining(tenant_id: int) -> Optional[int]:
    """Return days remaining in trial, or None if not in trial."""
    sub = get_tenant_subscription(tenant_id)
    if not sub or sub.status != "trial" or not sub.trial_ends_at:
        return None
    now = datetime.now(timezone.utc)
    ends = sub.trial_ends_at
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    return max(0, (ends - now).days)


def create_subscription(
    tenant_id: int,
    plan_id: int,
    billing_cycle: str = "monthly",
) -> TenantSubscription:
    """Create or update a subscription for a tenant."""
    now = datetime.now(timezone.utc)
    plan = db.session.get(SubscriptionPlan, plan_id)
    if billing_cycle == "yearly":
        period_end = now + timedelta(days=365)
    else:
        period_end = now + timedelta(days=30)

    sub = get_tenant_subscription(tenant_id)
    if sub:
        sub.plan_id = plan_id
        sub.billing_cycle = billing_cycle
        sub.status = "active"
        sub.current_period_start = now
        sub.current_period_end = period_end
        sub.trial_ends_at = None
    else:
        sub = TenantSubscription(
            tenant_id=tenant_id,
            plan_id=plan_id,
            status="active",
            billing_cycle=billing_cycle,
            current_period_start=now,
            current_period_end=period_end,
        )
        db.session.add(sub)
    db.session.commit()
    logger.info("Created/updated subscription for tenant %s (plan=%s)", tenant_id, plan.name if plan else plan_id)
    return sub


def cancel_subscription(tenant_id: int) -> None:
    """Cancel a subscription (effective at period end)."""
    sub = get_tenant_subscription(tenant_id)
    if not sub:
        return
    sub.cancelled_at = datetime.now(timezone.utc)
    db.session.commit()
    logger.info("Cancelled subscription for tenant %s", tenant_id)


def record_payment(
    tenant_id: int,
    amount,
    payment_method: str,
    *,
    bank_reference: str = "",
    notes: str = "",
    stripe_payment_intent_id: str = "",
) -> Payment:
    """Record a payment for a tenant."""
    from decimal import Decimal
    sub = get_tenant_subscription(tenant_id)
    now = datetime.now(timezone.utc)
    payment = Payment(
        tenant_id=tenant_id,
        subscription_id=sub.id if sub else None,
        amount=Decimal(str(amount)),
        payment_method=payment_method,
        status="completed",
        bank_reference=bank_reference or None,
        notes=notes or None,
        stripe_payment_intent_id=stripe_payment_intent_id or None,
        paid_at=now,
    )
    db.session.add(payment)
    db.session.commit()
    logger.info("Recorded payment of %s for tenant %s", amount, tenant_id)
    return payment


def reactivate_after_payment(tenant_id: int) -> None:
    """Reactivate a suspended/past_due subscription after payment."""
    sub = get_tenant_subscription(tenant_id)
    if not sub:
        return
    if sub.status in ("suspended", "past_due", "grace_period", "cancelled"):
        now = datetime.now(timezone.utc)
        if sub.billing_cycle == "yearly":
            period_end = now + timedelta(days=365)
        else:
            period_end = now + timedelta(days=30)
        sub.status = "active"
        sub.current_period_start = now
        sub.current_period_end = period_end
        sub.grace_period_ends_at = None
        sub.cancelled_at = None
        db.session.commit()
        logger.info("Reactivated subscription for tenant %s", tenant_id)


def check_subscription_expiry() -> None:
    """Check all subscriptions and transition expired ones.

    Should be called periodically (e.g., via a scheduled task or on request).
    """
    now = datetime.now(timezone.utc)
    grace_days = _get_grace_period_days()

    for sub in TenantSubscription.query.all():
        if sub.status == "trial" and sub.trial_ends_at:
            trial_end = sub.trial_ends_at
            if trial_end.tzinfo is None:
                trial_end = trial_end.replace(tzinfo=timezone.utc)
            if now > trial_end:
                if sub.stripe_subscription_id:
                    sub.status = "active"
                else:
                    sub.status = "suspended"
                    logger.info("Trial expired for tenant %s -> suspended", sub.tenant_id)

        elif sub.status == "active" and sub.current_period_end:
            period_end = sub.current_period_end
            if period_end.tzinfo is None:
                period_end = period_end.replace(tzinfo=timezone.utc)
            if now > period_end:
                sub.status = "past_due"
                logger.info("Subscription expired for tenant %s -> past_due", sub.tenant_id)

        elif sub.status == "past_due" and sub.current_period_end:
            period_end = sub.current_period_end
            if period_end.tzinfo is None:
                period_end = period_end.replace(tzinfo=timezone.utc)
            if now > period_end + timedelta(days=7):
                sub.status = "grace_period"
                sub.grace_period_ends_at = now + timedelta(days=grace_days)
                logger.info("Tenant %s -> grace_period", sub.tenant_id)

        elif sub.status == "grace_period" and sub.grace_period_ends_at:
            grace_end = sub.grace_period_ends_at
            if grace_end.tzinfo is None:
                grace_end = grace_end.replace(tzinfo=timezone.utc)
            if now > grace_end:
                sub.status = "suspended"
                logger.info("Grace period expired for tenant %s -> suspended", sub.tenant_id)

    db.session.commit()


def get_plan_limits(tenant_id: int) -> dict:
    """Return the current plan limits for a tenant."""
    sub = get_tenant_subscription(tenant_id)
    if not sub:
        return {"max_users": 0, "max_partners": 0, "max_invoices_per_month": 0}
    plan = sub.plan
    return {
        "max_users": plan.max_users,
        "max_partners": plan.max_partners,
        "max_invoices_per_month": plan.max_invoices_per_month,
    }


def check_limit(tenant_id: int, resource_type: str) -> bool:
    """Return True if the tenant can create more of resource_type."""
    limits = get_plan_limits(tenant_id)
    limit_val = limits.get(f"max_{resource_type}", 0)
    if limit_val == 0:
        return True  # unlimited

    from models import Partner, Invoice, UserTenant
    if resource_type == "partners":
        count = Partner.query.filter_by(tenant_id=tenant_id, is_deleted=False).count()
    elif resource_type == "users":
        count = UserTenant.query.filter_by(tenant_id=tenant_id).count()
    elif resource_type == "invoices_per_month":
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        count = Invoice.query.filter(
            Invoice.tenant_id == tenant_id,
            Invoice.created_at >= start_of_month,
        ).count()
    else:
        return True

    return count < limit_val
