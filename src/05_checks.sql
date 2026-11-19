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

-- check: this pull overlaps the previous one (no plays lost between runs)
-- Spotify returns the last 50 plays. If all 50 were new and older plays exist, the
-- pulls didn't overlap, and any plays in between were never captured.
SELECT CASE
    WHEN (SELECT COUNT(*) FROM stg_plays WHERE track_id IS NOT NULL) = 50
     AND (SELECT COUNT(*) FROM plays WHERE run_id IN (SELECT run_id FROM stg_plays)) = 50
     AND EXISTS (SELECT 1 FROM plays WHERE run_id NOT IN (SELECT run_id FROM stg_plays))
    THEN 1 ELSE 0
END;
