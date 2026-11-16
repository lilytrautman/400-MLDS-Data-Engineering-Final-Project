# This script pulls my Spotify data from the Spotify API and saves it to data/raw as is.
# The cleaning happens in clean_data.py.
# Run it from the project folder with python src/read_api.py
# The first time it opens a Spotify login page in the browser to give the app access.

import os

import pandas as pd
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# This reads the Spotify app keys from the .env file. The lines in .env should look like
# SPOTIFY_CLIENT_ID=yourid and SPOTIFY_CLIENT_SECRET=yoursecret
for line in open(".env"):
    if line.startswith("SPOTIFY_"):
        key, value = line.strip().split("=", 1)
        os.environ[key] = value

# This logs in to Spotify. The redirect uri has to match the one in the Spotify developer dashboard.
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=os.environ["SPOTIFY_CLIENT_ID"],
    client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
    redirect_uri="http://127.0.0.1:8888/callback",
    scope="user-top-read user-read-recently-played user-library-read",
    cache_path=".cache",
))


# This turns one track from the API into a row for the csv. Songs can have more than one
# artist, so the artists get joined together with a | like "Drake|Rihanna".
def track_row(track, source):
    return {
        "track_id": track["id"],
        "name": track["name"],
        "artist_ids": "|".join(artist["id"] for artist in track["artists"]),
        "artist_names": "|".join(artist["name"] for artist in track["artists"]),
        "album": track["album"]["name"],
        "release_date": track["album"]["release_date"],
        "duration_ms": track["duration_ms"],
        "explicit": track["explicit"],
        "source": source,
    }


os.makedirs("data/raw", exist_ok=True)

# This is my listening data. It has my top tracks for the last month, 6 months and all time,
# every song I've saved, and the last 50 songs I played.
user_tracks = []
for time_range in ["short_term", "medium_term", "long_term"]:
    top = sp.current_user_top_tracks(limit=50, time_range=time_range)
    for rank, track in enumerate(top["items"]):
        row = track_row(track, "top_" + time_range)
        row["rank"] = rank
        user_tracks.append(row)

# Saved tracks come 50 at a time, so this keeps going to the next page until there isn't one
page = sp.current_user_saved_tracks(limit=50)
while page:
    for item in page["items"]:
        row = track_row(item["track"], "saved")
        row["added_at"] = item["added_at"]
        user_tracks.append(row)
    page = sp.next(page)

recent = sp.current_user_recently_played(limit=50)
for item in recent["items"]:
    row = track_row(item["track"], "recent")
    row["played_at"] = item["played_at"]
    user_tracks.append(row)

pd.DataFrame(user_tracks).to_csv("data/raw/user_tracks.csv", index=False)
print("user tracks:", len(user_tracks), "rows")

# This is my top 20 artists from the last 6 months
top_artists = []
artists = sp.current_user_top_artists(limit=20, time_range="medium_term")
for rank, artist in enumerate(artists["items"]):
    top_artists.append({"artist_id": artist["id"], "name": artist["name"], "rank": rank})

pd.DataFrame(top_artists).to_csv("data/raw/top_artists.csv", index=False)
print("top artists:", len(top_artists), "rows")

# These are songs by my top artists that we could recommend. Spotify only lets new apps get
# 10 search results at a time, so this asks 5 times to get 50 songs per artist.
candidates = []
for artist in top_artists:
    for offset in range(0, 50, 10):
        results = sp.search(q='artist:"' + artist["name"] + '"', type="track", limit=10, offset=offset)
        for track in results["tracks"]["items"]:
            row = track_row(track, "candidate")
            row["seed_artist"] = artist["name"]
            candidates.append(row)

pd.DataFrame(candidates).to_csv("data/raw/candidate_tracks.csv", index=False)
print("candidate tracks:", len(candidates), "rows")
