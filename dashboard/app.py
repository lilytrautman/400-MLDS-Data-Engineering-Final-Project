"""Spotify listening dashboard.

Run from the repo root:
    python dashboard/app.py
then open http://127.0.0.1:5000.

Each tab is its own Blueprint in views/, so the views can be built on separate
branches without touching each other's files.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Flask, redirect, url_for

from db import LOCAL_TZ, close_db, query
from views.compare import bp as compare_bp
from views.individual import bp as individual_bp

# The Airflow DAG (DAG/spotify_daily.py) runs at these hours, Chicago time
RUN_HOURS = [9, 21]


def short_time(d):
    """e.g. Nov 23, 12:53 PM"""
    return f"{d:%b} {d.day}, {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def pipeline_status():
    """What the status strip under the top bar shows: when the pipeline last ran,
    when it runs next, how much is loaded and how many tracks each source covers."""
    try:
        run = query("SELECT run_id FROM stg_users LIMIT 1")
        counts = query(
            "SELECT (SELECT COUNT(*) FROM users) AS listeners, (SELECT COUNT(*) FROM plays) AS plays, "
            "(SELECT COUNT(*) FROM tracks) AS tracks, (SELECT COUNT(*) FROM audio_features) AS features, "
            "(SELECT COUNT(*) FROM deezer_tracks) AS deezer"
        )[0]
    except (FileNotFoundError, sqlite3.Error):
        return None

    last = None
    if run:
        # run ids are UTC start times, e.g. 2026-11-23T1853Z
        started = datetime.strptime(run[0]["run_id"], "%Y-%m-%dT%H%MZ").replace(tzinfo=timezone.utc)
        last = short_time(started.astimezone(LOCAL_TZ))

    now = datetime.now(LOCAL_TZ)
    upcoming = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in RUN_HOURS]
    upcoming = [t for t in upcoming if t > now] or [upcoming[0] + timedelta(days=1)]
    nxt = upcoming[0]
    return {**counts, "last": last, "next": f"{nxt.hour % 12 or 12} {'AM' if nxt.hour < 12 else 'PM'}"}


def create_app():
    app = Flask(__name__)
    app.teardown_appcontext(close_db)
    app.register_blueprint(individual_bp)
    app.register_blueprint(compare_bp)
    app.context_processor(lambda: {"status": pipeline_status()})

    @app.route("/")
    def home():
        return redirect(url_for("individual.index"))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
