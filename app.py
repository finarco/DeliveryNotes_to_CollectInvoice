from __future__ import annotations

import datetime
import os
from typing import Optional

import yaml
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from werkzeug.security import check_password_hash, generate_password_hash

from config_models import AppConfig, EmailConfig, SuperfakturaConfig
from mailer import send_document_email
from superfaktura_client import SuperFakturaClient


db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="admin")


class Partner(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    note = db.Column(db.String(255))
    street = db.Column(db.String(120))
    street_number = db.Column(db.String(30))
    postal_code = db.Column(db.String(20))
    city = db.Column(db.String(120))
    group_code = db.Column(db.String(60))
    ico = db.Column(db.String(20))
    dic = db.Column(db.String(20))
    ic_dph = db.Column(db.String(20))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(60))
    price_level = db.Column(db.String(60))
    discount_percent = db.Column(db.Float, default=0.0)
    contacts = db.relationship("Contact", backref="partner", cascade="all, delete-orphan")
    addresses = db.relationship(
        "PartnerAddress", backref="partner", cascade="all, delete-orphan"
    )


class PartnerAddress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    address_type = db.Column(db.String(40), nullable=False)
    related_partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"))
    street = db.Column(db.String(120))
    street_number = db.Column(db.String(30))
    postal_code = db.Column(db.String(20))
    city = db.Column(db.String(120))
    related_partner = db.relationship("Partner", foreign_keys=[related_partner_id])


class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(60))
    role = db.Column(db.String(60))
    can_order = db.Column(db.Boolean, default=False)
    can_receive = db.Column(db.Boolean, default=False)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255))
    long_text = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False)
    is_service = db.Column(db.Boolean, default=True)
    discount_excluded = db.Column(db.Boolean, default=False)
    price_history = db.relationship(
        "ProductPriceHistory", backref="product", cascade="all, delete-orphan"
    )


class ProductPriceHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    price = db.Column(db.Float, nullable=False)
    changed_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class Bundle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    bundle_price = db.Column(db.Float, nullable=False)
    discount_excluded = db.Column(db.Boolean, default=False)
    items = db.relationship("BundleItem", backref="bundle", cascade="all, delete-orphan")
    price_history = db.relationship(
        "BundlePriceHistory", backref="bundle", cascade="all, delete-orphan"
    )


class BundlePriceHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bundle_id = db.Column(db.Integer, db.ForeignKey("bundle.id"), nullable=False)
    price = db.Column(db.Float, nullable=False)
    changed_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class BundleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bundle_id = db.Column(db.Integer, db.ForeignKey("bundle.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    product = db.relationship("Product")


class ProductRestriction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    restricted_with_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    product = db.relationship("Product", foreign_keys=[product_id])
    restricted_with = db.relationship("Product", foreign_keys=[restricted_with_id])


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    pickup_address_id = db.Column(db.Integer, db.ForeignKey("partner_address.id"))
    delivery_address_id = db.Column(db.Integer, db.ForeignKey("partner_address.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    pickup_datetime = db.Column(db.DateTime)
    delivery_datetime = db.Column(db.DateTime)
    pickup_method = db.Column(db.String(60))
    delivery_method = db.Column(db.String(60))
    payment_method = db.Column(db.String(60))
    payment_terms = db.Column(db.String(120))
    show_prices = db.Column(db.Boolean, default=True)
    confirmed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    partner = db.relationship("Partner")
    pickup_address = db.relationship("PartnerAddress", foreign_keys=[pickup_address_id])
    delivery_address = db.relationship("PartnerAddress", foreign_keys=[delivery_address_id])
    created_by = db.relationship("User")
    items = db.relationship("OrderItem", backref="order", cascade="all, delete-orphan")


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Float, nullable=False)
    product = db.relationship("Product")


class DeliveryNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    primary_order_id = db.Column(db.Integer, db.ForeignKey("order.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    show_prices = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    invoiced = db.Column(db.Boolean, default=False)
    planned_delivery_datetime = db.Column(db.DateTime)
    actual_delivery_datetime = db.Column(db.DateTime)
    confirmed = db.Column(db.Boolean, default=False)
    primary_order = db.relationship("Order")
    created_by = db.relationship("User")
    items = db.relationship("DeliveryItem", backref="delivery_note", cascade="all, delete-orphan")
    orders = db.relationship(
        "DeliveryNoteOrder", backref="delivery_note", cascade="all, delete-orphan"
    )


class DeliveryNoteOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    delivery_note_id = db.Column(
        db.Integer, db.ForeignKey("delivery_note.id"), nullable=False
    )
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    order = db.relationship("Order")


class DeliveryItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    delivery_note_id = db.Column(db.Integer, db.ForeignKey("delivery_note.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    bundle_id = db.Column(db.Integer, db.ForeignKey("bundle.id"))
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Float, nullable=False)
    line_total = db.Column(db.Float, nullable=False, default=0.0)
    product = db.relationship("Product")
    bundle = db.relationship("Bundle")
    components = db.relationship(
        "DeliveryItemComponent", backref="delivery_item", cascade="all, delete-orphan"
    )


class DeliveryItemComponent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    delivery_item_id = db.Column(
        db.Integer, db.ForeignKey("delivery_item.id"), nullable=False
    )
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    product = db.relationship("Product")


class Vehicle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    notes = db.Column(db.String(255))
    active = db.Column(db.Boolean, default=True)
    schedules = db.relationship(
        "VehicleSchedule", backref="vehicle", cascade="all, delete-orphan"
    )


class VehicleSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicle.id"), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=False)  # 0=Mon
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)


class LogisticsPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"))
    delivery_note_id = db.Column(db.Integer, db.ForeignKey("delivery_note.id"))
    plan_type = db.Column(db.String(40), nullable=False)  # pickup/delivery
    planned_datetime = db.Column(db.DateTime, nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicle.id"))
    order = db.relationship("Order")
    delivery_note = db.relationship("DeliveryNote")
    vehicle = db.relationship("Vehicle")


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    total = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(30), default="draft")
    partner = db.relationship("Partner")
    items = db.relationship("InvoiceItem", backref="invoice", cascade="all, delete-orphan")


class InvoiceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"), nullable=False)
    source_delivery_id = db.Column(db.Integer, db.ForeignKey("delivery_note.id"))
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Float, nullable=False)
    total = db.Column(db.Float, nullable=False)
    is_manual = db.Column(db.Boolean, default=False)


ROLE_PERMISSIONS = {
    "admin": {"manage_all"},
    "operator": {"manage_partners", "manage_orders", "manage_delivery", "manage_invoices"},
    "collector": {"manage_delivery"},
    "customer": {"view_own"},
}


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    app_cfg = raw.get("app", {})
    email_cfg = raw.get("email", {})
    sf_cfg = raw.get("superfaktura", {})
    return (
        AppConfig(
            name=app_cfg.get("name", "Dodacie listy"),
            secret_key=app_cfg.get("secret_key", "change-me"),
            base_currency=app_cfg.get("base_currency", "EUR"),
            show_prices_default=app_cfg.get("show_prices_default", True),
        ),
        EmailConfig(
            enabled=email_cfg.get("enabled", False),
            smtp_host=email_cfg.get("smtp_host", ""),
            smtp_port=int(email_cfg.get("smtp_port", 587)),
            smtp_user=email_cfg.get("smtp_user", ""),
            smtp_password=email_cfg.get("smtp_password", ""),
            sender=email_cfg.get("sender", ""),
            operator_cc=email_cfg.get("operator_cc", ""),
        ),
        SuperfakturaConfig(
            enabled=sf_cfg.get("enabled", False),
            api_email=sf_cfg.get("api_email", ""),
            api_key=sf_cfg.get("api_key", ""),
            company_id=str(sf_cfg.get("company_id", "")),
            base_url=sf_cfg.get("base_url", "https://api.superfaktura.sk"),
        ),
        raw.get("database", {}).get("uri", "sqlite:///delivery_notes.db"),
    )


def create_app():
    app_cfg, email_cfg, sf_cfg, db_uri = load_config()
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.secret_key = app_cfg.secret_key
    app.config["APP_CONFIG"] = app_cfg
    app.config["EMAIL_CONFIG"] = email_cfg
    app.config["SF_CONFIG"] = sf_cfg

    db.init_app(app)

    with app.app_context():
        db.create_all()
        ensure_admin_user()

    @app.context_processor
    def inject_globals():
        return {"app_config": app_cfg}

    def current_user() -> Optional[User]:
        user_id = session.get("user_id")
        if not user_id:
            return None
        return User.query.get(user_id)

    def require_login():
        if not current_user():
            return redirect(url_for("login"))
        return None

    def require_role(required_permission: str):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        permissions = ROLE_PERMISSIONS.get(user.role, set())
        if required_permission not in permissions and "manage_all" not in permissions:
            flash("Nemáte oprávnenie na tento krok.")
            return redirect(url_for("index"))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = User.query.filter_by(username=username).first()
            if user and check_password_hash(user.password_hash, password):
                session["user_id"] = user.id
                flash("Prihlásenie úspešné.")
                return redirect(url_for("index"))
            flash("Nesprávne prihlasovacie údaje.")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        login_redirect = require_login()
        if login_redirect:
            return login_redirect
        return render_template(
            "index.html",
            partner_count=Partner.query.count(),
            order_count=Order.query.count(),
            delivery_count=DeliveryNote.query.count(),
            invoice_count=Invoice.query.count(),
        )

    @app.route("/partners", methods=["GET", "POST"])
    def partners():
        login_redirect = require_role("manage_partners")
        if login_redirect:
            return login_redirect
        if request.method == "POST":
            partner = Partner(
                name=request.form.get("name", "").strip(),
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
                discount_percent=float(request.form.get("discount_percent", 0) or 0),
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
            flash("Partner uložený.")
            return redirect(url_for("partners"))
        return render_template("partners.html", partners=Partner.query.all())

    @app.route("/partners/<int:partner_id>/contacts", methods=["POST"])
    def add_contact(partner_id: int):
        login_redirect = require_role("manage_partners")
        if login_redirect:
            return login_redirect
        partner = Partner.query.get_or_404(partner_id)
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
        flash("Kontakt uložený.")
        return redirect(url_for("partners"))

    @app.route("/partners/<int:partner_id>/addresses", methods=["POST"])
    def add_address(partner_id: int):
        login_redirect = require_role("manage_partners")
        if login_redirect:
            return login_redirect
        partner = Partner.query.get_or_404(partner_id)
        related_partner_id = request.form.get("related_partner_id")
        address = PartnerAddress(
            partner_id=partner.id,
            address_type=request.form.get("address_type", "").strip() or "other",
            related_partner_id=int(related_partner_id) if related_partner_id else None,
            street=request.form.get("street", ""),
            street_number=request.form.get("street_number", ""),
            postal_code=request.form.get("postal_code", ""),
            city=request.form.get("city", ""),
        )
        db.session.add(address)
        db.session.commit()
        flash("Adresa uložená.")
        return redirect(url_for("partners"))

    @app.route("/products", methods=["GET", "POST"])
    def products():
        login_redirect = require_role("manage_orders")
        if login_redirect:
            return login_redirect
        if request.method == "POST":
            price = float(request.form.get("price", 0) or 0)
            product = Product(
                name=request.form.get("name", "").strip(),
                description=request.form.get("description", ""),
                long_text=request.form.get("long_text", ""),
                price=price,
                is_service=request.form.get("is_service") == "on",
                discount_excluded=request.form.get("discount_excluded") == "on",
            )
            db.session.add(product)
            db.session.flush()
            product.price_history.append(ProductPriceHistory(price=price))
            db.session.commit()
            flash("Produkt uložený.")
            return redirect(url_for("products"))
        return render_template("products.html", products=Product.query.all())

    @app.route("/bundles", methods=["GET", "POST"])
    def bundles():
        login_redirect = require_role("manage_orders")
        if login_redirect:
            return login_redirect
        products = Product.query.all()
        if request.method == "POST":
            bundle_price = float(request.form.get("bundle_price", 0) or 0)
            bundle = Bundle(
                name=request.form.get("name", "").strip(),
                bundle_price=bundle_price,
                discount_excluded=request.form.get("discount_excluded") == "on",
            )
            db.session.add(bundle)
            db.session.flush()
            for product in products:
                qty = int(request.form.get(f"bundle_product_{product.id}", 0) or 0)
                if qty > 0:
                    bundle.items.append(
                        BundleItem(product_id=product.id, quantity=qty)
                    )
            bundle.price_history.append(BundlePriceHistory(price=bundle_price))
            db.session.commit()
            flash("Kombinácia uložená.")
            return redirect(url_for("bundles"))
        return render_template(
            "bundles.html",
            bundles=Bundle.query.order_by(Bundle.id.desc()).all(),
            products=products,
        )

    @app.route("/orders", methods=["GET", "POST"])
    def orders():
        login_redirect = require_role("manage_orders")
        if login_redirect:
            return login_redirect
        partners = Partner.query.all()
        addresses = PartnerAddress.query.all()
        products = Product.query.all()
        if request.method == "POST":
            partner_id = int(request.form.get("partner_id"))
            show_prices = request.form.get("show_prices") == "on"
            pickup_address_id = request.form.get("pickup_address_id")
            delivery_address_id = request.form.get("delivery_address_id")
            order = Order(
                partner_id=partner_id,
                pickup_address_id=int(pickup_address_id)
                if pickup_address_id
                else None,
                delivery_address_id=int(delivery_address_id)
                if delivery_address_id
                else None,
                created_by_id=session.get("user_id"),
                pickup_datetime=parse_datetime(request.form.get("pickup_datetime")),
                delivery_datetime=parse_datetime(request.form.get("delivery_datetime")),
                pickup_method=request.form.get("pickup_method", ""),
                delivery_method=request.form.get("delivery_method", ""),
                payment_method=request.form.get("payment_method", ""),
                payment_terms=request.form.get("payment_terms", ""),
                show_prices=show_prices,
            )
            db.session.add(order)
            db.session.flush()
            for product in products:
                qty = int(request.form.get(f"product_{product.id}", 0) or 0)
                if qty > 0:
                    price = product.price
                    order.items.append(
                        OrderItem(product_id=product.id, quantity=qty, unit_price=price)
                    )
            db.session.commit()
            flash("Objednávka vytvorená.")
            return redirect(url_for("orders"))
        return render_template(
            "orders.html",
            orders=Order.query.order_by(Order.created_at.desc()).all(),
            partners=partners,
            addresses=addresses,
            products=products,
        )

    @app.route("/orders/<int:order_id>/confirm", methods=["POST"])
    def confirm_order(order_id: int):
        login_redirect = require_role("manage_orders")
        if login_redirect:
            return login_redirect
        order = Order.query.get_or_404(order_id)
        order.confirmed = True
        db.session.commit()
        flash("Objednávka potvrdená.")
        return redirect(url_for("orders"))

    @app.route("/orders/<int:order_id>/unconfirm", methods=["POST"])
    def unconfirm_order(order_id: int):
        login_redirect = require_role("manage_all")
        if login_redirect:
            return login_redirect
        order = Order.query.get_or_404(order_id)
        order.confirmed = False
        db.session.commit()
        flash("Potvrdenie objednávky zrušené.")
        return redirect(url_for("orders"))

    @app.route("/delivery-notes", methods=["GET", "POST"])
    def delivery_notes():
        login_redirect = require_role("manage_delivery")
        if login_redirect:
            return login_redirect
        orders = Order.query.order_by(Order.created_at.desc()).all()
        products = Product.query.all()
        bundles = Bundle.query.all()
        if request.method == "POST":
            order_ids = request.form.getlist("order_ids")
            selected_orders = Order.query.filter(Order.id.in_(order_ids)).all()
            if not selected_orders:
                flash("Objednávka neexistuje.")
                return redirect(url_for("delivery_notes"))
            group_codes = {order.partner.group_code for order in selected_orders}
            group_codes.discard(None)
            if len(group_codes) > 1:
                flash("Objednávky musia byť v rovnakej partnerskej skupine.")
                return redirect(url_for("delivery_notes"))
            delivery = DeliveryNote(
                primary_order_id=selected_orders[0].id,
                created_by_id=session.get("user_id"),
                show_prices=request.form.get("show_prices") == "on",
                planned_delivery_datetime=parse_datetime(
                    request.form.get("planned_delivery_datetime")
                ),
            )
            db.session.add(delivery)
            db.session.flush()
            for order in selected_orders:
                delivery.orders.append(DeliveryNoteOrder(order_id=order.id))
                for item in order.items:
                    line_total = item.unit_price * item.quantity
                    delivery_item = DeliveryItem(
                        product_id=item.product_id,
                        quantity=item.quantity,
                        unit_price=item.unit_price,
                        line_total=line_total,
                    )
                    delivery.items.append(delivery_item)
            for product in products:
                extra_qty = int(request.form.get(f"extra_{product.id}", 0) or 0)
                if extra_qty > 0:
                    line_total = product.price * extra_qty
                    delivery_item = DeliveryItem(
                        product_id=product.id,
                        quantity=extra_qty,
                        unit_price=product.price,
                        line_total=line_total,
                    )
                    delivery.items.append(delivery_item)
            for bundle in bundles:
                bundle_qty = int(request.form.get(f"bundle_{bundle.id}", 0) or 0)
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
            db.session.commit()
            flash("Dodací list vytvorený.")
            return redirect(url_for("delivery_notes"))
        return render_template(
            "delivery_notes.html",
            delivery_notes=DeliveryNote.query.order_by(DeliveryNote.created_at.desc()).all(),
            orders=orders,
            products=products,
            bundles=bundles,
        )

    @app.route("/vehicles", methods=["GET", "POST"])
    def vehicles():
        login_redirect = require_role("manage_delivery")
        if login_redirect:
            return login_redirect
        if request.method == "POST":
            vehicle = Vehicle(
                name=request.form.get("name", "").strip(),
                notes=request.form.get("notes", ""),
                active=request.form.get("active") == "on",
            )
            db.session.add(vehicle)
            db.session.commit()
            flash("Vozidlo uložené.")
            return redirect(url_for("vehicles"))
        return render_template("vehicles.html", vehicles=Vehicle.query.all())

    @app.route("/vehicles/<int:vehicle_id>/schedules", methods=["POST"])
    def add_vehicle_schedule(vehicle_id: int):
        login_redirect = require_role("manage_delivery")
        if login_redirect:
            return login_redirect
        vehicle = Vehicle.query.get_or_404(vehicle_id)
        schedule = VehicleSchedule(
            vehicle_id=vehicle.id,
            day_of_week=int(request.form.get("day_of_week", 0)),
            start_time=parse_time(request.form.get("start_time")) or datetime.time(8, 0),
            end_time=parse_time(request.form.get("end_time")) or datetime.time(16, 0),
        )
        db.session.add(schedule)
        db.session.commit()
        flash("Operačný čas uložený.")
        return redirect(url_for("vehicles"))

    @app.route("/logistics", methods=["GET", "POST"])
    def logistics_dashboard():
        login_redirect = require_role("manage_delivery")
        if login_redirect:
            return login_redirect
        interval = request.args.get("interval", "weekly")
        plans = LogisticsPlan.query.order_by(LogisticsPlan.planned_datetime.desc()).all()
        orders = Order.query.order_by(Order.created_at.desc()).all()
        delivery_notes = DeliveryNote.query.order_by(DeliveryNote.created_at.desc()).all()
        vehicles = Vehicle.query.filter_by(active=True).all()
        if request.method == "POST":
            plan = LogisticsPlan(
                order_id=int(request.form.get("order_id")) if request.form.get("order_id") else None,
                delivery_note_id=int(request.form.get("delivery_note_id"))
                if request.form.get("delivery_note_id")
                else None,
                plan_type=request.form.get("plan_type", "pickup"),
                planned_datetime=parse_datetime(request.form.get("planned_datetime"))
                or datetime.datetime.utcnow(),
                vehicle_id=int(request.form.get("vehicle_id")) if request.form.get("vehicle_id") else None,
            )
            db.session.add(plan)
            db.session.commit()
            flash("Plán zvozu/dodania uložený.")
            return redirect(url_for("logistics_dashboard", interval=interval))
        return render_template(
            "logistics.html",
            plans=plans,
            interval=interval,
            orders=orders,
            delivery_notes=delivery_notes,
            vehicles=vehicles,
        )

    @app.route("/delivery-notes/<int:delivery_id>/confirm", methods=["POST"])
    def confirm_delivery(delivery_id: int):
        login_redirect = require_role("manage_delivery")
        if login_redirect:
            return login_redirect
        delivery = DeliveryNote.query.get_or_404(delivery_id)
        delivery.confirmed = True
        delivery.actual_delivery_datetime = datetime.datetime.utcnow()
        db.session.commit()
        flash("Dodací list potvrdený.")
        return redirect(url_for("delivery_notes"))

    @app.route("/delivery-notes/<int:delivery_id>/unconfirm", methods=["POST"])
    def unconfirm_delivery(delivery_id: int):
        login_redirect = require_role("manage_all")
        if login_redirect:
            return login_redirect
        delivery = DeliveryNote.query.get_or_404(delivery_id)
        delivery.confirmed = False
        db.session.commit()
        flash("Potvrdenie dodacieho listu zrušené.")
        return redirect(url_for("delivery_notes"))

    @app.route("/delivery-notes/<int:delivery_id>/pdf")
    def delivery_pdf(delivery_id: int):
        login_redirect = require_login()
        if login_redirect:
            return login_redirect
        delivery = DeliveryNote.query.get_or_404(delivery_id)
        pdf_path = generate_delivery_pdf(delivery, app_cfg)
        return send_file(pdf_path, as_attachment=True)

    @app.route("/invoices", methods=["GET", "POST"])
    def invoices():
        login_redirect = require_role("manage_invoices")
        if login_redirect:
            return login_redirect
        partners = Partner.query.all()
        if request.method == "POST":
            partner_id = int(request.form.get("partner_id"))
            invoice = build_invoice_for_partner(partner_id)
            flash(f"Faktúra {invoice.id} vytvorená.")
            return redirect(url_for("invoices"))
        return render_template(
            "invoices.html",
            invoices=Invoice.query.order_by(Invoice.created_at.desc()).all(),
            partners=partners,
        )

    @app.route("/invoices/<int:invoice_id>/items", methods=["POST"])
    def add_invoice_item(invoice_id: int):
        login_redirect = require_role("manage_invoices")
        if login_redirect:
            return login_redirect
        invoice = Invoice.query.get_or_404(invoice_id)
        description = request.form.get("description", "").strip()
        quantity = int(request.form.get("quantity", 1) or 1)
        unit_price = float(request.form.get("unit_price", 0) or 0)
        total = unit_price * quantity
        invoice.items.append(
            InvoiceItem(
                description=description,
                quantity=quantity,
                unit_price=unit_price,
                total=total,
                is_manual=True,
            )
        )
        invoice.total += total
        db.session.commit()
        flash("Manuálna položka pridaná.")
        return redirect(url_for("invoices"))

    @app.route("/invoices/<int:invoice_id>/pdf")
    def invoice_pdf(invoice_id: int):
        login_redirect = require_login()
        if login_redirect:
            return login_redirect
        invoice = Invoice.query.get_or_404(invoice_id)
        pdf_path = generate_invoice_pdf(invoice, app_cfg)
        return send_file(pdf_path, as_attachment=True)

    @app.route("/invoices/<int:invoice_id>/send")
    def send_invoice(invoice_id: int):
        login_redirect = require_role("manage_invoices")
        if login_redirect:
            return login_redirect
        invoice = Invoice.query.get_or_404(invoice_id)
        pdf_path = generate_invoice_pdf(invoice, app_cfg)
        email_cfg = app.config["EMAIL_CONFIG"]
        if email_cfg.enabled and invoice.partner.email:
            send_document_email(
                email_cfg,
                subject=f"Faktúra {invoice.id}",
                recipient=invoice.partner.email,
                cc=email_cfg.operator_cc,
                body=f"Dobrý deň, v prílohe posielame faktúru {invoice.id}.",
                attachment_path=pdf_path,
            )
            flash("Faktúra odoslaná emailom.")
        else:
            flash("Odosielanie emailov nie je zapnuté alebo chýba email.")
        return redirect(url_for("invoices"))

    @app.route("/invoices/<int:invoice_id>/export")
    def export_invoice(invoice_id: int):
        login_redirect = require_role("manage_invoices")
        if login_redirect:
            return login_redirect
        invoice = Invoice.query.get_or_404(invoice_id)
        sf_cfg = app.config["SF_CONFIG"]
        if not sf_cfg.enabled:
            flash("Superfaktúra API nie je zapnutá.")
            return redirect(url_for("invoices"))
        client = SuperFakturaClient(sf_cfg)
        result = client.send_invoice(invoice)
        invoice.status = "sent" if result else "error"
        db.session.commit()
        flash("Faktúra exportovaná do Superfaktúry.")
        return redirect(url_for("invoices"))

    return app


def parse_date(raw: Optional[str]):
    if not raw:
        return None
    return datetime.datetime.strptime(raw, "%Y-%m-%d").date()


def parse_datetime(raw: Optional[str]):
    if not raw:
        return None
    return datetime.datetime.strptime(raw, "%Y-%m-%dT%H:%M")


def parse_time(raw: Optional[str]):
    if not raw:
        return None
    return datetime.datetime.strptime(raw, "%H:%M").time()


def ensure_admin_user():
    if User.query.count() == 0:
        admin = User(
            username="admin",
            password_hash=generate_password_hash("admin"),
            role="admin",
        )
        db.session.add(admin)
        db.session.commit()


def build_invoice_for_partner(partner_id: int) -> Invoice:
    partner = Partner.query.get_or_404(partner_id)
    query = DeliveryNote.query.join(Order, DeliveryNote.primary_order_id == Order.id).join(
        Partner, Order.partner_id == Partner.id
    )
    if partner.group_code:
        query = query.filter(Partner.group_code == partner.group_code)
    else:
        query = query.filter(Order.partner_id == partner_id)
    unbilled_notes = query.filter(DeliveryNote.invoiced.is_(False)).all()
    invoice = Invoice(partner_id=partner_id, status="draft")
    db.session.add(invoice)
    total = 0.0
    for note in unbilled_notes:
        for item in note.items:
            line_total = item.line_total or (item.unit_price * item.quantity)
            item_name = (
                item.product.name if item.product else item.bundle.name if item.bundle else "Položka"
            )
            description = (
                f"Dodací list {note.id}: {item_name} ({item.quantity}x)"
            )
            invoice.items.append(
                InvoiceItem(
                    source_delivery_id=note.id,
                    description=description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    total=line_total,
                )
            )
            total += line_total
        note.invoiced = True
    invoice.total = total
    db.session.commit()
    return invoice


def generate_delivery_pdf(delivery: DeliveryNote, app_cfg: AppConfig) -> str:
    os.makedirs("output", exist_ok=True)
    filename = f"output/delivery_{delivery.id}.pdf"
    pdf = canvas.Canvas(filename, pagesize=A4)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(20 * mm, 285 * mm, f"Dodací list {delivery.id}")
    pdf.setFont("Helvetica", 10)
    partner_name = delivery.primary_order.partner.name if delivery.primary_order else ""
    pdf.drawString(20 * mm, 278 * mm, f"Partner: {partner_name}")
    pdf.drawString(20 * mm, 272 * mm, f"Dátum: {delivery.created_at.date()}")
    pdf.drawString(20 * mm, 266 * mm, f"Ceny: {'Áno' if delivery.show_prices else 'Nie'}")
    pdf.drawString(
        20 * mm,
        260 * mm,
        f"Plán: {delivery.planned_delivery_datetime or ''} | Skutočnosť: {delivery.actual_delivery_datetime or ''}",
    )
    y = 250
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(20 * mm, y * mm, "Položky")
    y -= 6
    pdf.setFont("Helvetica", 9)
    for item in delivery.items:
        name = item.product.name if item.product else item.bundle.name
        line = f"{name} - {item.quantity}x"
        if delivery.show_prices:
            line += f" | {item.unit_price:.2f} {app_cfg.base_currency}"
            line += f" | {item.line_total:.2f} {app_cfg.base_currency}"
        pdf.drawString(25 * mm, y * mm, line)
        y -= 5
        for component in item.components:
            comp_line = f"  - {component.product.name}: {component.quantity}x"
            pdf.drawString(30 * mm, y * mm, comp_line)
            y -= 4
        if y < 20:
            pdf.showPage()
            y = 280
    pdf.save()
    return filename


def generate_invoice_pdf(invoice: Invoice, app_cfg: AppConfig) -> str:
    os.makedirs("output", exist_ok=True)
    filename = f"output/invoice_{invoice.id}.pdf"
    pdf = canvas.Canvas(filename, pagesize=A4)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(20 * mm, 285 * mm, f"Zúčtovacia faktúra {invoice.id}")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(20 * mm, 278 * mm, f"Partner: {invoice.partner.name}")
    pdf.drawString(20 * mm, 272 * mm, f"Dátum: {invoice.created_at.date()}")
    y = 255
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(20 * mm, y * mm, "Položky")
    y -= 6
    pdf.setFont("Helvetica", 9)
    for item in invoice.items:
        line = (
            f"{item.description} | {item.quantity}x | "
            f"{item.unit_price:.2f} {app_cfg.base_currency}"
        )
        pdf.drawString(25 * mm, y * mm, line)
        y -= 5
        if y < 20:
            pdf.showPage()
            y = 280
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(
        20 * mm, 20 * mm, f"Spolu: {invoice.total:.2f} {app_cfg.base_currency}"
    )
    pdf.save()
    return filename


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
