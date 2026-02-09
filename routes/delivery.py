"""Delivery note routes."""

from itertools import groupby

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
from models import (
    Bundle,
    DeliveryItem,
    DeliveryItemComponent,
    DeliveryNote,
    DeliveryNoteOrder,
    Order,
    Product,
)
from services.audit import log_action
from services.auth import get_current_user, role_required
from services.numbering import generate_number
from services.pdf import generate_delivery_pdf
from utils import parse_datetime, safe_int, utc_now

delivery_bp = Blueprint("delivery", __name__)


@delivery_bp.route("/delivery-notes", methods=["GET", "POST"])
@role_required("manage_delivery")
def list_delivery_notes():
    all_orders = Order.query.order_by(Order.created_at.desc()).all()
    products = Product.query.filter_by(is_active=True).all()
    bundles = Bundle.query.filter_by(is_active=True).all()

    if request.method == "POST":
        order_ids = request.form.getlist("order_ids")
        selected_orders = Order.query.filter(Order.id.in_(order_ids)).all()
        if not selected_orders:
            flash("Objednávka neexistuje.", "danger")
            return redirect(url_for("delivery.list_delivery_notes"))

        group_codes = {
            order.partner.group_code or None for order in selected_orders
        }
        group_codes.discard(None)
        if len(group_codes) > 1:
            flash(
                "Objednávky musia byť v rovnakej partnerskej skupine.",
                "danger",
            )
            return redirect(url_for("delivery.list_delivery_notes"))

        user = get_current_user()
        delivery = DeliveryNote(
            primary_order_id=selected_orders[0].id,
            created_by_id=user.id,
            show_prices=request.form.get("show_prices") == "on",
            planned_delivery_datetime=parse_datetime(
                request.form.get("planned_delivery_datetime")
            ),
        )
        db.session.add(delivery)
        db.session.flush()
        delivery.note_number = generate_number(
            "delivery_note",
            partner_id=selected_orders[0].partner_id,
        )

        for order in selected_orders:
            delivery.orders.append(DeliveryNoteOrder(order_id=order.id))
            for item in order.items:
                line_total = item.unit_price * item.quantity
                delivery.items.append(
                    DeliveryItem(
                        product_id=item.product_id,
                        quantity=item.quantity,
                        unit_price=item.unit_price,
                        line_total=line_total,
                    )
                )

        for product in products:
            extra_qty = safe_int(request.form.get(f"extra_{product.id}"))
            if extra_qty > 0:
                line_total = product.price * extra_qty
                delivery.items.append(
                    DeliveryItem(
                        product_id=product.id,
                        quantity=extra_qty,
                        unit_price=product.price,
                        line_total=line_total,
                    )
                )

        for bundle in bundles:
            bundle_qty = safe_int(request.form.get(f"bundle_{bundle.id}"))
            if bundle_qty > 0:
                line_total = bundle.bundle_price * bundle_qty
                delivery_item = DeliveryItem(
                    bundle_id=bundle.id,
                    quantity=bundle_qty,
                    unit_price=bundle.bundle_price,
                    line_total=line_total,
                )
                for bundle_item in bundle.items:
                    delivery_item.components.append(
                        DeliveryItemComponent(
                            product_id=bundle_item.product_id,
                            quantity=bundle_item.quantity * bundle_qty,
                        )
                    )
                delivery.items.append(delivery_item)

        log_action("create", "delivery_note", delivery.id, "created")
        db.session.commit()
        flash("Dodací list vytvorený.", "success")
        return redirect(url_for("delivery.list_delivery_notes"))

    query = DeliveryNote.query.order_by(DeliveryNote.created_at.desc())

    page = max(1, safe_int(request.args.get("page"), default=1))
    per_page = 20
    total = query.count()
    delivery_list = (
        query.offset((page - 1) * per_page).limit(per_page).all()
    )

    # Prepare grouped data for timeline view
    delivery_notes_by_date = []
    for date_key, notes in groupby(
        sorted(delivery_list, key=lambda n: n.created_at.date() if n.created_at else None),
        key=lambda n: n.created_at.date() if n.created_at else None
    ):
        delivery_notes_by_date.append((date_key, list(notes)))

    return render_template(
        "delivery_notes.html",
        delivery_notes=delivery_list,
        delivery_notes_by_date=delivery_notes_by_date,
        total=total,
        page=page,
        per_page=per_page,
        orders=all_orders,
        products=products,
        bundles=bundles,
    )


@delivery_bp.route("/delivery-notes/<int:delivery_id>/edit", methods=["POST"])
@role_required("manage_delivery")
def edit_delivery(delivery_id: int):
    delivery = db.get_or_404(DeliveryNote, delivery_id)
    if delivery.is_locked:
        flash("Dodací list je uzamknutý.", "danger")
        return redirect(url_for("delivery.list_delivery_notes"))
    if delivery.logistics_plans or delivery.invoice_item_refs or delivery.invoiced:
        flash("Dodací list má priradený zvoz alebo faktúru a nemôže byť upravený.", "danger")
        return redirect(url_for("delivery.list_delivery_notes"))
    delivery.planned_delivery_datetime = parse_datetime(
        request.form.get("planned_delivery_datetime")
    )
    delivery.show_prices = request.form.get("show_prices") == "on"
    log_action("edit", "delivery_note", delivery.id, "updated")
    db.session.commit()
    flash("Dodací list upravený.", "success")
    return redirect(url_for("delivery.list_delivery_notes"))


@delivery_bp.route("/delivery-notes/<int:delivery_id>/delete", methods=["POST"])
@role_required("manage_delivery")
def delete_delivery(delivery_id: int):
    delivery = db.get_or_404(DeliveryNote, delivery_id)
    if delivery.is_locked:
        flash("Dodací list je uzamknutý a nemôže byť vymazaný.", "danger")
        return redirect(url_for("delivery.list_delivery_notes"))
    if delivery.logistics_plans or delivery.invoice_item_refs or delivery.invoiced:
        flash("Dodací list má priradený zvoz alebo faktúru a nemôže byť vymazaný.", "danger")
        return redirect(url_for("delivery.list_delivery_notes"))
    log_action("delete", "delivery_note", delivery.id, f"deleted DN #{delivery.id}")
    db.session.delete(delivery)
    db.session.commit()
    flash("Dodací list vymazaný.", "warning")
    return redirect(url_for("delivery.list_delivery_notes"))


@delivery_bp.route(
    "/delivery-notes/<int:delivery_id>/confirm", methods=["POST"]
)
@role_required("manage_delivery")
def confirm_delivery(delivery_id: int):
    delivery = db.get_or_404(DeliveryNote, delivery_id)
    delivery.confirmed = True
    delivery.actual_delivery_datetime = utc_now()
    log_action("confirm", "delivery_note", delivery.id, "confirmed")
    db.session.commit()
    flash("Dodací list potvrdený.", "success")
    return redirect(url_for("delivery.list_delivery_notes"))


@delivery_bp.route(
    "/delivery-notes/<int:delivery_id>/unconfirm", methods=["POST"]
)
@role_required("manage_all")
def unconfirm_delivery(delivery_id: int):
    delivery = db.get_or_404(DeliveryNote, delivery_id)
    delivery.confirmed = False
    log_action("unconfirm", "delivery_note", delivery.id, "unconfirmed")
    db.session.commit()
    flash("Potvrdenie dodacieho listu zrušené.", "warning")
    return redirect(url_for("delivery.list_delivery_notes"))


@delivery_bp.route("/delivery-notes/<int:delivery_id>/pdf")
@role_required("manage_delivery")
def delivery_pdf(delivery_id: int):
    delivery = db.get_or_404(DeliveryNote, delivery_id)
    app_cfg = current_app.config["APP_CONFIG"]
    pdf_path = generate_delivery_pdf(delivery, app_cfg)
    return send_file(pdf_path, as_attachment=True)
