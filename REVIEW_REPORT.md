# Code Quality, Best Practices & Feature Review

**Project:** DeliveryNotes_to_CollectInvoice
**Date:** 2026-01-30
**Scope:** Full codebase review post security-fix (app.py, templates, config, tests)

> Companion to `SECURITY_AUDIT.md`. CRITICAL/HIGH security issues are already fixed.
> This report covers code sanity, best-practice improvements, and proposed features.

---

## Part 1 — Code Sanity Issues (Bugs & Logic Errors)

### BUG-1. `log_action()` does its own `db.session.commit()` — breaks transactional integrity

**File:** `app.py:488-499`

Every call to `log_action()` commits the database session independently. In most
routes the pattern is:

```python
db.session.commit()                          # 1st commit — entity saved
log_action("create", "order", order.id, …)   # 2nd commit — audit log
```

If the audit commit fails (e.g., database full), the entity is persisted but has
no audit trail. Worse, if `log_action` is accidentally called *before*
`db.session.commit()`, it commits whatever is in the session prematurely.

**Fix:** Remove `db.session.commit()` from `log_action()`. Have callers commit
the entity and the audit log in a single transaction:

```python
log_action("create", "order", order.id, …)  # only adds to session
db.session.commit()                          # single atomic commit
```

---

### BUG-2. `orders.html` renders two "Ceny" columns — second one duplicates the first

**File:** `templates/orders.html:112-139`

The table header has 7 columns (`ID, Partner, Zvoz, Doručenie, Platba, Ceny, Stav`),
but the body renders **8** cells — `Ceny` appears twice:
- Line 132‑137: a conditional "Áno/Nie" block based on role + `show_prices`
- Line 139: unconditional `{{ "Áno" if order.show_prices else "Nie" }}`

The second `<td>` on line 139 is orphaned (no matching `<th>`), shifting
all subsequent columns one cell to the right. The "Stav" data appears under
no header.

**Fix:** Remove the duplicate `<td>` on line 139, or add an "Interné" header for it.

---

### BUG-3. Same duplicate "Ceny" bug in `delivery_notes.html`

**File:** `templates/delivery_notes.html:92-99`

Identical issue. Two `<td>` elements for price visibility, only one `<th>`.

---

### BUG-4. Page number is not lower-bounded

**File:** `app.py:785, 901, 988, 1077`

```python
page = safe_int(request.args.get("page"), default=1)
```

No check for `page < 1`. A URL like `?page=-5` produces a negative SQL
`OFFSET`, which SQLite silently ignores (returns all rows), but PostgreSQL
raises an error. This would break a future database migration.

**Fix:** Add `page = max(1, page)` after parsing.

---

### BUG-5. `status_filter` on invoices accepts arbitrary strings

**File:** `app.py:1074-1076`

```python
status_filter = request.args.get("status")
if status_filter:
    query = query.filter(Invoice.status == status_filter)
```

Any string is accepted. While SQLAlchemy parameterizes it (no injection), a
URL like `?status=xyzzy` silently returns zero results instead of being
rejected. The allowed values are `draft`, `sent`, `error`.

**Fix:** Validate against an allowlist:
```python
VALID_STATUSES = {"draft", "sent", "error"}
if status_filter and status_filter in VALID_STATUSES:
```

---

### BUG-6. No database migration support — schema changes break existing databases

**File:** `app.py:452`

```python
db.create_all()
```

`create_all()` only creates tables that don't exist. It does **not** alter
existing tables. The recent security fix added `must_change_password` to
`User` and changed `Float → Numeric` on monetary columns. Any database
created before the fix will be missing the new column and will crash on
login.

**Fix:** Adopt Flask-Migrate (Alembic) for schema versioning:
```
pip install Flask-Migrate
flask db init
flask db migrate -m "add must_change_password, numeric columns"
flask db upgrade
```

---

### BUG-7. `build_invoice_for_partner` uses `db.get_or_404` outside request context awareness

**File:** `app.py:1227`

`db.get_or_404(Partner, partner_id)` raises `werkzeug.exceptions.NotFound`
if the partner doesn't exist. This is fine inside a route but will crash with
a confusing error if this function is ever called from a CLI script, a
scheduled task, or a test without request context.

**Fix:** Use `db.session.get(Partner, partner_id)` and raise `ValueError` if
not found.

---

### BUG-8. Partner creation allows empty name

**File:** `app.py:609`

```python
name=request.form.get("name", "").strip()
```

`Partner.name` is `nullable=False`, but an empty string `""` is not NULL. A
partner with a blank name can be created, breaking display logic.

**Fix:** Validate `name` is non-empty before creating the Partner:
```python
name = request.form.get("name", "").strip()
if not name:
    flash("Názov partnera je povinný.", "danger")
    return redirect(url_for("partners"))
```

---

### BUG-9. Partner discount is stored but never applied to orders/invoices

**File:** `app.py:115` (model) vs. `app.py:770` (order creation)

`Partner.discount_percent` and `Product.discount_excluded` exist in the data
model, but order creation uses `product.price` directly without applying any
discount:

```python
price = product.price   # no discount logic
order.items.append(OrderItem(…, unit_price=price))
```

This means the discount infrastructure is dead code.

**Fix:** Apply discount during order/delivery/invoice creation, respecting
`discount_excluded`:
```python
discount = partner.discount_percent or 0
if product.discount_excluded:
    discount = 0
price = round(product.price * (1 - discount / 100), 2)
```

---

### BUG-10. `Order.created_by_id` uses `session.get("user_id")` directly

**File:** `app.py:756`

```python
created_by_id=session.get("user_id")
```

If the session is tampered with or the user was deleted from the database
since login, this sets a foreign key to a non-existent user. Delivery note
creation (line 845) has the same issue.

**Fix:** Use `current_user().id` — which queries the database and returns
`None` if the user doesn't exist (caught by `require_role` earlier in the
same route).

---

## Part 2 — Best Practice Improvements

### Architecture

| # | Issue | Recommendation |
|---|-------|----------------|
| A1 | **1350-line monolith** (`app.py`) — models, routes, config, PDF gen, business logic all in one file | Split into modules: `models.py`, `routes/`, `services/`, `pdf.py` |
| A2 | **No Flask Blueprints** — all routes as closures inside `create_app()` | Use Blueprints: `partners_bp`, `orders_bp`, `invoices_bp`, etc. |
| A3 | **No service layer** — business logic is inside route handlers | Extract `OrderService`, `InvoiceService`, `DeliveryService` classes |
| A4 | **No database migrations** | Add `Flask-Migrate` (wraps Alembic) — see BUG-6 |
| A5 | **No application factory done properly** — routes are closures, not registered via Blueprints | Refactor to proper factory + Blueprint pattern |

### Data Model

| # | Issue | Recommendation |
|---|-------|----------------|
| D1 | **No `updated_at` timestamp** on any model | Add `updated_at = db.Column(db.DateTime, onupdate=utc_now)` to mutable entities |
| D2 | **No soft delete** — cascade deletes lose history | Add `is_active` / `deleted_at` flag to Partner, Product, Order |
| D3 | **`OrderItem.quantity` is Integer** — cannot handle fractional units (0.5 kg) | Change to `db.Numeric(10, 3)` for models where fractional qty is needed |
| D4 | **No database indexes** on foreign keys and filter columns | Add `db.Index` on `Order.partner_id`, `DeliveryNote.invoiced`, `Invoice.status`, `AuditLog.created_at` |
| D5 | **`AuditLog.details` is String(255)** — too short for complex operations | Change to `db.Text` |
| D6 | **No invoice number** — uses auto-increment `id` | Add `invoice_number` with sequential numbering (e.g., `FV-2026-0001`) per Slovak legal requirements |
| D7 | **No VAT/DPH fields** | Add `vat_rate` to Product and invoice line items — Slovak law requires 20%/10%/0% VAT tracking |
| D8 | **`DeliveryNote.primary_order_id` is nullable** but always set in practice | Make `nullable=False` or document when it can be NULL |

### Code Quality

| # | Issue | Recommendation |
|---|-------|----------------|
| Q1 | **No WTForms** for form validation despite Flask-WTF being installed | Define form classes per entity — gives server-side validation, CSRF, and type coercion |
| Q2 | **Magic numbers** — `20` (per_page), `8` (session hours), `5` (rate limit) | Move to config constants |
| Q3 | **No type hints** on route functions | Add return type annotations (`-> Response`) |
| Q4 | **PDF generation is inline** — 70 lines of ReportLab code in app.py | Extract to `pdf_generator.py` module |
| Q5 | **Email/Superfaktura calls are synchronous** — block request handler (up to 30s timeout) | Use background task queue (Celery, RQ, or at minimum `threading.Thread`) |
| Q6 | **No healthcheck endpoint** | Add `GET /health` returning JSON `{"status": "ok", "db": true}` |
| Q7 | **No structured logging** | Switch to JSON logging for production (easier to parse in log aggregators) |
| Q8 | **`flash(str(e))` leaks internals** — 2 remaining instances | Show generic messages; log `str(e)` server-side only |

### Testing

| # | Issue | Recommendation |
|---|-------|----------------|
| T1 | **No integration tests** for full workflow (partner → order → delivery → invoice) | Add end-to-end test covering the complete business process |
| T2 | **No negative tests** for business logic edge cases | Test: invoice for deleted partner, delivery note with already-invoiced items, overlapping vehicle schedules |
| T3 | **No test for `build_invoice_for_partner` discount logic** | Will be needed once BUG-9 is fixed |
| T4 | **`test_app.py` is also monolithic** (1500+ lines) | Split into `test_routes.py`, `test_models.py`, `test_business_logic.py`, `test_security.py` |

---

## Part 3 — Proposed New Functionalities

### Priority 1 — Required for production use

| # | Feature | Description | Rationale |
|---|---------|-------------|-----------|
| F1 | **User management UI** | Admin page to create/edit/delete users and assign roles | Currently only the initial admin exists; no way to add operators/collectors/customers |
| F2 | **Edit/Delete entities** | Edit partners, products, orders; soft-delete with confirmation | Only "create" operations exist; no way to correct mistakes |
| F3 | **Invoice numbering** | Sequential numbers like `FV-2026-0001` per year | Slovak law requires sequential numbering on tax documents |
| F4 | **VAT (DPH) support** | Track VAT rate per product, calculate VAT on invoices | Legally required on Slovak invoices — 20% standard, 10% reduced, 0% exempt |
| F5 | **Partner discount application** | Apply `discount_percent` during order/delivery/invoice creation | Data model exists but logic is unused (BUG-9) |
| F6 | **Search/filter** | Text search on partners by name/ICO; orders by date range | Partners page loads ALL partners; doesn't scale beyond ~50 |
| F7 | **Data export (CSV/Excel)** | Export partner list, order history, invoice summary | Required for accounting handoff and tax reporting |

### Priority 2 — Important for business operations

| # | Feature | Description | Rationale |
|---|---------|-------------|-----------|
| F8 | **Credit notes** (Dobropis) | Issue corrections/refunds linked to original invoice | Standard business requirement for returns and corrections |
| F9 | **Email templates** | HTML email templates for invoices, delivery confirmations | Currently sends plain-text one-liner emails |
| F10 | **Dashboard analytics** | Charts showing revenue, order volume, delivery counts over time | Dashboard currently only shows 4 static counters |
| F11 | **Delivery note signing** | Digital signature/confirmation capture on delivery | Proof of delivery for dispute resolution |
| F12 | **Payment tracking** | Track invoice payment status (unpaid/partial/paid) and due dates | No way to know which invoices have been paid |
| F13 | **Notifications** | Email alerts for new orders, overdue deliveries, unpaid invoices | Proactive business monitoring |
| F14 | **Recurring orders** | Template/scheduled orders for regular customers | Common pattern for delivery businesses with weekly schedules |

### Priority 3 — Nice-to-have

| # | Feature | Description |
|---|---------|-------------|
| F15 | **REST API** | JSON API for mobile app or third-party integrations |
| F16 | **Batch operations** | Select multiple delivery notes → create invoice; bulk confirm |
| F17 | **Document templates** | Customizable PDF layouts (company logo, footer, terms) |
| F18 | **Activity feed** | Real-time audit log view on dashboard |
| F19 | **Data retention** | Auto-archive records older than N years |
| F20 | **Multi-language** | i18n support beyond Slovak (Czech, English) |
| F21 | **Import from CSV** | Bulk import partners, products from spreadsheet |
| F22 | **Price list management** | Multiple price lists per partner/group with effective dates |

---

## Part 4 — Recommended Refactoring Roadmap

### Phase 1: Stabilization (immediate)
1. Fix BUG-1 through BUG-10
2. Add Flask-Migrate for database schema management
3. Add server-side input validation (WTForms or manual)
4. Fix template column misalignment (BUG-2, BUG-3)

### Phase 2: Architecture cleanup
5. Split `app.py` into modules (models, routes, services, pdf)
6. Introduce Flask Blueprints
7. Extract PDF generation to separate module
8. Add background task processing for email/API calls

### Phase 3: Business features
9. User management UI (F1)
10. Edit/Delete operations (F2)
11. Invoice numbering + VAT (F3, F4)
12. Partner discount application (F5)
13. Search/filter + data export (F6, F7)

### Phase 4: Advanced features
14. Credit notes, payment tracking, recurring orders (F8, F12, F14)
15. Dashboard analytics (F10)
16. REST API (F15)

---

## Appendix: Summary Counts

| Category | Count |
|----------|-------|
| Bugs / Logic errors | 10 |
| Architecture improvements | 5 |
| Data model improvements | 8 |
| Code quality improvements | 8 |
| Testing improvements | 4 |
| Proposed features (P1) | 7 |
| Proposed features (P2) | 7 |
| Proposed features (P3) | 8 |
| **Total items** | **57** |
