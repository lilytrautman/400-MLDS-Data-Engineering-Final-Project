-- Data quality checks, run by pipeline.py after every load.
--
-- Each check is a comment line naming it, followed by one query that returns a
-- single number: how many rows break the rule. 0 means the check passes.
--
-- Primary keys, NOT NULL and CHECK constraints are already enforced by 02_schema.sql.
-- The orphan checks back up the foreign keys, which SQLite only enforces when
-- PRAGMA foreign_keys is on (it's off by default, e.g. in the sqlite3 shell).

-- check: plays point at real tracks
SELECT COUNT(*)
FROM plays p
LEFT JOIN tracks t ON p.track_id = t.track_id
WHERE t.track_id IS NULL;

-- check: every staged track was loaded
SELECT COUNT(*)
FROM stg_tracks
WHERE track_id IS NOT NULL
  AND track_id NOT IN (SELECT track_id FROM tracks);

-- check: no plays in the future
SELECT COUNT(*)
FROM plays
WHERE played_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now');

-- check: this pull overlaps the previous one (no plays lost between runs)
-- Spotify returns the last 50 plays. If all 50 were new and older plays exist, the
-- pulls didn't overlap, and any plays in between were never captured.
SELECT CASE
    WHEN (SELECT COUNT(*) FROM stg_plays WHERE track_id IS NOT NULL) = 50
     AND (SELECT COUNT(*) FROM plays WHERE run_id IN (SELECT run_id FROM stg_plays)) = 50
     AND EXISTS (SELECT 1 FROM plays WHERE run_id NOT IN (SELECT run_id FROM stg_plays))
    THEN 1 ELSE 0
END;

-- check: saved tracks point at real tracks
SELECT COUNT(*)
FROM saved_tracks c
LEFT JOIN tracks p ON c.track_id = p.track_id
WHERE p.track_id IS NULL;

-- check: top tracks point at real tracks
SELECT COUNT(*)
FROM top_tracks c
LEFT JOIN tracks p ON c.track_id = p.track_id
WHERE p.track_id IS NULL;

-- check: top artists point at real artists
SELECT COUNT(*)
FROM top_artists c
LEFT JOIN artists p ON c.artist_id = p.artist_id
WHERE p.artist_id IS NULL;

-- check: track credits point at real tracks
SELECT COUNT(*)
FROM track_artists c
LEFT JOIN tracks p ON c.track_id = p.track_id
WHERE p.track_id IS NULL;

-- check: track credits point at real artists
SELECT COUNT(*)
FROM track_artists c
LEFT JOIN artists p ON c.artist_id = p.artist_id
WHERE p.artist_id IS NULL;

-- check: audio features point at real tracks
SELECT COUNT(*)
FROM audio_features c
LEFT JOIN tracks p ON c.track_id = p.track_id
WHERE p.track_id IS NULL;

-- check: deezer rows point at real tracks
SELECT COUNT(*)
FROM deezer_tracks c
LEFT JOIN tracks p ON c.track_id = p.track_id
WHERE p.track_id IS NULL;

-- check: tracks point at real albums
SELECT COUNT(*)
FROM tracks tr
LEFT JOIN albums a ON tr.album_id = a.album_id
WHERE tr.album_id IS NOT NULL AND a.album_id IS NULL;

-- check: every staged audio feature row was loaded or dropped
SELECT COUNT(*)
FROM stg_audio_features
WHERE track_id IS NOT NULL
  AND track_id NOT IN (SELECT track_id FROM audio_features)
  AND track_id NOT IN (SELECT row_key FROM dropped_rows WHERE source_table = 'stg_audio_features');

-- check: every track has an artist
SELECT COUNT(*)
FROM tracks
WHERE track_id NOT IN (SELECT track_id FROM track_artists);

-- check: no liked songs added in the future
SELECT COUNT(*)
FROM saved_tracks
WHERE added_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now');
