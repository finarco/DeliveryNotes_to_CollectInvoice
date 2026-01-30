"""Admin routes — user management (Feature F1)."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from extensions import db
from models import VALID_ROLES, User
from services.audit import log_action
from services.auth import role_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/users", methods=["GET", "POST"])
@role_required("manage_all")
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "operator")

        if not username:
            flash("Meno používateľa je povinné.", "danger")
            return redirect(url_for("admin.users"))
        if len(password) < 8:
            flash("Heslo musí mať aspoň 8 znakov.", "danger")
            return redirect(url_for("admin.users"))
        if role not in VALID_ROLES:
            flash("Neplatná rola.", "danger")
            return redirect(url_for("admin.users"))
        if User.query.filter_by(username=username).first():
            flash("Používateľ s týmto menom už existuje.", "danger")
            return redirect(url_for("admin.users"))

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            role=role,
            must_change_password=True,
        )
        db.session.add(user)
        db.session.flush()
        log_action("create", "user", user.id, f"role={role}")
        db.session.commit()
        flash(f"Používateľ '{username}' vytvorený.", "success")
        return redirect(url_for("admin.users"))

    all_users = User.query.order_by(User.id).all()
    return render_template(
        "admin/users.html", users=all_users, valid_roles=VALID_ROLES
    )


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@role_required("manage_all")
def toggle_user(user_id: int):
    user = db.get_or_404(User, user_id)
    user.is_active = not user.is_active
    action = "activate" if user.is_active else "deactivate"
    log_action(action, "user", user.id, f"is_active={user.is_active}")
    db.session.commit()
    status = "aktivovaný" if user.is_active else "deaktivovaný"
    flash(f"Používateľ '{user.username}' {status}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route(
    "/users/<int:user_id>/reset-password", methods=["POST"]
)
@role_required("manage_all")
def reset_password(user_id: int):
    user = db.get_or_404(User, user_id)
    new_password = request.form.get("new_password", "")
    if len(new_password) < 8:
        flash("Heslo musí mať aspoň 8 znakov.", "danger")
        return redirect(url_for("admin.users"))
    user.password_hash = generate_password_hash(new_password)
    user.must_change_password = True
    log_action("reset_password", "user", user.id, "password reset by admin")
    db.session.commit()
    flash(f"Heslo pre '{user.username}' bolo resetované.", "success")
    return redirect(url_for("admin.users"))
