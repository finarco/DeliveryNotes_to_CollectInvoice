"""Product and bundle management routes."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import db
from models import (
    Bundle,
    BundleItem,
    BundlePriceHistory,
    DeliveryItem,
    OrderItem,
    Product,
    ProductPriceHistory,
    ProductRestriction,
)
from services.audit import log_action
from services.auth import role_required
from services.numbering import generate_number
from utils import safe_float, safe_int
from services.tenant import tenant_query, stamp_tenant, tenant_get_or_404

products_bp = Blueprint("products", __name__)


@products_bp.route("/products", methods=["GET", "POST"])
@role_required("manage_orders")
def list_products():
    if request.method == "POST":
        price = safe_float(request.form.get("price"))
        product = Product(
            name=request.form.get("name", "").strip(),
            description=request.form.get("description", ""),
            long_text=request.form.get("long_text", ""),
            price=price,
            vat_rate=safe_float(request.form.get("vat_rate"), default=20.0),
            is_service=request.form.get("is_service") == "on",
            discount_excluded=request.form.get("discount_excluded") == "on",
        )
        stamp_tenant(product)
        db.session.add(product)
        db.session.flush()
        product.product_number = generate_number(
            "product", is_service=product.is_service
        )
        ph = ProductPriceHistory(price=price)
        stamp_tenant(ph)
        product.price_history.append(ph)
        db.session.commit()
        flash("Produkt uložený.", "success")
        return redirect(url_for("products.list_products"))
    return render_template("products.html", products=tenant_query(Product).all())


@products_bp.route("/products/<int:product_id>/toggle", methods=["POST"])
@role_required("manage_orders")
def toggle_product(product_id: int):
    product = tenant_get_or_404(Product, product_id)
    product.is_active = not product.is_active
    action = "activate" if product.is_active else "deactivate"
    log_action(action, "product", product.id, f"is_active={product.is_active}")
    db.session.commit()
    status = "aktivovaný" if product.is_active else "deaktivovaný"
    flash(f"Produkt '{product.name}' {status}.", "success")
    return redirect(url_for("products.list_products"))


@products_bp.route("/products/<int:product_id>/edit", methods=["POST"])
@role_required("manage_orders")
def edit_product(product_id: int):
    product = tenant_get_or_404(Product, product_id)
    product.name = request.form.get("name", "").strip() or product.name
    product.description = request.form.get("description", "")
    product.long_text = request.form.get("long_text", "")
    new_price = safe_float(request.form.get("price"))
    if new_price != product.price:
        ph = ProductPriceHistory(price=new_price)
        stamp_tenant(ph)
        product.price_history.append(ph)
    product.price = new_price
    product.vat_rate = safe_float(request.form.get("vat_rate"), default=20.0)
    product.is_service = request.form.get("is_service") == "on"
    product.discount_excluded = request.form.get("discount_excluded") == "on"
    log_action("edit", "product", product.id, "updated")
    db.session.commit()
    flash(f"Produkt '{product.name}' upravený.", "success")
    return redirect(url_for("products.list_products"))


@products_bp.route("/products/<int:product_id>/delete", methods=["POST"])
@role_required("manage_orders")
def delete_product(product_id: int):
    product = tenant_get_or_404(Product, product_id)
    # Block deletion if the product is referenced in orders, deliveries, or bundles
    in_orders = tenant_query(OrderItem).filter_by(product_id=product.id).first()
    in_deliveries = tenant_query(DeliveryItem).filter_by(product_id=product.id).first()
    in_bundles = tenant_query(BundleItem).filter_by(product_id=product.id).first()
    if in_orders or in_deliveries or in_bundles:
        refs = []
        if in_orders:
            refs.append("objednávkach")
        if in_deliveries:
            refs.append("dodacích listoch")
        if in_bundles:
            refs.append("kombináciách")
        flash(
            f"Produkt '{product.name}' nie je možné vymazať — je použitý v {', '.join(refs)}. "
            f"Použite deaktiváciu.",
            "danger",
        )
        return redirect(url_for("products.list_products"))
    name = product.name
    # Clean up restrictions referencing this product
    tenant_query(ProductRestriction).filter(
        (ProductRestriction.product_id == product.id)
        | (ProductRestriction.restricted_with_id == product.id)
    ).delete(synchronize_session="fetch")
    log_action("delete", "product", product.id, f"deleted: {name}")
    db.session.delete(product)
    db.session.commit()
    flash(f"Produkt '{name}' vymazaný.", "warning")
    return redirect(url_for("products.list_products"))


@products_bp.route("/bundles", methods=["GET", "POST"])
@role_required("manage_orders")
def list_bundles():
    all_products = tenant_query(Product).all()
    if request.method == "POST":
        bundle_price = safe_float(request.form.get("bundle_price"))
        bundle = Bundle(
            name=request.form.get("name", "").strip(),
            bundle_price=bundle_price,
            discount_excluded=request.form.get("discount_excluded") == "on",
        )
        stamp_tenant(bundle)
        db.session.add(bundle)
        db.session.flush()
        bundle.bundle_number = generate_number("bundle")
        for product in all_products:
            qty = safe_int(request.form.get(f"bundle_product_{product.id}"))
            if qty > 0:
                bi = BundleItem(product_id=product.id, quantity=qty)
                stamp_tenant(bi)
                bundle.items.append(bi)
        bph = BundlePriceHistory(price=bundle_price)
        stamp_tenant(bph)
        bundle.price_history.append(bph)
        db.session.commit()
        flash("Kombinácia uložená.", "success")
        return redirect(url_for("products.list_bundles"))
    return render_template(
        "bundles.html",
        bundles=tenant_query(Bundle).order_by(Bundle.id.desc()).all(),
        products=all_products,
    )


@products_bp.route("/bundles/<int:bundle_id>/toggle", methods=["POST"])
@role_required("manage_orders")
def toggle_bundle(bundle_id: int):
    bundle = tenant_get_or_404(Bundle, bundle_id)
    bundle.is_active = not bundle.is_active
    action = "activate" if bundle.is_active else "deactivate"
    log_action(action, "bundle", bundle.id, f"is_active={bundle.is_active}")
    db.session.commit()
    status = "aktivovaná" if bundle.is_active else "deaktivovaná"
    flash(f"Kombinácia '{bundle.name}' {status}.", "success")
    return redirect(url_for("products.list_bundles"))


@products_bp.route("/bundles/<int:bundle_id>/edit", methods=["POST"])
@role_required("manage_orders")
def edit_bundle(bundle_id: int):
    bundle = tenant_get_or_404(Bundle, bundle_id)
    bundle.name = request.form.get("name", "").strip() or bundle.name
    new_price = safe_float(request.form.get("bundle_price"))
    if new_price != bundle.bundle_price:
        bph = BundlePriceHistory(price=new_price)
        stamp_tenant(bph)
        bundle.price_history.append(bph)
    bundle.bundle_price = new_price
    bundle.discount_excluded = request.form.get("discount_excluded") == "on"
    log_action("edit", "bundle", bundle.id, "updated")
    db.session.commit()
    flash(f"Kombinácia '{bundle.name}' upravená.", "success")
    return redirect(url_for("products.list_bundles"))


@products_bp.route("/bundles/<int:bundle_id>/delete", methods=["POST"])
@role_required("manage_orders")
def delete_bundle(bundle_id: int):
    bundle = tenant_get_or_404(Bundle, bundle_id)
    # Block deletion if the bundle is referenced in deliveries
    in_deliveries = tenant_query(DeliveryItem).filter_by(bundle_id=bundle.id).first()
    if in_deliveries:
        flash(
            f"Kombinácia '{bundle.name}' nie je možné vymazať — je použitá v dodacích listoch. "
            f"Použite deaktiváciu.",
            "danger",
        )
        return redirect(url_for("products.list_bundles"))
    name = bundle.name
    log_action("delete", "bundle", bundle.id, f"deleted: {name}")
    db.session.delete(bundle)
    db.session.commit()
    flash(f"Kombinácia '{name}' vymazaná.", "warning")
    return redirect(url_for("products.list_bundles"))
