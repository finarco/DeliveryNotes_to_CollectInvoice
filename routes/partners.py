"""Partner management routes."""

import json

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import db
from models import Contact, DeliveryNote, DeliveryNoteOrder, Invoice, Order, Partner, PartnerAddress
from services.audit import log_action
from services.auth import role_required
from utils import safe_float, safe_int
from services.tenant import tenant_query, stamp_tenant, tenant_get_or_404

partners_bp = Blueprint("partners", __name__)


@partners_bp.route("/partners/lookup")
@role_required("manage_partners")
def lookup_partner():
    """Proxy to RPO/ARES business registers for company lookup."""
    from flask import jsonify
    from services.company_lookup import lookup_by_ico, search_by_name

    ico = request.args.get("ico", "").strip()
    name = request.args.get("name", "").strip()

    if ico:
        result = lookup_by_ico(ico)
        if result:
            return jsonify(result)
        return jsonify({"error": "Firma nebola nájdená."}), 404
    elif name:
        results = search_by_name(name)
        return jsonify(results)

    return jsonify({"error": "Zadajte IČO alebo názov."}), 400


@partners_bp.route("/partners/search")
@role_required("manage_partners")
def search_partners():
    """Search existing partners by name or ICO for autocomplete."""
    from flask import jsonify
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify([])
    results = tenant_query(Partner).filter(
        Partner.is_deleted.is_(False),
        db.or_(
            Partner.name.ilike(f"%{q}%"),
            Partner.ico.ilike(f"%{q}%"),
        )
    ).limit(20).all()
    return jsonify([
        {"id": p.id, "name": p.name, "ico": p.ico or "", "city": p.city or ""}
        for p in results
    ])


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
        stamp_tenant(partner)
        db.session.add(partner)
        db.session.flush()
        hq_addr = PartnerAddress(
            address_type="headquarters",
            related_partner_id=partner.id,
            street=partner.street,
            street_number=partner.street_number,
            postal_code=partner.postal_code,
            city=partner.city,
        )
        stamp_tenant(hq_addr)
        partner.addresses.append(hq_addr)
        db.session.commit()
        # Return JSON for AJAX requests (inline partner creation)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            from flask import jsonify
            return jsonify({"id": partner.id, "name": partner.name})
        flash("Partner uložený.", "success")
        return redirect(url_for("partners.list_partners"))
    partners = tenant_query(Partner).filter_by(is_deleted=False).all()
    # Build safe JSON for partner contacts to avoid XSS via innerHTML
    contacts_map = {}
    for p in partners:
        contacts_map[str(p.id)] = [
            {
                "id": c.id,
                "name": c.name or "",
                "role": c.role or "",
                "email": c.email or "",
                "phone": c.phone or "",
                "canOrder": bool(c.can_order),
                "canReceive": bool(c.can_receive),
            }
            for c in p.contacts
        ]
    return render_template(
        "partners.html",
        partners=partners,
        partner_contacts_json=json.dumps(contacts_map),
    )


@partners_bp.route("/partners/<int:partner_id>/toggle", methods=["POST"])
@role_required("manage_partners")
def toggle_partner(partner_id: int):
    partner = tenant_get_or_404(Partner, partner_id)
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
    partner = tenant_get_or_404(Partner, partner_id)
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
    partner = tenant_get_or_404(Partner, partner_id)
    partner.is_deleted = True
    partner.is_active = False

    # Lock all associated orders
    orders = tenant_query(Order).filter_by(partner_id=partner.id).all()
    for order in orders:
        order.is_locked = True

    # Lock all associated invoices
    invoices = tenant_query(Invoice).filter_by(partner_id=partner.id).all()
    for inv in invoices:
        inv.is_locked = True

    # Lock all delivery notes linked to partner's orders
    order_ids = [o.id for o in orders]
    if order_ids:
        dn_links = tenant_query(DeliveryNoteOrder).filter(
            DeliveryNoteOrder.order_id.in_(order_ids)
        ).all()
        dn_ids = {link.delivery_note_id for link in dn_links}
        if dn_ids:
            tenant_query(DeliveryNote).filter(DeliveryNote.id.in_(dn_ids)).update(
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
    partner = tenant_get_or_404(Partner, partner_id)
    contact = Contact(
        partner_id=partner.id,
        name=request.form.get("name", "").strip(),
        email=request.form.get("email", ""),
        phone=request.form.get("phone", ""),
        role=request.form.get("role", ""),
        can_order=request.form.get("can_order") == "on",
        can_receive=request.form.get("can_receive") == "on",
    )
    stamp_tenant(contact)
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
    tenant_get_or_404(Partner, partner_id)
    contact = tenant_get_or_404(Contact, contact_id)
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
    tenant_get_or_404(Partner, partner_id)
    contact = tenant_get_or_404(Contact, contact_id)
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
    partner = tenant_get_or_404(Partner, partner_id)
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
    stamp_tenant(address)
    db.session.add(address)
    db.session.commit()
    flash("Adresa uložená.", "success")
    return redirect(url_for("partners.list_partners"))
