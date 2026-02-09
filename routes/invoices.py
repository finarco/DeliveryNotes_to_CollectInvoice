"""Invoice management routes."""

import logging

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask import current_app

from extensions import db
from mailer import MailerError, send_document_email
from models import Invoice, InvoiceItem, Partner, VALID_INVOICE_STATUSES
from services.audit import log_action
from services.auth import role_required
from services.invoice import build_invoice_for_partner
from services.pdf import generate_invoice_pdf
from superfaktura_client import SuperFakturaClient, SuperFakturaError
from utils import safe_float, safe_int

logger = logging.getLogger(__name__)

invoices_bp = Blueprint("invoices", __name__)


@invoices_bp.route("/invoices", methods=["GET", "POST"])
@role_required("manage_invoices")
def list_invoices():
    partners = Partner.query.filter_by(is_active=True, is_deleted=False).all()
    if request.method == "POST":
        partner_id = safe_int(request.form.get("partner_id"))
        if not partner_id:
            flash("Partner je povinný.", "danger")
            return redirect(url_for("invoices.list_invoices"))
        try:
            invoice = build_invoice_for_partner(partner_id)
            log_action(
                "create", "invoice", invoice.id, f"partner={partner_id}"
            )
            db.session.commit()
            flash(
                f"Faktúra {invoice.invoice_number or invoice.id} vytvorená.",
                "success",
            )
        except ValueError as e:
            flash(str(e), "danger")
        return redirect(url_for("invoices.list_invoices"))

    query = Invoice.query.order_by(Invoice.created_at.desc())

    # Calculate stats for dashboard
    all_invoices = Invoice.query.all()
    total_revenue = sum(inv.total_amount or 0 for inv in all_invoices)
    paid_amount = sum(inv.total_amount or 0 for inv in all_invoices if inv.paid)
    unpaid_amount = sum(inv.total_amount or 0 for inv in all_invoices if not inv.paid)

    # Calculate overdue (simplified - invoices not paid)
    overdue_amount = unpaid_amount

    page = max(1, safe_int(request.args.get("page"), default=1))
    per_page = 20
    total = query.count()
    invoices_list = (
        query.offset((page - 1) * per_page).limit(per_page).all()
    )
    return render_template(
        "invoices.html",
        invoices=invoices_list,
        total=total,
        page=page,
        per_page=per_page,
        partners=partners,
        valid_invoice_statuses=sorted(VALID_INVOICE_STATUSES),
        total_revenue=total_revenue,
        paid_amount=paid_amount,
        unpaid_amount=unpaid_amount,
        overdue_amount=overdue_amount,
    )


@invoices_bp.route("/invoices/<int:invoice_id>/edit", methods=["POST"])
@role_required("manage_all")
def edit_invoice(invoice_id: int):
    invoice = db.get_or_404(Invoice, invoice_id)
    if invoice.is_locked:
        flash("Faktúra je uzamknutá.", "danger")
        return redirect(url_for("invoices.list_invoices"))
    new_status = request.form.get("status", "").strip()
    if new_status and new_status in VALID_INVOICE_STATUSES:
        invoice.status = new_status
    log_action("edit", "invoice", invoice.id, f"status={invoice.status}")
    db.session.commit()
    flash("Faktúra upravená.", "success")
    return redirect(url_for("invoices.list_invoices"))


@invoices_bp.route("/invoices/<int:invoice_id>/delete", methods=["POST"])
@role_required("manage_all")
def delete_invoice(invoice_id: int):
    invoice = db.get_or_404(Invoice, invoice_id)
    if invoice.is_locked:
        flash("Faktúra je uzamknutá a nemôže byť vymazaná.", "danger")
        return redirect(url_for("invoices.list_invoices"))
    log_action("delete", "invoice", invoice.id, f"deleted invoice #{invoice.id}")
    db.session.delete(invoice)
    db.session.commit()
    flash("Faktúra vymazaná.", "warning")
    return redirect(url_for("invoices.list_invoices"))


@invoices_bp.route(
    "/invoices/<int:invoice_id>/items", methods=["POST"]
)
@role_required("manage_invoices")
def add_invoice_item(invoice_id: int):
    invoice = db.get_or_404(Invoice, invoice_id)
    description = request.form.get("description", "").strip()
    quantity = safe_int(request.form.get("quantity"), default=1)
    unit_price = safe_float(request.form.get("unit_price"))
    total = unit_price * quantity
    vat_rate = safe_float(request.form.get("vat_rate"), default=20.0)
    vat_amount = round(total * vat_rate / 100, 2)
    total_with_vat = round(total + vat_amount, 2)

    invoice.items.append(
        InvoiceItem(
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            total=total,
            vat_rate=vat_rate,
            vat_amount=vat_amount,
            total_with_vat=total_with_vat,
            is_manual=True,
        )
    )
    invoice.total += total
    invoice.total_with_vat = (invoice.total_with_vat or 0) + total_with_vat
    db.session.commit()
    log_action("create", "invoice_item", invoice.id, "manual")
    db.session.commit()
    flash("Manuálna položka pridaná.", "success")
    return redirect(url_for("invoices.list_invoices"))


@invoices_bp.route("/invoices/<int:invoice_id>/pdf")
@role_required("manage_invoices")
def invoice_pdf(invoice_id: int):
    invoice = db.get_or_404(Invoice, invoice_id)
    app_cfg = current_app.config["APP_CONFIG"]
    pdf_path = generate_invoice_pdf(invoice, app_cfg)
    return send_file(pdf_path, as_attachment=True)


@invoices_bp.route(
    "/invoices/<int:invoice_id>/send", methods=["POST"]
)
@role_required("manage_invoices")
def send_invoice(invoice_id: int):
    invoice = db.get_or_404(Invoice, invoice_id)
    app_cfg = current_app.config["APP_CONFIG"]
    email_cfg = current_app.config["EMAIL_CONFIG"]
    pdf_path = generate_invoice_pdf(invoice, app_cfg)

    display = invoice.invoice_number or str(invoice.id)
    if email_cfg.enabled and invoice.partner.email:
        try:
            send_document_email(
                email_cfg,
                subject=f"Faktúra {display}",
                recipient=invoice.partner.email,
                cc=email_cfg.operator_cc,
                body=f"Dobrý deň, v prílohe posielame faktúru {display}.",
                attachment_path=pdf_path,
            )
            log_action("email", "invoice", invoice.id, "sent")
            db.session.commit()
            flash("Faktúra odoslaná emailom.", "success")
        except MailerError as e:
            logger.error("Failed to send invoice %s email: %s", invoice_id, e)
            flash(f"Chyba pri odosielaní emailu: {e}", "danger")
    else:
        flash(
            "Odosielanie emailov nie je zapnuté alebo chýba email.",
            "warning",
        )
    return redirect(url_for("invoices.list_invoices"))


@invoices_bp.route(
    "/invoices/<int:invoice_id>/export", methods=["POST"]
)
@role_required("manage_invoices")
def export_invoice(invoice_id: int):
    invoice = db.get_or_404(Invoice, invoice_id)
    sf_cfg = current_app.config["SF_CONFIG"]
    if not sf_cfg.enabled:
        flash("Superfaktúra API nie je zapnutá.", "warning")
        return redirect(url_for("invoices.list_invoices"))

    client = SuperFakturaClient(sf_cfg)
    try:
        result = client.send_invoice(invoice)
        invoice.status = "sent" if result else "error"
        log_action("export", "invoice", invoice.id, invoice.status)
        db.session.commit()
        flash("Faktúra exportovaná do Superfaktúry.", "success")
    except SuperFakturaError as e:
        logger.error(
            "Failed to export invoice %s to Superfaktura: %s", invoice_id, e
        )
        invoice.status = "error"
        db.session.commit()
        flash(f"Chyba pri exporte do Superfaktúry: {e}", "danger")
    return redirect(url_for("invoices.list_invoices"))
