"""Individual tab: one user's listening trends (Alex)."""

from flask import Blueprint, render_template, request

from db import get_users

bp = Blueprint("individual", __name__, url_prefix="/individual")


@bp.route("/")
def index():
    users = get_users()
    user_ids = [u["user_id"] for u in users]
    user_id = request.args.get("user")
    if user_id not in user_ids:
        user_id = user_ids[0] if user_ids else None
    return render_template("individual.html", users=users, user_id=user_id)
