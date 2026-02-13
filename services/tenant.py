"""Tenant context and data isolation services."""

from __future__ import annotations

from typing import Optional

from flask import abort, g
from sqlalchemy import event

from extensions import db


def get_current_tenant():
    """Return the active Tenant object from ``g``, or None."""
    return getattr(g, "current_tenant", None)


def get_current_tenant_id() -> Optional[int]:
    """Return the active tenant_id from ``g``, or None."""
    tenant = get_current_tenant()
    return tenant.id if tenant else None


def require_tenant() -> int:
    """Return the current tenant_id or abort with 403."""
    tid = get_current_tenant_id()
    if tid is None:
        abort(403, description="No tenant selected.")
    return tid


def tenant_query(model):
    """Return a query on *model* filtered to the current tenant.

    Usage::

        partners = tenant_query(Partner).filter_by(is_deleted=False).all()
    """
    tid = require_tenant()
    return model.query.filter_by(tenant_id=tid)


def stamp_tenant(obj):
    """Set ``tenant_id`` on *obj* to the current tenant.

    Call before ``db.session.add()``.  Returns *obj* for chaining.
    """
    if hasattr(obj, "tenant_id"):
        obj.tenant_id = require_tenant()
    return obj


def tenant_get_or_404(model, obj_id):
    """Fetch a single object by PK, verifying it belongs to the current tenant.

    Replaces ``db.get_or_404(Model, id)`` throughout all routes.
    """
    tid = require_tenant()
    obj = db.session.get(model, obj_id)
    if obj is None:
        abort(404)
    if hasattr(obj, "tenant_id") and obj.tenant_id != tid:
        abort(404)
    return obj


class TenantSecurityError(Exception):
    """Raised when a cross-tenant write is attempted."""


def _enforce_tenant_on_flush(session, flush_context):
    """Verify that all new/dirty tenant-scoped objects match the current tenant.

    This is a safety net — the primary isolation is ``tenant_query()`` and
    ``stamp_tenant()``.  This guard catches programming errors that bypass
    those helpers.
    """
    try:
        tid = getattr(g, "_tenant_id", None)
    except RuntimeError:
        # Outside request context (CLI, migrations, tests without app ctx)
        return

    if tid is None:
        return

    for obj in list(session.new) + list(session.dirty):
        obj_tid = getattr(obj, "tenant_id", None)
        if obj_tid is not None and obj_tid != tid:
            raise TenantSecurityError(
                f"Cross-tenant write blocked: {type(obj).__name__} "
                f"has tenant_id={obj_tid}, active tenant is {tid}"
            )


def register_tenant_guards(app):
    """Register the after_flush event listener.  Call once during app init."""
    event.listen(db.session, "after_flush", _enforce_tenant_on_flush)
