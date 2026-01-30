"""Partner management routes."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import db
from models import Contact, Partner, PartnerAddress
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
    return render_template("partners.html", partners=Partner.query.all())


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
