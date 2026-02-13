"""Blueprint registration."""

from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.billing import billing_bp
from routes.dashboard import dashboard_bp
from routes.db_tools import db_tools_bp
from routes.delivery import delivery_bp
from routes.invoices import invoices_bp
from routes.logistics import logistics_bp
from routes.orders import orders_bp
from routes.partners import partners_bp
from routes.products import products_bp
from routes.tenant import tenant_bp
from routes.vehicles import vehicles_bp

ALL_BLUEPRINTS = [
    auth_bp,
    tenant_bp,
    billing_bp,
    dashboard_bp,
    partners_bp,
    products_bp,
    orders_bp,
    delivery_bp,
    invoices_bp,
    vehicles_bp,
    logistics_bp,
    admin_bp,
    db_tools_bp,
]


def register_blueprints(app):
    """Register all application blueprints on *app*."""
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)
