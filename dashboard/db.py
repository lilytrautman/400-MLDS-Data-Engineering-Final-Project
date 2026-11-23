"""Read-only access to data/spotify.db for the dashboard.

Every request gets its own connection, opened read-only so the dashboard can never
change the pipeline's data, and closed when the request ends. Rows come back as
plain dicts so they can go straight into templates or JSON.
"""

import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import g

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "spotify.db"

# The database stores UTC; charts by hour or weekday should use Chicago time.
LOCAL_TZ = ZoneInfo("America/Chicago")


def get_db():
    """Return this request's read-only connection, opening it on first use."""
    if "db" not in g:
        if not DB_PATH.exists():
            raise FileNotFoundError(
                f"No database at {DB_PATH}. Save spotify.db in the data/ folder at the repo root."
            )
        g.db = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, params=()):
    """Run a SELECT and return the rows as a list of dicts."""
    return [dict(row) for row in get_db().execute(sql, params).fetchall()]


def get_users():
    """Every connected account, for user dropdowns. Never hardcode user ids."""
    return query(
        "SELECT user_id, COALESCE(display_name, user_id) AS display_name, country "
        "FROM users ORDER BY display_name COLLATE NOCASE"
    )
