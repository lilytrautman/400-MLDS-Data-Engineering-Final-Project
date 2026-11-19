"""Pull raw data from the Spotify, ReccoBeats and Deezer APIs.

Every API response is saved untouched as JSON in data/raw/<run_id>/, one file per
endpoint. Later steps read these files, so they can be rerun without calling the
APIs again, and the files are a record of exactly what each API returned.

Usage (from the repo root):
    python src/ingest.py
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

RAW_DIR = Path("data/raw")
DB_PATH = Path("data/spotify.db")  # read to skip tracks that are already enriched

# Spotify: read-only access to the profile, listening history, top items and liked songs
SCOPES = "user-read-private user-top-read user-read-recently-played user-library-read"
REDIRECT_URI = "http://127.0.0.1:8888/callback"  # must match the Spotify developer dashboard
TIME_RANGES = ["short_term", "medium_term", "long_term"]  # ~4 weeks, ~6 months, ~1 year

# Enrichment APIs (no keys needed)
RECCOBEATS_URL = "https://api.reccobeats.com/v1/audio-features"
RECCOBEATS_BATCH_SIZE = 40  # the API rejects requests with more than 40 ids
DEEZER_URL = "https://api.deezer.com/track/isrc:{isrc}"
PAUSE_SECONDS = 0.5  # wait between calls to stay within the free APIs' rate limits


def now_utc():
    """Return the current UTC time as ISO-8601 text, e.g. 2026-11-19T03:00:12Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def login():
    """Return an authenticated Spotify client.

    The app keys come from .env. The first login opens a browser; after that the
    token is cached in .cache and refreshed automatically.
    """
    load_dotenv()
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=os.environ["SPOTIFY_CLIENT_ID"],
        client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=REDIRECT_URI,
        scope=SCOPES,
        cache_path=".cache",
    ))


def save(run_dir, name, items):
    """Write items to <run_dir>/<name>.json, together with the time they were pulled."""
    path = Path(run_dir) / f"{name}.json"
    path.write_text(json.dumps({"pulled_at": now_utc(), "items": items}, indent=2))
    print(f"{name}: {len(items)} items")


def load(run_dir, name):
    """Return the items saved in <run_dir>/<name>.json."""
    return json.loads((Path(run_dir) / f"{name}.json").read_text())["items"]


def get_all(sp, page):
    """Return every item of a paginated Spotify response, following its "next" links."""
    items = []
    while page:
        items.extend(page["items"])
        page = sp.next(page) if page["next"] else None
    return items


def get_json(url, params=None, retries=1):
    """GET a URL and return the parsed JSON, or None if it still fails after retrying.

    Failures are printed, not raised, so one bad request doesn't stop the whole run.
    """
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, timeout=20)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as error:
            print(f"  request failed (attempt {attempt + 1}): {error}")
            if attempt < retries:
                time.sleep(5)
    return None


def pull_spotify(sp, run_dir):
    """Save one raw JSON file per Spotify endpoint."""
    save(run_dir, "me", [sp.current_user()])

    # Spotify only keeps the last 50 plays, so each run captures what's new since the last one
    save(run_dir, "recently_played", sp.current_user_recently_played(limit=50)["items"])

    # Top 50 tracks and artists for each window; one snapshot per run tracks rank changes
    for time_range in TIME_RANGES:
        top_tracks = sp.current_user_top_tracks(limit=50, time_range=time_range)
        save(run_dir, f"top_tracks_{time_range}", top_tracks["items"])
        top_artists = sp.current_user_top_artists(limit=50, time_range=time_range)
        save(run_dir, f"top_artists_{time_range}", top_artists["items"])

    # Liked songs come 50 per page, so follow every page back to the first like
    save(run_dir, "saved_tracks", get_all(sp, sp.current_user_saved_tracks(limit=50)))


def main():
    """Run one ingestion and return the folder the raw files were saved in."""
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    run_dir = RAW_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    sp = login()
    pull_spotify(sp, run_dir)
    return run_dir


if __name__ == "__main__":
    main()
