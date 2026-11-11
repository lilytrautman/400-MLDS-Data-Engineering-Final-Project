"""Pull the user's Spotify listening data + candidate tracks into data/raw/.

Reads SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET from the project's .env file,
and needs the redirect URI below added to your app in the Spotify developer dashboard.
First run opens a browser to log in; the token is cached in .cache afterwards.
"""
from pathlib import Path

import pandas as pd
import spotipy
from spotipy.oauth2 import SpotifyOAuth

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
ENV = dict(
    line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines()
    if "=" in line and not line.lstrip().startswith("#")
)
ENV = {k.strip(): v.strip().strip("\"'") for k, v in ENV.items()}
SCOPES = "user-top-read user-read-recently-played user-library-read playlist-read-private"
SEARCH_LIMIT = 10  # Spotify rejects >10 per search page for this app
N_ARTISTS = 20     # how many of your top artists to pull candidate songs from

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=ENV["SPOTIFY_CLIENT_ID"], client_secret=ENV["SPOTIFY_CLIENT_SECRET"],
    scope=SCOPES, redirect_uri="http://127.0.0.1:8888/callback",
    cache_path=str(ROOT / ".cache")))


def track_row(t, source, **extra):
    return {
        "track_id": t["id"],
        "name": t["name"],
        "artist_ids": [a["id"] for a in t["artists"]],
        "artist_names": [a["name"] for a in t["artists"]],
        "album_id": t["album"]["id"],
        "album": t["album"]["name"],
        "album_type": t["album"].get("album_type"),
        "album_total_tracks": t["album"].get("total_tracks"),
        "release_date": t["album"].get("release_date"),
        "release_date_precision": t["album"].get("release_date_precision"),
        "track_number": t.get("track_number"),
        "duration_ms": t["duration_ms"],
        "explicit": t["explicit"],
        "isrc": (t.get("external_ids") or {}).get("isrc"),  # join key for outside datasets
        "source": source,
        **extra,
    }


def paged(page):
    while page:
        yield from page["items"]
        page = sp.next(page) if page.get("next") else None


def is_track(t):
    return t and t.get("type") == "track" and t.get("id")  # skips podcasts/local files


def user_tracks():
    """Everything that signals the user likes a song (positives)."""
    rows = []
    for rng in ("short_term", "medium_term", "long_term"):
        for rank, t in enumerate(sp.current_user_top_tracks(limit=50, time_range=rng)["items"]):
            rows.append(track_row(t, f"top_{rng}", rank=rank))
    for it in paged(sp.current_user_saved_tracks(limit=50)):
        rows.append(track_row(it["track"], "saved", added_at=it["added_at"]))
    for it in sp.current_user_recently_played(limit=50)["items"]:
        rows.append(track_row(it["track"], "recent", played_at=it["played_at"]))
    for pl in paged(sp.current_user_playlists(limit=50)):
        try:
            for it in paged(sp.playlist_items(pl["id"], limit=100)):
                t = it.get("track") or it.get("item")
                if is_track(t):
                    rows.append(track_row(t, "playlist", playlist_id=pl["id"],
                                          playlist_name=pl["name"], added_at=it.get("added_at")))
        except spotipy.SpotifyException:  # playlists you don't own can 403 in dev mode
            continue
    return rows


def candidate_tracks():
    """Songs by the user's top artists -- the pool the model ranks.
    (Genres/popularity are stripped for new apps, so artist is the only seed left.)"""
    top = sp.current_user_top_artists(limit=N_ARTISTS, time_range="medium_term")["items"]
    rows = []
    for a in top:
        for offset in range(0, 50, SEARCH_LIMIT):
            res = sp.search(q=f'artist:"{a['name']}"', type="track", limit=SEARCH_LIMIT, offset=offset)
            rows += [track_row(t, "candidate", seed_artist=a["name"])
                     for t in res["tracks"]["items"] if is_track(t)]
    return rows


def load():
    """Pull everything from the API and return it as DataFrames."""
    return {
        "user_tracks": pd.DataFrame(user_tracks()),
        "candidate_tracks": pd.DataFrame(candidate_tracks()),
    }


def store(tables):
    RAW.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        # list columns (artist_ids, genres, ...) -> "a|b|c" so CSV round-trips; split with .str.split("|")
        out = df.apply(lambda col: col.map(lambda v: "|".join(v) if isinstance(v, list) else v))
        out.to_csv(RAW / f"{name}.csv", index=False)
        print(f"{name}: {df.shape[0]} rows x {df.shape[1]} cols")


if __name__ == "__main__":
    store(load())
