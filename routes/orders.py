"""Order management routes."""

from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from extensions import db
from models import Bundle, LogisticsPlan, Order, OrderItem, Partner, PartnerAddress, Product
from services.audit import log_action
from services.auth import get_current_user, role_required
from services.numbering import generate_number
from utils import parse_datetime, safe_int
from services.tenant import tenant_query, stamp_tenant, tenant_get_or_404

orders_bp = Blueprint("orders", __name__)


@orders_bp.route("/orders/partner-addresses/<int:partner_id>", methods=["GET"])
@role_required("manage_orders")
def partner_addresses(partner_id: int):
    addresses = tenant_query(PartnerAddress).filter_by(partner_id=partner_id).all()
    return jsonify([
        {
            "id": a.id,
            "label": f"{a.address_type} - {a.street} {a.street_number}, {a.city}",
        }
        for a in addresses
    ])


@orders_bp.route("/orders", methods=["GET", "POST"])
@role_required("manage_orders")
def list_orders():
    partners = tenant_query(Partner).filter_by(is_active=True, is_deleted=False).all()
    products = tenant_query(Product).filter_by(is_active=True).all()
    bundles = tenant_query(Bundle).filter_by(is_active=True).all()

    if request.method == "POST":
        partner_id = safe_int(request.form.get("partner_id"))
        if not partner_id:
            flash("Partner je povinný.", "danger")
            return redirect(url_for("orders.list_orders"))
        # Verify partner belongs to current tenant
        tenant_get_or_404(Partner, partner_id)
        show_prices = request.form.get("show_prices") == "on"
        pickup_address_id = safe_int(request.form.get("pickup_address_id")) or None
        delivery_address_id = safe_int(request.form.get("delivery_address_id")) or None
        # Verify addresses belong to current tenant
        if pickup_address_id:
            tenant_get_or_404(PartnerAddress, pickup_address_id)
        if delivery_address_id:
            tenant_get_or_404(PartnerAddress, delivery_address_id)

        user = get_current_user()
        order = Order(
            partner_id=partner_id,
            pickup_address_id=pickup_address_id,
            delivery_address_id=delivery_address_id,
            created_by_id=user.id,
            pickup_datetime=parse_datetime(
                request.form.get("pickup_datetime")
            ),
            delivery_datetime=parse_datetime(
                request.form.get("delivery_datetime")
            ),
            pickup_method=request.form.get("pickup_method", ""),
            delivery_method=request.form.get("delivery_method", ""),
            payment_method=request.form.get("payment_method", ""),
            payment_terms=request.form.get("payment_terms", ""),
            show_prices=show_prices,
        )
        stamp_tenant(order)
        db.session.add(order)
        db.session.flush()
        order.order_number = generate_number(
            "order", partner_id=partner_id
        )
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
            if qty and qty > 0:
                if item_type == "product":
                    pid = safe_int(request.form.get(f"items[{idx}][product_id]"))
                    if pid:
                        oi = OrderItem(product_id=pid, quantity=qty, unit_price=unit_price)
                        stamp_tenant(oi)
                        order.items.append(oi)
                elif item_type == "bundle":
                    bid = safe_int(request.form.get(f"items[{idx}][bundle_id]"))
                    if bid:
                        oi = OrderItem(bundle_id=bid, quantity=qty, unit_price=unit_price)
                        stamp_tenant(oi)
                        order.items.append(oi)
                elif item_type == "manual":
                    name = request.form.get(f"items[{idx}][manual_name]", "").strip()
                    if name:
                        oi = OrderItem(is_manual=True, manual_name=name, quantity=qty, unit_price=unit_price)
                        stamp_tenant(oi)
                        order.items.append(oi)
            idx += 1
        log_action("create", "order", order.id, f"partner={partner_id}")
        db.session.commit()
        flash("Objednávka vytvorená.", "success")
        return redirect(url_for("orders.list_orders"))

    query = tenant_query(Order).order_by(Order.created_at.desc())

    page = max(1, safe_int(request.args.get("page"), default=1))
    per_page = 20
    total = query.count()
    orders_list = (
        query.offset((page - 1) * per_page).limit(per_page).all()
    )
    return render_template(
        "orders.html",
        orders=orders_list,
        total=total,
        page=page,
        per_page=per_page,
        partners=partners,
        products=products,
        bundles=bundles,
    )


@orders_bp.route("/orders/<int:order_id>/detail", methods=["GET"])
@role_required("manage_orders")
def order_detail(order_id: int):
    order = tenant_get_or_404(Order, order_id)
    items = []
    for item in order.items:
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
        items.append({
            "type": item_type,
            "product_id": item.product_id,
            "bundle_id": item.bundle_id,
            "is_manual": item.is_manual or False,
            "manual_name": item.manual_name,
            "name": name,
            "quantity": item.quantity,
            "unit_price": str(item.unit_price),
        })
    return jsonify({
        "id": order.id,
        "order_number": order.order_number or f"#{order.id}",
        "partner_id": order.partner_id,
        "partner_name": order.partner.name if order.partner else "",
        "pickup_datetime": order.pickup_datetime.strftime("%Y-%m-%dT%H:%M") if order.pickup_datetime else "",
        "delivery_datetime": order.delivery_datetime.strftime("%Y-%m-%dT%H:%M") if order.delivery_datetime else "",
        "pickup_method": order.pickup_method or "",
        "delivery_method": order.delivery_method or "",
        "payment_method": order.payment_method or "",
        "payment_terms": order.payment_terms or "",
        "show_prices": order.show_prices,
        "confirmed": order.confirmed,
        "is_locked": order.is_locked,
        "has_delivery_notes": bool(order.delivery_note_links),
        "items": items,
    })


@orders_bp.route("/orders/<int:order_id>/edit", methods=["POST"])
@role_required("manage_orders")
def edit_order(order_id: int):
    order = tenant_get_or_404(Order, order_id)
    if order.is_locked:
        flash("Objednávka je uzamknutá.", "danger")
        return redirect(url_for("orders.list_orders"))
    if order.delivery_note_links:
        flash("Objednávka má priradený dodací list a nemôže byť upravená.", "danger")
        return redirect(url_for("orders.list_orders"))
    order.pickup_datetime = parse_datetime(request.form.get("pickup_datetime"))
    order.delivery_datetime = parse_datetime(request.form.get("delivery_datetime"))
    order.pickup_method = request.form.get("pickup_method", "")
    order.delivery_method = request.form.get("delivery_method", "")
    order.payment_method = request.form.get("payment_method", "")
    order.payment_terms = request.form.get("payment_terms", "")
    order.show_prices = request.form.get("show_prices") == "on"
    # Replace items
    order.items.clear()
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
                    oi = OrderItem(product_id=pid, quantity=qty, unit_price=unit_price)
                    stamp_tenant(oi)
                    order.items.append(oi)
            elif item_type == "bundle":
                bid = safe_int(request.form.get(f"items[{idx}][bundle_id]"))
                if bid:
                    oi = OrderItem(bundle_id=bid, quantity=qty, unit_price=unit_price)
                    stamp_tenant(oi)
                    order.items.append(oi)
            elif item_type == "manual":
                name = request.form.get(f"items[{idx}][manual_name]", "").strip()
                if name:
                    oi = OrderItem(is_manual=True, manual_name=name, quantity=qty, unit_price=unit_price)
                    stamp_tenant(oi)
                    order.items.append(oi)
        idx += 1
    log_action("edit", "order", order.id, "updated")
    db.session.commit()
    flash("Objednávka upravená.", "success")
    return redirect(url_for("orders.list_orders"))


@orders_bp.route("/orders/<int:order_id>/delete", methods=["POST"])
@role_required("manage_orders")
def delete_order(order_id: int):
    order = tenant_get_or_404(Order, order_id)
    if order.is_locked:
        flash("Objednávka je uzamknutá a nemôže byť vymazaná.", "danger")
        return redirect(url_for("orders.list_orders"))
    if order.delivery_note_links:
        flash("Objednávka má priradený dodací list a nemôže byť vymazaná.", "danger")
        return redirect(url_for("orders.list_orders"))
    if tenant_query(LogisticsPlan).filter_by(order_id=order.id).first():
        flash("Objednávka má priradený logistický plán a nemôže byť vymazaná.", "danger")
        return redirect(url_for("orders.list_orders"))
    log_action("delete", "order", order.id, f"deleted order #{order.id}")
    db.session.delete(order)
    db.session.commit()
    flash("Objednávka vymazaná.", "warning")
    return redirect(url_for("orders.list_orders"))


@orders_bp.route(
    "/orders/<int:order_id>/confirm", methods=["POST"]
)
@role_required("manage_orders")
def confirm_order(order_id: int):
    order = tenant_get_or_404(Order, order_id)
    order.confirmed = True
    log_action("confirm", "order", order.id, "confirmed")
    db.session.commit()
    flash("Objednávka potvrdená.", "success")
    return redirect(url_for("orders.list_orders"))


@orders_bp.route(
    "/orders/<int:order_id>/unconfirm", methods=["POST"]
)
@role_required("manage_all")
def unconfirm_order(order_id: int):
    order = tenant_get_or_404(Order, order_id)
    order.confirmed = False
    log_action("unconfirm", "order", order.id, "unconfirmed")
    db.session.commit()
    flash("Potvrdenie objednávky zrušené.", "warning")
    return redirect(url_for("orders.list_orders"))
