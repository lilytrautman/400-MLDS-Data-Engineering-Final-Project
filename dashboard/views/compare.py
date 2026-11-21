"""Compare tab: tastes and habits across all connected users (Gloria)."""

from flask import Blueprint, render_template

from db import get_users

bp = Blueprint("compare", __name__, url_prefix="/compare")


@bp.route("/")
def index():
    return render_template("compare.html", users=get_users())
