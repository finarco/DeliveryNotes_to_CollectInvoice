"""Invoice management routes."""

import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask import current_app

from extensions import db
from mailer import MailerError, send_document_email
from models import (
    DeliveryNote,
    DeliveryNoteOrder,
    Invoice,
    InvoiceItem,
    Order,
    Partner,
    VALID_INVOICE_STATUSES,
)
from services.audit import log_action
from services.auth import role_required
from services.invoice import generate_invoice_number
from services.pdf import generate_invoice_pdf
from superfaktura_client import SuperFakturaClient, SuperFakturaError
from utils import safe_float, safe_int
from services.tenant import tenant_query, stamp_tenant, tenant_get_or_404

logger = logging.getLogger(__name__)

invoices_bp = Blueprint("invoices", __name__)


@invoices_bp.route("/invoices/partner-delivery-notes/<int:partner_id>", methods=["GET"])
@role_required("manage_invoices")
def partner_delivery_notes(partner_id: int):
    """Return uninvoiced delivery notes for a partner (respecting group_code)."""
    partner = db.session.get(Partner, partner_id)
    if not partner:
        return jsonify([])

    # Build query for uninvoiced delivery notes
    # Support both partner_id-based and order-based delivery notes
    if partner.group_code:
        # Get all partner IDs in the same group
        group_partner_ids = [
            p.id for p in tenant_query(Partner).filter_by(group_code=partner.group_code).all()
        ]
        # DNs with direct partner_id
        direct_query = tenant_query(DeliveryNote).filter(
            DeliveryNote.partner_id.in_(group_partner_ids),
            DeliveryNote.invoiced.is_(False),
        )
        # DNs linked via orders (legacy)
        order_query = (
            tenant_query(DeliveryNote)
            .join(DeliveryNoteOrder, DeliveryNote.id == DeliveryNoteOrder.delivery_note_id)
            .join(Order, DeliveryNoteOrder.order_id == Order.id)
            .join(Partner, Order.partner_id == Partner.id)
            .filter(Partner.group_code == partner.group_code)
            .filter(DeliveryNote.invoiced.is_(False))
            .filter(DeliveryNote.partner_id.is_(None))
        )
        unbilled_notes = direct_query.union(order_query).all()
    else:
        direct_query = tenant_query(DeliveryNote).filter(
            DeliveryNote.partner_id == partner_id,
            DeliveryNote.invoiced.is_(False),
        )
        order_query = (
            tenant_query(DeliveryNote)
            .join(DeliveryNoteOrder, DeliveryNote.id == DeliveryNoteOrder.delivery_note_id)
            .join(Order, DeliveryNoteOrder.order_id == Order.id)
            .filter(Order.partner_id == partner_id)
            .filter(DeliveryNote.invoiced.is_(False))
            .filter(DeliveryNote.partner_id.is_(None))
        )
        unbilled_notes = direct_query.union(order_query).all()

    result = []
    for note in unbilled_notes:
        items = []
        for item in note.items:
            if item.product:
                name = item.product.name
            elif item.bundle:
                name = item.bundle.name
            elif item.is_manual:
                name = item.manual_name or "Manuálna položka"
            else:
                name = "Položka"
            line_total = item.line_total or (item.unit_price * item.quantity)
            description = f"{name} ({item.quantity}x)"
            items.append({
                "description": description,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
                "line_total": str(line_total),
                "product_id": item.product_id,
                "bundle_id": item.bundle_id,
                "is_manual": item.is_manual or False,
                "manual_name": item.manual_name,
                "product_name": name,
            })
        # Determine partner name
        pname = ""
        if note.partner:
            pname = note.partner.name
        elif note.primary_order and note.primary_order.partner:
            pname = note.primary_order.partner.name
        result.append({
            "id": note.id,
            "note_number": note.note_number or f"DL-{note.id}",
            "partner_name": pname,
            "created_at": note.created_at.strftime("%Y-%m-%d") if note.created_at else "",
            "items": items,
        })
    return jsonify(result)


@invoices_bp.route("/invoices", methods=["GET", "POST"])
@role_required("manage_invoices")
def list_invoices():
    partners = tenant_query(Partner).filter_by(is_active=True, is_deleted=False).all()
    if request.method == "POST":
        partner_id = safe_int(request.form.get("partner_id"))
        if not partner_id:
            flash("Partner je povinný.", "danger")
            return redirect(url_for("invoices.list_invoices"))

        dn_ids = request.form.getlist("delivery_note_ids")
        selected_dns = (
            tenant_query(DeliveryNote).filter(DeliveryNote.id.in_(dn_ids)).all()
            if dn_ids else []
        )

        invoice_number = generate_invoice_number(partner_id=partner_id)
        invoice = Invoice(
            partner_id=partner_id,
            invoice_number=invoice_number,
            status="draft",
        )
        stamp_tenant(invoice)
        db.session.add(invoice)

        _Q2 = Decimal("0.01")
        total = Decimal("0")
        total_with_vat = Decimal("0")

        # Parse items from dynamic table
        idx = 0
        while True:
            item_type = request.form.get(f"items[{idx}][type]")
            if item_type is None:
                break
            qty = safe_int(request.form.get(f"items[{idx}][quantity]"))
            price_str = request.form.get(f"items[{idx}][unit_price]", "0")
            try:
                unit_price = Decimal(price_str) if price_str else Decimal("0")
            except InvalidOperation:
                unit_price = Decimal("0")
            description = request.form.get(f"items[{idx}][description]", "").strip()
            vat_rate_str = request.form.get(f"items[{idx}][vat_rate]", "20")
            try:
                vat_rate = Decimal(vat_rate_str) if vat_rate_str else Decimal("20")
            except InvalidOperation:
                vat_rate = Decimal("20")
            source_dn_id = safe_int(request.form.get(f"items[{idx}][source_delivery_id]")) or None
            is_manual = request.form.get(f"items[{idx}][type]") == "manual"

            if qty and qty > 0 and description:
                line_total = (unit_price * qty).quantize(_Q2, rounding=ROUND_HALF_UP)
                vat_amount = (line_total * vat_rate / Decimal("100")).quantize(
                    _Q2, rounding=ROUND_HALF_UP
                )
                line_total_with_vat = line_total + vat_amount

                invoice.items.append(InvoiceItem(
                    source_delivery_id=source_dn_id,
                    description=description,
                    quantity=qty,
                    unit_price=unit_price,
                    total=line_total,
                    vat_rate=vat_rate,
                    vat_amount=vat_amount,
                    total_with_vat=line_total_with_vat,
                    is_manual=is_manual,
                ))
                total += line_total
                total_with_vat += line_total_with_vat
            idx += 1

        # If no items were parsed (e.g., empty form) but DNs were selected,
        # check we actually got items
        if not invoice.items and not selected_dns:
            db.session.rollback()
            flash("Žiadne položky na fakturáciu.", "danger")
            return redirect(url_for("invoices.list_invoices"))

        invoice.total = total.quantize(_Q2, rounding=ROUND_HALF_UP)
        invoice.total_with_vat = total_with_vat.quantize(_Q2, rounding=ROUND_HALF_UP)

        # Mark selected delivery notes as invoiced
        for dn in selected_dns:
            dn.invoiced = True

        log_action("create", "invoice", invoice.id, f"partner={partner_id}")
        db.session.commit()
        flash(
            f"Faktúra {invoice.invoice_number or invoice.id} vytvorená.",
            "success",
        )
        return redirect(url_for("invoices.list_invoices"))

    query = tenant_query(Invoice).order_by(Invoice.created_at.desc())

    # Calculate stats for dashboard
    all_invoices = tenant_query(Invoice).all()
    total_revenue = sum(inv.total_with_vat or 0 for inv in all_invoices)
    paid_amount = sum(inv.total_with_vat or 0 for inv in all_invoices if inv.status == "paid")
    unpaid_amount = sum(inv.total_with_vat or 0 for inv in all_invoices if inv.status != "paid")

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


@invoices_bp.route("/invoices/<int:invoice_id>/detail", methods=["GET"])
@role_required("manage_invoices")
def invoice_detail(invoice_id: int):
    invoice = tenant_get_or_404(Invoice, invoice_id)
    items = []
    for item in invoice.items:
        items.append({
            "type": "delivery" if item.source_delivery_id else "manual",
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": str(item.unit_price),
            "vat_rate": str(item.vat_rate or 20),
            "total": str(item.total),
            "vat_amount": str(item.vat_amount or 0),
            "total_with_vat": str(item.total_with_vat or 0),
            "source_delivery_id": item.source_delivery_id,
            "is_manual": item.is_manual or False,
        })
    return jsonify({
        "id": invoice.id,
        "invoice_number": invoice.invoice_number or f"#{invoice.id}",
        "partner_id": invoice.partner_id,
        "partner_name": invoice.partner.name if invoice.partner else "",
        "status": invoice.status or "draft",
        "is_locked": invoice.is_locked,
        "total": str(invoice.total or 0),
        "total_with_vat": str(invoice.total_with_vat or 0),
        "items": items,
    })


@invoices_bp.route("/invoices/<int:invoice_id>/edit", methods=["POST"])
@role_required("manage_all")
def edit_invoice(invoice_id: int):
    invoice = tenant_get_or_404(Invoice, invoice_id)
    if invoice.is_locked:
        flash("Faktúra je uzamknutá.", "danger")
        return redirect(url_for("invoices.list_invoices"))
    new_status = request.form.get("status", "").strip()
    if new_status and new_status in VALID_INVOICE_STATUSES:
        invoice.status = new_status
    # Replace items if any were submitted
    has_items = request.form.get("items[0][type]") is not None
    if has_items:
        invoice.items.clear()
        _Q2 = Decimal("0.01")
        total = Decimal("0")
        total_with_vat = Decimal("0")
        idx = 0
        while True:
            item_type = request.form.get(f"items[{idx}][type]")
            if item_type is None:
                break
            qty = safe_int(request.form.get(f"items[{idx}][quantity]"))
            price_str = request.form.get(f"items[{idx}][unit_price]", "0")
            try:
                unit_price = Decimal(price_str) if price_str else Decimal("0")
            except InvalidOperation:
                unit_price = Decimal("0")
            description = request.form.get(f"items[{idx}][description]", "").strip()
            vat_rate_str = request.form.get(f"items[{idx}][vat_rate]", "20")
            try:
                vat_rate = Decimal(vat_rate_str) if vat_rate_str else Decimal("20")
            except InvalidOperation:
                vat_rate = Decimal("20")
            source_dn_id = safe_int(request.form.get(f"items[{idx}][source_delivery_id]")) or None
            is_manual = item_type == "manual"
            if qty and qty > 0 and description:
                line_total = (unit_price * qty).quantize(_Q2, rounding=ROUND_HALF_UP)
                vat_amount = (line_total * vat_rate / Decimal("100")).quantize(
                    _Q2, rounding=ROUND_HALF_UP
                )
                line_total_with_vat = line_total + vat_amount
                invoice.items.append(InvoiceItem(
                    source_delivery_id=source_dn_id,
                    description=description,
                    quantity=qty,
                    unit_price=unit_price,
                    total=line_total,
                    vat_rate=vat_rate,
                    vat_amount=vat_amount,
                    total_with_vat=line_total_with_vat,
                    is_manual=is_manual,
                ))
                total += line_total
                total_with_vat += line_total_with_vat
            idx += 1
        invoice.total = total.quantize(_Q2, rounding=ROUND_HALF_UP)
        invoice.total_with_vat = total_with_vat.quantize(_Q2, rounding=ROUND_HALF_UP)
    log_action("edit", "invoice", invoice.id, f"status={invoice.status}")
    db.session.commit()
    flash("Faktúra upravená.", "success")
    return redirect(url_for("invoices.list_invoices"))


@invoices_bp.route("/invoices/<int:invoice_id>/delete", methods=["POST"])
@role_required("manage_all")
def delete_invoice(invoice_id: int):
    invoice = tenant_get_or_404(Invoice, invoice_id)
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
    invoice = tenant_get_or_404(Invoice, invoice_id)
    description = request.form.get("description", "").strip()
    quantity = safe_int(request.form.get("quantity"), default=1)
    unit_price = safe_float(request.form.get("unit_price"))
    total = round(unit_price * quantity, 2)
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
    invoice.total = float(invoice.total or 0) + total
    invoice.total_with_vat = float(invoice.total_with_vat or 0) + total_with_vat
    db.session.commit()
    log_action("create", "invoice_item", invoice.id, "manual")
    db.session.commit()
    flash("Manuálna položka pridaná.", "success")
    return redirect(url_for("invoices.list_invoices"))


@invoices_bp.route("/invoices/<int:invoice_id>/pdf")
@role_required("manage_invoices")
def invoice_pdf(invoice_id: int):
    invoice = tenant_get_or_404(Invoice, invoice_id)
    app_cfg = current_app.config["APP_CONFIG"]
    pdf_path = generate_invoice_pdf(invoice, app_cfg)
    return send_file(pdf_path, as_attachment=True)


@invoices_bp.route(
    "/invoices/<int:invoice_id>/send", methods=["POST"]
)
@role_required("manage_invoices")
def send_invoice(invoice_id: int):
    invoice = tenant_get_or_404(Invoice, invoice_id)
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
    invoice = tenant_get_or_404(Invoice, invoice_id)
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
