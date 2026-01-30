"""Authentication and authorization services."""

from __future__ import annotations

import logging
import secrets
from functools import wraps
from typing import Optional

from flask import flash, g, redirect, session, url_for
from werkzeug.security import generate_password_hash

from extensions import db
from models import ROLE_PERMISSIONS, User

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
    """Decorator that checks user has *permission* (or ``manage_all``)."""

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = get_current_user()
            if not user:
                return redirect(url_for("auth.login"))
            permissions = ROLE_PERMISSIONS.get(user.role, set())
            if permission not in permissions and "manage_all" not in permissions:
                flash("Nemáte oprávnenie na tento krok.", "danger")
                return redirect(url_for("dashboard.index"))
            return f(*args, **kwargs)

        return decorated

    return decorator


def ensure_admin_user():
    """Create a default admin user if the users table is empty."""
    if User.query.count() == 0:
        password = secrets.token_urlsafe(12)
        admin = User(
            username="admin",
            password_hash=generate_password_hash(password),
            role="admin",
            must_change_password=True,
        )
        db.session.add(admin)
        db.session.commit()
        logger.warning(
            "Created default admin user. Initial password: %s "
            "(change immediately after first login)",
            password,
        )
