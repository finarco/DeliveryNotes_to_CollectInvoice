"""Tag-based entity numbering service.

Supported tags:
  [YYYY]    4-digit year
  [YY]      2-digit year
  [MM]      month (01-12)
  [DD]      day (01-31)
  [PARTNER] partner ID
  [TYPE]    T for goods, S for service
  [C+]      counter — number of C's = digit width (resets per scope)

Everything outside brackets is literal text.
Example: ``DL[YY][MM]-[CCCC]`` -> ``DL2601-0001``
"""

from __future__ import annotations

import datetime
import re
from typing import Optional

from extensions import db
from models import NumberingConfig, NumberSequence

_TAG_RE = re.compile(r"\[([A-Z]+)\]")


def _next_sequence(entity_type: str, scope_key: str) -> int:
    """Atomically increment and return the next sequence value."""
    seq = NumberSequence.query.filter_by(
        entity_type=entity_type, scope_key=scope_key
    ).with_for_update().first()
    if not seq:
        seq = NumberSequence(
            entity_type=entity_type, scope_key=scope_key, last_value=1
        )
        db.session.add(seq)
        db.session.flush()
        return 1
    # Atomic increment via SQL expression to prevent race conditions
    seq.last_value = NumberSequence.last_value + 1
    db.session.flush()
    # Refresh to get the actual value after increment
    db.session.refresh(seq)
    return seq.last_value


def generate_number(
    entity_type: str,
    *,
    partner_id: Optional[int] = None,
    is_service: Optional[bool] = None,
) -> Optional[str]:
    """Generate the next formatted number for *entity_type* using tag pattern.

    Returns ``None`` when no pattern is configured.
    """
    config = NumberingConfig.query.filter_by(entity_type=entity_type).first()
    if not config or not config.pattern:
        return None

    now = datetime.datetime.now()
    pattern = config.pattern

    scope_parts: list[str] = []
    result_parts: list[str | None] = []
    counter_digits = 0
    counter_pos = -1
    last_end = 0

    for match in _TAG_RE.finditer(pattern):
        tag = match.group(1)
        start, end = match.start(), match.end()

        # Literal text before this tag
        if start > last_end:
            result_parts.append(pattern[last_end:start])

        if tag == "YYYY":
            val = str(now.year)
            result_parts.append(val)
            scope_parts.append(val)
        elif tag == "YY":
            val = str(now.year % 100).zfill(2)
            result_parts.append(val)
            scope_parts.append(val)
        elif tag == "MM":
            val = f"{now.month:02d}"
            result_parts.append(val)
            scope_parts.append(val)
        elif tag == "DD":
            val = f"{now.day:02d}"
            result_parts.append(val)
            scope_parts.append(val)
        elif tag == "PARTNER":
            val = str(partner_id) if partner_id else "0"
            result_parts.append(val)
            scope_parts.append(val)
        elif tag == "TYPE":
            val = ""
            if is_service is not None:
                val = "S" if is_service else "T"
            result_parts.append(val)
            if val:
                scope_parts.append(val)
        elif all(c == "C" for c in tag) and tag:
            # Counter tag: [C], [CC], [CCC], [CCCC], etc.
            counter_digits = len(tag)
            counter_pos = len(result_parts)
            result_parts.append(None)  # placeholder
        else:
            # Unknown tag — keep as literal
            result_parts.append(match.group(0))

        last_end = end

    # Trailing literal text
    if last_end < len(pattern):
        result_parts.append(pattern[last_end:])

    # Resolve counter
    if counter_pos >= 0:
        scope_key = "-".join(scope_parts) if scope_parts else ""
        seq = _next_sequence(entity_type, scope_key)
        result_parts[counter_pos] = str(seq).zfill(counter_digits)

    return "".join(p for p in result_parts if p is not None)
