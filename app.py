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
from models import ROLE_PERMISSIONS, AppSetting, User
from routes import register_blueprints
from services.auth import ensure_admin_user

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
        ],
        "partner": [
            ("is_active", "BOOLEAN DEFAULT 1"),
            ("is_deleted", "BOOLEAN DEFAULT 0"),
            ("created_at", "DATETIME"),
            ("updated_at", "DATETIME"),
        ],
        "product": [
            ("product_number", "VARCHAR(60)"),
            ("vat_rate", "REAL DEFAULT 20.0"),
            ("is_active", "BOOLEAN DEFAULT 1"),
            ("created_at", "DATETIME"),
            ("updated_at", "DATETIME"),
        ],
        "bundle": [
            ("bundle_number", "VARCHAR(60)"),
            ("discount_excluded", "BOOLEAN DEFAULT 0"),
            ("is_active", "BOOLEAN DEFAULT 1"),
            ("created_at", "DATETIME"),
            ("updated_at", "DATETIME"),
        ],
        "order": [
            ("order_number", "VARCHAR(60)"),
            ("pickup_address_id", "INTEGER REFERENCES partner_address(id)"),
            ("delivery_address_id", "INTEGER REFERENCES partner_address(id)"),
            ("is_locked", "BOOLEAN DEFAULT 0"),
            ("updated_at", "DATETIME"),
        ],
        "delivery_note": [
            ("note_number", "VARCHAR(60)"),
            ("updated_at", "DATETIME"),
            ("actual_delivery_datetime", "DATETIME"),
            ("is_locked", "BOOLEAN DEFAULT 0"),
        ],
        "vehicle": [
            ("registration_number", "VARCHAR(20)"),
        ],
        "invoice": [
            ("invoice_number", "VARCHAR(30)"),
            ("updated_at", "DATETIME"),
            ("total_with_vat", "REAL DEFAULT 0.0"),
            ("is_locked", "BOOLEAN DEFAULT 0"),
        ],
        "invoice_item": [
            ("source_delivery_id", "INTEGER REFERENCES delivery_note(id)"),
            ("vat_rate", "REAL DEFAULT 20.0"),
            ("vat_amount", "REAL DEFAULT 0.0"),
            ("total_with_vat", "REAL DEFAULT 0.0"),
            ("is_manual", "BOOLEAN DEFAULT 0"),
        ],
        "numbering_config": [
            ("pattern", "VARCHAR(120) DEFAULT ''"),
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

    # Unique index that SQLite cannot add inline with ALTER TABLE
    try:
        db.session.execute(
            text('CREATE UNIQUE INDEX IF NOT EXISTS "uq_invoice_number" '
                 'ON "invoice" (invoice_number)')
        )
    except Exception:
        pass  # index already exists or table not yet created

    db.session.commit()


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

    # Migrate schema, create new tables & seed admin
    with app.app_context():
        _migrate_schema()
        db.create_all()
        ensure_admin_user()

    # Register all blueprints
    register_blueprints(app)

    # ------------------------------------------------------------------
    # Request hooks
    # ------------------------------------------------------------------

    @app.before_request
    def load_current_user():
        """Set ``g.current_user`` from the session for every request."""
        user_id = session.get("user_id")
        if user_id:
            user = db.session.get(User, user_id)
            if user and not user.is_active:
                session.clear()
                g.current_user = None
            else:
                g.current_user = user
        else:
            g.current_user = None

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
                exp_val = AppSetting.query.filter_by(
                    key="password_expiry_value"
                ).first()
                exp_unit = AppSetting.query.filter_by(
                    key="password_expiry_unit"
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

    @app.context_processor
    def inject_globals():
        """Inject common variables into every template."""
        user = getattr(g, "current_user", None)
        user_permissions = set()
        if user:
            user_permissions = ROLE_PERMISSIONS.get(user.role, set())
        # Dynamic site name from DB settings, fallback to config
        site_name = app_cfg.name
        try:
            setting = AppSetting.query.filter_by(key="site_name").first()
            if setting and setting.value:
                site_name = setting.value
        except Exception:
            pass
        return {
            "app_config": app_cfg,
            "site_name": site_name,
            "current_user": user,
            "user_permissions": user_permissions,
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
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
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
