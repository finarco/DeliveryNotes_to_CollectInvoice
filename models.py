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
# Tenant
# ---------------------------------------------------------------------------

class Tenant(db.Model):
    """A tenant represents an isolated business entity (company/organization)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(60), unique=True, nullable=False)
    ico = db.Column(db.String(20))
    dic = db.Column(db.String(20))
    ic_dph = db.Column(db.String(20))
    street = db.Column(db.String(120))
    city = db.Column(db.String(120))
    postal_code = db.Column(db.String(20))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(60))
    billing_email = db.Column(db.String(120))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    user_memberships = db.relationship(
        "UserTenant", backref="tenant", cascade="all, delete-orphan"
    )


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
    is_superadmin = db.Column(db.Boolean, default=False)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"))
    password_changed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    partner = db.relationship("Partner", foreign_keys=[partner_id])
    tenant_memberships = db.relationship(
        "UserTenant", backref="user", cascade="all, delete-orphan"
    )


class UserTenant(db.Model):
    """Associates users with tenants, optionally overriding the user's global role."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    role_override = db.Column(db.String(30))
    is_default = db.Column(db.Boolean, default=False)

    # tenant = backref from Tenant.user_memberships
    # user = backref from User.tenant_memberships

    __table_args__ = (
        db.UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),
    )


# ---------------------------------------------------------------------------
# Partner & related
# ---------------------------------------------------------------------------

class Partner(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
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
    discount_percent = db.Column(db.Numeric(10, 2, asdecimal=True), default=0.0)
    is_active = db.Column(db.Boolean, default=True)
    is_deleted = db.Column(db.Boolean, default=False)
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
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
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
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
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
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    product_number = db.Column(db.String(60))
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255))
    long_text = db.Column(db.Text)
    price = db.Column(db.Numeric(10, 2, asdecimal=True), nullable=False)
    vat_rate = db.Column(db.Numeric(5, 2, asdecimal=True), default=20.0)
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
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    price = db.Column(db.Numeric(10, 2, asdecimal=True), nullable=False)
    changed_at = db.Column(db.DateTime, default=utc_now)


class Bundle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    bundle_number = db.Column(db.String(60))
    name = db.Column(db.String(120), nullable=False)
    bundle_price = db.Column(db.Numeric(10, 2, asdecimal=True), nullable=False)
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
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    bundle_id = db.Column(db.Integer, db.ForeignKey("bundle.id"), nullable=False)
    price = db.Column(db.Numeric(10, 2, asdecimal=True), nullable=False)
    changed_at = db.Column(db.DateTime, default=utc_now)


class BundleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    bundle_id = db.Column(db.Integer, db.ForeignKey("bundle.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)

    product = db.relationship("Product")


class ProductRestriction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    restricted_with_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)

    product = db.relationship("Product", foreign_keys=[product_id])
    restricted_with = db.relationship("Product", foreign_keys=[restricted_with_id])


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    order_number = db.Column(db.String(60))
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
    is_locked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    partner = db.relationship("Partner")
    pickup_address = db.relationship("PartnerAddress", foreign_keys=[pickup_address_id])
    delivery_address = db.relationship("PartnerAddress", foreign_keys=[delivery_address_id])
    created_by = db.relationship("User")
    items = db.relationship("OrderItem", backref="order", cascade="all, delete-orphan")
    delivery_note_links = db.relationship(
        "DeliveryNoteOrder",
        foreign_keys="DeliveryNoteOrder.order_id",
        viewonly=True,
    )

    __table_args__ = (
        db.Index("ix_order_partner_id", "partner_id"),
        db.Index("ix_order_confirmed", "confirmed"),
    )

    @property
    def status(self):
        """Computed status based on confirmed and is_locked flags."""
        if self.is_locked:
            return "completed"
        elif self.confirmed:
            return "processing"
        return "pending"

    @property
    def total_price(self):
        return sum(item.quantity * item.unit_price for item in self.items)


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    bundle_id = db.Column(db.Integer, db.ForeignKey("bundle.id"))
    is_manual = db.Column(db.Boolean, default=False)
    manual_name = db.Column(db.String(200))
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(10, 2, asdecimal=True), nullable=False)

    product = db.relationship("Product")
    bundle = db.relationship("Bundle")

    __table_args__ = (
        db.CheckConstraint(
            "product_id IS NOT NULL OR bundle_id IS NOT NULL OR is_manual = 1",
            name="ck_order_item_has_source",
        ),
    )


# ---------------------------------------------------------------------------
# Delivery notes
# ---------------------------------------------------------------------------

class DeliveryNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    note_number = db.Column(db.String(60))
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"))
    primary_order_id = db.Column(db.Integer, db.ForeignKey("order.id"), index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    show_prices = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)
    invoiced = db.Column(db.Boolean, default=False)
    planned_delivery_datetime = db.Column(db.DateTime)
    actual_delivery_datetime = db.Column(db.DateTime)
    confirmed = db.Column(db.Boolean, default=False)
    is_locked = db.Column(db.Boolean, default=False)

    partner = db.relationship("Partner")
    primary_order = db.relationship("Order")
    created_by = db.relationship("User")
    items = db.relationship(
        "DeliveryItem", backref="delivery_note", cascade="all, delete-orphan"
    )
    orders = db.relationship(
        "DeliveryNoteOrder", backref="delivery_note", cascade="all, delete-orphan"
    )
    logistics_plans = db.relationship(
        "LogisticsPlan",
        foreign_keys="LogisticsPlan.delivery_note_id",
        viewonly=True,
    )
    invoice_item_refs = db.relationship(
        "InvoiceItem",
        foreign_keys="InvoiceItem.source_delivery_id",
        viewonly=True,
    )

    __table_args__ = (
        db.Index("ix_delivery_note_invoiced", "invoiced"),
        db.Index("ix_delivery_note_confirmed", "confirmed"),
    )


class DeliveryNoteOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    delivery_note_id = db.Column(
        db.Integer, db.ForeignKey("delivery_note.id"), nullable=False
    )
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)

    order = db.relationship("Order")


class DeliveryItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    delivery_note_id = db.Column(
        db.Integer, db.ForeignKey("delivery_note.id"), nullable=False
    )
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    bundle_id = db.Column(db.Integer, db.ForeignKey("bundle.id"))
    is_manual = db.Column(db.Boolean, default=False)
    manual_name = db.Column(db.String(200))
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(10, 2, asdecimal=True), nullable=False)
    line_total = db.Column(db.Numeric(10, 2, asdecimal=True), nullable=False, default=0.0)

    product = db.relationship("Product")
    bundle = db.relationship("Bundle")
    components = db.relationship(
        "DeliveryItemComponent", backref="delivery_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.CheckConstraint(
            "product_id IS NOT NULL OR bundle_id IS NOT NULL OR is_manual = 1",
            name="ck_delivery_item_has_source",
        ),
    )


class DeliveryItemComponent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
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
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    name = db.Column(db.String(120), nullable=False)
    registration_number = db.Column(db.String(20))
    notes = db.Column(db.String(255))
    active = db.Column(db.Boolean, default=True)

    schedules = db.relationship(
        "VehicleSchedule", backref="vehicle", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "registration_number", name="uq_vehicle_reg_tenant"),
    )


class VehicleSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicle.id"), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=False)  # 0=Mon
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)


class LogisticsPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
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
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
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
# Application settings
# ---------------------------------------------------------------------------

class AppSetting(db.Model):
    """Key-value store for per-tenant settings (tenant_id=NULL for global)."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    key = db.Column(db.String(80), nullable=False)
    value = db.Column(db.Text)

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "key", name="uq_app_setting_tenant_key"),
    )


class NumberingConfig(db.Model):
    """Tag-based numbering pattern per entity type per tenant.

    Pattern example: ``DL[YY][MM]-[CCCC]`` -> ``DL2601-0001``
    Tags: [YYYY] [YY] [MM] [DD] [PARTNER] [TYPE] [C+]
    Counter resets when preceding scope-tags change.
    """
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    entity_type = db.Column(db.String(40), nullable=False)
    pattern = db.Column(db.String(120), default="")

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "entity_type", name="uq_numbering_config_tenant"),
    )


class NumberSequence(db.Model):
    """Sequence counters per entity type, scope, and tenant."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    entity_type = db.Column(db.String(40), nullable=False)
    scope_key = db.Column(db.String(120), default="")
    last_value = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "entity_type", "scope_key", name="uq_number_sequence"),
    )


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

VALID_INVOICE_STATUSES = {"draft", "sent", "paid", "error"}


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    invoice_number = db.Column(db.String(30))
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)
    total = db.Column(db.Numeric(10, 2, asdecimal=True), default=0.0)
    total_with_vat = db.Column(db.Numeric(10, 2, asdecimal=True), default=0.0)
    status = db.Column(db.String(30), default="draft")
    is_locked = db.Column(db.Boolean, default=False)

    partner = db.relationship("Partner")
    items = db.relationship("InvoiceItem", backref="invoice", cascade="all, delete-orphan")

    __table_args__ = (
        db.Index("ix_invoice_status", "status"),
        db.Index("ix_invoice_partner_id", "partner_id"),
        db.UniqueConstraint("tenant_id", "invoice_number", name="uq_invoice_number_tenant"),
    )


class InvoiceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"), nullable=False)
    source_delivery_id = db.Column(db.Integer, db.ForeignKey("delivery_note.id"))
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(10, 2, asdecimal=True), nullable=False)
    total = db.Column(db.Numeric(10, 2, asdecimal=True), nullable=False)
    vat_rate = db.Column(db.Numeric(5, 2, asdecimal=True), default=20.0)
    vat_amount = db.Column(db.Numeric(10, 2, asdecimal=True), default=0.0)
    total_with_vat = db.Column(db.Numeric(10, 2, asdecimal=True), default=0.0)
    is_manual = db.Column(db.Boolean, default=False)


# ---------------------------------------------------------------------------
# PDF Templates
# ---------------------------------------------------------------------------

class PdfTemplate(db.Model):
    """Admin-editable HTML/CSS templates for PDF generation, per tenant."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), index=True)
    entity_type = db.Column(db.String(40), nullable=False)
    html_content = db.Column(db.Text, default="")
    css_content = db.Column(db.Text, default="")

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "entity_type", name="uq_pdf_template_tenant"),
    )


# ---------------------------------------------------------------------------
# Subscription billing
# ---------------------------------------------------------------------------

VALID_SUBSCRIPTION_STATUSES = {"trial", "active", "past_due", "grace_period", "suspended", "cancelled"}
VALID_BILLING_CYCLES = {"monthly", "yearly"}
VALID_PAYMENT_METHODS = {"stripe", "bank_transfer", "manual"}
VALID_PAYMENT_STATUSES = {"pending", "completed", "failed", "refunded"}


class SubscriptionPlan(db.Model):
    """Defines available subscription tiers (e.g., Free, Basic, Pro)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    slug = db.Column(db.String(60), unique=True, nullable=False)
    description = db.Column(db.Text)
    price_monthly = db.Column(db.Numeric(10, 2, asdecimal=True), default=0.0)
    price_yearly = db.Column(db.Numeric(10, 2, asdecimal=True), default=0.0)
    currency = db.Column(db.String(10), default="EUR")
    max_users = db.Column(db.Integer, default=0)  # 0 = unlimited
    max_partners = db.Column(db.Integer, default=0)
    max_invoices_per_month = db.Column(db.Integer, default=0)
    features = db.Column(db.Text)  # JSON string for feature flags
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utc_now)


class TenantSubscription(db.Model):
    """Links a tenant to its active subscription plan."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), unique=True, nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey("subscription_plan.id"), nullable=False)
    status = db.Column(db.String(30), default="trial")
    billing_cycle = db.Column(db.String(20), default="monthly")
    current_period_start = db.Column(db.DateTime)
    current_period_end = db.Column(db.DateTime)
    trial_ends_at = db.Column(db.DateTime)
    trial_extended_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    trial_extended_at = db.Column(db.DateTime)
    original_trial_days = db.Column(db.Integer, default=30)
    grace_period_ends_at = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)
    stripe_customer_id = db.Column(db.String(120))
    stripe_subscription_id = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    tenant = db.relationship("Tenant", backref=db.backref("subscription", uselist=False))
    plan = db.relationship("SubscriptionPlan")
    trial_extended_by = db.relationship("User", foreign_keys=[trial_extended_by_id])


class Payment(db.Model):
    """Tracks every payment event for a tenant subscription."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    subscription_id = db.Column(db.Integer, db.ForeignKey("tenant_subscription.id"))
    amount = db.Column(db.Numeric(10, 2, asdecimal=True), nullable=False)
    currency = db.Column(db.String(10), default="EUR")
    payment_method = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(30), default="pending")
    stripe_payment_intent_id = db.Column(db.String(120))
    bank_reference = db.Column(db.String(120))
    invoice_url = db.Column(db.String(255))
    paid_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utc_now)

    tenant = db.relationship("Tenant")
    subscription = db.relationship("TenantSubscription")
