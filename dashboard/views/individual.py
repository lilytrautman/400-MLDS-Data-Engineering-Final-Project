"""Individual tab: one user's listening trends (Alex).

Everything is computed for the user picked in the dropdown and handed to the
template as one JSON blob, which individual.html turns into Plotly charts.
"""

from collections import Counter, defaultdict
from datetime import datetime

from flask import Blueprint, render_template, request

from db import LOCAL_TZ, get_users, query

bp = Blueprint("individual", __name__, url_prefix="/individual")

TIME_RANGES = ["short_term", "medium_term", "long_term"]

# Only plot a quarter's average mood when enough liked songs have audio features
MIN_SONGS_PER_QUARTER = 3


def to_local(ts):
    """'2026-11-20T18:19:16.410Z' (UTC) -> datetime in Chicago time."""
    return datetime.fromisoformat(ts).astimezone(LOCAL_TZ)


def summary(user_id):
    liked = query("SELECT COUNT(*) AS n FROM saved_tracks WHERE user_id = ?", (user_id,))[0]["n"]
    plays = query(
        "SELECT COUNT(*) AS n, MIN(played_at) AS first, MAX(played_at) AS last "
        "FROM plays WHERE user_id = ?",
        (user_id,),
    )[0]
    since = None
    if plays["first"]:
        d = to_local(plays["first"])
        since = f"{d:%b} {d.day}, {d.year}"
    return {"liked": liked, "plays": plays["n"], "plays_since": since}


def mood_over_time(user_id):
    """Average valence (happy vs. sad) and energy of liked songs, by quarter liked."""
    rows = query(
        """
        SELECT s.added_at, af.valence, af.energy
        FROM saved_tracks s
        LEFT JOIN audio_features af ON af.track_id = s.track_id
        WHERE s.user_id = ?
        """,
        (user_id,),
    )
    groups = defaultdict(lambda: {"liked": 0, "valence": [], "energy": []})
    for r in rows:
        d = to_local(r["added_at"])
        g = groups[(d.year, (d.month - 1) // 3 + 1)]
        g["liked"] += 1
        # Count a song only if it has both scores, so the two averages use the same songs
        if r["valence"] is not None and r["energy"] is not None:
            g["valence"].append(r["valence"])
            g["energy"].append(r["energy"])

    if not groups:
        return []
    # Every quarter from the first like to the last, so quiet quarters show as gaps
    first, last = min(groups), max(groups)
    quarters = []
    year, qtr = first
    while (year, qtr) <= last:
        quarters.append((year, qtr))
        year, qtr = (year + 1, 1) if qtr == 4 else (year, qtr + 1)

    out = []
    for year, qtr in quarters:
        g = groups.get((year, qtr), {"liked": 0, "valence": [], "energy": []})
        n = len(g["valence"])
        out.append({
            "quarter": f"{year} Q{qtr}",
            "liked": g["liked"],
            "with_features": n,
            "valence": round(sum(g["valence"]) / n, 3) if n >= MIN_SONGS_PER_QUARTER else None,
            "energy": round(sum(g["energy"]) / n, 3) if n >= MIN_SONGS_PER_QUARTER else None,
        })
    return out


def release_years(user_id):
    """Liked songs per album release year, with empty years filled in as 0."""
    rows = query(
        """
        SELECT a.release_year AS year, COUNT(*) AS songs
        FROM saved_tracks s
        JOIN tracks t ON t.track_id = s.track_id
        LEFT JOIN albums a ON a.album_id = t.album_id
        WHERE s.user_id = ? AND a.release_year IS NOT NULL
        GROUP BY a.release_year
        """,
        (user_id,),
    )
    if not rows:
        return []
    counts = {r["year"]: r["songs"] for r in rows}
    return [{"year": y, "songs": counts.get(y, 0)} for y in range(min(counts), max(counts) + 1)]


def listening_hours(user_id):
    """Plays per hour of the day, in Chicago time."""
    rows = query("SELECT played_at FROM plays WHERE user_id = ?", (user_id,))
    hours = Counter(to_local(r["played_at"]).hour for r in rows)
    days = len({to_local(r["played_at"]).date() for r in rows})
    return {"days": days, "hours": [{"hour": h, "plays": hours.get(h, 0)} for h in range(24)]}


# The id column and the "names" query for each top list. Each query returns the
# list's rows for one snapshot, with the item's id as item_id.
TOP_LISTS = {
    "top_tracks": (
        "track_id",
        """
        SELECT tt.rank, tt.track_id AS item_id,
               COALESCE(t.name, '(removed from Spotify)') AS track,
               COALESCE(ar.name, '') AS artist,
               s.track_id IS NOT NULL AS liked
        FROM top_tracks tt
        JOIN tracks t ON t.track_id = tt.track_id
        LEFT JOIN track_artists ta ON ta.track_id = tt.track_id AND ta.artist_order = 0
        LEFT JOIN artists ar ON ar.artist_id = ta.artist_id
        LEFT JOIN saved_tracks s ON s.user_id = tt.user_id AND s.track_id = tt.track_id
        WHERE tt.user_id = :user AND tt.time_range = :range
          AND tt.snapshot_date = :date AND tt.rank <= :limit
        ORDER BY tt.rank
        """,
    ),
    "top_artists": (
        "artist_id",
        """
        SELECT ta.rank, ta.artist_id AS item_id, a.name AS artist
        FROM top_artists ta
        JOIN artists a ON a.artist_id = ta.artist_id
        WHERE ta.user_id = :user AND ta.time_range = :range
          AND ta.snapshot_date = :date AND ta.rank <= :limit
        ORDER BY ta.rank
        """,
    ),
}


def _top_list(table, user_id, time_range, limit):
    """One ranked list from the latest daily snapshot, with each item's move since
    the snapshot before it: {"kind": "move", "by": 3} is up three places."""
    id_col, list_sql = TOP_LISTS[table]
    dates = [
        r["snapshot_date"]
        for r in query(
            f"SELECT DISTINCT snapshot_date FROM {table} "
            "WHERE user_id = ? AND time_range = ? ORDER BY snapshot_date DESC LIMIT 2",
            (user_id, time_range),
        )
    ]
    if not dates:
        return []

    # Yesterday's ranks by item, or None when there's only one snapshot so far
    prev = None
    if len(dates) == 2:
        prev = {
            r["id"]: r["rank"]
            for r in query(
                f"SELECT {id_col} AS id, rank FROM {table} "
                "WHERE user_id = ? AND time_range = ? AND snapshot_date = ?",
                (user_id, time_range, dates[1]),
            )
        }

    rows = query(list_sql, {"user": user_id, "range": time_range, "date": dates[0], "limit": limit})
    for r in rows:
        item = r.pop("item_id")
        if prev is None:
            r["change"] = {"kind": "none"}
        elif item not in prev:
            r["change"] = {"kind": "new"}
        else:
            r["change"] = {"kind": "move", "by": prev[item] - r["rank"]}
        if "liked" in r:
            r["liked"] = bool(r["liked"])
    return rows


def top_lists(user_id, limit=10):
    """Top tracks and artists for each time range (4 weeks, 6 months, 1 year)."""
    return {
        tr: {
            "tracks": _top_list("top_tracks", user_id, tr, limit),
            "artists": _top_list("top_artists", user_id, tr, limit),
        }
        for tr in TIME_RANGES
    }


def liked_vs_played(user_id):
    """How much of what the user actually plays is in their liked songs."""
    plays = query(
        """
        SELECT COUNT(*) AS plays,
               COALESCE(SUM(s.track_id IS NOT NULL), 0) AS liked_plays
        FROM plays p
        LEFT JOIN saved_tracks s ON s.user_id = p.user_id AND s.track_id = p.track_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    )[0]
    top = query(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(s.track_id IS NOT NULL), 0) AS liked
        FROM top_tracks tt
        LEFT JOIN saved_tracks s ON s.user_id = tt.user_id AND s.track_id = tt.track_id
        WHERE tt.user_id = ? AND tt.time_range = 'short_term'
          AND tt.snapshot_date = (SELECT MAX(snapshot_date) FROM top_tracks
                                  WHERE user_id = ? AND time_range = 'short_term')
        """,
        (user_id, user_id),
    )[0]
    not_liked = query(
        """
        SELECT t.name AS track, ar.name AS artist, COUNT(*) AS plays
        FROM plays p
        JOIN tracks t ON t.track_id = p.track_id
        LEFT JOIN track_artists ta ON ta.track_id = p.track_id AND ta.artist_order = 0
        LEFT JOIN artists ar ON ar.artist_id = ta.artist_id
        LEFT JOIN saved_tracks s ON s.user_id = p.user_id AND s.track_id = p.track_id
        WHERE p.user_id = ? AND s.track_id IS NULL
        GROUP BY p.track_id
        HAVING COUNT(*) >= 2
        ORDER BY plays DESC, t.name
        LIMIT 5
        """,
        (user_id,),
    )
    return {
        "plays": plays["plays"],
        "liked_plays": plays["liked_plays"],
        "top_short": top["n"],
        "top_short_liked": top["liked"],
        "replayed_not_liked": not_liked,
    }


@bp.route("/")
def index():
    users = get_users()
    user_ids = [u["user_id"] for u in users]
    user_id = request.args.get("user")
    if user_id not in user_ids:
        user_id = user_ids[0] if user_ids else None

    data = None
    if user_id:
        data = {
            "summary": summary(user_id),
            "mood": mood_over_time(user_id),
            "years": release_years(user_id),
            "hours": listening_hours(user_id),
            "top": top_lists(user_id),
            "liked_vs_played": liked_vs_played(user_id),
        }
    return render_template("individual.html", users=users, user_id=user_id, data=data)
