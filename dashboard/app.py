"""Spotify listening dashboard.

Run from the repo root:
    python dashboard/app.py
then open http://127.0.0.1:5000.

Each tab is its own Blueprint in views/, so the views can be built on separate
branches without touching each other's files.
"""

from flask import Flask, redirect, url_for

from db import close_db
from views.compare import bp as compare_bp
from views.individual import bp as individual_bp


def create_app():
    app = Flask(__name__)
    app.teardown_appcontext(close_db)
    app.register_blueprint(individual_bp)
    app.register_blueprint(compare_bp)

    @app.route("/")
    def home():
        return redirect(url_for("individual.index"))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
