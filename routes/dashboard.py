"""Dashboard route."""

from flask import Blueprint, render_template

from models import DeliveryNote, Invoice, Order, Partner
from services.auth import login_required

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        partner_count=Partner.query.count(),
        order_count=Order.query.count(),
        delivery_count=DeliveryNote.query.count(),
        invoice_count=Invoice.query.count(),
    )
