"""Authentication routes."""

import re

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db, limiter
from models import User
from services.auth import get_current_user, login_required
from utils import utc_now


def _validate_password(password: str) -> str | None:
    """Return error message if password is weak, else None."""
    if len(password) < 8:
        return "Heslo musí mať aspoň 8 znakov."
    if not re.search(r"[A-Z]", password):
        return "Heslo musí obsahovať aspoň jedno veľké písmeno."
    if not re.search(r"[a-z]", password):
        return "Heslo musí obsahovať aspoň jedno malé písmeno."
    if not re.search(r"\d", password):
        return "Heslo musí obsahovať aspoň jednu číslicu."
    return None

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if (
            user
            and user.is_active
            and check_password_hash(user.password_hash, password)
        ):
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            flash("Prihlásenie úspešné.", "success")
            if user.must_change_password:
                return redirect(url_for("auth.change_password"))
            return redirect(url_for("dashboard.index"))
        flash("Nesprávne prihlasovacie údaje.", "danger")
    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per minute")
def change_password():
    user = get_current_user()
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        if not check_password_hash(user.password_hash, current_pw):
            flash("Aktuálne heslo je nesprávne.", "danger")
        elif (pw_error := _validate_password(new_pw)):
            flash(pw_error, "danger")
        elif new_pw != confirm_pw:
            flash("Heslá sa nezhodujú.", "danger")
        else:
            user.password_hash = generate_password_hash(new_pw)
            user.must_change_password = False
            user.password_changed_at = utc_now()
            db.session.commit()
            flash("Heslo úspešne zmenené.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template(
        "change_password.html", must_change=user.must_change_password
    )
