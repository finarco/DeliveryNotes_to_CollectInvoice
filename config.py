"""Configuration loading — YAML file + environment-variable overrides."""

from __future__ import annotations

import logging
import os
import secrets

import yaml

from config_models import AppConfig, EmailConfig, GopayConfig, SuperfakturaConfig

logger = logging.getLogger(__name__)


def load_config():
    """Load configuration from *config.yaml* with env-var overrides.

    Environment variables take precedence over config.yaml values.
    Returns (AppConfig, EmailConfig, SuperfakturaConfig, GopayConfig, database_uri).
    """
    config_path = os.environ.get("CONFIG_PATH", "config.yaml")
    raw: dict = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}

    app_cfg = raw.get("app", {})
    email_cfg = raw.get("email", {})
    sf_cfg = raw.get("superfaktura", {})
    gopay_cfg = raw.get("gopay", {})
    db_cfg = raw.get("database", {})

    secret_key = os.environ.get("APP_SECRET_KEY", app_cfg.get("secret_key", ""))
    if not secret_key or secret_key == "change-me":
        secret_key = secrets.token_hex(32)
        logger.warning(
            "Using auto-generated secret key. Set APP_SECRET_KEY env var "
            "or app.secret_key in config.yaml for stable sessions across restarts."
        )

    return (
        AppConfig(
            name=app_cfg.get("name", "ObDoFa"),
            secret_key=secret_key,
            base_currency=app_cfg.get("base_currency", "EUR"),
            show_prices_default=app_cfg.get("show_prices_default", True),
        ),
        EmailConfig(
            enabled=os.environ.get(
                "EMAIL_ENABLED", str(email_cfg.get("enabled", False))
            ).lower()
            in ("true", "1", "yes"),
            smtp_host=os.environ.get("SMTP_HOST", email_cfg.get("smtp_host", "")),
            smtp_port=int(os.environ.get("SMTP_PORT", email_cfg.get("smtp_port", 587))),
            smtp_user=os.environ.get("SMTP_USER", email_cfg.get("smtp_user", "")),
            smtp_password=os.environ.get("SMTP_PASSWORD", email_cfg.get("smtp_password", "")),
            sender=os.environ.get("EMAIL_SENDER", email_cfg.get("sender", "")),
            operator_cc=os.environ.get("EMAIL_OPERATOR_CC", email_cfg.get("operator_cc", "")),
        ),
        SuperfakturaConfig(
            enabled=os.environ.get(
                "SUPERFAKTURA_ENABLED", str(sf_cfg.get("enabled", False))
            ).lower()
            in ("true", "1", "yes"),
            api_email=os.environ.get("SUPERFAKTURA_API_EMAIL", sf_cfg.get("api_email", "")),
            api_key=os.environ.get("SUPERFAKTURA_API_KEY", sf_cfg.get("api_key", "")),
            company_id=os.environ.get(
                "SUPERFAKTURA_COMPANY_ID", str(sf_cfg.get("company_id", ""))
            ),
            base_url=os.environ.get(
                "SUPERFAKTURA_BASE_URL",
                sf_cfg.get("base_url", "https://api.superfaktura.sk"),
            ),
        ),
        GopayConfig(
            enabled=os.environ.get(
                "GOPAY_ENABLED", str(gopay_cfg.get("enabled", False))
            ).lower()
            in ("true", "1", "yes"),
            goid=os.environ.get("GOPAY_GOID", gopay_cfg.get("goid", "")),
            client_id=os.environ.get("GOPAY_CLIENT_ID", gopay_cfg.get("client_id", "")),
            client_secret=os.environ.get("GOPAY_CLIENT_SECRET", gopay_cfg.get("client_secret", "")),
            gateway_url=os.environ.get(
                "GOPAY_GATEWAY_URL",
                gopay_cfg.get("gateway_url", "https://gw.sandbox.gopay.com/api"),
            ),
        ),
        os.environ.get("DATABASE_URI", db_cfg.get("uri", "sqlite:///delivery_notes.db")),
    )


def enable_sqlite_fks(dbapi_conn, _connection_record):
    """Enable foreign key enforcement for SQLite connections."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
