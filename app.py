"""Application factory — clean entry point for the Flask application."""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, g, redirect, render_template, request, session, url_for
from sqlalchemy import event

from config import load_config, enable_sqlite_fks
from extensions import csrf, db, limiter
from models import ROLE_PERMISSIONS, User
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
        os.environ.get("FLASK_ENV", "") == "production"
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

    # Create tables & seed admin
    with app.app_context():
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
            g.current_user = db.session.get(User, user_id)
        else:
            g.current_user = None

    @app.before_request
    def check_password_change():
        """Force users with ``must_change_password`` to change it."""
        if not request.endpoint or request.endpoint in (
            "auth.login",
            "auth.logout",
            "auth.change_password",
            "static",
        ):
            return None
        user = getattr(g, "current_user", None)
        if user and user.must_change_password:
            return redirect(url_for("auth.change_password"))
        return None

    @app.context_processor
    def inject_globals():
        """Inject common variables into every template."""
        user = getattr(g, "current_user", None)
        user_permissions = set()
        if user:
            user_permissions = ROLE_PERMISSIONS.get(user.role, set())
        return {
            "app_config": app_cfg,
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
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = (
            "strict-origin-when-cross-origin"
        )
        response.headers["Permissions-Policy"] = (
            "geolocation=(), camera=(), microphone=()"
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
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", 5000))
    logger.info("Starting application on %s:%s (debug=%s)", host, port, debug_mode)
    app.run(host=host, port=port, debug=debug_mode)
