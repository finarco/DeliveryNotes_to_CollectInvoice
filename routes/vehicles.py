"""Vehicle management routes."""

import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import db
from models import Vehicle, VehicleSchedule
from services.audit import log_action
from services.auth import role_required
from utils import parse_time, safe_int

vehicles_bp = Blueprint("vehicles", __name__)


@vehicles_bp.route("/vehicles", methods=["GET", "POST"])
@role_required("manage_delivery")
def list_vehicles():
    if request.method == "POST":
        vehicle = Vehicle(
            name=request.form.get("name", "").strip(),
            notes=request.form.get("notes", ""),
            active=request.form.get("active") == "on",
        )
        db.session.add(vehicle)
        db.session.flush()
        log_action("create", "vehicle", vehicle.id, "vehicle created")
        db.session.commit()
        flash("Vozidlo uložené.", "success")
        return redirect(url_for("vehicles.list_vehicles"))
    return render_template("vehicles.html", vehicles=Vehicle.query.all())


@vehicles_bp.route(
    "/vehicles/<int:vehicle_id>/schedules", methods=["POST"]
)
@role_required("manage_delivery")
def add_vehicle_schedule(vehicle_id: int):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    start_time = (
        parse_time(request.form.get("start_time")) or datetime.time(8, 0)
    )
    end_time = (
        parse_time(request.form.get("end_time")) or datetime.time(16, 0)
    )
    if start_time >= end_time:
        flash("Začiatok musí byť pred koncom.", "danger")
        return redirect(url_for("vehicles.list_vehicles"))

    day_of_week = safe_int(request.form.get("day_of_week"))
    schedule = VehicleSchedule(
        vehicle_id=vehicle.id,
        day_of_week=day_of_week,
        start_time=start_time,
        end_time=end_time,
    )

    overlaps = VehicleSchedule.query.filter_by(
        vehicle_id=vehicle.id, day_of_week=day_of_week
    ).all()
    for existing in overlaps:
        if (
            schedule.start_time < existing.end_time
            and schedule.end_time > existing.start_time
        ):
            flash("Čas sa prekrýva s existujúcim harmonogramom.", "danger")
            return redirect(url_for("vehicles.list_vehicles"))

    db.session.add(schedule)
    log_action(
        "create",
        "vehicle_schedule",
        schedule.id,
        f"vehicle={vehicle.id}",
    )
    db.session.commit()
    flash("Operačný čas uložený.", "success")
    return redirect(url_for("vehicles.list_vehicles"))
