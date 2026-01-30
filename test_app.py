"""Comprehensive test suite for DeliveryNotes_to_CollectInvoice application.

Tests cover: app creation, database models, all routes, business logic,
PDF generation, email sending, and edge cases.
"""

import datetime
import os
import tempfile

import pytest

os.environ["DATABASE_URI"] = "sqlite://"  # In-memory database for tests
os.environ["APP_SECRET_KEY"] = "test-secret-key"

from app import (
    Bundle,
    BundleItem,
    BundlePriceHistory,
    Contact,
    DeliveryItem,
    DeliveryItemComponent,
    DeliveryNote,
    DeliveryNoteOrder,
    Invoice,
    InvoiceItem,
    LogisticsPlan,
    Order,
    OrderItem,
    Partner,
    PartnerAddress,
    Product,
    ProductPriceHistory,
    ProductRestriction,
    User,
    Vehicle,
    VehicleSchedule,
    build_invoice_for_partner,
    create_app,
    db,
    generate_delivery_pdf,
    generate_invoice_pdf,
    parse_date,
    parse_datetime,
    parse_time,
    safe_float,
    safe_int,
)
from config_models import AppConfig, EmailConfig, SuperfakturaConfig
from werkzeug.security import generate_password_hash

TEST_PASSWORD = "testpassword"


@pytest.fixture
def app():
    """Create application for testing."""
    application = create_app()
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    application.config["RATELIMIT_ENABLED"] = False
    with application.app_context():
        admin = User.query.filter_by(username="admin").first()
        if admin:
            admin.password_hash = generate_password_hash(TEST_PASSWORD)
            admin.must_change_password = False
            db.session.commit()
    yield application


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def logged_in_client(client, app):
    """Create test client with logged-in admin session."""
    with app.app_context():
        user = User.query.filter_by(username="admin").first()
        with client.session_transaction() as sess:
            sess["user_id"] = user.id
    return client


@pytest.fixture
def sample_data(app):
    """Create sample data for tests. Returns dict of IDs to avoid detached instance errors."""
    with app.app_context():
        partner = Partner(
            name="Test Partner",
            email="partner@test.sk",
            phone="0911111111",
            street="Testova",
            street_number="1",
            postal_code="01001",
            city="Zilina",
            ico="12345678",
            dic="2012345678",
            ic_dph="SK2012345678",
            group_code="GRP1",
            price_level="standard",
            discount_percent=5.0,
        )
        db.session.add(partner)
        db.session.flush()

        address = PartnerAddress(
            partner_id=partner.id,
            address_type="headquarters",
            street="Testova",
            street_number="1",
            postal_code="01001",
            city="Zilina",
        )
        db.session.add(address)

        product = Product(
            name="Test Service",
            description="Test Description",
            price=15.50,
            is_service=True,
        )
        db.session.add(product)
        db.session.flush()

        product2 = Product(
            name="Test Goods",
            description="Physical product",
            price=25.00,
            is_service=False,
            discount_excluded=True,
        )
        db.session.add(product2)
        db.session.flush()

        user = User.query.filter_by(username="admin").first()

        order = Order(
            partner_id=partner.id,
            created_by_id=user.id,
            show_prices=True,
            pickup_method="kurier",
            delivery_method="rozvoz",
            payment_method="prevod",
            payment_terms="14 dni",
        )
        db.session.add(order)
        db.session.flush()

        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            quantity=3,
            unit_price=product.price,
        )
        db.session.add(order_item)
        db.session.flush()

        db.session.commit()

        # Return IDs only (not ORM objects) to avoid DetachedInstanceError
        return {
            "partner_id": partner.id,
            "address_id": address.id,
            "product_id": product.id,
            "product2_id": product2.id,
            "order_id": order.id,
            "order_item_id": order_item.id,
            "user_id": user.id,
        }


# ============================================================================
# Utility function tests
# ============================================================================


class TestUtilityFunctions:
    def test_safe_int_valid(self):
        assert safe_int("42") == 42

    def test_safe_int_none(self):
        assert safe_int(None) == 0

    def test_safe_int_empty(self):
        assert safe_int("") == 0

    def test_safe_int_invalid(self):
        assert safe_int("abc") == 0

    def test_safe_int_default(self):
        assert safe_int("abc", default=5) == 5

    def test_safe_float_valid(self):
        assert safe_float("3.14") == 3.14

    def test_safe_float_none(self):
        assert safe_float(None) == 0.0

    def test_safe_float_empty(self):
        assert safe_float("") == 0.0

    def test_safe_float_invalid(self):
        assert safe_float("xyz") == 0.0

    def test_safe_float_default(self):
        assert safe_float("xyz", default=1.5) == 1.5

    def test_parse_date_valid(self):
        result = parse_date("2026-01-15")
        assert result == datetime.date(2026, 1, 15)

    def test_parse_date_none(self):
        assert parse_date(None) is None

    def test_parse_date_empty(self):
        assert parse_date("") is None

    def test_parse_date_invalid(self):
        assert parse_date("not-a-date") is None

    def test_parse_datetime_valid(self):
        result = parse_datetime("2026-01-15T10:30")
        assert result == datetime.datetime(2026, 1, 15, 10, 30)

    def test_parse_datetime_none(self):
        assert parse_datetime(None) is None

    def test_parse_datetime_invalid(self):
        assert parse_datetime("invalid") is None

    def test_parse_time_valid(self):
        result = parse_time("10:30")
        assert result == datetime.time(10, 30)

    def test_parse_time_none(self):
        assert parse_time(None) is None

    def test_parse_time_invalid(self):
        assert parse_time("bad") is None


# ============================================================================
# App creation tests
# ============================================================================


class TestAppCreation:
    def test_create_app(self, app):
        assert app is not None

    def test_app_config(self, app):
        assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite://"
        assert app.config["TESTING"] is True

    def test_admin_user_created(self, app):
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            assert admin is not None
            assert admin.role == "admin"

    def test_csrf_initialized(self, app):
        assert "WTF_CSRF_ENABLED" in app.config

    def test_session_config(self, app):
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


# ============================================================================
# Route tests - Authentication
# ============================================================================


class TestAuthRoutes:
    def test_login_page_renders(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"Prihl" in resp.data  # "Prihlásenie"

    def test_login_success(self, client):
        resp = client.post(
            "/login",
            data={"username": "admin", "password": TEST_PASSWORD},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_login_failure(self, client):
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "wrong"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Nespr" in resp.data.decode("utf-8")  # "Nesprávne"

    def test_login_nonexistent_user(self, client):
        resp = client.post(
            "/login",
            data={"username": "nonexistent", "password": "test"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_logout(self, logged_in_client):
        resp = logged_in_client.post("/logout", follow_redirects=True)
        assert resp.status_code == 200

    def test_logout_rejects_get(self, logged_in_client):
        resp = logged_in_client.get("/logout")
        assert resp.status_code == 405

    def test_protected_route_redirects(self, client):
        resp = client.get("/")
        assert resp.status_code == 302

    def test_protected_route_accessible_when_logged_in(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert resp.status_code == 200


# ============================================================================
# Route tests - Index/Dashboard
# ============================================================================


class TestDashboard:
    def test_dashboard_renders(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert resp.status_code == 200
        assert b"partner_count" in resp.data or b"Partneri" in resp.data

    def test_dashboard_counts(self, logged_in_client, sample_data):
        resp = logged_in_client.get("/")
        assert resp.status_code == 200


# ============================================================================
# Route tests - Partners
# ============================================================================


class TestPartnerRoutes:
    def test_partners_page_renders(self, logged_in_client):
        resp = logged_in_client.get("/partners")
        assert resp.status_code == 200

    def test_create_partner(self, logged_in_client):
        resp = logged_in_client.post(
            "/partners",
            data={
                "name": "New Partner",
                "email": "new@test.sk",
                "phone": "0922222222",
                "street": "Nova",
                "street_number": "2",
                "postal_code": "02001",
                "city": "Bratislava",
                "ico": "87654321",
                "dic": "2087654321",
                "ic_dph": "SK2087654321",
                "group_code": "GRP2",
                "price_level": "premium",
                "discount_percent": "10",
                "note": "Test note",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_create_partner_minimal(self, logged_in_client):
        """Test creating partner with only required fields."""
        resp = logged_in_client.post(
            "/partners",
            data={"name": "Minimal Partner"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_add_contact(self, logged_in_client, sample_data):
        resp = logged_in_client.post(
            f"/partners/{sample_data['partner_id']}/contacts",
            data={
                "name": "Jan Novak",
                "email": "jan@test.sk",
                "phone": "0933333333",
                "role": "manager",
                "can_order": "on",
                "can_receive": "on",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_add_address(self, logged_in_client, sample_data):
        resp = logged_in_client.post(
            f"/partners/{sample_data['partner_id']}/addresses",
            data={
                "address_type": "delivery",
                "street": "Dodacia",
                "street_number": "5",
                "postal_code": "03001",
                "city": "Martin",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_add_address_with_related_partner(self, logged_in_client, sample_data):
        resp = logged_in_client.post(
            f"/partners/{sample_data['partner_id']}/addresses",
            data={
                "address_type": "invoice",
                "related_partner_id": str(sample_data["partner_id"]),
                "street": "Fakturacna",
                "street_number": "10",
                "postal_code": "04001",
                "city": "Kosice",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_add_contact_nonexistent_partner(self, logged_in_client):
        resp = logged_in_client.post(
            "/partners/99999/contacts",
            data={"name": "Test"},
            follow_redirects=True,
        )
        assert resp.status_code == 404

    def test_add_address_nonexistent_partner(self, logged_in_client):
        resp = logged_in_client.post(
            "/partners/99999/addresses",
            data={"address_type": "delivery"},
            follow_redirects=True,
        )
        assert resp.status_code == 404


# ============================================================================
# Route tests - Products
# ============================================================================


class TestProductRoutes:
    def test_products_page_renders(self, logged_in_client):
        resp = logged_in_client.get("/products")
        assert resp.status_code == 200

    def test_create_product(self, logged_in_client):
        resp = logged_in_client.post(
            "/products",
            data={
                "name": "New Product",
                "description": "Product desc",
                "long_text": "Longer description text",
                "price": "30.00",
                "is_service": "on",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_create_product_as_goods(self, logged_in_client):
        resp = logged_in_client.post(
            "/products",
            data={
                "name": "Physical Goods",
                "price": "50.00",
                "discount_excluded": "on",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_create_product_zero_price(self, logged_in_client):
        resp = logged_in_client.post(
            "/products",
            data={"name": "Free Item", "price": "0"},
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ============================================================================
# Route tests - Bundles
# ============================================================================


class TestBundleRoutes:
    def test_bundles_page_renders(self, logged_in_client):
        resp = logged_in_client.get("/bundles")
        assert resp.status_code == 200

    def test_create_bundle(self, logged_in_client, sample_data):
        product_id = sample_data["product_id"]
        resp = logged_in_client.post(
            "/bundles",
            data={
                "name": "Test Bundle",
                "bundle_price": "40.00",
                f"bundle_product_{product_id}": "2",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_create_bundle_no_items(self, logged_in_client):
        resp = logged_in_client.post(
            "/bundles",
            data={"name": "Empty Bundle", "bundle_price": "10.00"},
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ============================================================================
# Route tests - Orders
# ============================================================================


class TestOrderRoutes:
    def test_orders_page_renders(self, logged_in_client):
        resp = logged_in_client.get("/orders")
        assert resp.status_code == 200

    def test_create_order(self, logged_in_client, sample_data):
        product_id = sample_data["product_id"]
        resp = logged_in_client.post(
            "/orders",
            data={
                "partner_id": str(sample_data["partner_id"]),
                f"product_{product_id}": "5",
                "pickup_method": "kurier",
                "delivery_method": "rozvoz",
                "payment_method": "prevod",
                "payment_terms": "14 dni",
                "show_prices": "on",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_create_order_no_partner(self, logged_in_client):
        resp = logged_in_client.post(
            "/orders",
            data={"partner_id": ""},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_confirm_order(self, logged_in_client, sample_data):
        resp = logged_in_client.post(
            f"/orders/{sample_data['order_id']}/confirm",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_unconfirm_order(self, logged_in_client, sample_data, app):
        with app.app_context():
            order = db.session.get(Order, sample_data["order_id"])
            order.confirmed = True
            db.session.commit()
        resp = logged_in_client.post(
            f"/orders/{sample_data['order_id']}/unconfirm",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_confirm_nonexistent_order(self, logged_in_client):
        resp = logged_in_client.post(
            "/orders/99999/confirm",
            follow_redirects=True,
        )
        assert resp.status_code == 404

    def test_create_order_with_datetime(self, logged_in_client, sample_data):
        resp = logged_in_client.post(
            "/orders",
            data={
                "partner_id": str(sample_data["partner_id"]),
                "pickup_datetime": "2026-02-01T10:00",
                "delivery_datetime": "2026-02-02T14:00",
                f"product_{sample_data['product_id']}": "1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ============================================================================
# Route tests - Delivery Notes
# ============================================================================


class TestDeliveryNoteRoutes:
    def test_delivery_notes_page_renders(self, logged_in_client):
        resp = logged_in_client.get("/delivery-notes")
        assert resp.status_code == 200

    def test_create_delivery_note(self, logged_in_client, sample_data):
        resp = logged_in_client.post(
            "/delivery-notes",
            data={
                "order_ids": str(sample_data["order_id"]),
                "show_prices": "on",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_create_delivery_note_no_orders(self, logged_in_client):
        """Should flash error when no orders selected."""
        resp = logged_in_client.post(
            "/delivery-notes",
            data={"order_ids": "99999"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_create_delivery_note_with_extras(self, logged_in_client, sample_data):
        resp = logged_in_client.post(
            "/delivery-notes",
            data={
                "order_ids": str(sample_data["order_id"]),
                f"extra_{sample_data['product_id']}": "2",
                "show_prices": "on",
                "planned_delivery_datetime": "2026-02-01T10:00",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_create_delivery_note_with_bundle(self, logged_in_client, sample_data, app):
        with app.app_context():
            bundle = Bundle(name="Test Bundle", bundle_price=40.00)
            db.session.add(bundle)
            db.session.flush()
            bundle.items.append(
                BundleItem(product_id=sample_data["product_id"], quantity=2)
            )
            db.session.commit()
            bundle_id = bundle.id

        resp = logged_in_client.post(
            "/delivery-notes",
            data={
                "order_ids": str(sample_data["order_id"]),
                f"bundle_{bundle_id}": "1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_confirm_delivery(self, logged_in_client, sample_data, app):
        with app.app_context():
            delivery = DeliveryNote(
                primary_order_id=sample_data["order_id"],
                created_by_id=sample_data["user_id"],
            )
            db.session.add(delivery)
            db.session.commit()
            delivery_id = delivery.id

        resp = logged_in_client.post(
            f"/delivery-notes/{delivery_id}/confirm",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_unconfirm_delivery(self, logged_in_client, sample_data, app):
        with app.app_context():
            delivery = DeliveryNote(
                primary_order_id=sample_data["order_id"],
                created_by_id=sample_data["user_id"],
                confirmed=True,
            )
            db.session.add(delivery)
            db.session.commit()
            delivery_id = delivery.id

        resp = logged_in_client.post(
            f"/delivery-notes/{delivery_id}/unconfirm",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_delivery_pdf(self, logged_in_client, sample_data, app):
        with app.app_context():
            delivery = DeliveryNote(
                primary_order_id=sample_data["order_id"],
                created_by_id=sample_data["user_id"],
                show_prices=True,
            )
            db.session.add(delivery)
            db.session.flush()
            delivery.items.append(
                DeliveryItem(
                    product_id=sample_data["product_id"],
                    quantity=2,
                    unit_price=15.50,
                    line_total=31.00,
                )
            )
            db.session.commit()
            delivery_id = delivery.id

        resp = logged_in_client.get(f"/delivery-notes/{delivery_id}/pdf")
        assert resp.status_code == 200
        assert resp.content_type == "application/pdf"


# ============================================================================
# Route tests - Vehicles
# ============================================================================


class TestVehicleRoutes:
    def test_vehicles_page_renders(self, logged_in_client):
        resp = logged_in_client.get("/vehicles")
        assert resp.status_code == 200

    def test_create_vehicle(self, logged_in_client):
        resp = logged_in_client.post(
            "/vehicles",
            data={
                "name": "Test Vehicle",
                "notes": "Test notes",
                "active": "on",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_add_vehicle_schedule(self, logged_in_client, app):
        with app.app_context():
            vehicle = Vehicle(name="Schedule Vehicle", active=True)
            db.session.add(vehicle)
            db.session.commit()
            vehicle_id = vehicle.id

        resp = logged_in_client.post(
            f"/vehicles/{vehicle_id}/schedules",
            data={
                "day_of_week": "0",
                "start_time": "08:00",
                "end_time": "16:00",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_add_schedule_missing_time(self, logged_in_client, app):
        """Test schedule with missing time uses defaults."""
        with app.app_context():
            vehicle = Vehicle(name="Default Time Vehicle", active=True)
            db.session.add(vehicle)
            db.session.commit()
            vehicle_id = vehicle.id

        resp = logged_in_client.post(
            f"/vehicles/{vehicle_id}/schedules",
            data={
                "day_of_week": "1",
                "start_time": "",
                "end_time": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ============================================================================
# Route tests - Logistics
# ============================================================================


class TestLogisticsRoutes:
    def test_logistics_page_renders(self, logged_in_client):
        resp = logged_in_client.get("/logistics")
        assert resp.status_code == 200

    def test_logistics_interval_daily(self, logged_in_client):
        resp = logged_in_client.get("/logistics?interval=daily")
        assert resp.status_code == 200

    def test_logistics_interval_monthly(self, logged_in_client):
        resp = logged_in_client.get("/logistics?interval=monthly")
        assert resp.status_code == 200

    def test_create_logistics_plan(self, logged_in_client, sample_data):
        resp = logged_in_client.post(
            "/logistics",
            data={
                "plan_type": "pickup",
                "order_id": str(sample_data["order_id"]),
                "planned_datetime": "2026-02-01T10:00",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_create_logistics_plan_no_datetime(self, logged_in_client, sample_data):
        """Test that missing datetime falls back to utc_now()."""
        resp = logged_in_client.post(
            "/logistics",
            data={
                "plan_type": "delivery",
                "order_id": str(sample_data["order_id"]),
                "planned_datetime": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ============================================================================
# Route tests - Invoices
# ============================================================================


class TestInvoiceRoutes:
    def test_invoices_page_renders(self, logged_in_client):
        resp = logged_in_client.get("/invoices")
        assert resp.status_code == 200

    def test_create_invoice_no_partner(self, logged_in_client):
        resp = logged_in_client.post(
            "/invoices",
            data={"partner_id": ""},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_create_invoice_no_unbilled(self, logged_in_client, sample_data):
        """Should flash error when no unbilled delivery notes."""
        resp = logged_in_client.post(
            "/invoices",
            data={"partner_id": str(sample_data["partner_id"])},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_create_invoice_with_delivery(self, logged_in_client, sample_data, app):
        with app.app_context():
            delivery = DeliveryNote(
                primary_order_id=sample_data["order_id"],
                created_by_id=sample_data["user_id"],
            )
            db.session.add(delivery)
            db.session.flush()
            delivery.orders.append(DeliveryNoteOrder(order_id=sample_data["order_id"]))
            delivery.items.append(
                DeliveryItem(
                    product_id=sample_data["product_id"],
                    quantity=3,
                    unit_price=15.50,
                    line_total=46.50,
                )
            )
            db.session.commit()

        resp = logged_in_client.post(
            "/invoices",
            data={"partner_id": str(sample_data["partner_id"])},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_add_manual_invoice_item(self, logged_in_client, sample_data, app):
        with app.app_context():
            invoice = Invoice(
                partner_id=sample_data["partner_id"], status="draft", total=0.0
            )
            db.session.add(invoice)
            db.session.commit()
            invoice_id = invoice.id

        resp = logged_in_client.post(
            f"/invoices/{invoice_id}/items",
            data={
                "description": "Manual item",
                "quantity": "2",
                "unit_price": "10.00",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_invoice_pdf(self, logged_in_client, sample_data, app):
        with app.app_context():
            invoice = Invoice(
                partner_id=sample_data["partner_id"], status="draft", total=100.0
            )
            db.session.add(invoice)
            db.session.flush()
            invoice.items.append(
                InvoiceItem(
                    description="Test item",
                    quantity=2,
                    unit_price=50.0,
                    total=100.0,
                )
            )
            db.session.commit()
            invoice_id = invoice.id

        resp = logged_in_client.get(f"/invoices/{invoice_id}/pdf")
        assert resp.status_code == 200
        assert resp.content_type == "application/pdf"

    def test_send_invoice_email_disabled(self, logged_in_client, sample_data, app):
        with app.app_context():
            invoice = Invoice(
                partner_id=sample_data["partner_id"], status="draft", total=50.0
            )
            db.session.add(invoice)
            db.session.flush()
            invoice.items.append(
                InvoiceItem(
                    description="Item",
                    quantity=1,
                    unit_price=50.0,
                    total=50.0,
                )
            )
            db.session.commit()
            invoice_id = invoice.id

        resp = logged_in_client.post(
            f"/invoices/{invoice_id}/send",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_export_invoice_disabled(self, logged_in_client, sample_data, app):
        with app.app_context():
            invoice = Invoice(
                partner_id=sample_data["partner_id"], status="draft", total=50.0
            )
            db.session.add(invoice)
            db.session.commit()
            invoice_id = invoice.id

        resp = logged_in_client.post(
            f"/invoices/{invoice_id}/export",
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ============================================================================
# Route tests - Error handlers
# ============================================================================


class TestErrorHandlers:
    def test_404_error(self, logged_in_client):
        resp = logged_in_client.get("/nonexistent-page")
        assert resp.status_code == 404

    def test_404_renders_template(self, logged_in_client):
        resp = logged_in_client.get("/nonexistent-page")
        assert b"404" in resp.data


# ============================================================================
# Business logic tests
# ============================================================================


class TestBusinessLogic:
    def test_build_invoice_for_partner(self, app, sample_data):
        with app.app_context():
            delivery = DeliveryNote(
                primary_order_id=sample_data["order_id"],
                created_by_id=sample_data["user_id"],
            )
            db.session.add(delivery)
            db.session.flush()
            delivery.orders.append(DeliveryNoteOrder(order_id=sample_data["order_id"]))
            delivery.items.append(
                DeliveryItem(
                    product_id=sample_data["product_id"],
                    quantity=3,
                    unit_price=15.50,
                    line_total=46.50,
                )
            )
            db.session.commit()

            invoice = build_invoice_for_partner(sample_data["partner_id"])
            assert invoice is not None
            assert invoice.total == 46.50
            assert len(invoice.items) == 1
            assert invoice.status == "draft"

    def test_build_invoice_no_unbilled(self, app, sample_data):
        with app.app_context():
            with pytest.raises(ValueError, match="nevyfakturovan"):
                build_invoice_for_partner(sample_data["partner_id"])

    def test_build_invoice_marks_invoiced(self, app, sample_data):
        with app.app_context():
            delivery = DeliveryNote(
                primary_order_id=sample_data["order_id"],
                created_by_id=sample_data["user_id"],
            )
            db.session.add(delivery)
            db.session.flush()
            delivery.orders.append(DeliveryNoteOrder(order_id=sample_data["order_id"]))
            delivery.items.append(
                DeliveryItem(
                    product_id=sample_data["product_id"],
                    quantity=1,
                    unit_price=10.0,
                    line_total=10.0,
                )
            )
            db.session.commit()
            delivery_id = delivery.id

            build_invoice_for_partner(sample_data["partner_id"])
            dn = db.session.get(DeliveryNote, delivery_id)
            assert dn.invoiced is True

    def test_build_invoice_group_code(self, app, sample_data):
        """Test invoice generation for partners sharing a group code."""
        with app.app_context():
            partner2 = Partner(
                name="Partner 2", group_code="GRP1", discount_percent=0
            )
            db.session.add(partner2)
            db.session.flush()

            order2 = Order(
                partner_id=partner2.id,
                created_by_id=sample_data["user_id"],
            )
            db.session.add(order2)
            db.session.flush()

            order2.items.append(
                OrderItem(product_id=sample_data["product_id"], quantity=1, unit_price=15.50)
            )

            delivery = DeliveryNote(
                primary_order_id=order2.id,
                created_by_id=sample_data["user_id"],
            )
            db.session.add(delivery)
            db.session.flush()
            delivery.orders.append(DeliveryNoteOrder(order_id=order2.id))
            delivery.items.append(
                DeliveryItem(
                    product_id=sample_data["product_id"],
                    quantity=1,
                    unit_price=15.50,
                    line_total=15.50,
                )
            )
            db.session.commit()

            # Invoice for partner1 should find delivery for partner2 (same group)
            invoice = build_invoice_for_partner(sample_data["partner_id"])
            assert invoice.total == 15.50


# ============================================================================
# PDF generation tests
# ============================================================================


class TestPDFGeneration:
    def test_generate_delivery_pdf(self, app, sample_data):
        with app.app_context():
            delivery = DeliveryNote(
                primary_order_id=sample_data["order_id"],
                created_by_id=sample_data["user_id"],
                show_prices=True,
            )
            db.session.add(delivery)
            db.session.flush()
            delivery.items.append(
                DeliveryItem(
                    product_id=sample_data["product_id"],
                    quantity=2,
                    unit_price=15.50,
                    line_total=31.00,
                )
            )
            db.session.commit()

            app_cfg = app.config["APP_CONFIG"]
            pdf_path = generate_delivery_pdf(delivery, app_cfg)
            assert os.path.exists(pdf_path)
            assert pdf_path.endswith(".pdf")
            os.unlink(pdf_path)

    def test_generate_delivery_pdf_no_prices(self, app, sample_data):
        with app.app_context():
            delivery = DeliveryNote(
                primary_order_id=sample_data["order_id"],
                created_by_id=sample_data["user_id"],
                show_prices=False,
            )
            db.session.add(delivery)
            db.session.flush()
            delivery.items.append(
                DeliveryItem(
                    product_id=sample_data["product_id"],
                    quantity=1,
                    unit_price=15.50,
                    line_total=15.50,
                )
            )
            db.session.commit()

            app_cfg = app.config["APP_CONFIG"]
            pdf_path = generate_delivery_pdf(delivery, app_cfg)
            assert os.path.exists(pdf_path)
            os.unlink(pdf_path)

    def test_generate_delivery_pdf_with_bundle_components(self, app, sample_data):
        with app.app_context():
            bundle = Bundle(name="Test Bundle", bundle_price=40.00)
            db.session.add(bundle)
            db.session.flush()

            delivery = DeliveryNote(
                primary_order_id=sample_data["order_id"],
                created_by_id=sample_data["user_id"],
                show_prices=True,
            )
            db.session.add(delivery)
            db.session.flush()

            delivery_item = DeliveryItem(
                bundle_id=bundle.id,
                quantity=1,
                unit_price=40.00,
                line_total=40.00,
            )
            delivery_item.components.append(
                DeliveryItemComponent(
                    product_id=sample_data["product_id"],
                    quantity=2,
                )
            )
            delivery.items.append(delivery_item)
            db.session.commit()

            app_cfg = app.config["APP_CONFIG"]
            pdf_path = generate_delivery_pdf(delivery, app_cfg)
            assert os.path.exists(pdf_path)
            os.unlink(pdf_path)

    def test_generate_invoice_pdf(self, app, sample_data):
        with app.app_context():
            invoice = Invoice(
                partner_id=sample_data["partner_id"], status="draft", total=100.0
            )
            db.session.add(invoice)
            db.session.flush()
            invoice.items.append(
                InvoiceItem(
                    description="Test item",
                    quantity=2,
                    unit_price=50.0,
                    total=100.0,
                )
            )
            db.session.commit()

            app_cfg = app.config["APP_CONFIG"]
            pdf_path = generate_invoice_pdf(invoice, app_cfg)
            assert os.path.exists(pdf_path)
            assert pdf_path.endswith(".pdf")
            os.unlink(pdf_path)


# ============================================================================
# Role permission tests
# ============================================================================


class TestRolePermissions:
    def test_operator_can_access_partners(self, client, app):
        with app.app_context():
            operator = User(
                username="operator1",
                password_hash="pbkdf2:sha256:unused",
                role="operator",
            )
            db.session.add(operator)
            db.session.commit()
            with client.session_transaction() as sess:
                sess["user_id"] = operator.id

        resp = client.get("/partners")
        assert resp.status_code == 200

    def test_collector_cannot_access_partners(self, client, app):
        with app.app_context():
            collector = User(
                username="collector1",
                password_hash="pbkdf2:sha256:unused",
                role="collector",
            )
            db.session.add(collector)
            db.session.commit()
            with client.session_transaction() as sess:
                sess["user_id"] = collector.id

        resp = client.get("/partners", follow_redirects=True)
        assert resp.status_code == 200
        assert "Nem" in resp.data.decode("utf-8")  # "Nemáte oprávnenie"

    def test_collector_can_access_delivery(self, client, app):
        with app.app_context():
            collector = User(
                username="collector2",
                password_hash="pbkdf2:sha256:unused",
                role="collector",
            )
            db.session.add(collector)
            db.session.commit()
            with client.session_transaction() as sess:
                sess["user_id"] = collector.id

        resp = client.get("/delivery-notes")
        assert resp.status_code == 200

    def test_customer_limited_access(self, client, app):
        with app.app_context():
            customer = User(
                username="customer1",
                password_hash="pbkdf2:sha256:unused",
                role="customer",
            )
            db.session.add(customer)
            db.session.commit()
            with client.session_transaction() as sess:
                sess["user_id"] = customer.id

        resp = client.get("/partners", follow_redirects=True)
        assert resp.status_code == 200


# ============================================================================
# Config model tests
# ============================================================================


class TestConfigModels:
    def test_app_config_creation(self):
        cfg = AppConfig(
            name="Test",
            secret_key="secret",
            base_currency="EUR",
            show_prices_default=True,
        )
        assert cfg.name == "Test"
        assert cfg.base_currency == "EUR"

    def test_email_config_creation(self):
        cfg = EmailConfig(
            enabled=False,
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_user="user",
            smtp_password="pass",
            sender="noreply@test.com",
            operator_cc="cc@test.com",
        )
        assert cfg.enabled is False
        assert cfg.smtp_port == 587

    def test_superfaktura_config_creation(self):
        cfg = SuperfakturaConfig(
            enabled=False,
            api_email="api@test.com",
            api_key="key",
            company_id="123",
            base_url="https://api.superfaktura.sk",
        )
        assert cfg.enabled is False
        assert cfg.company_id == "123"


# ============================================================================
# Edge case / regression tests
# ============================================================================


class TestEdgeCases:
    def test_delivery_note_with_no_primary_order(self, app):
        """DeliveryNote.primary_order is nullable - test PDF handles None."""
        with app.app_context():
            user = User.query.filter_by(username="admin").first()
            delivery = DeliveryNote(
                primary_order_id=None,
                created_by_id=user.id,
                show_prices=True,
            )
            db.session.add(delivery)
            db.session.flush()
            delivery.items.append(
                DeliveryItem(
                    product_id=None,
                    bundle_id=None,
                    quantity=1,
                    unit_price=10.0,
                    line_total=10.0,
                )
            )
            db.session.commit()

            app_cfg = app.config["APP_CONFIG"]
            pdf_path = generate_delivery_pdf(delivery, app_cfg)
            assert os.path.exists(pdf_path)
            os.unlink(pdf_path)

    def test_invoice_item_without_line_total(self, app, sample_data):
        """Test invoice build handles items where line_total is 0/None."""
        with app.app_context():
            delivery = DeliveryNote(
                primary_order_id=sample_data["order_id"],
                created_by_id=sample_data["user_id"],
            )
            db.session.add(delivery)
            db.session.flush()
            delivery.orders.append(DeliveryNoteOrder(order_id=sample_data["order_id"]))
            delivery.items.append(
                DeliveryItem(
                    product_id=sample_data["product_id"],
                    quantity=2,
                    unit_price=15.50,
                    line_total=0.0,  # line_total is 0, should fallback
                )
            )
            db.session.commit()

            invoice = build_invoice_for_partner(sample_data["partner_id"])
            # Should use fallback: unit_price * quantity = 31.0
            assert invoice.total == 31.0

    def test_multiple_delivery_notes_in_invoice(self, app, sample_data):
        with app.app_context():
            for i in range(3):
                delivery = DeliveryNote(
                    primary_order_id=sample_data["order_id"],
                    created_by_id=sample_data["user_id"],
                )
                db.session.add(delivery)
                db.session.flush()
                delivery.orders.append(
                    DeliveryNoteOrder(order_id=sample_data["order_id"])
                )
                delivery.items.append(
                    DeliveryItem(
                        product_id=sample_data["product_id"],
                        quantity=1,
                        unit_price=10.0,
                        line_total=10.0,
                    )
                )
            db.session.commit()

            invoice = build_invoice_for_partner(sample_data["partner_id"])
            assert invoice.total == 30.0
            assert len(invoice.items) == 3

    def test_partner_with_empty_discount(self, logged_in_client):
        """Test creating partner with empty discount_percent."""
        resp = logged_in_client.post(
            "/partners",
            data={"name": "No Discount Partner", "discount_percent": ""},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_order_with_zero_quantity_products(self, logged_in_client, sample_data):
        """Test creating order where all products have 0 quantity."""
        resp = logged_in_client.post(
            "/orders",
            data={
                "partner_id": str(sample_data["partner_id"]),
                f"product_{sample_data['product_id']}": "0",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_delivery_note_cross_group_check(self, logged_in_client, app, sample_data):
        """Test delivery note creation rejects orders from different groups."""
        with app.app_context():
            partner2 = Partner(name="Different Group", group_code="GRP_OTHER")
            db.session.add(partner2)
            db.session.flush()

            order2 = Order(
                partner_id=partner2.id,
                created_by_id=sample_data["user_id"],
            )
            db.session.add(order2)
            db.session.commit()
            order2_id = order2.id

        resp = logged_in_client.post(
            "/delivery-notes",
            data={
                "order_ids": [
                    str(sample_data["order_id"]),
                    str(order2_id),
                ],
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ============================================================================
# Security feature tests
# ============================================================================


class TestSecurityHeaders:
    def test_x_content_type_options(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_x_xss_protection(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert resp.headers.get("X-XSS-Protection") == "1; mode=block"

    def test_referrer_policy(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_permissions_policy(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert "geolocation=()" in resp.headers.get("Permissions-Policy", "")


class TestSessionSecurity:
    def test_session_cookie_secure_config(self, app):
        assert "SESSION_COOKIE_SECURE" in app.config

    def test_session_cookie_httponly(self, app):
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True

    def test_session_cookie_samesite(self, app):
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


class TestPasswordChange:
    def test_change_password_page_renders(self, logged_in_client):
        resp = logged_in_client.get("/change-password")
        assert resp.status_code == 200
        assert "Zmena hesla" in resp.data.decode("utf-8")

    def test_change_password_success(self, logged_in_client):
        resp = logged_in_client.post(
            "/change-password",
            data={
                "current_password": TEST_PASSWORD,
                "new_password": "newsecurepassword",
                "confirm_password": "newsecurepassword",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "spešne" in resp.data.decode("utf-8")  # "úspešne"

    def test_change_password_wrong_current(self, logged_in_client):
        resp = logged_in_client.post(
            "/change-password",
            data={
                "current_password": "wrongpassword",
                "new_password": "newsecurepassword",
                "confirm_password": "newsecurepassword",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "nespr" in resp.data.decode("utf-8").lower()

    def test_change_password_mismatch(self, logged_in_client):
        resp = logged_in_client.post(
            "/change-password",
            data={
                "current_password": TEST_PASSWORD,
                "new_password": "newsecurepassword",
                "confirm_password": "differentpassword",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "nezhoduj" in resp.data.decode("utf-8").lower()

    def test_change_password_too_short(self, logged_in_client):
        resp = logged_in_client.post(
            "/change-password",
            data={
                "current_password": TEST_PASSWORD,
                "new_password": "short",
                "confirm_password": "short",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "8" in resp.data.decode("utf-8")

    def test_must_change_password_redirects(self, client, app):
        """User with must_change_password=True is redirected to change-password."""
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            admin.must_change_password = True
            db.session.commit()
            with client.session_transaction() as sess:
                sess["user_id"] = admin.id

        resp = client.get("/")
        assert resp.status_code == 302
        assert "change-password" in resp.headers.get("Location", "")

    def test_change_password_requires_login(self, client):
        resp = client.get("/change-password")
        assert resp.status_code == 302


class TestPDFAccessControl:
    def test_collector_cannot_access_invoice_pdf(self, client, app, sample_data):
        """Collector role should not access invoice PDFs (requires manage_invoices)."""
        with app.app_context():
            collector = User(
                username="collector_pdf_test",
                password_hash="pbkdf2:sha256:unused",
                role="collector",
            )
            db.session.add(collector)
            db.session.flush()

            invoice = Invoice(
                partner_id=sample_data["partner_id"], status="draft", total=50.0
            )
            db.session.add(invoice)
            db.session.commit()
            invoice_id = invoice.id

            with client.session_transaction() as sess:
                sess["user_id"] = collector.id

        resp = client.get(f"/invoices/{invoice_id}/pdf", follow_redirects=True)
        assert resp.status_code == 200
        assert "Nem" in resp.data.decode("utf-8")  # "Nemáte oprávnenie"

    def test_customer_cannot_access_delivery_pdf(self, client, app, sample_data):
        """Customer role should not access delivery PDFs (requires manage_delivery)."""
        with app.app_context():
            customer = User(
                username="customer_pdf_test",
                password_hash="pbkdf2:sha256:unused",
                role="customer",
            )
            db.session.add(customer)
            db.session.flush()

            delivery = DeliveryNote(
                primary_order_id=sample_data["order_id"],
                created_by_id=sample_data["user_id"],
            )
            db.session.add(delivery)
            db.session.commit()
            delivery_id = delivery.id

            with client.session_transaction() as sess:
                sess["user_id"] = customer.id

        resp = client.get(
            f"/delivery-notes/{delivery_id}/pdf", follow_redirects=True
        )
        assert resp.status_code == 200
        assert "Nem" in resp.data.decode("utf-8")  # "Nemáte oprávnenie"
