# Security Audit Report

**Project:** DeliveryNotes_to_CollectInvoice
**Date:** 2026-01-29
**Scope:** Full codebase review (app.py, mailer.py, superfaktura_client.py, config_models.py, config.yaml, templates/*, .env.example, .gitignore, requirements.txt)

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 3 |
| HIGH     | 7 |
| MEDIUM   | 8 |
| LOW      | 7 |
| **Total** | **25** |

---

## CRITICAL Findings

### C1. Dead-code bug: mailer.py sends email TWICE, first time without error handling

**File:** `mailer.py:68-77`
**Description:** The `send_document_email` function contains a bare SMTP send *outside* the `try/except` block (lines 68-71), followed by a second send *inside* the `try` block (lines 72-77). The first send has no error handling and will crash the application on any SMTP failure. If the first send succeeds, the second send also executes, **sending the email twice** to the recipient.

```python
# Line 68-71: UNPROTECTED send (no try/except)
with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
    server.starttls()
    server.login(config.smtp_user, config.smtp_password)
    server.send_message(message)

# Line 72-77: SECOND send inside try/except (duplicate)
try:
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as server:
        server.starttls()
        server.login(config.smtp_user, config.smtp_password)
        server.send_message(message)
```

**Impact:** Duplicate invoices/documents sent to customers; unhandled exceptions crash the application and may leak stack traces.
**Recommendation:** Remove the unprotected send block (lines 68-71). Keep only the try/except version.

---

### C2. Dead-code bug: superfaktura_client.py returns before error handling

**File:** `superfaktura_client.py:64-70`
**Description:** The `send_invoice` method makes the API call and returns on line 70, **before** the `try/except` block that contains proper error handling (lines 72-98). The error handling is dead code and never executes.

```python
# Line 64-70: Executes and returns
response = requests.post(url, auth=(...), json=payload, timeout=30)
return response.status_code in {200, 201}

# Line 72-98: DEAD CODE - never reached
try:
    response = requests.post(...)
    response.raise_for_status()
```

**Impact:** Network errors, timeouts, and HTTP errors propagate as unhandled exceptions instead of being caught as `SuperFakturaError`. This can crash the application and leak internal details in error responses.
**Recommendation:** Remove the bare request/return block (lines 64-70). Keep only the try/except version.

---

### C3. Default admin credentials with no forced password change

**File:** `app.py:1137-1145`
**Description:** The `ensure_admin_user()` function creates an admin user with `username="admin"` and `password="admin"`. There is no mechanism to force a password change on first login.

```python
admin = User(
    username="admin",
    password_hash=generate_password_hash("admin"),
    role="admin",
)
```

**Impact:** Any attacker who discovers the application can log in with full admin access using `admin/admin`.
**Recommendation:**
1. Generate a random password on first run and print it to the console/log once.
2. Implement a forced password change mechanism on first login.
3. Add a `must_change_password` flag to the User model.

---

## HIGH Findings

### H1. No rate limiting on login endpoint

**File:** `app.py:500-511`
**Description:** The `/login` route has no rate limiting or account lockout mechanism. An attacker can make unlimited login attempts for brute-force attacks.

**Impact:** Credential brute-force attacks are trivially possible.
**Recommendation:** Implement login rate limiting using `Flask-Limiter` (e.g., 5 attempts per minute per IP). Add an account lockout after N failed attempts with exponential backoff.

---

### H2. SESSION_COOKIE_SECURE not set

**File:** `app.py:429-432`
**Description:** The session cookie configuration sets `HTTPONLY` and `SAMESITE` but omits `SESSION_COOKIE_SECURE = True`. Without this, session cookies are transmitted over unencrypted HTTP connections.

```python
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Missing: app.config["SESSION_COOKIE_SECURE"] = True
```

**Impact:** Session hijacking via network sniffing on non-HTTPS connections.
**Recommendation:** Set `SESSION_COOKIE_SECURE = True` in production. Use an environment variable toggle for local development.

---

### H3. No security headers

**File:** `app.py` (global)
**Description:** The application does not set any HTTP security headers:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Content-Security-Policy`
- `Strict-Transport-Security` (HSTS)
- `Referrer-Policy`
- `Permissions-Policy`

**Impact:** Vulnerable to clickjacking (iframe embedding), MIME-type sniffing attacks, and missing defense-in-depth against XSS.
**Recommendation:** Add a `@app.after_request` handler that sets security headers on every response. Consider using the `flask-talisman` package.

---

### H4. Float type used for financial calculations

**File:** `app.py` (all monetary columns)
**Description:** All monetary fields use `db.Float` (IEEE 754 floating point):
- `Product.price`, `OrderItem.unit_price`, `DeliveryItem.unit_price`, `DeliveryItem.line_total`
- `Invoice.total`, `InvoiceItem.unit_price`, `InvoiceItem.total`
- `Partner.discount_percent`, `Bundle.bundle_price`

```python
price = db.Column(db.Float, nullable=False)  # Imprecise for money
```

**Impact:** Floating-point rounding errors accumulate across invoices. Example: `0.1 + 0.2 = 0.30000000000000004` in IEEE 754. This leads to incorrect invoice totals, which can have legal/tax compliance consequences for Slovak business documents.
**Recommendation:** Replace `db.Float` with `db.Numeric(precision=10, scale=2)` for all monetary columns. Use Python's `decimal.Decimal` for all financial arithmetic.

---

### H5. No session regeneration on login (session fixation)

**File:** `app.py:500-511`
**Description:** On successful login, the session ID is not regenerated. The existing session simply gets `user_id` added to it.

```python
if user and check_password_hash(user.password_hash, password):
    session["user_id"] = user.id  # Session ID not regenerated
```

**Impact:** Session fixation attacks: an attacker who can set a victim's session cookie before login can hijack the session after the victim authenticates.
**Recommendation:** Clear and regenerate the session before setting the user ID:
```python
session.clear()
session["user_id"] = user.id
session.permanent = True
```

---

### H6. Logout via GET request (CSRF logout)

**File:** `app.py:513-516`
**Description:** The `/logout` endpoint uses the GET method, and it is not CSRF-protected (CSRF only protects POST by default in Flask-WTF).

```python
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))
```

**Impact:** An attacker can force-logout users by embedding `<img src="/logout">` in any page the user visits.
**Recommendation:** Change logout to POST method with CSRF token. Update the navigation template accordingly.

---

### H7. No IDOR protection on resource access

**File:** `app.py:975-982, 1044-1051`
**Description:** PDF download routes (`/delivery-notes/<id>/pdf`, `/invoices/<id>/pdf`) only check that a user is logged in (`require_login()`), not whether the user should have access to that specific resource. A customer-role user can download any partner's delivery notes or invoices by guessing/iterating IDs.

Similarly, `add_invoice_item` only checks the `manage_invoices` permission but doesn't verify the invoice belongs to the user's scope.

**Impact:** Unauthorized access to confidential business documents and financial data of other partners.
**Recommendation:** Implement resource-level authorization. For customer-role users, verify the requested resource belongs to their associated partner. Consider adding a `partner_id` field to the User model for customer scoping.

---

## MEDIUM Findings

### M1. No server-side input validation

**File:** `app.py` (all POST routes)
**Description:** The application accepts form inputs with no server-side validation beyond what SQLAlchemy column types enforce:
- No email format validation on partner/contact emails
- No ICO/DIC/IC_DPH format validation (Slovak tax identifiers have specific formats: ICO = 8 digits, DIC = 10 digits starting with country prefix)
- No validation that prices/quantities are non-negative (HTML `min="0"` is easily bypassed)
- No string length enforcement server-side

**Impact:** Invalid data in the database; negative quantities/prices could produce incorrect invoices; invalid tax identifiers could cause Superfaktura export failures.
**Recommendation:** Add server-side validation for all form inputs using WTForms or a validation library. Validate formats, ranges, and required fields before database insertion.

---

### M2. CDN assets loaded without Subresource Integrity (SRI)

**File:** `templates/base.html:8,53` and `templates/login.html:7`
**Description:** Bootstrap CSS and JS are loaded from `cdn.jsdelivr.net` without SRI hashes:

```html
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
```

**Impact:** If the CDN is compromised, malicious code could be injected into every page of the application (supply-chain attack).
**Recommendation:** Add SRI `integrity` and `crossorigin` attributes:
```html
<link href="...bootstrap.min.css" rel="stylesheet"
      integrity="sha384-..." crossorigin="anonymous">
```

---

### M3. Default host binding `0.0.0.0` exposes application to all interfaces

**File:** `app.py:1269`
**Description:** The default `FLASK_HOST` is `0.0.0.0`, which binds to all network interfaces.

```python
host = os.environ.get("FLASK_HOST", "0.0.0.0")
```

**Impact:** The application is accessible from any network interface on the host machine, not just localhost.
**Recommendation:** Default to `127.0.0.1` for development. Use `0.0.0.0` only explicitly in production behind a reverse proxy.

---

### M4. Secret key `"change-me"` committed in config.yaml

**File:** `config.yaml:3`
**Description:** The config file committed to the repository contains `secret_key: "change-me"`. While the application code detects this and auto-generates a random key, the auto-generated key changes on every restart, invalidating all active sessions.

**Impact:** Users are logged out on every application restart. If someone deploys without changing the key, sessions are unstable. If the `"change-me"` detection is bypassed (e.g., someone sets it to `"change-me-please"`), a weak known secret would be used.
**Recommendation:** Remove the secret_key from `config.yaml` entirely. Document that it must be set via the `APP_SECRET_KEY` environment variable. Add a startup check that refuses to start without a proper secret key in production mode.

---

### M5. No HTTPS enforcement or HSTS

**File:** `app.py` (global)
**Description:** The application runs on plain HTTP with no mechanism to enforce HTTPS or set HSTS headers.

**Impact:** All traffic including credentials and session cookies is transmitted in plaintext.
**Recommendation:** In production, deploy behind a TLS-terminating reverse proxy (nginx/caddy). Add HSTS header. Consider using `flask-talisman` for automatic HTTPS enforcement.

---

### M6. Flash messages expose exception details to users

**File:** `app.py:1000, 1075`
**Description:** Exception messages are shown directly to users via flash:

```python
flash(str(e), "danger")                           # line 1000
flash(f"Chyba pri odosielaní emailu: {e}", "danger")  # line 1075
```

**Impact:** Internal error details (stack traces, file paths, database errors) may be leaked to end users, aiding attackers in reconnaissance.
**Recommendation:** Show generic error messages to users. Log the detailed exception server-side.

---

### M7. Predictable PDF filenames and output directory

**File:** `app.py:1195, 1236`
**Description:** PDF files are stored with predictable names like `output/delivery_1.pdf`, `output/invoice_1.pdf`. Files persist on disk after generation.

**Impact:** If the `output/` directory is accidentally served by a misconfigured web server, all generated PDFs become publicly accessible. Old PDFs accumulate on disk without cleanup.
**Recommendation:**
1. Use temporary files or add random tokens to filenames.
2. Delete PDFs after sending the response (use `send_file` with a temp file, then cleanup).
3. Ensure the output directory is never served statically.

---

### M8. No password complexity or change mechanism

**File:** `app.py` (User model, login route)
**Description:** The application has no:
- Password complexity requirements
- Password change functionality
- Password reset mechanism
- Password expiration policy

**Impact:** Users can have trivially weak passwords. There's no recovery mechanism if a password is forgotten.
**Recommendation:** Add a password change route. Enforce minimum password length (e.g., 12 characters). Consider implementing password strength validation.

---

## LOW Findings

### L1. Duplicate imports in mailer.py and superfaktura_client.py

**File:** `mailer.py:1-4,7-11` and `superfaktura_client.py:1-3,6-8`
**Description:** Both files have duplicate import statements, indicating copy-paste or merge errors.

**Impact:** Code quality issue; no direct security impact but suggests insufficient code review.
**Recommendation:** Remove duplicate imports.

---

### L2. Log injection potential

**File:** `app.py:73, 84, 1113, 1123, 1133`
**Description:** User-controlled values are interpolated into log messages:

```python
logger.warning(f"Could not convert '{value}' to int, using default {default}")
logger.warning(f"Could not parse date: '{raw}'")
```

**Impact:** An attacker could inject misleading log entries (log forging) by supplying crafted input values containing newlines or log formatting characters.
**Recommendation:** Sanitize values before logging (strip newlines, limit length) or use structured logging.

---

### L3. No `Referrer-Policy` header

**File:** `app.py` (global)
**Description:** Missing `Referrer-Policy` header. Internal URLs may leak in the `Referer` header when navigating to external resources.

**Impact:** Internal URL paths could be exposed to the Superfaktura API or CDN.
**Recommendation:** Set `Referrer-Policy: strict-origin-when-cross-origin` or `no-referrer`.

---

### L4. `CONFIG_PATH` environment variable allows arbitrary file read

**File:** `app.py:360`
**Description:** The `CONFIG_PATH` environment variable determines which YAML file is loaded:

```python
config_path = os.environ.get("CONFIG_PATH", "config.yaml")
```

**Impact:** If an attacker can control environment variables, they could point the config loader at arbitrary YAML files. Low severity because environment variable control implies broader system compromise.
**Recommendation:** Validate that `CONFIG_PATH` resolves to a file within the application directory.

---

### L5. No `autocomplete="off"` on login form password field

**File:** `templates/login.html:33`
**Description:** The password input field doesn't disable browser autocomplete:

```html
<input class="form-control" type="password" name="password" required>
```

**Impact:** Browsers may cache credentials on shared/public computers.
**Recommendation:** Add `autocomplete="current-password"` to guide browser password managers correctly.

---

### L6. `pytest` included in production dependencies

**File:** `requirements.txt:10`
**Description:** `pytest==8.3.4` is listed in the main `requirements.txt` rather than a separate `requirements-dev.txt`.

**Impact:** Unnecessary test dependencies installed in production, increasing the attack surface.
**Recommendation:** Split into `requirements.txt` (production) and `requirements-dev.txt` (development/testing).

---

### L7. No `robots.txt` or `X-Robots-Tag`

**File:** Application-wide
**Description:** No mechanism to prevent search engine indexing of the application.

**Impact:** If exposed to the internet, login pages and application structure could be indexed by search engines.
**Recommendation:** Add a `robots.txt` route that disallows all crawling, and set `X-Robots-Tag: noindex, nofollow` header.

---

## Positive Security Observations

The following security practices are already well-implemented:

1. **CSRF protection** via Flask-WTF with proper token inclusion in templates
2. **Password hashing** using Werkzeug's `generate_password_hash`/`check_password_hash` (PBKDF2)
3. **Session cookie hardening** (`HTTPONLY`, `SAMESITE=Lax`, 8-hour lifetime)
4. **SQLAlchemy ORM** prevents SQL injection by design (parameterized queries)
5. **Jinja2 auto-escaping** prevents XSS in templates (all `{{ }}` expressions are escaped)
6. **`.env` excluded from git** via `.gitignore`
7. **Auto-generated secret key** when none is configured
8. **SQLite foreign key enforcement** explicitly enabled
9. **Role-based access control** with permission checks on routes
10. **Audit logging** for key business actions
11. **`yaml.safe_load()`** used instead of `yaml.load()` (prevents YAML deserialization attacks)
12. **Timeout on external requests** (30s for Superfaktura and SMTP)

---

## Recommended Action Plan (Priority Order)

### Immediate (before deployment)

1. **Fix C1** - Remove duplicate email send in `mailer.py`
2. **Fix C2** - Remove dead code in `superfaktura_client.py`
3. **Fix C3** - Generate random admin password on first run
4. **Fix H2** - Add `SESSION_COOKIE_SECURE` for production
5. **Fix H5** - Regenerate session on login
6. **Fix M4** - Remove secret key from `config.yaml`

### Short-term (first release)

7. **Fix H1** - Add login rate limiting (e.g., `Flask-Limiter`)
8. **Fix H3** - Add security headers (`@app.after_request` or `flask-talisman`)
9. **Fix H4** - Migrate monetary columns from Float to Numeric/Decimal
10. **Fix H6** - Change logout to POST method
11. **Fix H7** - Add resource-level authorization for PDF/invoice access
12. **Fix M1** - Add server-side input validation
13. **Fix M2** - Add SRI hashes to CDN assets

### Medium-term (hardening)

14. **Fix M3** - Default host to 127.0.0.1
15. **Fix M5** - Document HTTPS deployment requirements
16. **Fix M6** - Sanitize error messages shown to users
17. **Fix M7** - Use temp files for PDFs
18. **Fix M8** - Add password change/complexity features
19. **Fix L1-L7** - Address low-severity items

---

## Appendix: Files Reviewed

| File | Lines | Status |
|------|-------|--------|
| app.py | 1273 | Reviewed |
| config.yaml | 25 | Reviewed |
| config_models.py | 30 | Reviewed |
| mailer.py | 100 | Reviewed |
| superfaktura_client.py | 99 | Reviewed |
| .env.example | 32 | Reviewed |
| .gitignore | 8 | Reviewed |
| requirements.txt | 10 | Reviewed |
| templates/base.html | 67 | Reviewed |
| templates/login.html | 44 | Reviewed |
| templates/index.html | 42 | Reviewed |
| templates/partners.html | 144 | Reviewed |
| templates/products.html | 58 | Reviewed |
| templates/bundles.html | 59 | Reviewed |
| templates/orders.html | 170 | Reviewed |
| templates/delivery_notes.html | 131 | Reviewed |
| templates/invoices.html | 110 | Reviewed |
| templates/vehicles.html | 70 | Reviewed |
| templates/logistics.html | 89 | Reviewed |
| templates/error.html | 11 | Reviewed |
