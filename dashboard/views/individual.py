"""Individual tab: one user's listening trends (Alex).

Everything is computed for the user picked in the dropdown and handed to the
template as one JSON blob, which individual.html turns into Plotly charts.
"""

from collections import Counter, defaultdict
from datetime import datetime

from flask import Blueprint, render_template, request

from db import LOCAL_TZ, get_users, has_image_columns, query

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


def release_years(user_id, top_n=5):
    """Liked songs per album release year, with empty years filled in as 0.

    Each year also lists its top_n most popular liked songs (by Deezer rank,
    higher = more mainstream) for the chart's hover tooltip.
    """
    album_img = "a.image_url" if has_image_columns()["albums"] else "NULL"
    rows = query(
        f"""
        SELECT a.release_year AS year, t.name AS track, ar.name AS artist,
               d.rank AS popularity, {album_img} AS image_url
        FROM saved_tracks s
        JOIN tracks t ON t.track_id = s.track_id
        LEFT JOIN albums a ON a.album_id = t.album_id
        LEFT JOIN track_artists ta ON ta.track_id = t.track_id AND ta.artist_order = 0
        LEFT JOIN artists ar ON ar.artist_id = ta.artist_id
        LEFT JOIN deezer_tracks d ON d.track_id = t.track_id
        WHERE s.user_id = ? AND a.release_year IS NOT NULL
        """,
        (user_id,),
    )
    if not rows:
        return []
    by_year = defaultdict(list)
    for r in rows:
        by_year[r["year"]].append(r)

    out = []
    for y in range(min(by_year), max(by_year) + 1):
        songs = by_year.get(y, [])
        # Most popular first; songs Deezer couldn't match go last
        songs.sort(key=lambda r: (r["popularity"] is None, -(r["popularity"] or 0), r["track"] or ""))
        out.append({
            "year": y,
            "songs": len(songs),
            "top": [
                {
                    "track": r["track"] or "(removed from Spotify)",
                    "artist": r["artist"] or "",
                    "image_url": r["image_url"],
                }
                for r in songs[:top_n]
            ],
        })
    return out


def listening_hours(user_id, top_n=5):
    """Plays per hour of the day, in Chicago time.

    Each hour also lists its top_n most-played songs in that hour, for the
    chart's hover tooltip.
    """
    album_img = "a.image_url" if has_image_columns()["albums"] else "NULL"
    rows = query(
        f"""
        SELECT p.played_at, p.track_id, t.name AS track, ar.name AS artist,
               {album_img} AS image_url
        FROM plays p
        JOIN tracks t ON t.track_id = p.track_id
        LEFT JOIN albums a ON a.album_id = t.album_id
        LEFT JOIN track_artists ta ON ta.track_id = p.track_id AND ta.artist_order = 0
        LEFT JOIN artists ar ON ar.artist_id = ta.artist_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    )
    hours = Counter()
    days = set()
    per_hour = defaultdict(Counter)  # hour -> {track_id: plays in that hour}
    names = {}
    for r in rows:
        local = to_local(r["played_at"])
        hours[local.hour] += 1
        days.add(local.date())
        per_hour[local.hour][r["track_id"]] += 1
        names[r["track_id"]] = (
            r["track"] or "(removed from Spotify)",
            r["artist"] or "",
            r["image_url"],
        )

    def top_songs(hour):
        # Most plays in this hour first, ties alphabetical
        ranked = sorted(per_hour[hour].items(), key=lambda kv: (-kv[1], names[kv[0]][0].lower()))
        return [
            {
                "track": names[tid][0],
                "artist": names[tid][1],
                "plays": n,
                "image_url": names[tid][2],
            }
            for tid, n in ranked[:top_n]
        ]

    return {
        "days": len(days),
        "hours": [{"hour": h, "plays": hours.get(h, 0), "top": top_songs(h)} for h in range(24)],
    }


# The id column and the "names" query for each top list. Each query returns the
# list's rows for one snapshot, with the item's id as item_id.
def _top_list_spec(table):
    img_cols = has_image_columns()
    if table == "top_tracks":
        album_img = "a.image_url" if img_cols["albums"] else "NULL"
        return (
            "track_id",
            f"""
            SELECT tt.rank, tt.track_id AS item_id,
                   COALESCE(t.name, '(removed from Spotify)') AS track,
                   COALESCE(ar.name, '') AS artist,
                   {album_img} AS image_url,
                   s.track_id IS NOT NULL AS liked
            FROM top_tracks tt
            JOIN tracks t ON t.track_id = tt.track_id
            LEFT JOIN albums a ON a.album_id = t.album_id
            LEFT JOIN track_artists ta ON ta.track_id = tt.track_id AND ta.artist_order = 0
            LEFT JOIN artists ar ON ar.artist_id = ta.artist_id
            LEFT JOIN saved_tracks s ON s.user_id = tt.user_id AND s.track_id = tt.track_id
            WHERE tt.user_id = :user AND tt.time_range = :range
              AND tt.snapshot_date = :date AND tt.rank <= :limit
            ORDER BY tt.rank
            """,
        )
    artist_img = "a.image_url" if img_cols["artists"] else "NULL"
    return (
        "artist_id",
        f"""
        SELECT ta.rank, ta.artist_id AS item_id, a.name AS artist,
               {artist_img} AS image_url
        FROM top_artists ta
        JOIN artists a ON a.artist_id = ta.artist_id
        WHERE ta.user_id = :user AND ta.time_range = :range
          AND ta.snapshot_date = :date AND ta.rank <= :limit
        ORDER BY ta.rank
        """,
    )


def _top_list(table, user_id, time_range, limit):
    """One ranked list from the latest daily snapshot, with each item's move since
    the snapshot before it: {"kind": "move", "by": 3} is up three places."""
    id_col, list_sql = _top_list_spec(table)
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
        item = r["item_id"]  # top_lists() uses it, then removes it
        if prev is None:
            r["change"] = {"kind": "none"}
        elif item not in prev:
            r["change"] = {"kind": "new"}
        else:
            r["change"] = {"kind": "move", "by": prev[item] - r["rank"]}
        if "liked" in r:
            r["liked"] = bool(r["liked"])
    return rows


def _tracks_by_artist(user_id, time_range, top_n=5):
    """For the Top artists hover list: each artist's top_n tracks this user listens to.

    First the artist's songs in the user's Spotify top tracks for this time range
    (latest snapshot, Spotify's order), then, if fewer than top_n, the artist's
    most-played recorded songs. A track counts for every artist credited on it.
    Returns {artist_id: [{"track", "rank", "plays"}, ...]}.
    """
    # Recorded plays per track, for the play counts and the fill-up
    plays = {
        r["track_id"]: r["plays"]
        for r in query(
            "SELECT track_id, COUNT(*) AS plays FROM plays WHERE user_id = ? GROUP BY track_id",
            (user_id,),
        )
    }
    album_img = "al.image_url" if has_image_columns()["albums"] else "NULL"
    out = defaultdict(list)
    seen = defaultdict(set)

    def add(artist_id, track_id, name, rank, image_url):
        # Skip repeats by name too: Spotify can have two versions of one song
        key = (name or track_id).lower()
        if len(out[artist_id]) < top_n and key not in seen[artist_id]:
            seen[artist_id].add(key)
            out[artist_id].append({
                "track": name or "(removed from Spotify)",
                "rank": rank,
                "plays": plays.get(track_id, 0),
                "image_url": image_url,
            })

    # 1. Spotify top tracks for this time range
    for r in query(
        f"""
        SELECT ta.artist_id, tt.track_id, t.name, tt.rank, {album_img} AS image_url
        FROM top_tracks tt
        JOIN tracks t ON t.track_id = tt.track_id
        LEFT JOIN albums al ON al.album_id = t.album_id
        JOIN track_artists ta ON ta.track_id = tt.track_id
        WHERE tt.user_id = :user AND tt.time_range = :range
          AND tt.snapshot_date = (SELECT MAX(snapshot_date) FROM top_tracks
                                  WHERE user_id = :user AND time_range = :range)
        ORDER BY tt.rank
        """,
        {"user": user_id, "range": time_range},
    ):
        add(r["artist_id"], r["track_id"], r["name"], r["rank"], r["image_url"])

    # 2. Fill up with the most-played recorded songs
    for r in query(
        f"""
        SELECT ta.artist_id, p.track_id, t.name, {album_img} AS image_url, COUNT(*) AS n
        FROM plays p
        JOIN tracks t ON t.track_id = p.track_id
        LEFT JOIN albums al ON al.album_id = t.album_id
        JOIN track_artists ta ON ta.track_id = p.track_id
        WHERE p.user_id = ?
        GROUP BY ta.artist_id, p.track_id
        ORDER BY n DESC, t.name
        """,
        (user_id,),
    ):
        add(r["artist_id"], r["track_id"], r["name"], None, r["image_url"])
    return out


def top_lists(user_id, limit=10):
    """Top tracks and artists for each time range (4 weeks, 6 months, 1 year).
    Each top artist also carries "tracks": their top 5 tracks for the hover list."""
    out = {}
    for tr in TIME_RANGES:
        tracks = _top_list("top_tracks", user_id, tr, limit)
        artists = _top_list("top_artists", user_id, tr, limit)
        by_artist = _tracks_by_artist(user_id, tr) if artists else {}
        for a in artists:
            a["tracks"] = by_artist.get(a["item_id"], [])
        for r in tracks + artists:
            r.pop("item_id")
        out[tr] = {"tracks": tracks, "artists": artists}
    return out


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
    album_img = "a.image_url" if has_image_columns()["albums"] else "NULL"
    not_liked = query(
        f"""
        SELECT t.name AS track, ar.name AS artist, {album_img} AS image_url, COUNT(*) AS plays
        FROM plays p
        JOIN tracks t ON t.track_id = p.track_id
        LEFT JOIN albums a ON a.album_id = t.album_id
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

    selected_user = None
    user_index = 0
    data = None
    if user_id:
        user_index = user_ids.index(user_id)
        selected_user = {
            **users[user_index],
            "initial": (users[user_index]["display_name"].strip()[:1] or "?").upper(),
        }
        data = {
            "summary": summary(user_id),
            "mood": mood_over_time(user_id),
            "years": release_years(user_id),
            "hours": listening_hours(user_id),
            "top": top_lists(user_id),
            "liked_vs_played": liked_vs_played(user_id),
        }
    return render_template(
        "individual.html",
        users=users,
        user_id=user_id,
        selected_user=selected_user,
        user_index=user_index,
        data=data,
    )
