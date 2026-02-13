"""Authentication and authorization services."""

from __future__ import annotations

import logging
import secrets
from functools import wraps
from typing import Optional

from flask import flash, g, redirect, session, url_for
from werkzeug.security import generate_password_hash

from extensions import db
from models import ROLE_PERMISSIONS, Tenant, User, UserTenant

logger = logging.getLogger(__name__)


def get_current_user() -> Optional[User]:
    """Return the currently logged-in user from ``flask.g``."""
    return getattr(g, "current_user", None)


def login_required(f):
    """Decorator that redirects to login if user is not authenticated."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated


def role_required(permission: str):
    """Decorator that checks user has *permission* (or ``manage_all``).

    Uses ``UserTenant.role_override`` for the active tenant when available.
    """

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = get_current_user()
            if not user:
                return redirect(url_for("auth.login"))
            # Use role_override from current tenant membership if available
            effective_role = user.role
            active_tid = session.get("active_tenant_id")
            if active_tid:
                membership = UserTenant.query.filter_by(
                    user_id=user.id, tenant_id=active_tid
                ).first()
                if membership and membership.role_override:
                    effective_role = membership.role_override
            permissions = ROLE_PERMISSIONS.get(effective_role, set())
            if user.is_superadmin:
                permissions = permissions | {"manage_all"}
            if permission not in permissions and "manage_all" not in permissions:
                flash("Nemáte oprávnenie na tento krok.", "danger")
                return redirect(url_for("dashboard.index"))
            return f(*args, **kwargs)

        return decorated

    return decorator


def ensure_admin_user():
    """Create a default admin user if the users table is empty.

    Also assigns the admin to the default tenant and sets ``is_superadmin``.
    """
    if User.query.count() == 0:
        password = secrets.token_urlsafe(12)
        admin = User(
            username="admin",
            password_hash=generate_password_hash(password),
            role="admin",
            must_change_password=True,
            is_superadmin=True,
        )
        db.session.add(admin)
        db.session.flush()

        # Link to default tenant
        default_tenant = Tenant.query.filter_by(slug="default").first()
        if default_tenant:
            ut = UserTenant(
                user_id=admin.id,
                tenant_id=default_tenant.id,
                is_default=True,
            )
            db.session.add(ut)

        db.session.commit()
        # Print to stdout only — never log credentials to persistent log files
        print(
            f"Created default admin user. Initial password: {password} "
            "(change immediately after first login)"
        )
