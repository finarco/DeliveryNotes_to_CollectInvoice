# TODO: Numbering System Refactor

## Current State

The current numbering system uses:
- `NumberingConfig` - stores format patterns (e.g., `"DL[YY][MM]-[CCCC]"`)
- `NumberSequence` - stores current counter values per entity type and scope

## Proposed Changes

### Goal
Move numbering configuration from database metadata to a dedicated, more customizable table structure that allows:
- Per-partner numbering sequences
- Multiple numbering formats per entity type
- Flexible reset periods (yearly, monthly, custom)
- Audit trail for number generation

### Proposed New Schema

```python
class NumberingScheme(db.Model):
    """Defines a numbering scheme that can be applied to entities."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    entity_type = db.Column(db.String(40), nullable=False)  # order, delivery_note, invoice
    pattern = db.Column(db.String(120), nullable=False)  # e.g., "FV-[YYYY]-[PARTNER]-[CCCC]"

    # Scope configuration
    scope_type = db.Column(db.String(40), default="global")  # global, partner, yearly, monthly
    reset_period = db.Column(db.String(20), default="never")  # never, yearly, monthly

    # Priority for scheme selection
    priority = db.Column(db.Integer, default=0)

    # Conditions for when this scheme applies
    partner_group = db.Column(db.String(60))  # Apply only to partners in this group
    is_default = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)


class NumberingCounter(db.Model):
    """Stores counter values for numbering schemes."""
    id = db.Column(db.Integer, primary_key=True)
    scheme_id = db.Column(db.Integer, db.ForeignKey("numbering_scheme.id"), nullable=False)

    # Scope identifiers
    scope_key = db.Column(db.String(120), default="")  # e.g., "2026" for yearly, "2026-01" for monthly
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"))  # For per-partner numbering

    # Counter value
    last_value = db.Column(db.Integer, default=0)
    last_generated_at = db.Column(db.DateTime)

    __table_args__ = (
        db.UniqueConstraint("scheme_id", "scope_key", "partner_id", name="uq_numbering_counter"),
    )


class NumberingHistory(db.Model):
    """Audit trail for generated numbers."""
    id = db.Column(db.Integer, primary_key=True)
    scheme_id = db.Column(db.Integer, db.ForeignKey("numbering_scheme.id"), nullable=False)
    entity_type = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    generated_number = db.Column(db.String(60), nullable=False)
    counter_value = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
```

### Implementation Steps

1. **Phase 1: New Schema** (no breaking changes)
   - [ ] Create new `NumberingScheme`, `NumberingCounter`, `NumberingHistory` models
   - [ ] Create migration to add new tables
   - [ ] Create admin UI for managing numbering schemes

2. **Phase 2: Service Layer**
   - [ ] Create `NumberingService` class to handle all numbering logic
   - [ ] Implement scheme selection based on entity type, partner, etc.
   - [ ] Implement counter increment with proper locking
   - [ ] Add number generation audit logging

3. **Phase 3: Migration**
   - [ ] Migrate existing `NumberingConfig` entries to new `NumberingScheme`
   - [ ] Migrate existing `NumberSequence` to new `NumberingCounter`
   - [ ] Update all code using old numbering service

4. **Phase 4: Cleanup**
   - [ ] Deprecate old `NumberingConfig` and `NumberSequence`
   - [ ] Remove old numbering service code
   - [ ] Update documentation

### Extended Pattern Tags

New tags to support:
- `[SCHEME]` - Scheme name/code
- `[BRANCH]` - Branch/location code
- `[USER]` - User initials or code
- `[CUSTOM:field]` - Custom field from entity

### Admin UI Features

1. **Scheme Management**
   - Create/edit/delete numbering schemes
   - Preview generated numbers
   - Test pattern with sample data

2. **Counter Management**
   - View current counter values
   - Manually adjust counters (with audit logging)
   - Reset counters for new period

3. **History View**
   - Search generated numbers
   - View audit trail
   - Export to CSV

### Notes

- Ensure backward compatibility during migration
- All counter operations must be atomic (use database locking)
- Consider database transaction isolation levels
- Add comprehensive tests for concurrent number generation

---

*Created: 2026-02-05*
*Status: TODO*
*Priority: Medium*
