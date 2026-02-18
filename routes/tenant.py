"""Tenant selection and switching routes."""

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from extensions import db
from models import Tenant, UserTenant
from services.auth import login_required
from utils import safe_int

tenant_bp = Blueprint("tenant", __name__)


@tenant_bp.route("/select-tenant")
@login_required
def select_tenant():
    """Show tenant selection page."""
    from services.auth import get_current_user
    user = get_current_user()
    memberships = UserTenant.query.filter_by(user_id=user.id).all()
    tenants = [m.tenant for m in memberships if m.tenant and m.tenant.is_active]
    if not tenants and not user.is_superadmin:
        flash("Nemáte pridelené žiadne organizácie. Kontaktujte administrátora.", "warning")
    return render_template("select_tenant.html", tenants=tenants)


@tenant_bp.route("/switch-tenant", methods=["POST"])
@login_required
def switch_tenant():
    """Switch the active tenant."""
    from services.auth import get_current_user
    tenant_id = safe_int(request.form.get("tenant_id"))
    if not tenant_id:
        flash("Neplatná organizácia.", "danger")
        return redirect(url_for("tenant.select_tenant"))
    user = get_current_user()
    membership = UserTenant.query.filter_by(
        user_id=user.id, tenant_id=tenant_id
    ).first()
    if not membership and not user.is_superadmin:
        flash("Nemáte prístup k tejto organizácii.", "danger")
        return redirect(url_for("tenant.select_tenant"))
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant or not tenant.is_active:
        flash("Organizácia nie je aktívna.", "danger")
        return redirect(url_for("tenant.select_tenant"))
    session["active_tenant_id"] = tenant_id
    flash(f"Prepnuté na organizáciu '{tenant.name}'.", "success")
    return redirect(url_for("dashboard.index"))


@tenant_bp.route("/create-tenant", methods=["POST"])
@login_required
def create_tenant():
    """Create a new tenant for a logged-in user."""
    from services.auth import get_current_user
    from services.billing import create_trial_subscription
    import re

    user = get_current_user()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Názov organizácie je povinný.", "danger")
        return redirect(url_for("dashboard.index"))

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if Tenant.query.filter_by(slug=slug).first():
        slug = f"{slug}-{Tenant.query.count() + 1}"

    tenant = Tenant(name=name, slug=slug, is_active=True)
    db.session.add(tenant)
    db.session.flush()

    # Temporarily switch tenant context so the flush guard allows
    # writing UserTenant and TenantSubscription for the NEW tenant.
    prev_tenant_id = getattr(g, "_tenant_id", None)
    prev_tenant = getattr(g, "current_tenant", None)
    g._tenant_id = tenant.id
    g.current_tenant = tenant

    ut = UserTenant(user_id=user.id, tenant_id=tenant.id, is_default=False)
    db.session.add(ut)

    create_trial_subscription(tenant.id)
    db.session.commit()

    session["active_tenant_id"] = tenant.id
    flash(f"Organizácia '{name}' vytvorená.", "success")
    return redirect(url_for("dashboard.index"))
