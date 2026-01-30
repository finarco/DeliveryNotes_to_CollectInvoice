"""SQLAlchemy models and role-permission mapping."""

from __future__ import annotations

from extensions import db
from utils import utc_now

# ---------------------------------------------------------------------------
# Role / Permission mapping
# ---------------------------------------------------------------------------

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"manage_all"},
    "operator": {"manage_partners", "manage_orders", "manage_delivery", "manage_invoices"},
    "collector": {"manage_delivery"},
    "customer": {"view_own"},
}

VALID_ROLES = list(ROLE_PERMISSIONS.keys())


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="operator")
    must_change_password = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"))
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    partner = db.relationship("Partner", foreign_keys=[partner_id])


# ---------------------------------------------------------------------------
# Partner & related
# ---------------------------------------------------------------------------

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
    discount_percent = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    contacts = db.relationship("Contact", backref="partner", cascade="all, delete-orphan")
    addresses = db.relationship(
        "PartnerAddress",
        backref="partner",
        cascade="all, delete-orphan",
        foreign_keys="PartnerAddress.partner_id",
    )

    __table_args__ = (
        db.Index("ix_partner_group_code", "group_code"),
        db.Index("ix_partner_name", "name"),
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


# ---------------------------------------------------------------------------
# Product catalog
# ---------------------------------------------------------------------------

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255))
    long_text = db.Column(db.Text)
    price = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False)
    vat_rate = db.Column(db.Numeric(5, 2, asdecimal=False), default=20.0)
    is_service = db.Column(db.Boolean, default=True)
    discount_excluded = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    price_history = db.relationship(
        "ProductPriceHistory", backref="product", cascade="all, delete-orphan"
    )


class ProductPriceHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    price = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False)
    changed_at = db.Column(db.DateTime, default=utc_now)


class Bundle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    bundle_price = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False)
    discount_excluded = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    items = db.relationship("BundleItem", backref="bundle", cascade="all, delete-orphan")
    price_history = db.relationship(
        "BundlePriceHistory", backref="bundle", cascade="all, delete-orphan"
    )


class BundlePriceHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bundle_id = db.Column(db.Integer, db.ForeignKey("bundle.id"), nullable=False)
    price = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False)
    changed_at = db.Column(db.DateTime, default=utc_now)


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


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

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
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    partner = db.relationship("Partner")
    pickup_address = db.relationship("PartnerAddress", foreign_keys=[pickup_address_id])
    delivery_address = db.relationship("PartnerAddress", foreign_keys=[delivery_address_id])
    created_by = db.relationship("User")
    items = db.relationship("OrderItem", backref="order", cascade="all, delete-orphan")

    __table_args__ = (
        db.Index("ix_order_partner_id", "partner_id"),
        db.Index("ix_order_confirmed", "confirmed"),
    )


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False)

    product = db.relationship("Product")


# ---------------------------------------------------------------------------
# Delivery notes
# ---------------------------------------------------------------------------

class DeliveryNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    primary_order_id = db.Column(db.Integer, db.ForeignKey("order.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    show_prices = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)
    invoiced = db.Column(db.Boolean, default=False)
    planned_delivery_datetime = db.Column(db.DateTime)
    actual_delivery_datetime = db.Column(db.DateTime)
    confirmed = db.Column(db.Boolean, default=False)

    primary_order = db.relationship("Order")
    created_by = db.relationship("User")
    items = db.relationship(
        "DeliveryItem", backref="delivery_note", cascade="all, delete-orphan"
    )
    orders = db.relationship(
        "DeliveryNoteOrder", backref="delivery_note", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.Index("ix_delivery_note_invoiced", "invoiced"),
        db.Index("ix_delivery_note_confirmed", "confirmed"),
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
    delivery_note_id = db.Column(
        db.Integer, db.ForeignKey("delivery_note.id"), nullable=False
    )
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    bundle_id = db.Column(db.Integer, db.ForeignKey("bundle.id"))
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False)
    line_total = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False, default=0.0)

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


# ---------------------------------------------------------------------------
# Vehicles & logistics
# ---------------------------------------------------------------------------

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
    plan_type = db.Column(db.String(40), nullable=False)  # pickup / delivery
    planned_datetime = db.Column(db.DateTime, nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicle.id"))

    order = db.relationship("Order")
    delivery_note = db.relationship("DeliveryNote")
    vehicle = db.relationship("Vehicle")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(80), nullable=False)
    entity_type = db.Column(db.String(80), nullable=False)
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)  # D5: was String(255)
    created_at = db.Column(db.DateTime, default=utc_now)

    user = db.relationship("User")

    __table_args__ = (
        db.Index("ix_audit_log_created_at", "created_at"),
        db.Index("ix_audit_log_entity", "entity_type", "entity_id"),
    )


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

VALID_INVOICE_STATUSES = {"draft", "sent", "paid", "error"}


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(30), unique=True)  # D6: FV-2026-0001
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)
    total = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    total_with_vat = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    status = db.Column(db.String(30), default="draft")

    partner = db.relationship("Partner")
    items = db.relationship("InvoiceItem", backref="invoice", cascade="all, delete-orphan")

    __table_args__ = (
        db.Index("ix_invoice_status", "status"),
        db.Index("ix_invoice_partner_id", "partner_id"),
    )


class InvoiceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"), nullable=False)
    source_delivery_id = db.Column(db.Integer, db.ForeignKey("delivery_note.id"))
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False)
    total = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False)
    vat_rate = db.Column(db.Numeric(5, 2, asdecimal=False), default=20.0)
    vat_amount = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    total_with_vat = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    is_manual = db.Column(db.Boolean, default=False)
