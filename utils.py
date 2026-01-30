"""Utility / helper functions used across the application."""

from __future__ import annotations

import datetime
import logging
from datetime import timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------

def utc_now() -> datetime.datetime:
    """Return current UTC datetime.  Used as SQLAlchemy column default."""
    return datetime.datetime.now(timezone.utc)


def parse_date(raw: Optional[str]) -> Optional[datetime.date]:
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.warning("Could not parse date: %r", raw)
        return None


def parse_datetime(raw: Optional[str]) -> Optional[datetime.datetime]:
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except (ValueError, TypeError):
        logger.warning("Could not parse datetime: %r", raw)
        return None


def parse_time(raw: Optional[str]) -> Optional[datetime.time]:
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(raw, "%H:%M").time()
    except (ValueError, TypeError):
        logger.warning("Could not parse time: %r", raw)
        return None


# ---------------------------------------------------------------------------
# Safe type conversions
# ---------------------------------------------------------------------------

def safe_int(value, default: int = 0) -> int:
    """Safely convert *value* to ``int``, returning *default* on failure."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        logger.warning("Could not convert %r to int, using default %s", value, default)
        return default


def safe_float(value, default: float = 0.0) -> float:
    """Safely convert *value* to ``float``, returning *default* on failure."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        logger.warning("Could not convert %r to float, using default %s", value, default)
        return default
