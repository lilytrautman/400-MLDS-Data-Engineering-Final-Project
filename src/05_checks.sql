-- Data quality checks, run by pipeline.py after every load.
--
-- Each check is a comment line naming it, followed by one query that returns a
-- single number: how many rows break the rule. 0 means the check passes.
--
-- Primary keys, NOT NULL and CHECK constraints are enforced by 02_schema.sql when the
-- data is loaded, so they aren't repeated here.

-- check: no foreign key points at a missing row
SELECT COUNT(*) FROM pragma_foreign_key_check;

-- check: every track has at least one artist
SELECT COUNT(*)
FROM tracks
WHERE track_id NOT IN (SELECT track_id FROM track_artists);

-- check: no plays or likes in the future
SELECT (SELECT COUNT(*) FROM plays WHERE played_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
     + (SELECT COUNT(*) FROM saved_tracks WHERE added_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

-- check: every account's pull overlaps its previous one (no plays lost between runs)
-- Spotify returns the last 50 plays. If all 50 were new and older plays exist, the
-- pulls didn't overlap, and any plays in between were never captured. Counts the
-- accounts where that happened.
SELECT COUNT(*)
FROM (
    SELECT s.user_id, MAX(s.run_id) AS run_id
    FROM stg_plays AS s
    WHERE s.track_id IS NOT NULL
    GROUP BY s.user_id
    HAVING COUNT(*) = 50
) AS pulled
WHERE (SELECT COUNT(*) FROM plays AS p
       WHERE p.user_id = pulled.user_id AND p.run_id = pulled.run_id) = 50
  AND EXISTS (SELECT 1 FROM plays AS p
              WHERE p.user_id = pulled.user_id AND p.run_id <> pulled.run_id);

-- check: every account already in the database was pulled in this run
-- 01_ingest.py skips an account whose pull fails (usually an expired login) so the
-- others still load. Spotify only keeps the last 50 plays, so a skipped account loses
-- plays every day it goes unnoticed. This makes the run fail instead. The ingest log
-- names the account; log it back in with python src/login.py <name>.
-- To drop someone from the project for good, delete their rows from the database too.
SELECT COUNT(*)
FROM users
WHERE user_id NOT IN (SELECT user_id FROM stg_users);
