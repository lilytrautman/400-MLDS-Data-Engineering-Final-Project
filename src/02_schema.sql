-- Final tables for the Spotify pipeline (SQLite).
--
-- Every table is STRICT, so SQLite rejects values of the wrong type, and is created
-- with IF NOT EXISTS, so this file runs at the start of every pipeline run without
-- touching data already loaded. Tables are listed parents first, so every foreign
-- key points at a table defined above it.
--
-- Timestamps are ISO-8601 UTC text (2026-11-19T03:00:12Z): it sorts correctly and
-- works with SQLite's date functions.

-- One row per Spotify account
CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    display_name TEXT,
    country      TEXT
) STRICT;

-- One row per album. release_precision says whether release_date is a year,
-- a month or a full date; release_year is filled in for all three.
CREATE TABLE IF NOT EXISTS albums (
    album_id          TEXT PRIMARY KEY,
    name              TEXT,
    album_type        TEXT,
    release_date      TEXT,
    release_precision TEXT CHECK (release_precision IN ('year', 'month', 'day')),
    release_year      INTEGER
) STRICT;

-- One row per artist. Dev Mode no longer returns genres or popularity.
CREATE TABLE IF NOT EXISTS artists (
    artist_id TEXT PRIMARY KEY,
    name      TEXT
) STRICT;

-- One row per track. Release dates live in albums, not here. Songs that were removed
-- from Spotify stay (they're still real likes) but come back with no name and a
-- duration of 0, which cleaning stores as NULL.
CREATE TABLE IF NOT EXISTS tracks (
    track_id    TEXT PRIMARY KEY,
    name        TEXT,
    album_id    TEXT REFERENCES albums (album_id),
    duration_ms INTEGER CHECK (duration_ms > 0),
    explicit    INTEGER CHECK (explicit IN (0, 1)),
    isrc        TEXT
) STRICT;

-- Listening history, one row per play. A user can only play one thing at a time,
-- so (user_id, played_at) identifies a play. run_id is the run that first stored
-- the play, which shows how many new plays each daily run added.
CREATE TABLE IF NOT EXISTS plays (
    user_id      TEXT NOT NULL REFERENCES users (user_id),
    played_at    TEXT NOT NULL,
    track_id     TEXT NOT NULL REFERENCES tracks (track_id),
    context_type TEXT,
    run_id       TEXT NOT NULL,
    PRIMARY KEY (user_id, played_at)
) STRICT;

-- Which artists are credited on which track, in credit order (many-to-many)
CREATE TABLE IF NOT EXISTS track_artists (
    track_id     TEXT NOT NULL REFERENCES tracks (track_id),
    artist_id    TEXT NOT NULL REFERENCES artists (artist_id),
    artist_order INTEGER NOT NULL CHECK (artist_order >= 0),
    PRIMARY KEY (track_id, artist_id)
) STRICT;

-- Liked songs and when each was liked
CREATE TABLE IF NOT EXISTS saved_tracks (
    user_id  TEXT NOT NULL REFERENCES users (user_id),
    track_id TEXT NOT NULL REFERENCES tracks (track_id),
    added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, track_id)
) STRICT;

-- Top 50 tracks per time range, one snapshot per day, so rank changes are kept.
-- snapshot_date is the Chicago date the run started on (not UTC like the timestamps).
CREATE TABLE IF NOT EXISTS top_tracks (
    user_id       TEXT NOT NULL REFERENCES users (user_id),
    snapshot_date TEXT NOT NULL,
    time_range    TEXT NOT NULL CHECK (time_range IN ('short_term', 'medium_term', 'long_term')),
    rank          INTEGER NOT NULL CHECK (rank BETWEEN 1 AND 50),
    track_id      TEXT NOT NULL REFERENCES tracks (track_id),
    PRIMARY KEY (user_id, snapshot_date, time_range, rank)
) STRICT;

-- Top 50 artists per time range, same layout as top_tracks
CREATE TABLE IF NOT EXISTS top_artists (
    user_id       TEXT NOT NULL REFERENCES users (user_id),
    snapshot_date TEXT NOT NULL,
    time_range    TEXT NOT NULL CHECK (time_range IN ('short_term', 'medium_term', 'long_term')),
    rank          INTEGER NOT NULL CHECK (rank BETWEEN 1 AND 50),
    artist_id     TEXT NOT NULL REFERENCES artists (artist_id),
    PRIMARY KEY (user_id, snapshot_date, time_range, rank)
) STRICT;

-- Audio features from ReccoBeats. Separate from tracks because not every track has them.
CREATE TABLE IF NOT EXISTS audio_features (
    track_id         TEXT PRIMARY KEY REFERENCES tracks (track_id),
    acousticness     REAL CHECK (acousticness BETWEEN 0 AND 1),
    danceability     REAL CHECK (danceability BETWEEN 0 AND 1),
    energy           REAL CHECK (energy BETWEEN 0 AND 1),
    instrumentalness REAL CHECK (instrumentalness BETWEEN 0 AND 1),
    liveness         REAL CHECK (liveness BETWEEN 0 AND 1),
    speechiness      REAL CHECK (speechiness BETWEEN 0 AND 1),
    valence          REAL CHECK (valence BETWEEN 0 AND 1),
    tempo            REAL CHECK (tempo > 0),
    loudness         REAL CHECK (loudness <= 0),
    key              INTEGER CHECK (key BETWEEN -1 AND 11),
    mode             INTEGER CHECK (mode IN (0, 1))
) STRICT;

-- Popularity rank and BPM from Deezer, matched by ISRC
CREATE TABLE IF NOT EXISTS deezer_tracks (
    track_id  TEXT PRIMARY KEY REFERENCES tracks (track_id),
    deezer_id INTEGER NOT NULL,
    rank      INTEGER CHECK (rank >= 0),
    bpm       REAL
) STRICT;

-- Staged rows that cleaning didn't load, and why. The UNIQUE constraint stops a
-- rerun of the same run from logging the same drop twice.
CREATE TABLE IF NOT EXISTS dropped_rows (
    drop_id      INTEGER PRIMARY KEY,
    run_id       TEXT NOT NULL,
    source_table TEXT NOT NULL,
    row_key      TEXT,
    reason       TEXT NOT NULL,
    UNIQUE (run_id, source_table, row_key, reason)
) STRICT;
