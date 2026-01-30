"""Logistics planning routes."""

from datetime import timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import db
from models import DeliveryNote, LogisticsPlan, Order, Vehicle
from services.audit import log_action
from services.auth import role_required
from utils import parse_datetime, safe_int, utc_now

logistics_bp = Blueprint("logistics", __name__)


@logistics_bp.route("/logistics", methods=["GET", "POST"])
@role_required("manage_delivery")
def dashboard():
    interval = request.args.get("interval", "weekly")
    now = utc_now()

    if interval == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif interval == "monthly":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(
            day=1
        )
        end = next_month
    else:
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(days=7)

    plans_query = (
        LogisticsPlan.query.filter(
            LogisticsPlan.planned_datetime >= start,
            LogisticsPlan.planned_datetime < end,
        )
        .order_by(LogisticsPlan.planned_datetime.desc())
    )

    page = max(1, safe_int(request.args.get("page"), default=1))
    per_page = 20
    total = plans_query.count()
    plans = (
        plans_query.offset((page - 1) * per_page).limit(per_page).all()
    )

    all_orders = Order.query.order_by(Order.created_at.desc()).all()
    all_delivery_notes = DeliveryNote.query.order_by(
        DeliveryNote.created_at.desc()
    ).all()
    vehicles = Vehicle.query.filter_by(active=True).all()

    if request.method == "POST":
        plan = LogisticsPlan(
            order_id=safe_int(request.form.get("order_id")) or None,
            delivery_note_id=safe_int(request.form.get("delivery_note_id"))
            or None,
            plan_type=request.form.get("plan_type", "pickup"),
            planned_datetime=parse_datetime(
                request.form.get("planned_datetime")
            )
            or utc_now(),
            vehicle_id=safe_int(request.form.get("vehicle_id")) or None,
        )
        db.session.add(plan)
        log_action(
            "create", "logistics_plan", plan.id, plan.plan_type
        )
        db.session.commit()
        flash("Plán zvozu/dodania uložený.", "success")
        return redirect(
            url_for("logistics.dashboard", interval=interval)
        )

    return render_template(
        "logistics.html",
        plans=plans,
        interval=interval,
        total=total,
        page=page,
        per_page=per_page,
        orders=all_orders,
        delivery_notes=all_delivery_notes,
        vehicles=vehicles,
    )


@logistics_bp.route("/logistics/<int:plan_id>/edit", methods=["POST"])
@role_required("manage_delivery")
def edit_plan(plan_id: int):
    plan = db.get_or_404(LogisticsPlan, plan_id)
    plan.plan_type = request.form.get("plan_type", plan.plan_type)
    plan.order_id = safe_int(request.form.get("order_id")) or None
    plan.delivery_note_id = safe_int(request.form.get("delivery_note_id")) or None
    plan.vehicle_id = safe_int(request.form.get("vehicle_id")) or None
    plan.planned_datetime = parse_datetime(
        request.form.get("planned_datetime")
    ) or plan.planned_datetime
    log_action("edit", "logistics_plan", plan.id, "updated")
    db.session.commit()
    flash("Plán upravený.", "success")
    return redirect(url_for("logistics.dashboard"))


@logistics_bp.route("/logistics/<int:plan_id>/delete", methods=["POST"])
@role_required("manage_delivery")
def delete_plan(plan_id: int):
    plan = db.get_or_404(LogisticsPlan, plan_id)
    log_action("delete", "logistics_plan", plan.id, f"deleted plan #{plan.id}")
    db.session.delete(plan)
    db.session.commit()
    flash("Plán vymazaný.", "warning")
    return redirect(url_for("logistics.dashboard"))
