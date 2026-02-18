"""Billing and subscription management routes."""

import json

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from extensions import csrf, db
from models import AppSetting, Payment, SubscriptionPlan, Tenant, TenantSubscription, UserTenant
from services.auth import get_current_user, login_required, role_required
from services.tenant import get_current_tenant, get_current_tenant_id, require_tenant
from utils import safe_int

billing_bp = Blueprint("billing", __name__)


def _get_active_gateway() -> str:
    """Return the active payment gateway (tenant-scoped, falls back to global)."""
    from services.tenant import tenant_query
    try:
        row = tenant_query(AppSetting).filter_by(key="payment_gateway").first()
        if row and row.value:
            return row.value
    except Exception:
        pass
    # Fall back to global setting for backward compatibility
    row = AppSetting.query.filter_by(tenant_id=None, key="payment_gateway").first()
    return row.value if row and row.value else "gopay"


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
    # Find any pending payment for the current subscription
    pending_payment = None
    if sub:
        pending_payment = (
            Payment.query.filter_by(tenant_id=tid, status="pending")
            .filter(Payment.gopay_payment_id.isnot(None))
            .order_by(Payment.created_at.desc())
            .first()
        )
    return render_template(
        "billing/status.html",
        subscription=sub,
        plan=plan,
        payments=payments,
        plans=plans,
        pending_payment=pending_payment,
    )


@billing_bp.route("/billing/subscribe", methods=["POST"])
@login_required
def subscribe():
    """Choose a plan and initiate payment if needed."""
    tid = require_tenant()
    plan_id = safe_int(request.form.get("plan_id"))
    billing_cycle = request.form.get("billing_cycle", "monthly")
    plan = db.session.get(SubscriptionPlan, plan_id)
    if not plan or not plan.is_active:
        flash("Neplatny plan.", "danger")
        return redirect(url_for("billing.status"))

    # Determine price
    price = plan.price_monthly if billing_cycle == "monthly" else plan.price_yearly

    # Free plans — activate immediately
    if price == 0:
        from services.billing import create_subscription
        create_subscription(tid, plan_id, billing_cycle)
        flash(f"Plan '{plan.name}' bol aktivovany.", "success")
        return redirect(url_for("billing.status"))

    # Paid plan — route through selected payment gateway
    gateway = _get_active_gateway()

    if gateway == "gopay":
        return _subscribe_gopay(tid, plan, billing_cycle, price)
    elif gateway == "stripe":
        # Existing Stripe flow
        from services.billing import create_subscription
        create_subscription(tid, plan_id, billing_cycle)
        flash(f"Plan '{plan.name}' bol aktivovany.", "success")
        return redirect(url_for("billing.status"))
    else:
        # Manual gateway — admin handles payment offline
        from services.billing import create_subscription
        create_subscription(tid, plan_id, billing_cycle)
        flash(f"Plan '{plan.name}' aktivovany. Platbu realizujte bankovym prevodom.", "info")
        return redirect(url_for("billing.status"))


def _subscribe_gopay(tid, plan, billing_cycle, price):
    """Create a GoPay payment and redirect to the payment page."""
    from services.billing import get_tenant_subscription, record_payment
    from services.gopay_billing import create_gopay_payment

    tenant = db.session.get(Tenant, tid)
    if not tenant:
        flash("Tenant neexistuje.", "danger")
        return redirect(url_for("billing.status"))

    # Create or update subscription with pending_payment status
    sub = get_tenant_subscription(tid)
    if sub:
        sub.plan_id = plan.id
        sub.billing_cycle = billing_cycle
        if sub.status not in ("active", "trial"):
            sub.status = "pending_payment"
    else:
        from datetime import datetime, timezone
        sub = TenantSubscription(
            tenant_id=tid,
            plan_id=plan.id,
            status="pending_payment",
            billing_cycle=billing_cycle,
            current_period_start=datetime.now(timezone.utc),
        )
        db.session.add(sub)
    db.session.commit()

    # Build callback URLs
    return_url = url_for("billing.payment_return", _external=True)
    notify_url = url_for("billing.notify_gopay", _external=True)

    gopay_id, gw_url = create_gopay_payment(
        tenant, plan, billing_cycle, return_url, notify_url
    )
    if not gopay_id or not gw_url:
        flash("Nepodarilo sa vytvorit platbu cez GoPay. Skuste to znova.", "danger")
        return redirect(url_for("billing.status"))

    # Record pending payment
    payment = record_payment(
        tid,
        price,
        "gopay",
        gopay_payment_id=str(gopay_id),
        status="pending",
    )

    return redirect(url_for("billing.payment_page", payment_id=payment.id))


@billing_bp.route("/billing/pay/<int:payment_id>")
@login_required
def payment_page(payment_id):
    """Display the GoPay inline payment form."""
    tid = require_tenant()
    payment = Payment.query.filter_by(id=payment_id, tenant_id=tid).first()
    if not payment or not payment.gopay_payment_id:
        flash("Platba nenajdena.", "danger")
        return redirect(url_for("billing.status"))

    # Get the GoPay gateway URL for this payment
    from services.gopay_billing import get_gopay_payment_status, _get_embed_js_url

    status_data = get_gopay_payment_status(payment.gopay_payment_id)
    if not status_data:
        flash("Nepodarilo sa nacitat platbu z GoPay.", "danger")
        return redirect(url_for("billing.status"))

    gw_url = status_data.get("gw_url", "")
    state = status_data.get("state", "")

    if state == "PAID":
        flash("Platba uz bola uhradena.", "success")
        return redirect(url_for("billing.status"))

    # Get plan info for display
    sub = TenantSubscription.query.filter_by(tenant_id=tid).first()
    plan = sub.plan if sub else None
    billing_cycle = sub.billing_cycle if sub else "monthly"

    return render_template(
        "billing/pay.html",
        payment=payment,
        plan=plan,
        billing_cycle=billing_cycle,
        amount=payment.amount,
        gw_url=gw_url,
        gopay_embed_js=_get_embed_js_url(),
    )


@billing_bp.route("/billing/return")
def payment_return():
    """Handle return from GoPay after payment attempt."""
    gopay_id = request.args.get("id", "")
    if not gopay_id:
        flash("Neplatna platobna odpoved.", "danger")
        return redirect(url_for("billing.status"))

    from services.gopay_billing import get_gopay_payment_status

    status_data = get_gopay_payment_status(gopay_id)
    state = status_data.get("state", "UNKNOWN") if status_data else "UNKNOWN"

    # Find our payment record
    payment = Payment.query.filter_by(gopay_payment_id=str(gopay_id)).first()

    if state == "PAID" and payment:
        if payment.status != "completed":
            from datetime import datetime, timezone
            from services.billing import reactivate_after_payment
            payment.status = "completed"
            payment.paid_at = datetime.now(timezone.utc)
            db.session.commit()
            reactivate_after_payment(payment.tenant_id)

    return render_template(
        "billing/return.html",
        state=state,
        payment=payment,
        gopay_id=gopay_id,
    )


@billing_bp.route("/billing/notify/gopay")
@csrf.exempt
def notify_gopay():
    """GoPay notification endpoint (server-to-server callback)."""
    gopay_id = request.args.get("id", "")
    if not gopay_id:
        return json.dumps({"status": "error", "message": "missing id"}), 400

    from services.gopay_billing import handle_gopay_notification
    handle_gopay_notification(gopay_id)
    return json.dumps({"status": "ok"}), 200


@billing_bp.route("/billing/cancel", methods=["POST"])
@login_required
def cancel():
    """Cancel subscription (effective at period end)."""
    tid = require_tenant()
    from services.billing import cancel_subscription
    cancel_subscription(tid)
    flash("Predplatne bude zrusene na konci fakturacneho obdobia.", "warning")
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
        flash("Pristup zamietnuty.", "danger")
        return redirect(url_for("dashboard.index"))
    plans = SubscriptionPlan.query.order_by(SubscriptionPlan.sort_order).all()
    return render_template("admin/billing_plans.html", plans=plans)


@billing_bp.route("/admin/billing/plans", methods=["POST"])
@role_required("manage_all")
def admin_create_plan():
    """Create a new subscription plan."""
    user = get_current_user()
    if not user.is_superadmin:
        flash("Pristup zamietnuty.", "danger")
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
    flash(f"Plan '{plan.name}' vytvoreny.", "success")
    return redirect(url_for("billing.admin_plans"))


@billing_bp.route("/admin/billing/tenants")
@role_required("manage_all")
def admin_tenants():
    """Overview of all tenant subscriptions."""
    user = get_current_user()
    if not user.is_superadmin:
        flash("Pristup zamietnuty.", "danger")
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
        flash("Pristup zamietnuty.", "danger")
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
    flash("Platba zaznamenana.", "success")
    return redirect(url_for("billing.admin_tenants"))


@billing_bp.route("/admin/billing/tenants/<int:tenant_id>/extend-trial", methods=["POST"])
@role_required("manage_all")
def admin_extend_trial(tenant_id):
    """Extend a tenant's trial period."""
    user = get_current_user()
    if not user.is_superadmin:
        flash("Pristup zamietnuty.", "danger")
        return redirect(url_for("dashboard.index"))
    extra_days = safe_int(request.form.get("extra_days")) or 0
    if extra_days <= 0:
        flash("Zadajte platny pocet dni.", "danger")
        return redirect(url_for("billing.admin_tenants"))
    from services.billing import extend_trial
    extend_trial(tenant_id, extra_days, user.id)
    flash(f"Skusobne obdobie predlzene o {extra_days} dni.", "success")
    return redirect(url_for("billing.admin_tenants"))


@billing_bp.route("/admin/billing/tenants/<int:tenant_id>/reset-trial", methods=["POST"])
@role_required("manage_all")
def admin_reset_trial(tenant_id):
    """Reset a tenant's trial period to full duration."""
    user = get_current_user()
    if not user.is_superadmin:
        flash("Pristup zamietnuty.", "danger")
        return redirect(url_for("dashboard.index"))
    from services.billing import reset_trial
    reset_trial(tenant_id, user.id)
    flash("Skusobne obdobie obnovene.", "success")
    return redirect(url_for("billing.admin_tenants"))


# ---------------------------------------------------------------------------
# Stripe webhook
# ---------------------------------------------------------------------------

@billing_bp.route("/webhook/stripe", methods=["POST"])
@csrf.exempt
def webhook_stripe():
    """Handle Stripe webhook events."""
    from services.stripe_billing import handle_webhook
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature", "")
    result = handle_webhook(payload, sig_header)
    if result:
        return json.dumps({"status": "ok"}), 200
    return json.dumps({"status": "error"}), 400
