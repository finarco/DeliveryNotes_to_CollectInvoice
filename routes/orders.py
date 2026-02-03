"""Order management routes."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import db
from models import LogisticsPlan, Order, OrderItem, Partner, PartnerAddress, Product
from services.audit import log_action
from services.auth import get_current_user, role_required
from services.numbering import generate_number
from utils import parse_datetime, safe_int

orders_bp = Blueprint("orders", __name__)


@orders_bp.route("/orders", methods=["GET", "POST"])
@role_required("manage_orders")
def list_orders():
    partners = Partner.query.filter_by(is_active=True, is_deleted=False).all()
    addresses = PartnerAddress.query.all()
    products = Product.query.filter_by(is_active=True).all()

    if request.method == "POST":
        partner_id = safe_int(request.form.get("partner_id"))
        if not partner_id:
            flash("Partner je povinný.", "danger")
            return redirect(url_for("orders.list_orders"))
        show_prices = request.form.get("show_prices") == "on"
        pickup_address_id = request.form.get("pickup_address_id")
        delivery_address_id = request.form.get("delivery_address_id")

        user = get_current_user()
        order = Order(
            partner_id=partner_id,
            pickup_address_id=safe_int(pickup_address_id) or None,
            delivery_address_id=safe_int(delivery_address_id) or None,
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
        db.session.add(order)
        db.session.flush()
        order.order_number = generate_number(
            "order", partner_id=partner_id
        )
        all_products = Product.query.all()
        for product in all_products:
            qty = safe_int(request.form.get(f"product_{product.id}"))
            if qty > 0:
                order.items.append(
                    OrderItem(
                        product_id=product.id,
                        quantity=qty,
                        unit_price=product.price,
                    )
                )
        log_action("create", "order", order.id, f"partner={partner_id}")
        db.session.commit()
        flash("Objednávka vytvorená.", "success")
        return redirect(url_for("orders.list_orders"))

    query = Order.query.order_by(Order.created_at.desc())

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
        addresses=addresses,
        products=products,
    )


@orders_bp.route("/orders/<int:order_id>/edit", methods=["POST"])
@role_required("manage_orders")
def edit_order(order_id: int):
    order = db.get_or_404(Order, order_id)
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
    log_action("edit", "order", order.id, "updated")
    db.session.commit()
    flash("Objednávka upravená.", "success")
    return redirect(url_for("orders.list_orders"))


@orders_bp.route("/orders/<int:order_id>/delete", methods=["POST"])
@role_required("manage_orders")
def delete_order(order_id: int):
    order = db.get_or_404(Order, order_id)
    if order.is_locked:
        flash("Objednávka je uzamknutá a nemôže byť vymazaná.", "danger")
        return redirect(url_for("orders.list_orders"))
    if order.delivery_note_links:
        flash("Objednávka má priradený dodací list a nemôže byť vymazaná.", "danger")
        return redirect(url_for("orders.list_orders"))
    if LogisticsPlan.query.filter_by(order_id=order.id).first():
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
    order = db.get_or_404(Order, order_id)
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
    order = db.get_or_404(Order, order_id)
    order.confirmed = False
    log_action("unconfirm", "order", order.id, "unconfirmed")
    db.session.commit()
    flash("Potvrdenie objednávky zrušené.", "warning")
    return redirect(url_for("orders.list_orders"))
