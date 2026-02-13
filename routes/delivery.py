"""Delivery note routes."""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import groupby

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
from models import (
    Bundle,
    BundleItem,
    DeliveryItem,
    DeliveryItemComponent,
    DeliveryNote,
    DeliveryNoteOrder,
    Order,
    Partner,
    Product,
)
from services.audit import log_action
from services.auth import get_current_user, role_required
from services.numbering import generate_number
from services.pdf import generate_delivery_pdf
from utils import parse_datetime, safe_int, utc_now
from services.tenant import tenant_query, stamp_tenant, tenant_get_or_404

delivery_bp = Blueprint("delivery", __name__)


@delivery_bp.route("/delivery-notes/partner-orders/<int:partner_id>", methods=["GET"])
@role_required("manage_delivery")
def partner_orders(partner_id: int):
    """Return confirmed orders for a partner that have no delivery note links."""
    partner = tenant_query(Partner).filter_by(id=partner_id).first()
    if not partner:
        return jsonify([])

    # Build query for confirmed orders without delivery notes
    query = (
        tenant_query(Order)
        .join(Partner, Order.partner_id == Partner.id)
        .filter(Order.confirmed.is_(True))
        .outerjoin(DeliveryNoteOrder, Order.id == DeliveryNoteOrder.order_id)
        .filter(DeliveryNoteOrder.id.is_(None))
    )

    # Respect group_code
    if partner.group_code:
        query = query.filter(Partner.group_code == partner.group_code)
    else:
        query = query.filter(Order.partner_id == partner_id)

    orders = query.order_by(Order.created_at.desc()).all()

    result = []
    for order in orders:
        items = []
        for item in order.items:
            if item.product:
                name = item.product.name
            elif item.bundle:
                name = item.bundle.name
            elif item.is_manual:
                name = item.manual_name or "Manuálna položka"
            else:
                name = "Položka"
            items.append({
                "product_name": name,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
                "product_id": item.product_id,
                "bundle_id": item.bundle_id,
                "is_manual": item.is_manual,
                "manual_name": item.manual_name,
            })
        result.append({
            "id": order.id,
            "order_number": order.order_number or f"#{order.id}",
            "partner_name": order.partner.name,
            "items": items,
        })
    return jsonify(result)


@delivery_bp.route("/delivery-notes", methods=["GET", "POST"])
@role_required("manage_delivery")
def list_delivery_notes():
    partners = tenant_query(Partner).filter_by(is_active=True, is_deleted=False).all()
    products = tenant_query(Product).filter_by(is_active=True).all()
    bundles = tenant_query(Bundle).filter_by(is_active=True).all()

    if request.method == "POST":
        partner_id = safe_int(request.form.get("partner_id"))
        if not partner_id:
            flash("Partner je povinný.", "danger")
            return redirect(url_for("delivery.list_delivery_notes"))
        # Verify partner belongs to current tenant
        tenant_get_or_404(Partner, partner_id)

        user = get_current_user()
        order_ids = request.form.getlist("order_ids")
        selected_orders = (
            tenant_query(Order).filter(Order.id.in_(order_ids)).all() if order_ids else []
        )

        delivery = DeliveryNote(
            partner_id=partner_id,
            primary_order_id=selected_orders[0].id if selected_orders else None,
            created_by_id=user.id,
            show_prices=request.form.get("show_prices") == "on",
            planned_delivery_datetime=parse_datetime(
                request.form.get("planned_delivery_datetime")
            ),
        )
        stamp_tenant(delivery)
        db.session.add(delivery)
        db.session.flush()
        delivery.note_number = generate_number(
            "delivery_note", partner_id=partner_id,
        )

        # Link selected orders
        for order in selected_orders:
            dno = DeliveryNoteOrder(order_id=order.id)
            stamp_tenant(dno)
            delivery.orders.append(dno)

        # Parse items from dynamic table (same pattern as orders)
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
            if qty and qty > 0:
                if item_type == "product":
                    pid = safe_int(request.form.get(f"items[{idx}][product_id]"))
                    if pid:
                        line_total = unit_price * qty
                        di = DeliveryItem(
                            product_id=pid, quantity=qty,
                            unit_price=unit_price, line_total=line_total,
                        )
                        stamp_tenant(di)
                        delivery.items.append(di)
                elif item_type == "bundle":
                    bid = safe_int(request.form.get(f"items[{idx}][bundle_id]"))
                    if bid:
                        line_total = unit_price * qty
                        delivery_item = DeliveryItem(
                            bundle_id=bid, quantity=qty,
                            unit_price=unit_price, line_total=line_total,
                        )
                        stamp_tenant(delivery_item)
                        bundle = tenant_query(Bundle).filter_by(id=bid).first()
                        if bundle:
                            for bundle_item in bundle.items:
                                comp = DeliveryItemComponent(
                                    product_id=bundle_item.product_id,
                                    quantity=bundle_item.quantity * qty,
                                )
                                stamp_tenant(comp)
                                delivery_item.components.append(comp)
                        delivery.items.append(delivery_item)
                elif item_type == "manual":
                    name = request.form.get(f"items[{idx}][manual_name]", "").strip()
                    if name:
                        line_total = unit_price * qty
                        di = DeliveryItem(
                            is_manual=True, manual_name=name,
                            quantity=qty, unit_price=unit_price,
                            line_total=line_total,
                        )
                        stamp_tenant(di)
                        delivery.items.append(di)
                elif item_type == "order_item":
                    # Items sourced from an order — treat as product/bundle/manual
                    pid = safe_int(request.form.get(f"items[{idx}][product_id]"))
                    bid = safe_int(request.form.get(f"items[{idx}][bundle_id]"))
                    is_manual = request.form.get(f"items[{idx}][is_manual]") == "true"
                    manual_name = request.form.get(f"items[{idx}][manual_name]", "").strip()
                    line_total = unit_price * qty
                    if pid:
                        di = DeliveryItem(
                            product_id=pid, quantity=qty,
                            unit_price=unit_price, line_total=line_total,
                        )
                        stamp_tenant(di)
                        delivery.items.append(di)
                    elif bid:
                        delivery_item = DeliveryItem(
                            bundle_id=bid, quantity=qty,
                            unit_price=unit_price, line_total=line_total,
                        )
                        stamp_tenant(delivery_item)
                        bundle = tenant_query(Bundle).filter_by(id=bid).first()
                        if bundle:
                            for bundle_item in bundle.items:
                                comp = DeliveryItemComponent(
                                    product_id=bundle_item.product_id,
                                    quantity=bundle_item.quantity * qty,
                                )
                                stamp_tenant(comp)
                                delivery_item.components.append(comp)
                        delivery.items.append(delivery_item)
                    elif is_manual and manual_name:
                        di = DeliveryItem(
                            is_manual=True, manual_name=manual_name,
                            quantity=qty, unit_price=unit_price,
                            line_total=line_total,
                        )
                        stamp_tenant(di)
                        delivery.items.append(di)
            idx += 1

        log_action("create", "delivery_note", delivery.id, "created")
        db.session.commit()
        flash("Dodací list vytvorený.", "success")
        return redirect(url_for("delivery.list_delivery_notes"))

    query = tenant_query(DeliveryNote).order_by(DeliveryNote.created_at.desc())

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

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    return render_template(
        "delivery_notes.html",
        delivery_notes=delivery_list,
        delivery_notes_by_date=delivery_notes_by_date,
        total=total,
        page=page,
        per_page=per_page,
        partners=partners,
        products=products,
        bundles=bundles,
        today=today,
        yesterday=yesterday,
    )


@delivery_bp.route("/delivery-notes/<int:delivery_id>/detail", methods=["GET"])
@role_required("manage_delivery")
def delivery_detail(delivery_id: int):
    delivery = tenant_get_or_404(DeliveryNote, delivery_id)
    items = []
    for item in delivery.items:
        if item.product:
            name = item.product.name
            item_type = "product"
        elif item.bundle:
            name = item.bundle.name
            item_type = "bundle"
        elif item.is_manual:
            name = item.manual_name or "Manuálna položka"
            item_type = "manual"
        else:
            name = "Položka"
            item_type = "product"
        line_total = item.line_total or (item.unit_price * item.quantity)
        items.append({
            "type": item_type,
            "product_id": item.product_id,
            "bundle_id": item.bundle_id,
            "is_manual": item.is_manual or False,
            "manual_name": item.manual_name,
            "name": name,
            "quantity": item.quantity,
            "unit_price": str(item.unit_price),
            "line_total": str(line_total),
        })
    orders_list = []
    for link in delivery.orders:
        orders_list.append({
            "id": link.order.id,
            "order_number": link.order.order_number or f"#{link.order.id}",
        })
    return jsonify({
        "id": delivery.id,
        "note_number": delivery.note_number or f"DL-{delivery.id}",
        "partner_id": delivery.partner_id,
        "partner_name": delivery.partner.name if delivery.partner else "",
        "planned_delivery_datetime": delivery.planned_delivery_datetime.strftime("%Y-%m-%dT%H:%M") if delivery.planned_delivery_datetime else "",
        "actual_delivery_datetime": delivery.actual_delivery_datetime.strftime("%Y-%m-%dT%H:%M") if delivery.actual_delivery_datetime else "",
        "show_prices": delivery.show_prices,
        "confirmed": delivery.confirmed,
        "is_locked": delivery.is_locked,
        "has_logistics_plans": bool(delivery.logistics_plans),
        "has_invoice_refs": bool(delivery.invoice_item_refs),
        "invoiced": delivery.invoiced,
        "orders": orders_list,
        "items": items,
    })


@delivery_bp.route("/delivery-notes/<int:delivery_id>/edit", methods=["POST"])
@role_required("manage_delivery")
def edit_delivery(delivery_id: int):
    delivery = tenant_get_or_404(DeliveryNote, delivery_id)
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
    # Replace items
    delivery.items.clear()
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
        if qty and qty > 0:
            if item_type == "product":
                pid = safe_int(request.form.get(f"items[{idx}][product_id]"))
                if pid:
                    line_total = unit_price * qty
                    di = DeliveryItem(
                        product_id=pid, quantity=qty,
                        unit_price=unit_price, line_total=line_total,
                    )
                    stamp_tenant(di)
                    delivery.items.append(di)
            elif item_type == "bundle":
                bid = safe_int(request.form.get(f"items[{idx}][bundle_id]"))
                if bid:
                    line_total = unit_price * qty
                    delivery_item = DeliveryItem(
                        bundle_id=bid, quantity=qty,
                        unit_price=unit_price, line_total=line_total,
                    )
                    stamp_tenant(delivery_item)
                    bundle = tenant_query(Bundle).filter_by(id=bid).first()
                    if bundle:
                        for bundle_item in bundle.items:
                            comp = DeliveryItemComponent(
                                product_id=bundle_item.product_id,
                                quantity=bundle_item.quantity * qty,
                            )
                            stamp_tenant(comp)
                            delivery_item.components.append(comp)
                    delivery.items.append(delivery_item)
            elif item_type == "manual":
                name = request.form.get(f"items[{idx}][manual_name]", "").strip()
                if name:
                    line_total = unit_price * qty
                    di = DeliveryItem(
                        is_manual=True, manual_name=name,
                        quantity=qty, unit_price=unit_price,
                        line_total=line_total,
                    )
                    stamp_tenant(di)
                    delivery.items.append(di)
            elif item_type == "order_item":
                pid = safe_int(request.form.get(f"items[{idx}][product_id]"))
                bid = safe_int(request.form.get(f"items[{idx}][bundle_id]"))
                is_manual = request.form.get(f"items[{idx}][is_manual]") == "true"
                manual_name = request.form.get(f"items[{idx}][manual_name]", "").strip()
                line_total = unit_price * qty
                if pid:
                    di = DeliveryItem(
                        product_id=pid, quantity=qty,
                        unit_price=unit_price, line_total=line_total,
                    )
                    stamp_tenant(di)
                    delivery.items.append(di)
                elif bid:
                    delivery_item = DeliveryItem(
                        bundle_id=bid, quantity=qty,
                        unit_price=unit_price, line_total=line_total,
                    )
                    stamp_tenant(delivery_item)
                    bundle = tenant_query(Bundle).filter_by(id=bid).first()
                    if bundle:
                        for bundle_item in bundle.items:
                            comp = DeliveryItemComponent(
                                product_id=bundle_item.product_id,
                                quantity=bundle_item.quantity * qty,
                            )
                            stamp_tenant(comp)
                            delivery_item.components.append(comp)
                    delivery.items.append(delivery_item)
                elif is_manual and manual_name:
                    di = DeliveryItem(
                        is_manual=True, manual_name=manual_name,
                        quantity=qty, unit_price=unit_price,
                        line_total=line_total,
                    )
                    stamp_tenant(di)
                    delivery.items.append(di)
        idx += 1
    log_action("edit", "delivery_note", delivery.id, "updated")
    db.session.commit()
    flash("Dodací list upravený.", "success")
    return redirect(url_for("delivery.list_delivery_notes"))


@delivery_bp.route("/delivery-notes/<int:delivery_id>/delete", methods=["POST"])
@role_required("manage_delivery")
def delete_delivery(delivery_id: int):
    delivery = tenant_get_or_404(DeliveryNote, delivery_id)
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
    delivery = tenant_get_or_404(DeliveryNote, delivery_id)
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
    delivery = tenant_get_or_404(DeliveryNote, delivery_id)
    delivery.confirmed = False
    log_action("unconfirm", "delivery_note", delivery.id, "unconfirmed")
    db.session.commit()
    flash("Potvrdenie dodacieho listu zrušené.", "warning")
    return redirect(url_for("delivery.list_delivery_notes"))


@delivery_bp.route("/delivery-notes/<int:delivery_id>/pdf")
@role_required("manage_delivery")
def delivery_pdf(delivery_id: int):
    delivery = tenant_get_or_404(DeliveryNote, delivery_id)
    app_cfg = current_app.config["APP_CONFIG"]
    pdf_path = generate_delivery_pdf(delivery, app_cfg)
    return send_file(pdf_path, as_attachment=True)
