"""Application factory — clean entry point for the Flask application."""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, g, redirect, render_template, request, session, url_for
from sqlalchemy import event, inspect, text

from config import load_config, enable_sqlite_fks
from extensions import csrf, db, limiter
from models import ROLE_PERMISSIONS, AppSetting, Tenant, User, UserTenant
from routes import register_blueprints
from services.auth import ensure_admin_user
from services.tenant import register_tenant_guards

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def _needs_rebuild(insp, table_name: str) -> bool:
    """Check if a table needs rebuilding for manual item support.

    Returns True when the table's DDL still contains the old check
    constraint that does NOT allow ``is_manual = 1``, OR when a column
    that should be nullable is still NOT NULL (e.g. order_item.product_id).
    """
    if not insp.has_table(table_name):
        return False
    # Read the original CREATE TABLE statement from sqlite_master
    result = db.session.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table_name},
    ).scalar()
    if not result:
        return False
    # If the DDL has the old constraint without is_manual, rebuild
    if "product_id IS NOT NULL OR bundle_id IS NOT NULL" in result and "is_manual" not in result:
        return True
    # If order_item.product_id is still NOT NULL, rebuild
    if table_name == "order_item":
        cols = insp.get_columns(table_name)
        pid_col = next((c for c in cols if c["name"] == "product_id"), None)
        if pid_col and pid_col.get("nullable") is False:
            return True
    return False


def _rebuild_for_manual_items(insp):
    """Rebuild order_item and delivery_item tables if needed for manual items."""
    if _needs_rebuild(insp, "order_item"):
        logger.info("Rebuilding order_item table for manual item support")
        db.session.execute(text(
            'ALTER TABLE "order_item" RENAME TO "_order_item_old"'
        ))
        db.session.execute(text("""
            CREATE TABLE "order_item" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER REFERENCES tenant(id),
                order_id INTEGER NOT NULL REFERENCES "order"(id),
                product_id INTEGER REFERENCES product(id),
                bundle_id INTEGER REFERENCES bundle(id),
                is_manual BOOLEAN DEFAULT 0,
                manual_name VARCHAR(200),
                quantity INTEGER NOT NULL DEFAULT 1,
                unit_price NUMERIC(10,2) NOT NULL,
                CONSTRAINT ck_order_item_has_source
                    CHECK (product_id IS NOT NULL OR bundle_id IS NOT NULL OR is_manual = 1)
            )
        """))
        db.session.execute(text("""
            INSERT INTO "order_item" (id, tenant_id, order_id, product_id, bundle_id,
                is_manual, manual_name, quantity, unit_price)
            SELECT id, tenant_id, order_id, product_id, bundle_id,
                COALESCE(is_manual, 0), manual_name, quantity, unit_price
            FROM "_order_item_old"
        """))
        db.session.execute(text('DROP TABLE "_order_item_old"'))
        logger.info("Rebuilt order_item table successfully")

    if _needs_rebuild(insp, "delivery_item"):
        logger.info("Rebuilding delivery_item table for manual item support")
        db.session.execute(text(
            'ALTER TABLE "delivery_item" RENAME TO "_delivery_item_old"'
        ))
        db.session.execute(text("""
            CREATE TABLE "delivery_item" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER REFERENCES tenant(id),
                delivery_note_id INTEGER NOT NULL REFERENCES delivery_note(id),
                product_id INTEGER REFERENCES product(id),
                bundle_id INTEGER REFERENCES bundle(id),
                is_manual BOOLEAN DEFAULT 0,
                manual_name VARCHAR(200),
                quantity INTEGER NOT NULL DEFAULT 1,
                unit_price NUMERIC(10,2) NOT NULL,
                line_total NUMERIC(10,2) NOT NULL DEFAULT 0.0,
                CONSTRAINT ck_delivery_item_has_source
                    CHECK (product_id IS NOT NULL OR bundle_id IS NOT NULL OR is_manual = 1)
            )
        """))
        db.session.execute(text("""
            INSERT INTO "delivery_item" (id, tenant_id, delivery_note_id, product_id, bundle_id,
                is_manual, manual_name, quantity, unit_price, line_total)
            SELECT id, tenant_id, delivery_note_id, product_id, bundle_id,
                COALESCE(is_manual, 0), manual_name, quantity, unit_price,
                COALESCE(line_total, 0.0)
            FROM "_delivery_item_old"
        """))
        db.session.execute(text('DROP TABLE "_delivery_item_old"'))
        # Rebuild the child table's FK (delivery_item_component)
        if insp.has_table("delivery_item_component"):
            logger.info("Rebuilding delivery_item_component to restore FK")
            db.session.execute(text(
                'ALTER TABLE "delivery_item_component" RENAME TO "_dic_old"'
            ))
            db.session.execute(text("""
                CREATE TABLE "delivery_item_component" (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER REFERENCES tenant(id),
                    delivery_item_id INTEGER NOT NULL REFERENCES delivery_item(id),
                    product_id INTEGER NOT NULL REFERENCES product(id),
                    quantity INTEGER NOT NULL DEFAULT 1
                )
            """))
            db.session.execute(text("""
                INSERT INTO "delivery_item_component" (id, tenant_id, delivery_item_id, product_id, quantity)
                SELECT id, tenant_id, delivery_item_id, product_id, quantity
                FROM "_dic_old"
            """))
            db.session.execute(text('DROP TABLE "_dic_old"'))
        logger.info("Rebuilt delivery_item table successfully")


def _migrate_schema():
    """Add columns introduced by the refactor to existing tables.

    ``db.create_all()`` creates *new* tables but never alters existing ones.
    This function inspects every table and issues ``ALTER TABLE … ADD COLUMN``
    for any column the model defines but the database lacks.  It is safe to
    call repeatedly (idempotent).
    """
    # Map of table_name → [(column_name, SQL type + default)]
    _MIGRATIONS: dict[str, list[tuple[str, str]]] = {
        "user": [
            ("must_change_password", "BOOLEAN DEFAULT 0"),
            ("is_active", "BOOLEAN DEFAULT 1"),
            ("partner_id", "INTEGER REFERENCES partner(id)"),
            ("password_changed_at", "DATETIME"),
            ("created_at", "DATETIME"),
            ("updated_at", "DATETIME"),
            ("is_superadmin", "BOOLEAN DEFAULT 0"),
        ],
        "partner": [
            ("is_active", "BOOLEAN DEFAULT 1"),
            ("is_deleted", "BOOLEAN DEFAULT 0"),
            ("created_at", "DATETIME"),
            ("updated_at", "DATETIME"),
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "partner_address": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "contact": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "product": [
            ("product_number", "VARCHAR(60)"),
            ("vat_rate", "REAL DEFAULT 20.0"),
            ("is_active", "BOOLEAN DEFAULT 1"),
            ("created_at", "DATETIME"),
            ("updated_at", "DATETIME"),
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "product_price_history": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "bundle": [
            ("bundle_number", "VARCHAR(60)"),
            ("discount_excluded", "BOOLEAN DEFAULT 0"),
            ("is_active", "BOOLEAN DEFAULT 1"),
            ("created_at", "DATETIME"),
            ("updated_at", "DATETIME"),
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "bundle_price_history": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "bundle_item": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "product_restriction": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "order": [
            ("order_number", "VARCHAR(60)"),
            ("pickup_address_id", "INTEGER REFERENCES partner_address(id)"),
            ("delivery_address_id", "INTEGER REFERENCES partner_address(id)"),
            ("is_locked", "BOOLEAN DEFAULT 0"),
            ("updated_at", "DATETIME"),
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "order_item": [
            ("bundle_id", "INTEGER REFERENCES bundle(id)"),
            ("is_manual", "BOOLEAN DEFAULT 0"),
            ("manual_name", "VARCHAR(200)"),
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "delivery_note": [
            ("note_number", "VARCHAR(60)"),
            ("updated_at", "DATETIME"),
            ("actual_delivery_datetime", "DATETIME"),
            ("is_locked", "BOOLEAN DEFAULT 0"),
            ("partner_id", "INTEGER REFERENCES partner(id)"),
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "delivery_note_order": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "delivery_item": [
            ("is_manual", "BOOLEAN DEFAULT 0"),
            ("manual_name", "VARCHAR(200)"),
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "delivery_item_component": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "vehicle": [
            ("registration_number", "VARCHAR(20)"),
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "vehicle_schedule": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "logistics_plan": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "invoice": [
            ("invoice_number", "VARCHAR(30)"),
            ("updated_at", "DATETIME"),
            ("total_with_vat", "REAL DEFAULT 0.0"),
            ("is_locked", "BOOLEAN DEFAULT 0"),
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "invoice_item": [
            ("source_delivery_id", "INTEGER REFERENCES delivery_note(id)"),
            ("vat_rate", "REAL DEFAULT 20.0"),
            ("vat_amount", "REAL DEFAULT 0.0"),
            ("total_with_vat", "REAL DEFAULT 0.0"),
            ("is_manual", "BOOLEAN DEFAULT 0"),
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "audit_log": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "app_setting": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "numbering_config": [
            ("pattern", "VARCHAR(120) DEFAULT ''"),
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "number_sequence": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
        "pdf_template": [
            ("tenant_id", "INTEGER REFERENCES tenant(id)"),
        ],
    }

    insp = inspect(db.engine)
    for table_name, columns in _MIGRATIONS.items():
        if not insp.has_table(table_name):
            continue  # table will be created by db.create_all()
        existing = {col["name"] for col in insp.get_columns(table_name)}
        for col_name, col_sql in columns:
            if col_name not in existing:
                # Quote table name to handle SQL keywords like "order"
                stmt = f'ALTER TABLE "{table_name}" ADD COLUMN {col_name} {col_sql}'
                db.session.execute(text(stmt))
                logger.info("Migrated: %s.%s", table_name, col_name)

    # SQLite cannot ALTER COLUMN nullability or CHECK constraints.
    # Rebuild tables that need schema changes for manual item support.
    _rebuild_for_manual_items(insp)

    # Unique index that SQLite cannot add inline with ALTER TABLE
    try:
        db.session.execute(
            text('CREATE UNIQUE INDEX IF NOT EXISTS "uq_invoice_number" '
                 'ON "invoice" (invoice_number)')
        )
    except Exception:
        pass  # index already exists or table not yet created

    db.session.commit()


def _rebuild_unique_constraints():
    """Rebuild tables that need composite unique constraints for multi-tenancy.

    SQLite cannot ALTER or DROP constraints, so we rebuild via
    rename-create-copy-drop.
    """
    insp = inspect(db.engine)

    # --- app_setting: key was unique, now (tenant_id, key) ---
    if insp.has_table("app_setting"):
        ddl = db.session.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='app_setting'")
        ).scalar() or ""
        if '"key" VARCHAR(80) NOT NULL UNIQUE' in ddl or "UNIQUE" in ddl.split("key")[1].split(",")[0] if "key" in ddl else False:
            logger.info("Rebuilding app_setting for composite unique constraint")
            db.session.execute(text('ALTER TABLE "app_setting" RENAME TO "_app_setting_old"'))
            db.session.execute(text("""
                CREATE TABLE "app_setting" (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER REFERENCES tenant(id),
                    "key" VARCHAR(80) NOT NULL,
                    value TEXT,
                    UNIQUE(tenant_id, "key")
                )
            """))
            db.session.execute(text("""
                INSERT INTO "app_setting" (id, tenant_id, "key", value)
                SELECT id, tenant_id, "key", value FROM "_app_setting_old"
            """))
            db.session.execute(text('DROP TABLE "_app_setting_old"'))
            logger.info("Rebuilt app_setting table successfully")

    # --- numbering_config: entity_type was unique, now (tenant_id, entity_type) ---
    if insp.has_table("numbering_config"):
        ddl = db.session.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='numbering_config'")
        ).scalar() or ""
        if "UNIQUE" in ddl and "tenant_id" not in ddl:
            logger.info("Rebuilding numbering_config for composite unique constraint")
            db.session.execute(text('ALTER TABLE "numbering_config" RENAME TO "_numbering_config_old"'))
            db.session.execute(text("""
                CREATE TABLE "numbering_config" (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER REFERENCES tenant(id),
                    entity_type VARCHAR(40) NOT NULL,
                    pattern VARCHAR(120) DEFAULT '',
                    UNIQUE(tenant_id, entity_type)
                )
            """))
            db.session.execute(text("""
                INSERT INTO "numbering_config" (id, tenant_id, entity_type, pattern)
                SELECT id, tenant_id, entity_type, pattern FROM "_numbering_config_old"
            """))
            db.session.execute(text('DROP TABLE "_numbering_config_old"'))
            logger.info("Rebuilt numbering_config table successfully")

    # --- pdf_template: entity_type was unique, now (tenant_id, entity_type) ---
    if insp.has_table("pdf_template"):
        ddl = db.session.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='pdf_template'")
        ).scalar() or ""
        if "UNIQUE" in ddl and "tenant_id" not in ddl:
            logger.info("Rebuilding pdf_template for composite unique constraint")
            db.session.execute(text('ALTER TABLE "pdf_template" RENAME TO "_pdf_template_old"'))
            db.session.execute(text("""
                CREATE TABLE "pdf_template" (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER REFERENCES tenant(id),
                    entity_type VARCHAR(40) NOT NULL,
                    html_content TEXT DEFAULT '',
                    css_content TEXT DEFAULT '',
                    UNIQUE(tenant_id, entity_type)
                )
            """))
            db.session.execute(text("""
                INSERT INTO "pdf_template" (id, tenant_id, entity_type, html_content, css_content)
                SELECT id, tenant_id, entity_type, html_content, css_content FROM "_pdf_template_old"
            """))
            db.session.execute(text('DROP TABLE "_pdf_template_old"'))
            logger.info("Rebuilt pdf_template table successfully")

    # --- number_sequence: was (entity_type, scope_key), now (tenant_id, entity_type, scope_key) ---
    if insp.has_table("number_sequence"):
        ddl = db.session.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='number_sequence'")
        ).scalar() or ""
        if "UNIQUE" in ddl and "tenant_id" not in ddl:
            logger.info("Rebuilding number_sequence for composite unique constraint")
            db.session.execute(text('ALTER TABLE "number_sequence" RENAME TO "_number_sequence_old"'))
            db.session.execute(text("""
                CREATE TABLE "number_sequence" (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER REFERENCES tenant(id),
                    entity_type VARCHAR(40) NOT NULL,
                    scope_key VARCHAR(120) DEFAULT '',
                    last_value INTEGER DEFAULT 0,
                    UNIQUE(tenant_id, entity_type, scope_key)
                )
            """))
            db.session.execute(text("""
                INSERT INTO "number_sequence" (id, tenant_id, entity_type, scope_key, last_value)
                SELECT id, tenant_id, entity_type, scope_key, last_value FROM "_number_sequence_old"
            """))
            db.session.execute(text('DROP TABLE "_number_sequence_old"'))
            logger.info("Rebuilt number_sequence table successfully")

    # --- invoice: invoice_number was unique, now (tenant_id, invoice_number) ---
    if insp.has_table("invoice"):
        # Drop the old single-column unique index if it exists
        try:
            db.session.execute(text('DROP INDEX IF EXISTS "uq_invoice_number"'))
        except Exception:
            pass

    # --- vehicle: registration_number was unique, now (tenant_id, registration_number) ---
    if insp.has_table("vehicle"):
        ddl = db.session.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='vehicle'")
        ).scalar() or ""
        if "UNIQUE" in ddl and "tenant_id" not in ddl and "registration_number" in ddl:
            logger.info("Rebuilding vehicle for composite unique constraint")
            db.session.execute(text('ALTER TABLE "vehicle" RENAME TO "_vehicle_old"'))
            db.session.execute(text("""
                CREATE TABLE "vehicle" (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER REFERENCES tenant(id),
                    name VARCHAR(120) NOT NULL,
                    registration_number VARCHAR(20),
                    notes VARCHAR(255),
                    active BOOLEAN DEFAULT 1,
                    UNIQUE(tenant_id, registration_number)
                )
            """))
            db.session.execute(text("""
                INSERT INTO "vehicle" (id, tenant_id, name, registration_number, notes, active)
                SELECT id, tenant_id, name, registration_number, notes, active FROM "_vehicle_old"
            """))
            db.session.execute(text('DROP TABLE "_vehicle_old"'))
            logger.info("Rebuilt vehicle table successfully")

    db.session.commit()


def _migrate_tenants():
    """Create default tenant and assign all existing data to it.

    Safe to call repeatedly (idempotent).
    """
    from models import SubscriptionPlan, TenantSubscription
    from utils import utc_now as _utc_now
    from datetime import timedelta as _td

    default_tenant = Tenant.query.filter_by(slug="default").first()
    if default_tenant:
        return  # Already migrated

    # Create default tenant
    default_tenant = Tenant(
        name="Predvolená organizácia",
        slug="default",
        is_active=True,
    )
    db.session.add(default_tenant)
    db.session.flush()
    tid = default_tenant.id
    logger.info("Created default tenant (id=%s)", tid)

    # Backfill tenant_id on all tables where it is NULL
    tables_to_backfill = [
        "partner", "contact", "partner_address", "product",
        "product_price_history", "bundle", "bundle_price_history",
        "bundle_item", "product_restriction", '"order"', "order_item",
        "delivery_note", "delivery_note_order", "delivery_item",
        "delivery_item_component", "invoice", "invoice_item",
        "vehicle", "vehicle_schedule", "logistics_plan", "audit_log",
        "app_setting", "numbering_config", "number_sequence", "pdf_template",
    ]
    for table in tables_to_backfill:
        try:
            db.session.execute(
                text(f"UPDATE {table} SET tenant_id = :tid WHERE tenant_id IS NULL"),
                {"tid": tid},
            )
        except Exception:
            pass  # table may not exist yet

    # Assign all existing users to the default tenant
    users = User.query.all()
    for user in users:
        existing = UserTenant.query.filter_by(
            user_id=user.id, tenant_id=tid
        ).first()
        if not existing:
            db.session.add(UserTenant(
                user_id=user.id,
                tenant_id=tid,
                is_default=True,
            ))

    # Make the first admin a superadmin
    first_admin = User.query.filter_by(role="admin").first()
    if first_admin and not first_admin.is_superadmin:
        first_admin.is_superadmin = True

    # Seed default subscription plans
    _seed_subscription_plans()

    # Create a Pro subscription for the default tenant
    pro_plan = SubscriptionPlan.query.filter_by(slug="pro").first()
    if pro_plan:
        existing_sub = TenantSubscription.query.filter_by(tenant_id=tid).first()
        if not existing_sub:
            now = _utc_now()
            db.session.add(TenantSubscription(
                tenant_id=tid,
                plan_id=pro_plan.id,
                status="active",
                billing_cycle="yearly",
                current_period_start=now,
                current_period_end=now + _td(days=365),
            ))

    db.session.commit()
    logger.info("Default tenant migration complete")


def _seed_subscription_plans():
    """Create the default subscription plans if they don't exist."""
    from models import SubscriptionPlan
    from decimal import Decimal

    if SubscriptionPlan.query.count() > 0:
        return

    plans = [
        SubscriptionPlan(
            name="Free",
            slug="free",
            description="Základný plán pre malé firmy",
            price_monthly=Decimal("0.00"),
            price_yearly=Decimal("0.00"),
            max_users=2,
            max_partners=10,
            max_invoices_per_month=5,
            sort_order=1,
        ),
        SubscriptionPlan(
            name="Basic",
            slug="basic",
            description="Rozšírený plán s vyššími limitmi",
            price_monthly=Decimal("19.00"),
            price_yearly=Decimal("190.00"),
            max_users=10,
            max_partners=100,
            max_invoices_per_month=50,
            sort_order=2,
        ),
        SubscriptionPlan(
            name="Pro",
            slug="pro",
            description="Profesionálny plán bez limitov",
            price_monthly=Decimal("49.00"),
            price_yearly=Decimal("490.00"),
            max_users=0,  # unlimited
            max_partners=0,
            max_invoices_per_month=0,
            sort_order=3,
        ),
    ]
    for plan in plans:
        db.session.add(plan)
    db.session.flush()
    logger.info("Seeded default subscription plans")


def create_app():
    """Create and configure the Flask application."""
    app_cfg, email_cfg, sf_cfg, db_uri = load_config()

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.secret_key = app_cfg.secret_key
    app.config["APP_CONFIG"] = app_cfg
    app.config["EMAIL_CONFIG"] = email_cfg
    app.config["SF_CONFIG"] = sf_cfg

    # Session security
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = (
        os.environ.get("FLASK_ENV", "") != "development"
    )
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

    # Initialize extensions
    csrf.init_app(app)
    limiter.init_app(app)
    db.init_app(app)

    # SQLite foreign key enforcement
    if "sqlite" in db_uri:
        with app.app_context():
            event.listen(db.engine, "connect", enable_sqlite_fks)

    # Create new tables first (so FK references like tenant(id) exist),
    # then migrate columns on pre-existing tables, rebuild constraints,
    # backfill tenants & seed admin.
    with app.app_context():
        db.create_all()
        _migrate_schema()
        _rebuild_unique_constraints()
        _migrate_tenants()
        ensure_admin_user()

    # Register tenant write-protection guard
    register_tenant_guards(app)

    # Register all blueprints
    register_blueprints(app)

    # ------------------------------------------------------------------
    # Request hooks
    # ------------------------------------------------------------------

    # Endpoints that don't require a tenant
    _TENANT_EXEMPT = {
        "auth.login", "auth.logout", "auth.change_password",
        "tenant.select_tenant", "tenant.switch_tenant",
        "billing.webhook_stripe",
        "static",
    }

    @app.before_request
    def load_current_user_and_tenant():
        """Set ``g.current_user`` and ``g.current_tenant`` from the session."""
        user_id = session.get("user_id")
        if user_id:
            user = db.session.get(User, user_id)
            if user and not user.is_active:
                session.clear()
                g.current_user = None
                g.current_tenant = None
                g._tenant_id = None
            else:
                g.current_user = user
                # Load tenant from session
                tenant_id = session.get("active_tenant_id")
                if tenant_id and user:
                    # Verify user still has access to this tenant
                    membership = UserTenant.query.filter_by(
                        user_id=user.id, tenant_id=tenant_id
                    ).first()
                    if membership or user.is_superadmin:
                        tenant = db.session.get(Tenant, tenant_id)
                        if tenant and tenant.is_active:
                            g.current_tenant = tenant
                            g._tenant_id = tenant_id
                        else:
                            session.pop("active_tenant_id", None)
                            g.current_tenant = None
                            g._tenant_id = None
                    else:
                        session.pop("active_tenant_id", None)
                        g.current_tenant = None
                        g._tenant_id = None
                else:
                    g.current_tenant = None
                    g._tenant_id = None
        else:
            g.current_user = None
            g.current_tenant = None
            g._tenant_id = None

    @app.before_request
    def require_tenant_selection():
        """Redirect to tenant selection if user is logged in but has no active tenant."""
        if not request.endpoint or request.endpoint in _TENANT_EXEMPT:
            return None
        user = getattr(g, "current_user", None)
        if not user:
            return None  # login_required will handle redirect
        tenant = getattr(g, "current_tenant", None)
        if tenant:
            return None  # tenant is set, proceed
        # No tenant selected — check memberships
        memberships = UserTenant.query.filter_by(user_id=user.id).all()
        if len(memberships) == 1:
            # Auto-select the only available tenant
            t = db.session.get(Tenant, memberships[0].tenant_id)
            if t and t.is_active:
                session["active_tenant_id"] = t.id
                g.current_tenant = t
                g._tenant_id = t.id
                return None
        # Default tenant for single membership
        default_m = next((m for m in memberships if m.is_default), None)
        if default_m:
            t = db.session.get(Tenant, default_m.tenant_id)
            if t and t.is_active:
                session["active_tenant_id"] = t.id
                g.current_tenant = t
                g._tenant_id = t.id
                return None
        # Multiple or zero tenants — redirect to selection
        return redirect(url_for("tenant.select_tenant"))

    @app.before_request
    def check_password_change():
        """Force users with ``must_change_password`` or expired passwords."""
        if not request.endpoint or request.endpoint in (
            "auth.login",
            "auth.logout",
            "auth.change_password",
            "static",
        ):
            return None
        user = getattr(g, "current_user", None)
        if not user:
            return None
        if user.must_change_password:
            return redirect(url_for("auth.change_password"))
        # Check password expiry
        if user.password_changed_at:
            try:
                tenant = getattr(g, "current_tenant", None)
                tid = tenant.id if tenant else None
                exp_val = AppSetting.query.filter_by(
                    tenant_id=tid, key="password_expiry_value"
                ).first()
                exp_unit = AppSetting.query.filter_by(
                    tenant_id=tid, key="password_expiry_unit"
                ).first()
                if exp_val and exp_val.value and int(exp_val.value) > 0:
                    unit = exp_unit.value if exp_unit else "days"
                    days = int(exp_val.value)
                    if unit == "weeks":
                        days *= 7
                    elif unit == "months":
                        days *= 30
                    from datetime import datetime, timezone
                    age = datetime.now(timezone.utc) - (
                        user.password_changed_at.replace(tzinfo=timezone.utc)
                        if user.password_changed_at.tzinfo is None
                        else user.password_changed_at
                    )
                    if age.days >= days:
                        user.must_change_password = True
                        db.session.commit()
                        return redirect(url_for("auth.change_password"))
            except (ValueError, TypeError):
                pass
        return None

    @app.before_request
    def check_subscription_status():
        """Enforce subscription billing status on requests."""
        if not request.endpoint or request.endpoint in _TENANT_EXEMPT:
            return None
        # Also exempt billing pages so users can manage their subscription
        if request.endpoint and request.endpoint.startswith("billing."):
            return None
        user = getattr(g, "current_user", None)
        tenant = getattr(g, "current_tenant", None)
        if not user or not tenant:
            return None
        # Super admins bypass billing checks
        if user.is_superadmin:
            return None
        from models import TenantSubscription
        sub = TenantSubscription.query.filter_by(tenant_id=tenant.id).first()
        if not sub:
            return None  # No subscription — allow access (will be set up later)
        if sub.status in ("trial", "active"):
            return None
        if sub.status == "past_due":
            from flask import flash
            flash("Vaša platba je po splatnosti. Skontrolujte predplatné.", "warning")
            return None
        if sub.status == "grace_period":
            return None  # Warning shown via context processor
        if sub.status in ("suspended", "cancelled"):
            # Read-only mode: block POST/PUT/DELETE
            if request.method in ("POST", "PUT", "DELETE", "PATCH"):
                from flask import flash
                flash("Účet je pozastavený. Obnovte predplatné.", "danger")
                return redirect(url_for("billing.status"))
        return None

    @app.context_processor
    def inject_globals():
        """Inject common variables into every template."""
        user = getattr(g, "current_user", None)
        tenant = getattr(g, "current_tenant", None)
        user_permissions = set()
        if user:
            # Check for tenant-specific role override
            if tenant:
                membership = UserTenant.query.filter_by(
                    user_id=user.id, tenant_id=tenant.id
                ).first()
                if membership and membership.role_override:
                    user_permissions = ROLE_PERMISSIONS.get(membership.role_override, set())
                else:
                    user_permissions = ROLE_PERMISSIONS.get(user.role, set())
            else:
                user_permissions = ROLE_PERMISSIONS.get(user.role, set())
            # Superadmins always get manage_all
            if user.is_superadmin:
                user_permissions = user_permissions | {"manage_all"}

        # Dynamic site name from DB settings
        site_name = app_cfg.name
        try:
            tid = tenant.id if tenant else None
            setting = AppSetting.query.filter_by(
                tenant_id=tid, key="site_name"
            ).first()
            if setting and setting.value:
                site_name = setting.value
        except Exception:
            pass

        # User's tenant list for the switcher (eager-load tenant names)
        user_tenants = []
        if user:
            from sqlalchemy.orm import joinedload
            user_tenants = (
                UserTenant.query
                .options(joinedload(UserTenant.tenant))
                .filter_by(user_id=user.id)
                .join(Tenant, UserTenant.tenant_id == Tenant.id)
                .filter(Tenant.is_active.is_(True))
                .all()
            )

        # Subscription warning
        subscription_warning = None
        if tenant:
            from models import TenantSubscription
            sub = TenantSubscription.query.filter_by(tenant_id=tenant.id).first()
            if sub:
                if sub.status == "past_due":
                    subscription_warning = "Vaša platba je po splatnosti."
                elif sub.status == "grace_period" and sub.grace_period_ends_at:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    ends = sub.grace_period_ends_at
                    if ends.tzinfo is None:
                        ends = ends.replace(tzinfo=timezone.utc)
                    days_left = max(0, (ends - now).days)
                    subscription_warning = f"Účet bude pozastavený o {days_left} dní."
                elif sub.status == "trial" and sub.trial_ends_at:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    ends = sub.trial_ends_at
                    if ends.tzinfo is None:
                        ends = ends.replace(tzinfo=timezone.utc)
                    days_left = max(0, (ends - now).days)
                    # Get warning threshold
                    warn_days = 7
                    try:
                        warn_setting = AppSetting.query.filter_by(
                            tenant_id=None, key="billing_warning_days_before_due"
                        ).first()
                        if warn_setting and warn_setting.value:
                            warn_days = int(warn_setting.value)
                    except (ValueError, TypeError):
                        pass
                    if days_left <= warn_days:
                        subscription_warning = f"Váš skúšobný čas končí o {days_left} dní."
                elif sub.status in ("suspended", "cancelled"):
                    subscription_warning = "Účet je pozastavený. Obnovte predplatné."

        return {
            "app_config": app_cfg,
            "site_name": site_name,
            "current_user": user,
            "user_permissions": user_permissions,
            "current_tenant": tenant,
            "user_tenants": user_tenants,
            "subscription_warning": subscription_warning,
        }

    # ------------------------------------------------------------------
    # Security headers
    # ------------------------------------------------------------------

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # X-XSS-Protection "0" is recommended; the filter is deprecated
        # and can introduce vulnerabilities in older browsers
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = (
            "strict-origin-when-cross-origin"
        )
        response.headers["Permissions-Policy"] = (
            "geolocation=(), camera=(), microphone=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        if os.environ.get("FLASK_ENV") == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------

    @app.errorhandler(404)
    def not_found(_error):
        return (
            render_template(
                "error.html", code=404, message="Stránka nebola nájdená."
            ),
            404,
        )

    @app.errorhandler(500)
    def server_error(_error):
        return (
            render_template(
                "error.html", code=500, message="Vnútorná chyba servera."
            ),
            500,
        )

    @app.errorhandler(429)
    def ratelimit_handler(_error):
        from flask import flash

        flash("Príliš veľa pokusov. Skúste to neskôr.", "danger")
        return render_template("login.html"), 429

    return app


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = create_app()
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", 5000))
    logger.info("Starting application on %s:%s (debug=%s)", host, port, debug_mode)
    app.run(host=host, port=port, debug=debug_mode)
