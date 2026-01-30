"""Authentication routes."""

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
def change_password():
    user = get_current_user()
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        if not check_password_hash(user.password_hash, current_pw):
            flash("Aktuálne heslo je nesprávne.", "danger")
        elif len(new_pw) < 8:
            flash("Nové heslo musí mať aspoň 8 znakov.", "danger")
        elif new_pw != confirm_pw:
            flash("Heslá sa nezhodujú.", "danger")
        else:
            user.password_hash = generate_password_hash(new_pw)
            user.must_change_password = False
            db.session.commit()
            flash("Heslo úspešne zmenené.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template(
        "change_password.html", must_change=user.must_change_password
    )
