"""Run the Spotify pipeline end to end, in the order the src/ files are numbered:

    01_ingest.py   pull the APIs and save raw JSON          (skipped when rebuilding)
    02_schema.sql  create the final tables if they're missing
    03_stage.py    flatten the raw JSON into stg_* tables
    04_clean.sql   clean the staging tables into the final tables
    05_checks.sql  data quality checks

Usage (from the repo root):
    python src/pipeline.py                     full run: call the APIs, then load
    python src/pipeline.py data/raw/<run_id>   rebuild from saved raw files, no API calls

Exits with an error if any data quality check fails.
"""

import importlib
import re
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("data/spotify.db")
SRC_DIR = Path(__file__).parent  # the numbered steps sit next to this script

FINAL_TABLES = [
    "users", "albums", "artists", "tracks", "track_artists", "plays", "saved_tracks",
    "top_tracks", "top_artists", "audio_features", "deezer_tracks", "dropped_rows",
]


def load_step(name):
    """Import a numbered Python step, e.g. "03_stage".

    A module name can't start with a digit in an import statement, so it's loaded
    by name instead.
    """
    return importlib.import_module(name)


def run_sql_file(conn, name):
    """Run every statement in src/<name>."""
    conn.executescript((SRC_DIR / name).read_text())


def run_checks(conn):
    """Run each check in 05_checks.sql, print PASS or FAIL, and return how many failed."""
    checks = re.split(r"^-- check:", (SRC_DIR / "05_checks.sql").read_text(), flags=re.MULTILINE)
    failed = 0
    for check in checks[1:]:  # the text before the first check is the file's header
        name, query = check.split("\n", 1)
        bad_rows = conn.execute(query).fetchone()[0]
        print(f"{'PASS' if bad_rows == 0 else 'FAIL'}  {name.strip()}  ({bad_rows})")
        failed += bad_rows != 0
    return failed


def print_row_counts(conn):
    """Print how many rows each final table holds."""
    for table in FINAL_TABLES:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table:<15} {count:>7}")


def main():
    """Run the pipeline once, from the APIs or from a saved raw folder."""
    if len(sys.argv) > 1:
        run_dir = Path(sys.argv[1])
    else:
        # Only imported on a full run, so a rebuild from raw files never needs a Spotify login
        run_dir = load_step("01_ingest").main()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite leaves foreign keys off by default
    try:
        run_sql_file(conn, "02_schema.sql")
        load_step("03_stage").main(run_dir, conn)
        run_sql_file(conn, "04_clean.sql")
        conn.commit()

        failed = run_checks(conn)
        print_row_counts(conn)
    finally:
        conn.close()

    if failed:
        sys.exit(f"{failed} check(s) failed")


if __name__ == "__main__":
    main()
