"""Flatten the raw API responses into staging tables (stg_*) in SQLite.

Each stage_* function builds one DataFrame for one final table. The Spotify ones
read one account's folder; their rows are stacked across accounts before writing.
Nothing is cleaned here: nulls and duplicates are kept so that 04_clean.sql can log
and resolve them. Staging tables are replaced on every run, so they only ever hold
the current run.
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TIME_RANGES = ["short_term", "medium_term", "long_term"]
LOCAL_TZ = ZoneInfo("America/Chicago")  # same as the DAG schedule and the dashboard

AUDIO_FEATURES = [
    "acousticness", "danceability", "energy", "instrumentalness", "key", "liveness",
    "loudness", "mode", "speechiness", "tempo", "valence",
]


def load(run_dir, name):
    """Return the items saved in <run_dir>/<name>.json."""
    return json.loads((Path(run_dir) / f"{name}.json").read_text())["items"]


def write(conn, df, table, run_id):
    """Replace stg_<table> with df, tagging every row with the run it came from."""
    df = df.copy()
    df["run_id"] = run_id
    df.to_sql(f"stg_{table}", conn, if_exists="replace", index=False)
    print(f"stg_{table}: {len(df)} rows")


def account_dirs(run_dir):
    """Return the folder of every account fully pulled in this run (not *.tmp)."""
    return sorted(path.parent for path in Path(run_dir).glob("*/me.json")
                  if path.parent.suffix != ".tmp")


def all_tracks(account_dir):
    """Return every track object in one account's Spotify files, duplicates included."""
    tracks = [item["track"] for item in load(account_dir, "recently_played")]
    tracks += [item["track"] for item in load(account_dir, "saved_tracks")]
    for time_range in TIME_RANGES:
        tracks += load(account_dir, f"top_tracks_{time_range}")
    return tracks


def first_image_url(obj):
    """Return the first image URL from a Spotify object's 'images' list, or None."""
    images = obj.get("images") or []
    return images[0].get("url") if images else None


def load_user_names():
    """Read optional local data/user_names.json overrides (gitignored), keyed by account name or user_id."""
    path = Path(__file__).resolve().parent.parent / "data" / "user_names.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def parse_user_names(account_name, display_name, user_id, overrides):
    """Derive (first_name, last_name) from optional local overrides, account folder name, and Spotify display_name."""
    parts = [p for p in account_name.replace("-", "_").split("_") if p]
    first = parts[0].capitalize() if parts else None
    last = " ".join(p.capitalize() for p in parts[1:]) or None

    # Fall back to Spotify's display_name when it includes a last name (e.g. "Yash Ahlawat")
    if not last and display_name:
        words = display_name.strip().split()
        if len(words) >= 2:
            last = " ".join(words[1:])

    override = overrides.get(account_name.lower()) or overrides.get(user_id)
    if isinstance(override, dict):
        first = override.get("first_name") or first
        last = override.get("last_name") or last
    return first, last


def stage_users(account_dir):
    """One row for the account the data was pulled from."""
    overrides = load_user_names()
    account_name = Path(account_dir).name
    rows = []
    for item in load(account_dir, "me"):
        user_id = item.get("id")
        display_name = item.get("display_name")
        first_name, last_name = parse_user_names(account_name, display_name, user_id, overrides)
        rows.append({
            "user_id": user_id,
            "display_name": display_name,
            "first_name": first_name,
            "last_name": last_name,
            "country": item.get("country"),
            "image_url": first_image_url(item),
        })
    return pd.DataFrame(
        rows,
        columns=["user_id", "display_name", "first_name", "last_name", "country", "image_url"],
    )


def stage_plays(account_dir, user_id):
    """One row per play in recently played."""
    df = pd.json_normalize(load(account_dir, "recently_played"))
    # context is null for tracks not played from a playlist or album, and if every
    # context is null the column doesn't exist; reindex adds it back as empty
    df = df.reindex(columns=["played_at", "track.id", "context.type"])
    df = df.rename(columns={"track.id": "track_id", "context.type": "context_type"})
    df.insert(0, "user_id", user_id)
    return df


def stage_tracks(account_dir):
    """One row per track appearance across all files."""
    df = pd.json_normalize(all_tracks(account_dir))
    df = df.reindex(columns=["id", "name", "album.id", "duration_ms", "explicit", "external_ids.isrc"])
    return df.rename(columns={"id": "track_id", "album.id": "album_id", "external_ids.isrc": "isrc"})


def stage_track_artists(account_dir):
    """One row per (track, artist) pair, keeping the order artists are credited in."""
    rows = [
        {"track_id": track["id"], "artist_id": artist["id"], "artist_name": artist["name"],
         "artist_order": position}
        for track in all_tracks(account_dir)
        for position, artist in enumerate(track["artists"])
    ]
    return pd.DataFrame(rows)


def stage_albums(account_dir):
    """One row per album appearance, taken from every track's album."""
    albums = [track["album"] for track in all_tracks(account_dir)]
    df = pd.json_normalize(albums)
    df = df.reindex(columns=["id", "name", "album_type", "release_date", "release_date_precision"])
    df = df.rename(columns={"id": "album_id", "release_date_precision": "release_precision"})
    df["image_url"] = [first_image_url(album) for album in albums]
    return df


def stage_artists(account_dir):
    """One row per artist appearance, from track credits and the top-artist lists."""
    credited = stage_track_artists(account_dir)[["artist_id", "artist_name"]].rename(
        columns={"artist_name": "name"}
    )
    credited["image_url"] = None
    frames = [credited]
    for time_range in TIME_RANGES:
        items = load(account_dir, f"top_artists_{time_range}")
        rows = [
            {"artist_id": item.get("id"), "name": item.get("name"),
             "image_url": first_image_url(item)}
            for item in items
        ]
        frames.append(pd.DataFrame(rows, columns=["artist_id", "name", "image_url"]))
    return pd.concat(frames, ignore_index=True)


def stage_saved_tracks(account_dir, user_id):
    """One row per liked song, with the time it was liked."""
    df = pd.json_normalize(load(account_dir, "saved_tracks"))
    df = df.reindex(columns=["track.id", "added_at"]).rename(columns={"track.id": "track_id"})
    df.insert(0, "user_id", user_id)
    return df


def stage_top_tracks(account_dir, user_id, snapshot_date):
    """One row per position in each top-tracks list, for this run's snapshot date."""
    rows = [
        {"user_id": user_id, "snapshot_date": snapshot_date, "time_range": time_range,
         "rank": rank, "track_id": track["id"]}
        for time_range in TIME_RANGES
        for rank, track in enumerate(load(account_dir, f"top_tracks_{time_range}"), start=1)
    ]
    return pd.DataFrame(rows, columns=["user_id", "snapshot_date", "time_range", "rank", "track_id"])


def stage_top_artists(account_dir, user_id, snapshot_date):
    """One row per position in each top-artists list, for this run's snapshot date."""
    rows = [
        {"user_id": user_id, "snapshot_date": snapshot_date, "time_range": time_range,
         "rank": rank, "artist_id": artist["id"]}
        for time_range in TIME_RANGES
        for rank, artist in enumerate(load(account_dir, f"top_artists_{time_range}"), start=1)
    ]
    return pd.DataFrame(rows, columns=["user_id", "snapshot_date", "time_range", "rank", "artist_id"])


def stage_audio_features(run_dir):
    """One row per track ReccoBeats returned features for."""
    rows = []
    for entry in load(run_dir, "audio_features"):
        if entry["response"] is None:  # the request failed
            continue
        for features in entry["response"]["content"]:
            # "id" is ReccoBeats' own id; the Spotify id is the end of the track link
            row = {"track_id": features["href"].rsplit("/", 1)[-1]}
            row.update({name: features.get(name) for name in AUDIO_FEATURES})
            rows.append(row)
    return pd.DataFrame(rows, columns=["track_id"] + AUDIO_FEATURES)


def stage_deezer_tracks(run_dir):
    """One row per track Deezer found by ISRC."""
    rows = []
    for entry in load(run_dir, "deezer"):
        response = entry["response"]
        if response is None or "error" in response:  # failed request or no match
            continue
        artist_img = (response.get("artist") or {}).get("picture_medium")
        rows.append({"track_id": entry["track_id"], "deezer_id": response["id"],
                     "rank": response.get("rank"), "bpm": response.get("bpm"),
                     "artist_image_url": artist_img})
    return pd.DataFrame(rows, columns=["track_id", "deezer_id", "rank", "bpm", "artist_image_url"])


def stage_account(account_dir, snapshot_date):
    """Return every Spotify staging table for one account, keyed by table name."""
    users = stage_users(account_dir)
    user_id = users["user_id"].iloc[0]
    return {
        "users": users,
        "plays": stage_plays(account_dir, user_id),
        "tracks": stage_tracks(account_dir),
        "track_artists": stage_track_artists(account_dir),
        "albums": stage_albums(account_dir),
        "artists": stage_artists(account_dir),
        "saved_tracks": stage_saved_tracks(account_dir, user_id),
        "top_tracks": stage_top_tracks(account_dir, user_id, snapshot_date),
        "top_artists": stage_top_artists(account_dir, user_id, snapshot_date),
    }


def snapshot_date_for(run_id):
    """The Chicago date a run started on, e.g. 2026-11-24T0300Z -> 2026-11-23.

    Run ids are UTC times, and the 9 PM Chicago run is already the next day in UTC,
    so taking the date straight from the run id would label it a day ahead.
    """
    started = datetime.strptime(run_id, "%Y-%m-%dT%H%MZ").replace(tzinfo=timezone.utc)
    return started.astimezone(LOCAL_TZ).date().isoformat()


def main(run_dir, conn):
    """Write every staging table for one run."""
    run_dir = Path(run_dir)
    run_id = run_dir.name
    snapshot_date = snapshot_date_for(run_id)

    frames = defaultdict(list)
    # Tracks, albums and artists two people share appear twice here;
    # cleaning keeps one row per id.
    for account_dir in account_dirs(run_dir):
        for table, df in stage_account(account_dir, snapshot_date).items():
            frames[table].append(df)

    if not frames:
        raise RuntimeError(f"no finished account pulls in {run_dir}")

    for table, dfs in frames.items():
        write(conn, pd.concat(dfs, ignore_index=True), table, run_id)

    write(conn, stage_audio_features(run_dir), "audio_features", run_id)
    write(conn, stage_deezer_tracks(run_dir), "deezer_tracks", run_id)
