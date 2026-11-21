-- Clean the staging tables (stg_*) and load them into the final tables.
--
-- The same track, album or artist shows up in several raw files. Each final table's
-- primary key keeps one row per thing:
--   - History tables (plays, likes, top lists) use INSERT OR IGNORE: a row already
--     stored is never changed, so overlapping daily pulls never double count.
--   - Descriptive tables use ON CONFLICT DO UPDATE: the latest values win.
-- Parent tables load first, so foreign keys always point at rows that exist.
--
-- Rows that can't be loaded are logged to dropped_rows with a reason, instead of
-- silently disappearing or failing the whole load.
--
-- In INSERT ... SELECT ... ON CONFLICT, the SELECT needs a WHERE clause, or SQLite
-- reads ON as the start of a join. "WHERE true" is used where nothing is filtered.

-- users: profile fields are refreshed on every run
INSERT INTO users (user_id, display_name, country)
SELECT user_id, display_name, country
FROM stg_users
WHERE true
ON CONFLICT (user_id) DO UPDATE SET
    display_name = excluded.display_name,
    country      = excluded.country;

-- albums: songs removed from Spotify come back with an empty album name and
-- release date '0000', so both become NULL. Local files have no album id.
INSERT INTO albums (album_id, name, album_type, release_date, release_precision, release_year)
SELECT album_id,
       NULLIF(TRIM(name), ''),
       album_type,
       NULLIF(release_date, '0000'),
       release_precision,
       CASE WHEN release_date GLOB '[0-9][0-9][0-9][0-9]*' AND release_date <> '0000'
            THEN CAST(substr(release_date, 1, 4) AS INTEGER) END
FROM stg_albums
WHERE album_id IS NOT NULL
ON CONFLICT (album_id) DO UPDATE SET
    name              = excluded.name,
    album_type        = excluded.album_type,
    release_date      = excluded.release_date,
    release_precision = excluded.release_precision,
    release_year      = excluded.release_year;

-- artists: removed songs credit an artist with an empty name, so a known name is
-- never overwritten by a blank one
INSERT INTO artists (artist_id, name)
SELECT artist_id, NULLIF(TRIM(name), '')
FROM stg_artists
WHERE artist_id IS NOT NULL
ON CONFLICT (artist_id) DO UPDATE SET
    name = COALESCE(excluded.name, artists.name);

-- tracks: local files have no Spotify id and can't be loaded, so they're logged.
-- Removed songs keep their id but have an empty name and a duration of 0.
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_tracks', name, 'missing track_id (local file)'
FROM stg_tracks
WHERE track_id IS NULL;

INSERT INTO tracks (track_id, name, album_id, duration_ms, explicit, isrc)
SELECT track_id,
       NULLIF(TRIM(name), ''),
       album_id,
       NULLIF(CAST(duration_ms AS INTEGER), 0),
       CAST(explicit AS INTEGER),
       isrc
FROM stg_tracks
WHERE track_id IS NOT NULL
ON CONFLICT (track_id) DO UPDATE SET
    name        = excluded.name,
    album_id    = excluded.album_id,
    duration_ms = excluded.duration_ms,
    explicit    = excluded.explicit,
    isrc        = COALESCE(excluded.isrc, tracks.isrc);  -- never overwrite a known ISRC with NULL

-- track_artists: one row per credit, in the order Spotify lists the artists
INSERT OR IGNORE INTO track_artists (track_id, artist_id, artist_order)
SELECT track_id, artist_id, artist_order
FROM stg_track_artists
WHERE track_id IS NOT NULL AND artist_id IS NOT NULL;

-- plays: append-only history; plays of local files are logged, not loaded
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_plays', user_id || ' ' || played_at, 'missing track_id (local file)'
FROM stg_plays
WHERE track_id IS NULL;

INSERT OR IGNORE INTO plays (user_id, played_at, track_id, context_type, run_id)
SELECT user_id, played_at, track_id, context_type, run_id
FROM stg_plays
WHERE track_id IS NOT NULL;

-- saved_tracks: one row per liked song; liked local files are logged, not loaded
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_saved_tracks', user_id || ' ' || added_at, 'missing track_id (local file)'
FROM stg_saved_tracks
WHERE track_id IS NULL;

INSERT OR IGNORE INTO saved_tracks (user_id, track_id, added_at)
SELECT user_id, track_id, added_at
FROM stg_saved_tracks
WHERE track_id IS NOT NULL;

-- top_tracks / top_artists: one snapshot per day; a second run on the same day is ignored
INSERT OR IGNORE INTO top_tracks (user_id, snapshot_date, time_range, rank, track_id)
SELECT user_id, snapshot_date, time_range, rank, track_id
FROM stg_top_tracks;

INSERT OR IGNORE INTO top_artists (user_id, snapshot_date, time_range, rank, artist_id)
SELECT user_id, snapshot_date, time_range, rank, artist_id
FROM stg_top_artists;

-- audio_features: ReccoBeats is a small third-party API, so a value outside its valid
-- range is logged and skipped rather than failing the whole load on the CHECKs
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_audio_features', track_id, 'audio feature out of range'
FROM stg_audio_features
WHERE acousticness NOT BETWEEN 0 AND 1 OR danceability NOT BETWEEN 0 AND 1
   OR energy NOT BETWEEN 0 AND 1 OR instrumentalness NOT BETWEEN 0 AND 1
   OR liveness NOT BETWEEN 0 AND 1 OR speechiness NOT BETWEEN 0 AND 1
   OR valence NOT BETWEEN 0 AND 1 OR tempo <= 0 OR loudness > 0
   OR key NOT BETWEEN -1 AND 11 OR mode NOT IN (0, 1);

-- Skip the rows just logged. This filters on dropped_rows rather than NOT (<condition>):
-- a NULL feature makes the condition NULL, and the row would be neither logged nor loaded.
INSERT INTO audio_features (track_id, acousticness, danceability, energy, instrumentalness,
                            key, liveness, loudness, mode, speechiness, tempo, valence)
SELECT track_id, acousticness, danceability, energy, instrumentalness,
       key, liveness, loudness, mode, speechiness, tempo, valence
FROM stg_audio_features
WHERE track_id NOT IN (
    SELECT row_key FROM dropped_rows
    WHERE source_table = 'stg_audio_features'
      AND run_id IN (SELECT run_id FROM stg_audio_features)
)
ON CONFLICT (track_id) DO UPDATE SET
    acousticness     = excluded.acousticness,
    danceability     = excluded.danceability,
    energy           = excluded.energy,
    instrumentalness = excluded.instrumentalness,
    key              = excluded.key,
    liveness         = excluded.liveness,
    loudness         = excluded.loudness,
    mode             = excluded.mode,
    speechiness      = excluded.speechiness,
    tempo            = excluded.tempo,
    valence          = excluded.valence;

-- deezer_tracks: Deezer reports an unknown rank or BPM as 0 (about half the BPMs),
-- so 0 is stored as NULL
INSERT INTO deezer_tracks (track_id, deezer_id, rank, bpm)
SELECT track_id, deezer_id, NULLIF(rank, 0), NULLIF(bpm, 0)
FROM stg_deezer_tracks
WHERE true
ON CONFLICT (track_id) DO UPDATE SET
    deezer_id = excluded.deezer_id,
    rank      = excluded.rank,
    bpm       = excluded.bpm;
