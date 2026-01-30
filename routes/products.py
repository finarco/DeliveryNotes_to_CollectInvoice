"""Product and bundle management routes."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import db
from models import (
    Bundle,
    BundleItem,
    BundlePriceHistory,
    Product,
    ProductPriceHistory,
)
from services.auth import role_required
from utils import safe_float, safe_int

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
        db.session.add(product)
        db.session.flush()
        product.price_history.append(ProductPriceHistory(price=price))
        db.session.commit()
        flash("Produkt uložený.", "success")
        return redirect(url_for("products.list_products"))
    return render_template("products.html", products=Product.query.all())


@products_bp.route("/bundles", methods=["GET", "POST"])
@role_required("manage_orders")
def list_bundles():
    all_products = Product.query.all()
    if request.method == "POST":
        bundle_price = safe_float(request.form.get("bundle_price"))
        bundle = Bundle(
            name=request.form.get("name", "").strip(),
            bundle_price=bundle_price,
            discount_excluded=request.form.get("discount_excluded") == "on",
        )
        db.session.add(bundle)
        db.session.flush()
        for product in all_products:
            qty = safe_int(request.form.get(f"bundle_product_{product.id}"))
            if qty > 0:
                bundle.items.append(
                    BundleItem(product_id=product.id, quantity=qty)
                )
        bundle.price_history.append(BundlePriceHistory(price=bundle_price))
        db.session.commit()
        flash("Kombinácia uložená.", "success")
        return redirect(url_for("products.list_bundles"))
    return render_template(
        "bundles.html",
        bundles=Bundle.query.order_by(Bundle.id.desc()).all(),
        products=all_products,
    )
