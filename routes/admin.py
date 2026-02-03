"""Admin routes — user management (Feature F1)."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from utils import utc_now

from extensions import db
from models import (
    AppSetting,
    DeliveryNote,
    Invoice,
    NumberingConfig,
    Order,
    PdfTemplate,
    VALID_ROLES,
    User,
)
from services.pdf import get_default_css, get_default_html
from services.audit import log_action
from services.auth import role_required

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
        if len(password) < 8:
            flash("Heslo musí mať aspoň 8 znakov.", "danger")
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
        log_action("create", "user", user.id, f"role={role}")
        db.session.commit()
        flash(f"Používateľ '{username}' vytvorený.", "success")
        return redirect(url_for("admin.users"))

    all_users = User.query.order_by(User.id).all()
    return render_template(
        "admin/users.html", users=all_users, valid_roles=VALID_ROLES
    )


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@role_required("manage_all")
def toggle_user(user_id: int):
    user = db.get_or_404(User, user_id)
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
    user = db.get_or_404(User, user_id)
    new_password = request.form.get("new_password", "")
    if len(new_password) < 8:
        flash("Heslo musí mať aspoň 8 znakov.", "danger")
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
    order = db.get_or_404(Order, order_id)
    order.is_locked = False
    log_action("unlock", "order", order.id, "unlocked by admin")
    db.session.commit()
    flash(f"Objednávka #{order.id} odomknutá.", "success")
    return redirect(request.referrer or url_for("orders.list_orders"))


@admin_bp.route("/unlock/delivery/<int:delivery_id>", methods=["POST"])
@role_required("manage_all")
def unlock_delivery(delivery_id: int):
    delivery = db.get_or_404(DeliveryNote, delivery_id)
    delivery.is_locked = False
    log_action("unlock", "delivery_note", delivery.id, "unlocked by admin")
    db.session.commit()
    flash(f"Dodací list #{delivery.id} odomknutý.", "success")
    return redirect(request.referrer or url_for("delivery.list_delivery_notes"))


@admin_bp.route("/unlock/invoice/<int:invoice_id>", methods=["POST"])
@role_required("manage_all")
def unlock_invoice(invoice_id: int):
    invoice = db.get_or_404(Invoice, invoice_id)
    invoice.is_locked = False
    log_action("unlock", "invoice", invoice.id, "unlocked by admin")
    db.session.commit()
    flash(f"Faktúra #{invoice.id} odomknutá.", "success")
    return redirect(request.referrer or url_for("invoices.list_invoices"))


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

def _get_setting(key: str, default: str = "") -> str:
    row = AppSetting.query.filter_by(key=key).first()
    return row.value if row and row.value else default


def _set_setting(key: str, value: str):
    row = AppSetting.query.filter_by(key=key).first()
    if not row:
        row = AppSetting(key=key, value=value)
        db.session.add(row)
    else:
        row.value = value


@admin_bp.route("/settings", methods=["GET", "POST"])
@role_required("manage_all")
def settings():
    if request.method == "POST":
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

        # Numbering configs (tag-based patterns)
        for etype in _ENTITY_TYPES:
            config = NumberingConfig.query.filter_by(entity_type=etype).first()
            if not config:
                config = NumberingConfig(entity_type=etype)
                db.session.add(config)
            config.pattern = request.form.get(f"num_{etype}_pattern", "").strip()

        log_action("update", "settings", 0, "settings updated")
        db.session.commit()
        flash("Nastavenia uložené.", "success")
        return redirect(url_for("admin.settings"))

    # GET — load current values
    numbering = {}
    for etype in _ENTITY_TYPES:
        config = NumberingConfig.query.filter_by(entity_type=etype).first()
        numbering[etype] = config

    return render_template(
        "admin/settings.html",
        site_name=_get_setting("site_name", "ObDoFa"),
        password_expiry_value=_get_setting("password_expiry_value", "0"),
        password_expiry_unit=_get_setting("password_expiry_unit", "days"),
        numbering=numbering,
        entity_types=_ENTITY_TYPES,
    )


@admin_bp.route(
    "/users/<int:user_id>/force-password-change", methods=["POST"]
)
@role_required("manage_all")
def force_password_change(user_id: int):
    user = db.get_or_404(User, user_id)
    user.must_change_password = True
    log_action("force_password_change", "user", user.id, "forced by admin")
    db.session.commit()
    flash(
        f"Používateľ '{user.username}' musí zmeniť heslo pri ďalšom prihlásení.",
        "success",
    )
    return redirect(url_for("admin.users"))


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
            tmpl = PdfTemplate.query.filter_by(entity_type=etype).first()
            if not tmpl:
                tmpl = PdfTemplate(entity_type=etype)
                db.session.add(tmpl)
            tmpl.html_content = request.form.get(f"html_{etype}", "")
            tmpl.css_content = request.form.get(f"css_{etype}", "")
        log_action("update", "pdf_template", 0, "templates updated")
        db.session.commit()
        flash("PDF šablóny uložené.", "success")
        return redirect(url_for("admin.pdf_templates"))

    templates = {}
    for etype in _PDF_ENTITY_TYPES:
        tmpl = PdfTemplate.query.filter_by(entity_type=etype).first()
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
