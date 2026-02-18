"""Admin routes — user management (Feature F1)."""

import json
from urllib.parse import urlparse

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from utils import utc_now


def _safe_referrer(fallback: str) -> str:
    """Return request.referrer only if it is same-origin, else fallback."""
    ref = request.referrer
    if ref:
        parsed = urlparse(ref)
        if parsed.netloc == "" or parsed.netloc == request.host:
            return ref
    return fallback

from flask import abort

from extensions import db
from models import (
    AppSetting,
    DeliveryNote,
    Invoice,
    NumberingConfig,
    Order,
    Payment,
    PdfTemplate,
    Tenant,
    TenantSubscription,
    UserTenant,
    VALID_ROLES,
    User,
)
from services.pdf import get_default_css, get_default_html
from services.audit import log_action
from services.auth import get_current_user, role_required
from services.tenant import get_current_tenant_id, tenant_query, stamp_tenant, tenant_get_or_404


def _get_tenant_user_or_404(user_id: int) -> User:
    """Fetch a user by PK, verifying they belong to the current tenant."""
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    caller = get_current_user()
    if caller and caller.is_superadmin:
        return user
    tid = get_current_tenant_id()
    if not tid:
        abort(403)
    membership = UserTenant.query.filter_by(user_id=user_id, tenant_id=tid).first()
    if not membership:
        abort(404)
    return user

_ENTITY_TYPES = ["product", "bundle", "order", "delivery_note", "invoice"]

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/users", methods=["GET", "POST"])
@role_required("manage_all")
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "operator")

        if not username:
            flash("Meno používateľa je povinné.", "danger")
            return redirect(url_for("admin.users"))
        from routes.auth import _validate_password
        pw_error = _validate_password(password)
        if pw_error:
            flash(pw_error, "danger")
            return redirect(url_for("admin.users"))
        if role not in VALID_ROLES:
            flash("Neplatná rola.", "danger")
            return redirect(url_for("admin.users"))
        if User.query.filter_by(username=username).first():
            flash("Používateľ s týmto menom už existuje.", "danger")
            return redirect(url_for("admin.users"))

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            role=role,
            must_change_password=True,
        )
        db.session.add(user)
        db.session.flush()
        # Link new user to the current tenant
        from services.tenant import get_current_tenant_id
        tid = get_current_tenant_id()
        if tid:
            ut = UserTenant(user_id=user.id, tenant_id=tid)
            db.session.add(ut)
        log_action("create", "user", user.id, f"role={role}")
        db.session.commit()
        flash(f"Používateľ '{username}' vytvorený.", "success")
        return redirect(url_for("admin.users"))

    # Scope users to current tenant (superadmins see all)
    caller = get_current_user()
    if caller and caller.is_superadmin:
        all_users = User.query.order_by(User.id).all()
        active_count = User.query.filter_by(is_active=True).count()
        role_count = db.session.query(User.role).distinct().count()
    else:
        tid = get_current_tenant_id()
        tenant_user_ids = [
            ut.user_id for ut in UserTenant.query.filter_by(tenant_id=tid).all()
        ]
        all_users = User.query.filter(User.id.in_(tenant_user_ids)).order_by(User.id).all()
        active_count = User.query.filter(User.id.in_(tenant_user_ids), User.is_active.is_(True)).count()
        role_count = db.session.query(User.role).filter(User.id.in_(tenant_user_ids)).distinct().count()
    return render_template(
        "admin/users.html",
        users=all_users,
        valid_roles=VALID_ROLES,
        active_user_count=active_count,
        role_count=role_count,
    )


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@role_required("manage_all")
def toggle_user(user_id: int):
    user = _get_tenant_user_or_404(user_id)
    user.is_active = not user.is_active
    action = "activate" if user.is_active else "deactivate"
    log_action(action, "user", user.id, f"is_active={user.is_active}")
    db.session.commit()
    status = "aktivovaný" if user.is_active else "deaktivovaný"
    flash(f"Používateľ '{user.username}' {status}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route(
    "/users/<int:user_id>/reset-password", methods=["POST"]
)
@role_required("manage_all")
def reset_password(user_id: int):
    user = _get_tenant_user_or_404(user_id)
    new_password = request.form.get("new_password", "")
    if len(new_password) < 8:
        flash("Heslo musí mať aspoň 8 znakov.", "danger")
        return redirect(url_for("admin.users"))
    import re
    if not (re.search(r"[A-Z]", new_password) and re.search(r"[a-z]", new_password) and re.search(r"\d", new_password)):
        flash("Heslo musí obsahovať veľké, malé písmeno a číslicu.", "danger")
        return redirect(url_for("admin.users"))
    user.password_hash = generate_password_hash(new_password)
    user.must_change_password = True
    user.password_changed_at = utc_now()
    log_action("reset_password", "user", user.id, "password reset by admin")
    db.session.commit()
    flash(f"Heslo pre '{user.username}' bolo resetované.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/unlock/order/<int:order_id>", methods=["POST"])
@role_required("manage_all")
def unlock_order(order_id: int):
    order = tenant_get_or_404(Order, order_id)
    order.is_locked = False
    log_action("unlock", "order", order.id, "unlocked by admin")
    db.session.commit()
    flash(f"Objednávka #{order.id} odomknutá.", "success")
    return redirect(_safe_referrer(url_for("orders.list_orders")))


@admin_bp.route("/unlock/delivery/<int:delivery_id>", methods=["POST"])
@role_required("manage_all")
def unlock_delivery(delivery_id: int):
    delivery = tenant_get_or_404(DeliveryNote, delivery_id)
    delivery.is_locked = False
    log_action("unlock", "delivery_note", delivery.id, "unlocked by admin")
    db.session.commit()
    flash(f"Dodací list #{delivery.id} odomknutý.", "success")
    return redirect(_safe_referrer(url_for("delivery.list_delivery_notes")))


@admin_bp.route("/unlock/invoice/<int:invoice_id>", methods=["POST"])
@role_required("manage_all")
def unlock_invoice(invoice_id: int):
    invoice = tenant_get_or_404(Invoice, invoice_id)
    invoice.is_locked = False
    log_action("unlock", "invoice", invoice.id, "unlocked by admin")
    db.session.commit()
    flash(f"Faktúra #{invoice.id} odomknutá.", "success")
    return redirect(_safe_referrer(url_for("invoices.list_invoices")))


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

def _get_setting(key: str, default: str = "") -> str:
    row = tenant_query(AppSetting).filter_by(key=key).first()
    return row.value if row and row.value else default


def _set_setting(key: str, value: str):
    row = tenant_query(AppSetting).filter_by(key=key).first()
    if not row:
        row = AppSetting(key=key, value=value)
        stamp_tenant(row)
        db.session.add(row)
    else:
        row.value = value


@admin_bp.route("/settings", methods=["GET", "POST"])
@role_required("manage_all")
def settings():
    from flask import current_app
    from services.tenant import get_current_tenant
    import os
    from werkzeug.utils import secure_filename

    if request.method == "POST":
        # Tenant company data
        tenant = get_current_tenant()
        if tenant:
            tenant.name = request.form.get("tenant_name", "").strip() or tenant.name
            tenant.ico = request.form.get("tenant_ico", "").strip()
            tenant.dic = request.form.get("tenant_dic", "").strip()
            tenant.ic_dph = request.form.get("tenant_ic_dph", "").strip()
            tenant.street = request.form.get("tenant_street", "").strip()
            tenant.city = request.form.get("tenant_city", "").strip()
            tenant.postal_code = request.form.get("tenant_postal_code", "").strip()
            tenant.email = request.form.get("tenant_email", "").strip()
            tenant.phone = request.form.get("tenant_phone", "").strip()
            tenant.billing_email = request.form.get("tenant_billing_email", "").strip()

            # Logo upload
            logo_file = request.files.get("tenant_logo")
            if logo_file and logo_file.filename:
                filename = secure_filename(logo_file.filename)
                ext = os.path.splitext(filename)[1].lower()
                if ext in (".png", ".jpg", ".jpeg", ".svg", ".webp"):
                    logo_name = f"tenant_{tenant.id}_{filename}"
                    logo_path = os.path.join(
                        current_app.config["UPLOAD_FOLDER"], "logos", logo_name
                    )
                    logo_file.save(logo_path)
                    tenant.logo_filename = logo_name
                else:
                    flash("Neplatný formát loga (povolené: PNG, JPG, SVG, WebP).", "warning")

        # General
        _set_setting("site_name", request.form.get("site_name", "").strip())

        # Password policy
        _set_setting(
            "password_expiry_value",
            request.form.get("password_expiry_value", "0").strip(),
        )
        _set_setting(
            "password_expiry_unit",
            request.form.get("password_expiry_unit", "days").strip(),
        )

        # Payment gateway (stored as global setting with tenant_id=NULL)
        gateway_val = request.form.get("payment_gateway", "gopay").strip()
        if gateway_val in ("gopay", "stripe", "manual"):
            from models import AppSetting as _AS
            gw_row = _AS.query.filter_by(tenant_id=None, key="payment_gateway").first()
            if not gw_row:
                gw_row = _AS(tenant_id=None, key="payment_gateway", value=gateway_val)
                db.session.add(gw_row)
            else:
                gw_row.value = gateway_val

        # Invoice payment settings (per-tenant)
        for key in ("invoice_payment_gateway", "invoice_bank_iban",
                     "invoice_bank_swift", "invoice_bank_name"):
            _set_setting(key, request.form.get(key, "").strip())

        # DPH settings (per-tenant)
        _set_setting("default_vat_rate", request.form.get("default_vat_rate", "20").strip())
        _set_setting("auto_check_vat", "true" if request.form.get("auto_check_vat") else "false")
        _set_setting("fs_opendata_api_key", request.form.get("fs_opendata_api_key", "").strip())
        _set_setting("show_vat_reg_type", "true" if request.form.get("show_vat_reg_type") else "false")

        # SMTP / E-mail settings (per-tenant)
        _set_setting("smtp_host", request.form.get("smtp_host", "").strip())
        _set_setting("smtp_port", request.form.get("smtp_port", "587").strip())
        _set_setting("smtp_username", request.form.get("smtp_username", "").strip())
        # Only update password if non-empty (preserve existing value)
        smtp_pw = request.form.get("smtp_password", "").strip()
        if smtp_pw:
            _set_setting("smtp_password", smtp_pw)
        _set_setting("smtp_sender_email", request.form.get("smtp_sender_email", "").strip())
        _set_setting("smtp_sender_name", request.form.get("smtp_sender_name", "").strip())
        _set_setting("smtp_use_tls", "true" if request.form.get("smtp_use_tls") else "false")

        # Numbering configs (tag-based patterns)
        for etype in _ENTITY_TYPES:
            config = tenant_query(NumberingConfig).filter_by(entity_type=etype).first()
            if not config:
                config = NumberingConfig(entity_type=etype)
                stamp_tenant(config)
                db.session.add(config)
            config.pattern = request.form.get(f"num_{etype}_pattern", "").strip()

        log_action("update", "settings", 0, "settings updated")
        db.session.commit()
        flash("Nastavenia uložené.", "success")
        active_tab = request.form.get("_active_tab", "firma")
        return redirect(url_for("admin.settings", tab=active_tab))

    # GET — load current values
    tenant = get_current_tenant()

    numbering = {}
    for etype in _ENTITY_TYPES:
        config = tenant_query(NumberingConfig).filter_by(entity_type=etype).first()
        numbering[etype] = config

    # Load global payment gateway setting
    from models import AppSetting as _AS
    gw_row = _AS.query.filter_by(tenant_id=None, key="payment_gateway").first()
    payment_gateway = gw_row.value if gw_row and gw_row.value else "gopay"

    active_tab = request.args.get("tab", "firma")

    return render_template(
        "admin/settings.html",
        tenant=tenant,
        site_name=_get_setting("site_name", "ObDoFa"),
        password_expiry_value=_get_setting("password_expiry_value", "0"),
        password_expiry_unit=_get_setting("password_expiry_unit", "days"),
        numbering=numbering,
        entity_types=_ENTITY_TYPES,
        payment_gateway=payment_gateway,
        invoice_payment_gateway=_get_setting("invoice_payment_gateway", "bank_transfer"),
        invoice_bank_iban=_get_setting("invoice_bank_iban"),
        invoice_bank_swift=_get_setting("invoice_bank_swift"),
        invoice_bank_name=_get_setting("invoice_bank_name"),
        # DPH settings
        default_vat_rate=_get_setting("default_vat_rate", "20"),
        auto_check_vat=_get_setting("auto_check_vat", "false"),
        fs_opendata_api_key=_get_setting("fs_opendata_api_key"),
        show_vat_reg_type=_get_setting("show_vat_reg_type", "false"),
        # SMTP settings
        smtp_host=_get_setting("smtp_host"),
        smtp_port=_get_setting("smtp_port", "587"),
        smtp_username=_get_setting("smtp_username"),
        smtp_password=_get_setting("smtp_password"),
        smtp_sender_email=_get_setting("smtp_sender_email"),
        smtp_sender_name=_get_setting("smtp_sender_name"),
        smtp_use_tls=_get_setting("smtp_use_tls", "true"),
        # Tab state
        active_tab=active_tab,
    )


@admin_bp.route("/settings/test-fs-api", methods=["POST"])
@role_required("manage_all")
def test_fs_api():
    """AJAX: Test FS OpenData API key by querying ds_dphs."""
    import requests as http_requests
    data = request.get_json(silent=True) or {}
    api_key = data.get("api_key", "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "API kluc je prazdny."})
    try:
        resp = http_requests.get(
            "https://iz.opendata.financnasprava.sk/api/data/ds_dphs/search",
            params={"column": "ic_dph", "search": "SK2020317068", "page": 1},
            headers={"key": api_key},
            timeout=8,
        )
        if resp.status_code == 200:
            result = resp.json()
            count = len(result.get("data") or [])
            return jsonify({"ok": True, "message": f"API kluc je platny. Najdenych {count} zaznamov."})
        elif resp.status_code == 401:
            return jsonify({"ok": False, "error": "API kluc je neplatny alebo expiroval (401)."})
        else:
            return jsonify({"ok": False, "error": f"Neocakavany HTTP status: {resp.status_code}"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Chyba pripojenia: {e}"})


@admin_bp.route("/settings/test-email", methods=["POST"])
@role_required("manage_all")
def test_email():
    """AJAX: Send a test email via provided SMTP settings."""
    import smtplib
    from email.mime.text import MIMEText

    data = request.get_json(silent=True) or {}
    host = data.get("smtp_host", "").strip()
    port = int(data.get("smtp_port", 587))
    username = data.get("smtp_username", "").strip()
    password = data.get("smtp_password", "").strip()
    sender_email = data.get("smtp_sender_email", "").strip()
    sender_name = data.get("smtp_sender_name", "").strip()
    use_tls = data.get("smtp_use_tls", True)
    recipient = data.get("recipient", "").strip() or sender_email

    if not host or not sender_email:
        return jsonify({"ok": False, "error": "SMTP host a odosielatel su povinne."})

    msg = MIMEText("Toto je testovaci e-mail z aplikacie ObDoFa.", "plain", "utf-8")
    msg["Subject"] = "ObDoFa — Test SMTP"
    msg["From"] = f"{sender_name} <{sender_email}>" if sender_name else sender_email
    msg["To"] = recipient

    try:
        server = smtplib.SMTP(host, port, timeout=10)
        if use_tls:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.sendmail(sender_email, [recipient], msg.as_string())
        server.quit()
        return jsonify({"ok": True, "message": f"Testovaci e-mail odoslany na {recipient}."})
    except Exception as e:
        return jsonify({"ok": False, "error": f"SMTP chyba: {e}"})


@admin_bp.route(
    "/users/<int:user_id>/force-password-change", methods=["POST"]
)
@role_required("manage_all")
def force_password_change(user_id: int):
    user = _get_tenant_user_or_404(user_id)
    user.must_change_password = True
    log_action("force_password_change", "user", user.id, "forced by admin")
    db.session.commit()
    flash(
        f"Používateľ '{user.username}' musí zmeniť heslo pri ďalšom prihlásení.",
        "success",
    )
    return redirect(url_for("admin.users"))


# ------------------------------------------------------------------
# Bulk Refresh Partners from Registers
# ------------------------------------------------------------------

@admin_bp.route("/refresh-partners", methods=["GET", "POST"])
@role_required("manage_all")
def refresh_partners():
    from models import Partner
    if request.method == "POST":
        # Apply selected updates
        partner_ids = request.form.getlist("partner_id")
        fields = request.form.getlist("field")
        values = request.form.getlist("new_value")
        updated = 0
        for pid, field, value in zip(partner_ids, fields, values):
            try:
                p = tenant_get_or_404(Partner, int(pid))
                if field in ("name", "street", "city", "postal_code", "dic", "ic_dph", "street_number"):
                    setattr(p, field, value)
                    updated += 1
            except Exception:
                continue
        if updated:
            log_action("bulk_update", "partner", 0, f"updated {updated} fields")
            db.session.commit()
            flash(f"Aktualizovaných {updated} polí.", "success")
        else:
            flash("Žiadne zmeny na aplikovanie.", "info")
        return redirect(url_for("admin.settings"))

    # GET — check all partners against registers
    from services.company_lookup import lookup_by_ico
    partners = tenant_query(Partner).filter(
        Partner.is_deleted.is_(False),
        Partner.ico.isnot(None),
        Partner.ico != "",
    ).all()
    changes = []
    for p in partners:
        result = lookup_by_ico(p.ico)
        if not result:
            continue
        field_map = {
            "name": "name", "street": "street", "street_number": "street_number",
            "city": "city", "postal_code": "postal_code", "dic": "dic", "ic_dph": "ic_dph",
        }
        for reg_field, db_field in field_map.items():
            old = getattr(p, db_field, "") or ""
            new = result.get(reg_field, "") or ""
            if new and new != old:
                changes.append({
                    "partner_id": p.id,
                    "partner_name": p.name,
                    "field": db_field,
                    "old_value": old,
                    "new_value": new,
                })
    return render_template("admin/refresh_partners.html", changes=changes)


# ------------------------------------------------------------------
# Numbering Counter Reset
# ------------------------------------------------------------------

@admin_bp.route("/settings/reset-counter/<entity_type>", methods=["POST"])
@role_required("manage_all")
def reset_counter(entity_type: str):
    from models import NumberSequence
    if entity_type not in _ENTITY_TYPES:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "Neplatný typ entity."})
        flash("Neplatný typ entity.", "danger")
        return redirect(url_for("admin.settings"))
    seqs = tenant_query(NumberSequence).filter_by(entity_type=entity_type).all()
    for seq in seqs:
        seq.last_value = 0
    log_action("reset_counter", "numbering", 0, f"entity_type={entity_type}")
    db.session.commit()
    labels = {"product": "Produkty", "bundle": "Kombinácie", "order": "Objednávky",
              "delivery_note": "Dodacie listy", "invoice": "Faktúry"}
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    flash(f"Počítadlo pre '{labels.get(entity_type, entity_type)}' resetované.", "success")
    return redirect(url_for("admin.settings"))


# ------------------------------------------------------------------
# PDF Templates
# ------------------------------------------------------------------

_PDF_ENTITY_TYPES = ["delivery_note", "invoice"]
_PDF_LABELS = {"delivery_note": "Dodací list", "invoice": "Faktúra"}


@admin_bp.route("/pdf-templates", methods=["GET", "POST"])
@role_required("manage_all")
def pdf_templates():
    if request.method == "POST":
        for etype in _PDF_ENTITY_TYPES:
            tmpl = tenant_query(PdfTemplate).filter_by(entity_type=etype).first()
            if not tmpl:
                tmpl = PdfTemplate(entity_type=etype)
                stamp_tenant(tmpl)
                db.session.add(tmpl)
            tmpl.html_content = request.form.get(f"html_{etype}", "")
            tmpl.css_content = request.form.get(f"css_{etype}", "")
        log_action("update", "pdf_template", 0, "templates updated")
        db.session.commit()
        flash("PDF šablóny uložené.", "success")
        return redirect(url_for("admin.pdf_templates"))

    templates = {}
    for etype in _PDF_ENTITY_TYPES:
        tmpl = tenant_query(PdfTemplate).filter_by(entity_type=etype).first()
        templates[etype] = {
            "html": tmpl.html_content if tmpl and tmpl.html_content else get_default_html(etype),
            "css": tmpl.css_content if tmpl and tmpl.css_content else get_default_css(),
        }
    return render_template(
        "admin/pdf_templates.html",
        templates=templates,
        entity_types=_PDF_ENTITY_TYPES,
        labels=_PDF_LABELS,
    )


# ------------------------------------------------------------------
# PDF Layout Visual Editor
# ------------------------------------------------------------------

_DEFAULT_LAYOUT_CONFIG = {
    "header": {"show_logo": True, "logo_position": "left", "show_company_info": True},
    "columns": ["item_name", "quantity", "unit_price", "total"],
    "footer": {"show_qr_code": True, "show_bank_details": True, "show_notes": True},
    "fonts": {"heading": "Space Grotesk", "body": "Inter"},
    "colors": {"primary": "#1a1a2e", "accent": "#e94560"},
    "margins": {"top": 20, "bottom": 20, "left": 15, "right": 15},
    "paper": "A4",
}


def _load_layout_config(entity_type: str) -> dict:
    """Load layout_config JSON from DB for the current tenant, or return defaults."""
    tmpl = tenant_query(PdfTemplate).filter_by(entity_type=entity_type).first()
    if tmpl and tmpl.layout_config:
        try:
            return json.loads(tmpl.layout_config)
        except (ValueError, TypeError):
            pass
    return dict(_DEFAULT_LAYOUT_CONFIG)


@admin_bp.route("/pdf-templates/editor", methods=["GET", "POST"])
@role_required("manage_all")
def pdf_layout_editor():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        entity_type = data.get("entity_type", "")
        config = data.get("config", {})

        if entity_type not in _PDF_ENTITY_TYPES:
            return jsonify({"ok": False, "error": "Neplatny typ entity."}), 400

        tmpl = tenant_query(PdfTemplate).filter_by(entity_type=entity_type).first()
        if not tmpl:
            tmpl = PdfTemplate(entity_type=entity_type)
            stamp_tenant(tmpl)
            db.session.add(tmpl)

        try:
            tmpl.layout_config = json.dumps(config, ensure_ascii=False)
        except (ValueError, TypeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        log_action("update", "pdf_template", 0, f"layout_config updated for {entity_type}")
        db.session.commit()
        return jsonify({"ok": True})

    # GET — load saved configs for both entity types
    configs = {etype: _load_layout_config(etype) for etype in _PDF_ENTITY_TYPES}
    return render_template(
        "admin/pdf_layout_editor.html",
        configs=configs,
        entity_types=_PDF_ENTITY_TYPES,
        labels=_PDF_LABELS,
    )


@admin_bp.route("/pdf-templates/preview", methods=["POST"])
@role_required("manage_all")
def pdf_layout_preview():
    from services.pdf import render_layout_preview

    data = request.get_json(silent=True) or {}
    entity_type = data.get("entity_type", "delivery_note")
    config = data.get("config", {})

    if entity_type not in _PDF_ENTITY_TYPES:
        entity_type = "delivery_note"

    try:
        html_output = render_layout_preview(entity_type, config)
    except Exception as exc:  # pragma: no cover
        from flask import current_app
        current_app.logger.warning("pdf_layout_preview error: %s", exc)
        html_output = f"<p style='color:red'>Chyba generovania nahladu: {exc}</p>"

    from flask import Response
    return Response(html_output, mimetype="text/html")


# ------------------------------------------------------------------
# Superadmin Dashboard
# ------------------------------------------------------------------

@admin_bp.route("/superadmin")
@role_required("manage_all")
def superadmin_dashboard():
    """Global dashboard visible only to superadmins."""
    caller = get_current_user()
    if not caller or not caller.is_superadmin:
        abort(403)

    from sqlalchemy import func

    # Tenant metrics
    total_tenants = Tenant.query.count()
    active_tenants = Tenant.query.filter_by(is_active=True).count()

    # User metrics
    total_users = User.query.count()
    active_users = User.query.filter_by(is_active=True).count()

    # Subscription metrics
    active_subscriptions = TenantSubscription.query.filter_by(status="active").count()
    trial_subscriptions = TenantSubscription.query.filter_by(status="trial").count()

    # Payment metrics
    total_payments = Payment.query.filter_by(status="completed").count()
    monthly_revenue_row = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter_by(status="completed")
        .scalar()
    )
    monthly_revenue = float(monthly_revenue_row or 0)

    # Tenant list with enriched data
    tenants_raw = Tenant.query.order_by(Tenant.created_at.desc()).all()
    tenant_data = {}
    for t in tenants_raw:
        user_count = UserTenant.query.filter_by(tenant_id=t.id).count()
        sub = TenantSubscription.query.filter_by(tenant_id=t.id).first()
        tenant_data[t.id] = {
            "user_count": user_count,
            "plan_name": sub.plan.name if sub and sub.plan else None,
            "sub_status": sub.status if sub else None,
        }

    # Recent payments
    recent_payments = (
        Payment.query.order_by(Payment.created_at.desc()).limit(10).all()
    )

    return render_template(
        "admin/superadmin.html",
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        total_users=total_users,
        active_users=active_users,
        active_subscriptions=active_subscriptions,
        trial_subscriptions=trial_subscriptions,
        monthly_revenue=monthly_revenue,
        total_payments=total_payments,
        tenants=tenants_raw,
        tenant_data=tenant_data,
        recent_payments=recent_payments,
    )


# ------------------------------------------------------------------
# Superadmin Tenant CRUD
# ------------------------------------------------------------------

@admin_bp.route("/superadmin/tenants", methods=["POST"])
@role_required("manage_all")
def superadmin_create_tenant():
    """Create a new tenant + auto-create trial subscription."""
    caller = get_current_user()
    if not caller or not caller.is_superadmin:
        abort(403)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Názov organizácie je povinný.", "danger")
        return redirect(url_for("admin.superadmin_dashboard"))
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if Tenant.query.filter_by(slug=slug).first():
        slug = f"{slug}-{Tenant.query.count() + 1}"
    t = Tenant(name=name, slug=slug, ico=request.form.get("ico", "").strip(),
               dic=request.form.get("dic", "").strip(),
               street=request.form.get("street", "").strip(),
               city=request.form.get("city", "").strip(),
               postal_code=request.form.get("postal_code", "").strip(),
               email=request.form.get("email", "").strip(),
               phone=request.form.get("phone", "").strip(),
               is_active=True)
    db.session.add(t)
    db.session.flush()
    # Temporarily switch tenant context so the flush guard allows
    # writing TenantSubscription for the NEW tenant.
    prev_tenant_id = getattr(g, "_tenant_id", None)
    prev_tenant = getattr(g, "current_tenant", None)
    g._tenant_id = t.id
    g.current_tenant = t
    from services.billing import create_trial_subscription
    create_trial_subscription(t.id)
    log_action("create", "tenant", t.id, f"name={name}")
    db.session.commit()
    # Restore previous tenant context
    g._tenant_id = prev_tenant_id
    g.current_tenant = prev_tenant
    flash(f"Organizácia '{name}' vytvorená.", "success")
    return redirect(url_for("admin.superadmin_dashboard"))


@admin_bp.route("/superadmin/tenants/<int:tenant_id>/edit", methods=["POST"])
@role_required("manage_all")
def superadmin_edit_tenant(tenant_id):
    caller = get_current_user()
    if not caller or not caller.is_superadmin:
        abort(403)
    t = db.session.get(Tenant, tenant_id)
    if not t:
        abort(404)
    t.name = request.form.get("name", "").strip() or t.name
    t.ico = request.form.get("ico", "").strip()
    t.dic = request.form.get("dic", "").strip()
    t.street = request.form.get("street", "").strip()
    t.city = request.form.get("city", "").strip()
    t.postal_code = request.form.get("postal_code", "").strip()
    t.email = request.form.get("email", "").strip()
    t.phone = request.form.get("phone", "").strip()
    log_action("edit", "tenant", t.id, "edited by superadmin")
    db.session.commit()
    flash(f"Organizácia '{t.name}' upravená.", "success")
    return redirect(url_for("admin.superadmin_dashboard"))


@admin_bp.route("/superadmin/tenants/<int:tenant_id>/toggle", methods=["POST"])
@role_required("manage_all")
def superadmin_toggle_tenant(tenant_id):
    caller = get_current_user()
    if not caller or not caller.is_superadmin:
        abort(403)
    t = db.session.get(Tenant, tenant_id)
    if not t:
        abort(404)
    t.is_active = not t.is_active
    action = "activate" if t.is_active else "deactivate"
    log_action(action, "tenant", t.id, f"is_active={t.is_active}")
    db.session.commit()
    status = "aktivovaná" if t.is_active else "deaktivovaná"
    flash(f"Organizácia '{t.name}' {status}.", "success")
    return redirect(url_for("admin.superadmin_dashboard"))


@admin_bp.route("/superadmin/tenants/<int:tenant_id>/detail")
@role_required("manage_all")
def superadmin_tenant_detail(tenant_id):
    from flask import jsonify
    caller = get_current_user()
    if not caller or not caller.is_superadmin:
        abort(403)
    t = db.session.get(Tenant, tenant_id)
    if not t:
        abort(404)
    return jsonify({"id": t.id, "name": t.name, "slug": t.slug, "ico": t.ico or "", "dic": t.dic or "",
                    "street": t.street or "", "city": t.city or "", "postal_code": t.postal_code or "",
                    "email": t.email or "", "phone": t.phone or "", "is_active": t.is_active})
