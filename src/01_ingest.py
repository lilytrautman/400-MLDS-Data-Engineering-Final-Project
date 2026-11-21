"""Pull raw data from the Spotify, ReccoBeats and Deezer APIs.

Every account that has logged in (one token file in tokens/) is pulled into its
own folder, data/raw/<run_id>/<name>/, one JSON file per endpoint. Audio features
and Deezer data are looked up once for the tracks of all accounts together and saved
next to those folders. Later steps read these files, so they can be rerun without
calling the APIs again, and the files are a record of exactly what each API returned.

Usage (from the repo root):
    python src/login.py <name>     once per account
    python src/01_ingest.py
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
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth

ROOT = Path(__file__).resolve().parent.parent  # the repo root, wherever this runs from
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "spotify.db"  # read to skip tracks that are already enriched
TOKENS_DIR = ROOT / "tokens"  # one Spotify login per account, never committed

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


def auth_manager(name, open_browser=True):
    """Return the Spotify login for one account, stored in tokens/<name>.json.

    The app keys come from .env. show_dialog makes Spotify ask which account to use,
    so a browser already logged in as someone else doesn't log in the wrong person.
    """
    load_dotenv(ROOT / ".env")
    return SpotifyOAuth(
        client_id=os.environ["SPOTIFY_CLIENT_ID"],
        client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=REDIRECT_URI,
        scope=SCOPES,
        cache_handler=CacheFileHandler(cache_path=str(TOKENS_DIR / f"{name}.json")),
        open_browser=open_browser,
        show_dialog=True,
    )


def accounts():
    """Return the name of every account that has logged in, from its token file."""
    return sorted(path.stem for path in TOKENS_DIR.glob("*.json"))


def account_dirs(run_dir):
    """Return the folder of every account fully pulled in this run.

    A pull writes into <name>.tmp and is renamed when it finishes, so a failed pull
    is never picked up.
    """
    return sorted(path.parent for path in Path(run_dir).glob("*/me.json")
                  if path.parent.suffix != ".tmp")



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

def pull_account(name, run_dir):
    """Pull one account's Spotify data into <run_dir>/<name>/."""
    login = auth_manager(name, open_browser=False)
    # Refresh the saved token without ever asking for a login, so a scheduled run
    # fails with a clear message instead of waiting for someone to log in
    if not login.validate_token(login.cache_handler.get_cached_token()):
        raise RuntimeError(f"token expired or revoked, run: python src/login.py {name}")

    # Write into <name>.tmp and rename when done, so staging never reads a half pull
    tmp_dir = Path(run_dir) / f"{name}.tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    pull_spotify(spotipy.Spotify(auth_manager=login), tmp_dir)
    tmp_dir.rename(Path(run_dir) / name)


def unique_tracks(run_dir):
    """Return each distinct track found across accounts in this run's Spotify files.

    Plays and liked songs wrap the track in a "track" key; top tracks are track
    objects themselves. Local files have no Spotify id and are left out.
    """
    tracks = {}
    for account_dir in account_dirs(run_dir):
        for item in load(account_dir, "recently_played") + load(account_dir, "saved_tracks"):
            tracks[item["track"]["id"]] = item["track"]
        for time_range in TIME_RANGES:
            for track in load(account_dir, f"top_tracks_{time_range}"):
                tracks[track["id"]] = track
    tracks.pop(None, None)
    return list(tracks.values())


def stored_track_ids(table):
    """Return the track ids already stored in a final table (empty before the first load)."""
    if not DB_PATH.exists():
        return set()
    conn = sqlite3.connect(DB_PATH)
    try:
        return {row[0] for row in conn.execute(f"SELECT track_id FROM {table}")}
    except sqlite3.OperationalError:  # the table doesn't exist yet
        return set()
    finally:
        conn.close()


def pull_reccobeats(track_ids):
    """Fetch audio features for the given Spotify track ids, in batches.

    Returns one entry per request: the ids asked for and the raw response (None if
    the request failed). Tracks ReccoBeats doesn't know are simply absent from the
    response, so misses can be counted later.
    """
    responses = []
    for start in range(0, len(track_ids), RECCOBEATS_BATCH_SIZE):
        batch = track_ids[start:start + RECCOBEATS_BATCH_SIZE]
        data = get_json(RECCOBEATS_URL, {"ids": ",".join(batch)})
        responses.append({"asked": batch, "response": data})
        time.sleep(PAUSE_SECONDS)

    matched = sum(len(r["response"]["content"]) for r in responses if r["response"])
    print(f"reccobeats: {matched}/{len(track_ids)} tracks matched")
    return responses


def pull_deezer(tracks):
    """Look up each track on Deezer by its ISRC and return one entry per track looked up.

    Tracks without an ISRC can't be looked up and are skipped. Each entry keeps the
    raw response, misses included, so match rates can be measured later.
    """
    responses = []
    skipped = 0
    for track in tracks:
        isrc = (track.get("external_ids") or {}).get("isrc")
        if not isrc:
            skipped += 1
            continue
        data = get_json(DEEZER_URL.format(isrc=isrc))
        responses.append({"track_id": track["id"], "isrc": isrc, "response": data})
        time.sleep(PAUSE_SECONDS)

    # Deezer answers "not found" with HTTP 200 and an "error" key, not a failed request
    matched = sum(1 for r in responses if r["response"] is not None and "error" not in r["response"])
    print(f"deezer: {matched}/{len(responses)} tracks matched, {skipped} skipped (no ISRC)")
    return responses

def enrich(run_dir):
    """Look up audio features and Deezer data for this run's tracks not stored yet.

    Audio features and Deezer data don't change, so only tracks not stored yet are
    looked up. Tracks that got no match are asked again on the next run.
    """
    tracks = unique_tracks(run_dir)
    have_features = stored_track_ids("audio_features")
    have_deezer = stored_track_ids("deezer_tracks")
    save(run_dir, "audio_features",
         pull_reccobeats([t["id"] for t in tracks if t["id"] not in have_features]))
    save(run_dir, "deezer", pull_deezer([t for t in tracks if t["id"] not in have_deezer]))


def new_run_dir():
    """Create and return the folder for a new run, named by the UTC time it started."""
    run_dir = RAW_DIR / datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def main():
    """Run one ingestion and return the folder the raw files were saved in."""
    names = accounts()
    if not names:
        raise SystemExit("No accounts logged in yet. Run: python src/login.py <name>")

    run_dir = new_run_dir()
    for name in names:
        print(f"--- {name}")
        # One account failing shouldn't stop the others from loading
        try:
            pull_account(name, run_dir)
        except Exception as error:
            print(f"{name}: pull failed, skipped ({error})")

    enrich(run_dir)
    return run_dir


if __name__ == "__main__":
    main()
