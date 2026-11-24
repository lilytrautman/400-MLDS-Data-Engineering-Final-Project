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


def _table_columns(table):
    cache = g.setdefault("_table_columns", {})
    if table not in cache:
        cache[table] = {r["name"] for r in query(f"PRAGMA table_info({table})")}
    return cache[table]


def has_image_columns():
    """Check whether image_url exists on users, albums and artists (keeps old DB snapshots working)."""
    return {t: "image_url" in _table_columns(t) for t in ("users", "albums", "artists")}


def _first_word(name):
    text = (name or "").strip()
    return text.split()[0] if text else ""


def get_users():
    """Every connected account, for user dropdowns. Never hardcode user ids."""
    cols = _table_columns("users")
    img_sql = "image_url" if "image_url" in cols else "NULL AS image_url"
    first_sql = "first_name" if "first_name" in cols else "NULL AS first_name"
    last_sql = "last_name" if "last_name" in cols else "NULL AS last_name"
    rows = query(
        f"SELECT user_id, COALESCE(display_name, user_id) AS display_name, country, "
        f"{img_sql}, {first_sql}, {last_sql} FROM users"
    )
    for r in rows:
        raw_name = r["display_name"]
        r["display_name"] = (
            (r["first_name"] or "").strip()
            or _first_word(raw_name)
            or r["user_id"]
        )
    rows.sort(key=lambda r: r["display_name"].lower())
    return rows
