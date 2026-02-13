"""Vehicle management routes."""

import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import db
from models import LogisticsPlan, Vehicle, VehicleSchedule
from services.audit import log_action
from services.auth import role_required
from utils import parse_time, safe_int
from services.tenant import tenant_query, stamp_tenant, tenant_get_or_404

vehicles_bp = Blueprint("vehicles", __name__)


@vehicles_bp.route("/vehicles", methods=["GET", "POST"])
@role_required("manage_delivery")
def list_vehicles():
    if request.method == "POST":
        vehicle = Vehicle(
            name=request.form.get("name", "").strip(),
            registration_number=request.form.get("registration_number", "").strip() or None,
            notes=request.form.get("notes", ""),
            active=request.form.get("active") == "on",
        )
        stamp_tenant(vehicle)
        db.session.add(vehicle)
        db.session.flush()
        log_action("create", "vehicle", vehicle.id, "vehicle created")
        db.session.commit()
        flash("Vozidlo uložené.", "success")
        return redirect(url_for("vehicles.list_vehicles"))
    return render_template("vehicles.html", vehicles=tenant_query(Vehicle).all())


@vehicles_bp.route("/vehicles/<int:vehicle_id>/toggle", methods=["POST"])
@role_required("manage_delivery")
def toggle_vehicle(vehicle_id: int):
    vehicle = tenant_get_or_404(Vehicle, vehicle_id)
    vehicle.active = not vehicle.active
    action = "activate" if vehicle.active else "deactivate"
    log_action(action, "vehicle", vehicle.id, f"active={vehicle.active}")
    db.session.commit()
    status = "aktivované" if vehicle.active else "deaktivované"
    flash(f"Vozidlo '{vehicle.name}' {status}.", "success")
    return redirect(url_for("vehicles.list_vehicles"))


@vehicles_bp.route("/vehicles/<int:vehicle_id>/edit", methods=["POST"])
@role_required("manage_delivery")
def edit_vehicle(vehicle_id: int):
    vehicle = tenant_get_or_404(Vehicle, vehicle_id)
    vehicle.name = request.form.get("name", "").strip() or vehicle.name
    vehicle.registration_number = request.form.get("registration_number", "").strip() or None
    vehicle.notes = request.form.get("notes", "")
    vehicle.active = request.form.get("active") == "on"
    log_action("edit", "vehicle", vehicle.id, "updated")
    db.session.commit()
    flash(f"Vozidlo '{vehicle.name}' upravené.", "success")
    return redirect(url_for("vehicles.list_vehicles"))


@vehicles_bp.route("/vehicles/<int:vehicle_id>/delete", methods=["POST"])
@role_required("manage_delivery")
def delete_vehicle(vehicle_id: int):
    vehicle = tenant_get_or_404(Vehicle, vehicle_id)
    if tenant_query(LogisticsPlan).filter_by(vehicle_id=vehicle.id).first():
        flash(
            f"Vozidlo '{vehicle.name}' nie je možné vymazať — je priradené k logistickému plánu. "
            f"Použite deaktiváciu.",
            "danger",
        )
        return redirect(url_for("vehicles.list_vehicles"))
    name = vehicle.name
    log_action("delete", "vehicle", vehicle.id, f"deleted: {name}")
    db.session.delete(vehicle)
    db.session.commit()
    flash(f"Vozidlo '{name}' vymazané.", "warning")
    return redirect(url_for("vehicles.list_vehicles"))


@vehicles_bp.route(
    "/vehicles/<int:vehicle_id>/schedules", methods=["POST"]
)
@role_required("manage_delivery")
def add_vehicle_schedule(vehicle_id: int):
    vehicle = tenant_get_or_404(Vehicle, vehicle_id)
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

    overlaps = tenant_query(VehicleSchedule).filter_by(
        vehicle_id=vehicle.id, day_of_week=day_of_week
    ).all()
    for existing in overlaps:
        if (
            schedule.start_time < existing.end_time
            and schedule.end_time > existing.start_time
        ):
            flash("Čas sa prekrýva s existujúcim harmonogramom.", "danger")
            return redirect(url_for("vehicles.list_vehicles"))

    stamp_tenant(schedule)
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
