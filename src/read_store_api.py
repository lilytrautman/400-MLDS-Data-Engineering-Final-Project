"""Pull the user's Spotify tracks + candidate tracks from the API into data/raw/*.csv."""

import os
from pathlib import Path

import pandas as pd
import spotipy
from spotipy.oauth2 import SpotifyOAuth

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

# Load .env if present; real environment variables win.
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=os.environ["SPOTIFY_CLIENT_ID"],
    client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
    redirect_uri="http://127.0.0.1:8888/callback",
    scope="user-top-read user-read-recently-played user-library-read",
    cache_path=str(ROOT / ".cache"),
))


def track_row(t, source, **extra):
    """Flatten one Spotify track object into a CSV row."""
    return {
        "track_id": t["id"],
        "name": t["name"],
        "artist_ids": "|".join(a["id"] for a in t["artists"]),
        "artist_names": "|".join(a["name"] for a in t["artists"]),
        "album": t["album"]["name"],
        "release_date": t["album"].get("release_date"),
        "duration_ms": t["duration_ms"],
        "explicit": t["explicit"],
        "source": source,
        **extra,
    }


def user_tracks():
    """Songs the user likes: top tracks, saved tracks, recently played."""
    rows = []
    for rng in ("short_term", "medium_term", "long_term"):
        for rank, t in enumerate(sp.current_user_top_tracks(limit=50, time_range=rng)["items"]):
            rows.append(track_row(t, f"top_{rng}", rank=rank))
    page = sp.current_user_saved_tracks(limit=50)
    while page:
        rows += [track_row(it["track"], "saved", added_at=it["added_at"]) for it in page["items"]]
        page = sp.next(page)
    for it in sp.current_user_recently_played(limit=50)["items"]:
        rows.append(track_row(it["track"], "recent", played_at=it["played_at"]))
    return rows


def candidate_tracks():
    """Songs by the user's top 20 artists -- the pool to recommend from."""
    rows = []
    for a in sp.current_user_top_artists(limit=20, time_range="medium_term")["items"]:
        for offset in range(0, 50, 10):  # Spotify caps search pages at 10 for new apps
            res = sp.search(q=f'artist:"{a["name"]}"', type="track", limit=10, offset=offset)
            rows += [track_row(t, "candidate", seed_artist=a["name"]) for t in res["tracks"]["items"]]
    return rows


if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    for name, rows in [("user_tracks", user_tracks()), ("candidate_tracks", candidate_tracks())]:
        pd.DataFrame(rows).to_csv(RAW / f"{name}.csv", index=False)
        print(f"{name}: {len(rows)} rows -> data/raw/{name}.csv")
