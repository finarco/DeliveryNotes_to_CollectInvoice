"""Billing and subscription management routes."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import csrf, db
from models import Payment, SubscriptionPlan, Tenant, TenantSubscription, UserTenant
from services.auth import get_current_user, login_required, role_required
from services.tenant import get_current_tenant, get_current_tenant_id, require_tenant
from utils import safe_int

billing_bp = Blueprint("billing", __name__)


# ---------------------------------------------------------------------------
# Tenant admin endpoints
# ---------------------------------------------------------------------------

@billing_bp.route("/billing")
@login_required
def status():
    """Current tenant's subscription status and payment history."""
    tid = require_tenant()
    sub = TenantSubscription.query.filter_by(tenant_id=tid).first()
    plan = sub.plan if sub else None
    payments = (
        Payment.query.filter_by(tenant_id=tid)
        .order_by(Payment.created_at.desc())
        .limit(20)
        .all()
    )
    plans = SubscriptionPlan.query.filter_by(is_active=True).order_by(SubscriptionPlan.sort_order).all()
    return render_template(
        "billing/status.html",
        subscription=sub,
        plan=plan,
        payments=payments,
        plans=plans,
    )


@billing_bp.route("/billing/subscribe", methods=["POST"])
@login_required
def subscribe():
    """Choose a plan (for now, just record the selection)."""
    tid = require_tenant()
    plan_id = safe_int(request.form.get("plan_id"))
    billing_cycle = request.form.get("billing_cycle", "monthly")
    plan = db.session.get(SubscriptionPlan, plan_id)
    if not plan or not plan.is_active:
        flash("Neplatný plán.", "danger")
        return redirect(url_for("billing.status"))

    from services.billing import create_subscription
    create_subscription(tid, plan_id, billing_cycle)
    flash(f"Plán '{plan.name}' bol aktivovaný.", "success")
    return redirect(url_for("billing.status"))


@billing_bp.route("/billing/cancel", methods=["POST"])
@login_required
def cancel():
    """Cancel subscription (effective at period end)."""
    tid = require_tenant()
    from services.billing import cancel_subscription
    cancel_subscription(tid)
    flash("Predplatné bude zrušené na konci fakturačného obdobia.", "warning")
    return redirect(url_for("billing.status"))


@billing_bp.route("/billing/payments")
@login_required
def payments():
    """Payment history."""
    tid = require_tenant()
    payment_list = (
        Payment.query.filter_by(tenant_id=tid)
        .order_by(Payment.created_at.desc())
        .all()
    )
    return render_template("billing/payments.html", payments=payment_list)


# ---------------------------------------------------------------------------
# Super admin endpoints
# ---------------------------------------------------------------------------

@billing_bp.route("/admin/billing/plans")
@role_required("manage_all")
def admin_plans():
    """List/manage subscription plans."""
    user = get_current_user()
    if not user.is_superadmin:
        flash("Prístup zamietnutý.", "danger")
        return redirect(url_for("dashboard.index"))
    plans = SubscriptionPlan.query.order_by(SubscriptionPlan.sort_order).all()
    return render_template("admin/billing_plans.html", plans=plans)


@billing_bp.route("/admin/billing/plans", methods=["POST"])
@role_required("manage_all")
def admin_create_plan():
    """Create a new subscription plan."""
    user = get_current_user()
    if not user.is_superadmin:
        flash("Prístup zamietnutý.", "danger")
        return redirect(url_for("dashboard.index"))
    from decimal import Decimal
    plan = SubscriptionPlan(
        name=request.form.get("name", "").strip(),
        slug=request.form.get("slug", "").strip().lower(),
        description=request.form.get("description", ""),
        price_monthly=Decimal(request.form.get("price_monthly", "0")),
        price_yearly=Decimal(request.form.get("price_yearly", "0")),
        max_users=safe_int(request.form.get("max_users")) or 0,
        max_partners=safe_int(request.form.get("max_partners")) or 0,
        max_invoices_per_month=safe_int(request.form.get("max_invoices_per_month")) or 0,
        sort_order=safe_int(request.form.get("sort_order")) or 0,
    )
    db.session.add(plan)
    db.session.commit()
    flash(f"Plán '{plan.name}' vytvorený.", "success")
    return redirect(url_for("billing.admin_plans"))


@billing_bp.route("/admin/billing/tenants")
@role_required("manage_all")
def admin_tenants():
    """Overview of all tenant subscriptions."""
    user = get_current_user()
    if not user.is_superadmin:
        flash("Prístup zamietnutý.", "danger")
        return redirect(url_for("dashboard.index"))
    tenants = Tenant.query.order_by(Tenant.name).all()
    subscriptions = {
        s.tenant_id: s
        for s in TenantSubscription.query.all()
    }
    return render_template(
        "admin/billing_tenants.html",
        tenants=tenants,
        subscriptions=subscriptions,
    )


@billing_bp.route("/admin/billing/tenants/<int:tenant_id>/record-payment", methods=["POST"])
@role_required("manage_all")
def admin_record_payment(tenant_id):
    """Record a manual/bank transfer payment for a tenant."""
    user = get_current_user()
    if not user.is_superadmin:
        flash("Prístup zamietnutý.", "danger")
        return redirect(url_for("dashboard.index"))
    from decimal import Decimal
    from services.billing import record_payment, reactivate_after_payment
    amount = Decimal(request.form.get("amount", "0"))
    payment_method = request.form.get("payment_method", "manual")
    bank_reference = request.form.get("bank_reference", "")
    notes = request.form.get("notes", "")
    record_payment(
        tenant_id, amount, payment_method,
        bank_reference=bank_reference, notes=notes,
    )
    reactivate_after_payment(tenant_id)
    flash("Platba zaznamenaná.", "success")
    return redirect(url_for("billing.admin_tenants"))


@billing_bp.route("/admin/billing/tenants/<int:tenant_id>/extend-trial", methods=["POST"])
@role_required("manage_all")
def admin_extend_trial(tenant_id):
    """Extend a tenant's trial period."""
    user = get_current_user()
    if not user.is_superadmin:
        flash("Prístup zamietnutý.", "danger")
        return redirect(url_for("dashboard.index"))
    extra_days = safe_int(request.form.get("extra_days")) or 0
    if extra_days <= 0:
        flash("Zadajte platný počet dní.", "danger")
        return redirect(url_for("billing.admin_tenants"))
    from services.billing import extend_trial
    extend_trial(tenant_id, extra_days, user.id)
    flash(f"Skúšobné obdobie predĺžené o {extra_days} dní.", "success")
    return redirect(url_for("billing.admin_tenants"))


@billing_bp.route("/admin/billing/tenants/<int:tenant_id>/reset-trial", methods=["POST"])
@role_required("manage_all")
def admin_reset_trial(tenant_id):
    """Reset a tenant's trial period to full duration."""
    user = get_current_user()
    if not user.is_superadmin:
        flash("Prístup zamietnutý.", "danger")
        return redirect(url_for("dashboard.index"))
    from services.billing import reset_trial
    reset_trial(tenant_id, user.id)
    flash("Skúšobné obdobie obnovené.", "success")
    return redirect(url_for("billing.admin_tenants"))


# ---------------------------------------------------------------------------
# Stripe webhook
# ---------------------------------------------------------------------------

@billing_bp.route("/webhook/stripe", methods=["POST"])
@csrf.exempt
def webhook_stripe():
    """Handle Stripe webhook events."""
    from services.stripe_billing import handle_webhook
    import json
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature", "")
    result = handle_webhook(payload, sig_header)
    if result:
        return json.dumps({"status": "ok"}), 200
    return json.dumps({"status": "error"}), 400
