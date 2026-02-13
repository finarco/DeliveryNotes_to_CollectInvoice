"""Tenant selection and switching routes."""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

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
