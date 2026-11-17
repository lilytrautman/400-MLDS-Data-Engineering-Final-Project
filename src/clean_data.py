# This script cleans the Spotify data in the SQLite database and saves the cleaned tables back into it.
# Run read_api.py and store_data.py first, then run this from the project folder with python src/clean_data.py

import sqlite3

import pandas as pd

conn = sqlite3.connect("data/spotify.db")
user_tracks = pd.read_sql("SELECT * FROM user_tracks", conn)
candidates = pd.read_sql("SELECT * FROM candidate_tracks", conn)

# This checks for null values in each table
print("null values in user tracks:")
print(user_tracks.isna().sum())
print("null values in candidate tracks:")
print(candidates.isna().sum())

# Local files and songs that got taken off Spotify don't have a track id, so we can't use them
user_tracks = user_tracks.dropna(subset=["track_id"])
candidates = candidates.dropna(subset=["track_id"])

# The same song can show up in my top tracks more than once (like top this month and top all time),
# so this only removes rows that are exact copies. Searching the same artist 5 times can also
# give the same song twice, so candidates only keep one row per song.
user_tracks = user_tracks.drop_duplicates()
candidates = candidates.drop_duplicates(subset=["track_id"])

# Release dates come in different formats (some are just "2019", some are "2019-05-01"),
# so the first 4 characters give the year for all of them. This also turns length into minutes.
for df in [user_tracks, candidates]:
    df["release_year"] = df["release_date"].astype(str).str[:4].astype(int)
    df["duration_min"] = df["duration_ms"] / 60000

user_tracks["added_at"] = pd.to_datetime(user_tracks["added_at"])
user_tracks["played_at"] = pd.to_datetime(user_tracks["played_at"], format="ISO8601")

# This marks which candidate songs I already like (saved or in my top tracks). That's what
# the model will try to predict.
liked = user_tracks[user_tracks["source"] != "recent"]["track_id"]
candidates["liked"] = candidates["track_id"].isin(liked)
print("candidates I already like:", candidates["liked"].sum(), "out of", len(candidates))

# This saves the cleaned data as new tables in the database. The raw tables stay
# there too, so we can always go back to what came from the API.
user_tracks.to_sql("clean_user_tracks", conn, if_exists="replace", index=False)
candidates.to_sql("clean_candidate_tracks", conn, if_exists="replace", index=False)
print("saved", len(user_tracks), "user tracks and", len(candidates), "candidates to the database")

conn.close()
