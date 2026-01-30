"""Audit logging service."""

from __future__ import annotations

from typing import Optional

from extensions import db
from models import AuditLog
from services.auth import get_current_user


def log_action(
    action: str,
    entity_type: str,
    entity_id: Optional[int],
    details: str = "",
) -> None:
    """Record an audit log entry.

    NOTE: This does NOT commit — the caller is responsible for committing
    the session (BUG-1 fix: prevents breaking transactional integrity).
    """
    user = get_current_user()
    db.session.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
        )
    )
