"""Dashboard route."""

from datetime import datetime

from flask import Blueprint, render_template

from models import DeliveryNote, Invoice, Order, Partner
from services.auth import login_required

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    # Get recent activity (last 5 orders)
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()
    recent_activity = []

    for order in recent_orders:
        # Determine status
        status = "ČAKÁ"
        status_class = "pending"
        if order.status == "completed":
            status = "DOKONČENÉ"
            status_class = "success"
        elif order.status == "processing":
            status = "SPRACOVÁVA SA"
            status_class = "info"

        recent_activity.append({
            "date": order.created_at,
            "partner_name": order.partner.name if order.partner else "-",
            "type": "Objednávka",
            "amount": order.total_price if hasattr(order, "total_price") else 0.0,
            "status": status,
            "status_class": status_class,
        })

    # Get recent changes for activity feed
    recent_changes = []

    # Add recent invoices
    recent_invoices = Invoice.query.order_by(Invoice.created_at.desc()).limit(3).all()
    for invoice in recent_invoices:
        status = "ZAPLATENÉ" if invoice.paid else "NEUHRADENÉ"
        badge_class = "success" if invoice.paid else "warning"

        recent_changes.append({
            "title": f"Faktúra #{invoice.invoice_number}",
            "description": f"{invoice.partner.name if invoice.partner else 'N/A'}",
            "time": _format_time_ago(invoice.created_at),
            "status": status,
            "badge_class": badge_class,
            "type": "success" if invoice.paid else "warning",
        })

    # Add recent delivery notes
    recent_deliveries = DeliveryNote.query.order_by(
        DeliveryNote.created_at.desc()
    ).limit(2).all()
    for delivery in recent_deliveries:
        recent_changes.append({
            "title": f"Dodací list #{delivery.delivery_number}",
            "description": f"{delivery.partner.name if delivery.partner else 'N/A'}",
            "time": _format_time_ago(delivery.created_at),
            "status": "VYTVORENÉ",
            "badge_class": "info",
            "type": "info",
        })

    return render_template(
        "index.html",
        partner_count=Partner.query.count(),
        order_count=Order.query.count(),
        delivery_count=DeliveryNote.query.count(),
        invoice_count=Invoice.query.count(),
        recent_activity=recent_activity if recent_activity else None,
        recent_changes=recent_changes if recent_changes else None,
    )


def _format_time_ago(dt):
    """Format datetime as relative time string."""
    if not dt:
        return "Neznámy čas"

    now = datetime.now()
    diff = now - dt

    if diff.days > 7:
        return dt.strftime("%d.%m.%Y")
    elif diff.days > 0:
        return f"Pred {diff.days} dňami"
    elif diff.seconds > 3600:
        hours = diff.seconds // 3600
        return f"Pred {hours} hodinami"
    elif diff.seconds > 60:
        minutes = diff.seconds // 60
        return f"Pred {minutes} minútami"
    else:
        return "Práve teraz"
