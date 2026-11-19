-- Clean the staging tables (stg_*) and load them into the final tables.
--
-- Each table goes through the same three steps:
--   1. Rows that can't be loaded are logged to dropped_rows with a reason.
--   2. Duplicates are removed on the table's primary key (ROW_NUMBER, keep rn = 1).
--   3. Rows are upserted, so loading the same data twice never creates duplicates.
--      History tables use INSERT OR IGNORE (a stored row is never changed);
--      descriptive tables use ON CONFLICT DO UPDATE (the latest values win).
--
-- Parent tables load first, so foreign keys always point at rows that exist.
--
-- In INSERT ... SELECT ... ON CONFLICT, the SELECT needs a WHERE clause, or SQLite
-- reads ON as the start of a join. "WHERE true" is used where nothing is filtered.

-- users: one row per account; profile fields are refreshed on every run
INSERT INTO users (user_id, display_name, country)
SELECT user_id, display_name, country
FROM stg_users
WHERE true
ON CONFLICT (user_id) DO UPDATE SET
    display_name = excluded.display_name,
    country      = excluded.country;

-- albums: removed songs come with an empty album name and release date '0000'
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_albums', name, 'missing album_id'
FROM stg_albums
WHERE album_id IS NULL;

-- Keep one row per album, preferring one that has a release date
INSERT INTO albums (album_id, name, album_type, release_date, release_precision, release_year)
SELECT album_id,
       NULLIF(TRIM(name), ''),
       album_type,
       NULLIF(release_date, '0000'),
       release_precision,
       CASE WHEN release_date GLOB '[0-9][0-9][0-9][0-9]*' AND release_date <> '0000'
            THEN CAST(substr(release_date, 1, 4) AS INTEGER) END
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY album_id ORDER BY release_date IS NULL) AS rn
    FROM stg_albums
    WHERE album_id IS NOT NULL
)
WHERE rn = 1
ON CONFLICT (album_id) DO UPDATE SET
    name              = excluded.name,
    album_type        = excluded.album_type,
    release_date      = excluded.release_date,
    release_precision = excluded.release_precision,
    release_year      = excluded.release_year;

-- artists: the same artist appears in many credits; a real name wins over an empty one
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_artists', name, 'missing artist_id'
FROM stg_artists
WHERE artist_id IS NULL;

INSERT INTO artists (artist_id, name)
SELECT artist_id, NULLIF(TRIM(name), '')
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY artist_id ORDER BY name = '' OR name IS NULL) AS rn
    FROM stg_artists
    WHERE artist_id IS NOT NULL
)
WHERE rn = 1
ON CONFLICT (artist_id) DO UPDATE SET
    name = COALESCE(excluded.name, artists.name);

-- tracks: local files and removed songs have no Spotify id and can't be loaded
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_tracks', name, 'missing track_id (local file or removed song)'
FROM stg_tracks
WHERE track_id IS NULL;

-- The same track appears in several files; keep one row, preferring one with an ISRC
INSERT INTO tracks (track_id, name, album_id, duration_ms, explicit, isrc)
SELECT track_id,
       NULLIF(TRIM(name), ''),                       -- removed songs have an empty name
       album_id,
       NULLIF(CAST(duration_ms AS INTEGER), 0),      -- ...and a duration of 0
       CAST(explicit AS INTEGER),
       isrc
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY track_id ORDER BY isrc IS NULL) AS rn
    FROM stg_tracks
    WHERE track_id IS NOT NULL
)
WHERE rn = 1
ON CONFLICT (track_id) DO UPDATE SET
    name        = excluded.name,
    album_id    = excluded.album_id,
    duration_ms = excluded.duration_ms,
    explicit    = excluded.explicit,
    isrc        = COALESCE(excluded.isrc, tracks.isrc);  -- never overwrite a known ISRC with NULL

-- track_artists: credit links; the same pair shows up once per file the track is in
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_track_artists', artist_name, 'missing track_id or artist_id'
FROM stg_track_artists
WHERE track_id IS NULL OR artist_id IS NULL;

INSERT OR IGNORE INTO track_artists (track_id, artist_id, artist_order)
SELECT track_id, artist_id, artist_order
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY track_id, artist_id ORDER BY artist_order) AS rn
    FROM stg_track_artists
    WHERE track_id IS NOT NULL AND artist_id IS NOT NULL
)
WHERE rn = 1;

-- plays: append-only history. Each daily pull overlaps the previous one; plays already
-- stored are ignored, so overlapping pulls never double count
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_plays', played_at, 'missing track_id (local file or removed song)'
FROM stg_plays
WHERE track_id IS NULL;

INSERT OR IGNORE INTO plays (user_id, played_at, track_id, context_type, run_id)
SELECT user_id, played_at, track_id, context_type, run_id
FROM stg_plays
WHERE track_id IS NOT NULL;

-- saved_tracks: one row per liked song; already-stored likes are ignored
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_saved_tracks', added_at, 'missing track_id (local file or removed song)'
FROM stg_saved_tracks
WHERE track_id IS NULL;

INSERT OR IGNORE INTO saved_tracks (user_id, track_id, added_at)
SELECT user_id, track_id, added_at
FROM stg_saved_tracks
WHERE track_id IS NOT NULL;

-- top_tracks / top_artists: one snapshot per day; a second run on the same day is ignored
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_top_tracks', time_range || ' #' || rank, 'missing track_id'
FROM stg_top_tracks
WHERE track_id IS NULL;

INSERT OR IGNORE INTO top_tracks (user_id, snapshot_date, time_range, rank, track_id)
SELECT user_id, snapshot_date, time_range, rank, track_id
FROM stg_top_tracks
WHERE track_id IS NOT NULL;

INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_top_artists', time_range || ' #' || rank, 'missing artist_id'
FROM stg_top_artists
WHERE artist_id IS NULL;

INSERT OR IGNORE INTO top_artists (user_id, snapshot_date, time_range, rank, artist_id)
SELECT user_id, snapshot_date, time_range, rank, artist_id
FROM stg_top_artists
WHERE artist_id IS NOT NULL;

-- audio_features: rows with impossible values are logged instead of loaded
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_audio_features', track_id, 'audio feature out of range'
FROM stg_audio_features
WHERE acousticness NOT BETWEEN 0 AND 1 OR danceability NOT BETWEEN 0 AND 1 OR energy NOT BETWEEN 0 AND 1 OR instrumentalness NOT BETWEEN 0 AND 1 OR liveness NOT BETWEEN 0 AND 1 OR speechiness NOT BETWEEN 0 AND 1 OR valence NOT BETWEEN 0 AND 1
   OR tempo <= 0 OR loudness > 0;

-- Filter on dropped_rows rather than NOT (<condition>): a NULL feature would make the
-- condition NULL, and the row would be neither logged nor loaded
INSERT INTO audio_features (track_id, acousticness, danceability, energy, instrumentalness, key, liveness, loudness, mode, speechiness, tempo, valence)
SELECT track_id, acousticness, danceability, energy, instrumentalness, key, liveness, loudness, mode, speechiness, tempo, valence
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY track_id) AS rn
    FROM stg_audio_features
)
WHERE rn = 1
  AND track_id NOT IN (
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

-- deezer_tracks: a negative rank can't be right
INSERT OR IGNORE INTO dropped_rows (run_id, source_table, row_key, reason)
SELECT run_id, 'stg_deezer_tracks', track_id, 'negative Deezer rank'
FROM stg_deezer_tracks
WHERE rank < 0;

-- Deezer reports an unknown rank or BPM as 0 (about half the BPMs), so 0 is stored as NULL
INSERT INTO deezer_tracks (track_id, deezer_id, rank, bpm)
SELECT track_id, deezer_id, NULLIF(rank, 0), NULLIF(bpm, 0)
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY track_id) AS rn
    FROM stg_deezer_tracks
)
WHERE rn = 1
  AND track_id NOT IN (
      SELECT row_key FROM dropped_rows
      WHERE source_table = 'stg_deezer_tracks'
        AND run_id IN (SELECT run_id FROM stg_deezer_tracks)
  )
ON CONFLICT (track_id) DO UPDATE SET
    deezer_id = excluded.deezer_id,
    rank      = excluded.rank,
    bpm       = excluded.bpm;
