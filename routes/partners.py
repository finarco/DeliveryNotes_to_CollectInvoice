"""Partner management routes."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import db
from models import Contact, DeliveryNote, DeliveryNoteOrder, Invoice, Order, Partner, PartnerAddress
from services.audit import log_action
from services.auth import role_required
from utils import safe_float, safe_int

partners_bp = Blueprint("partners", __name__)


@partners_bp.route("/partners", methods=["GET", "POST"])
@role_required("manage_partners")
def list_partners():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Názov partnera je povinný.", "danger")
            return redirect(url_for("partners.list_partners"))
        partner = Partner(
            name=name,
            note=request.form.get("note", ""),
            street=request.form.get("street", ""),
            street_number=request.form.get("street_number", ""),
            postal_code=request.form.get("postal_code", ""),
            city=request.form.get("city", ""),
            group_code=request.form.get("group_code", ""),
            ico=request.form.get("ico", ""),
            dic=request.form.get("dic", ""),
            ic_dph=request.form.get("ic_dph", ""),
            email=request.form.get("email", ""),
            phone=request.form.get("phone", ""),
            price_level=request.form.get("price_level", ""),
            discount_percent=safe_float(
                request.form.get("discount_percent")
            ),
        )
        db.session.add(partner)
        db.session.flush()
        partner.addresses.append(
            PartnerAddress(
                address_type="headquarters",
                related_partner_id=partner.id,
                street=partner.street,
                street_number=partner.street_number,
                postal_code=partner.postal_code,
                city=partner.city,
            )
        )
        db.session.commit()
        flash("Partner uložený.", "success")
        return redirect(url_for("partners.list_partners"))
    return render_template(
        "partners.html",
        partners=Partner.query.filter_by(is_deleted=False).all(),
    )


@partners_bp.route("/partners/<int:partner_id>/toggle", methods=["POST"])
@role_required("manage_partners")
def toggle_partner(partner_id: int):
    partner = db.get_or_404(Partner, partner_id)
    partner.is_active = not partner.is_active
    action = "activate" if partner.is_active else "deactivate"
    log_action(action, "partner", partner.id, f"is_active={partner.is_active}")
    db.session.commit()
    status = "aktivovaný" if partner.is_active else "deaktivovaný"
    flash(f"Partner '{partner.name}' {status}.", "success")
    return redirect(url_for("partners.list_partners"))


@partners_bp.route("/partners/<int:partner_id>/edit", methods=["POST"])
@role_required("manage_partners")
def edit_partner(partner_id: int):
    partner = db.get_or_404(Partner, partner_id)
    partner.name = request.form.get("name", "").strip() or partner.name
    partner.note = request.form.get("note", "")
    partner.street = request.form.get("street", "")
    partner.street_number = request.form.get("street_number", "")
    partner.postal_code = request.form.get("postal_code", "")
    partner.city = request.form.get("city", "")
    partner.group_code = request.form.get("group_code", "")
    partner.ico = request.form.get("ico", "")
    partner.dic = request.form.get("dic", "")
    partner.ic_dph = request.form.get("ic_dph", "")
    partner.email = request.form.get("email", "")
    partner.phone = request.form.get("phone", "")
    partner.price_level = request.form.get("price_level", "")
    partner.discount_percent = safe_float(request.form.get("discount_percent"))
    log_action("edit", "partner", partner.id, "updated")
    db.session.commit()
    flash(f"Partner '{partner.name}' upravený.", "success")
    return redirect(url_for("partners.list_partners"))


@partners_bp.route("/partners/<int:partner_id>/delete", methods=["POST"])
@role_required("manage_partners")
def delete_partner(partner_id: int):
    partner = db.get_or_404(Partner, partner_id)
    partner.is_deleted = True
    partner.is_active = False

    # Lock all associated orders
    orders = Order.query.filter_by(partner_id=partner.id).all()
    for order in orders:
        order.is_locked = True

    # Lock all associated invoices
    invoices = Invoice.query.filter_by(partner_id=partner.id).all()
    for inv in invoices:
        inv.is_locked = True

    # Lock all delivery notes linked to partner's orders
    order_ids = [o.id for o in orders]
    if order_ids:
        dn_links = DeliveryNoteOrder.query.filter(
            DeliveryNoteOrder.order_id.in_(order_ids)
        ).all()
        dn_ids = {link.delivery_note_id for link in dn_links}
        if dn_ids:
            DeliveryNote.query.filter(DeliveryNote.id.in_(dn_ids)).update(
                {"is_locked": True}, synchronize_session="fetch"
            )

    log_action("delete", "partner", partner.id, "soft-deleted, locked docs")
    db.session.commit()
    flash(
        f"Partner '{partner.name}' vymazaný. Súvisiace dokumenty boli uzamknuté.",
        "warning",
    )
    return redirect(url_for("partners.list_partners"))


@partners_bp.route(
    "/partners/<int:partner_id>/contacts", methods=["POST"]
)
@role_required("manage_partners")
def add_contact(partner_id: int):
    partner = db.get_or_404(Partner, partner_id)
    contact = Contact(
        partner_id=partner.id,
        name=request.form.get("name", "").strip(),
        email=request.form.get("email", ""),
        phone=request.form.get("phone", ""),
        role=request.form.get("role", ""),
        can_order=request.form.get("can_order") == "on",
        can_receive=request.form.get("can_receive") == "on",
    )
    db.session.add(contact)
    db.session.commit()
    flash("Kontakt uložený.", "success")
    return redirect(url_for("partners.list_partners"))


@partners_bp.route(
    "/partners/<int:partner_id>/contacts/<int:contact_id>/edit",
    methods=["POST"],
)
@role_required("manage_partners")
def edit_contact(partner_id: int, contact_id: int):
    db.get_or_404(Partner, partner_id)
    contact = db.get_or_404(Contact, contact_id)
    if contact.partner_id != partner_id:
        flash("Kontakt nepatrí k tomuto partnerovi.", "danger")
        return redirect(url_for("partners.list_partners"))
    contact.name = request.form.get("name", "").strip() or contact.name
    contact.email = request.form.get("email", "")
    contact.phone = request.form.get("phone", "")
    contact.role = request.form.get("role", "")
    contact.can_order = request.form.get("can_order") == "on"
    contact.can_receive = request.form.get("can_receive") == "on"
    log_action("edit", "contact", contact.id, f"partner_id={partner_id}")
    db.session.commit()
    flash("Kontakt upravený.", "success")
    return redirect(url_for("partners.list_partners"))


@partners_bp.route(
    "/partners/<int:partner_id>/contacts/<int:contact_id>/delete",
    methods=["POST"],
)
@role_required("manage_all")
def delete_contact(partner_id: int, contact_id: int):
    db.get_or_404(Partner, partner_id)
    contact = db.get_or_404(Contact, contact_id)
    if contact.partner_id != partner_id:
        flash("Kontakt nepatrí k tomuto partnerovi.", "danger")
        return redirect(url_for("partners.list_partners"))
    log_action("delete", "contact", contact.id, f"partner_id={partner_id}, name={contact.name}")
    db.session.delete(contact)
    db.session.commit()
    flash("Kontakt vymazaný.", "success")
    return redirect(url_for("partners.list_partners"))


@partners_bp.route(
    "/partners/<int:partner_id>/addresses", methods=["POST"]
)
@role_required("manage_partners")
def add_address(partner_id: int):
    partner = db.get_or_404(Partner, partner_id)
    related_partner_id = (
        safe_int(request.form.get("related_partner_id")) or None
    )
    address = PartnerAddress(
        partner_id=partner.id,
        address_type=request.form.get("address_type", "").strip() or "other",
        related_partner_id=related_partner_id,
        street=request.form.get("street", ""),
        street_number=request.form.get("street_number", ""),
        postal_code=request.form.get("postal_code", ""),
        city=request.form.get("city", ""),
    )
    db.session.add(address)
    db.session.commit()
    flash("Adresa uložená.", "success")
    return redirect(url_for("partners.list_partners"))
