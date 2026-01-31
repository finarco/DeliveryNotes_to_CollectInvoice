"""Entity numbering service — generates formatted numbers from config."""

from __future__ import annotations

import datetime
from typing import Optional

from extensions import db
from models import NumberingConfig, NumberSequence


def _next_sequence(entity_type: str, scope_key: str) -> int:
    """Atomically increment and return the next sequence value."""
    seq = NumberSequence.query.filter_by(
        entity_type=entity_type, scope_key=scope_key
    ).first()
    if not seq:
        seq = NumberSequence(entity_type=entity_type, scope_key=scope_key, last_value=0)
        db.session.add(seq)
    seq.last_value += 1
    db.session.flush()
    return seq.last_value


def generate_number(
    entity_type: str,
    *,
    partner_id: Optional[int] = None,
    is_service: Optional[bool] = None,
) -> Optional[str]:
    """Generate the next formatted number for *entity_type*.

    Returns ``None`` if no numbering config exists for the entity type.
    """
    config = NumberingConfig.query.filter_by(entity_type=entity_type).first()
    if not config:
        return None

    now = datetime.datetime.now()
    parts: list[str] = []
    scope_parts: list[str] = []

    if config.prefix:
        parts.append(config.prefix)

    # Product/bundle type indicator
    if config.include_type_indicator and is_service is not None:
        indicator = config.service_indicator if is_service else config.goods_indicator
        if indicator:
            parts.append(indicator)

    # Partner ID
    if config.include_partner_id and partner_id:
        parts.append(str(partner_id))
        scope_parts.append(str(partner_id))

    # Year
    if config.include_year:
        parts.append(str(now.year))
        scope_parts.append(str(now.year))

    # Month
    if config.include_month:
        parts.append(f"{now.month:02d}")
        scope_parts.append(f"{now.month:02d}")

    # Sequence
    scope_key = "-".join(scope_parts) if scope_parts else ""
    seq = _next_sequence(entity_type, scope_key)
    digits = max(config.sequence_digits or 1, 1)
    parts.append(str(seq).zfill(digits))

    sep = config.separator or "-"
    return sep.join(parts)
