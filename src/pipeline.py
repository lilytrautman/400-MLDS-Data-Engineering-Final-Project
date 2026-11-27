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
The Airflow DAG in DAG/ runs one step per task with python src/pipeline.py <step>.
"""

import importlib
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent  # the numbered steps sit next to this script
DB_PATH = SRC_DIR.parent / "data" / "spotify.db"
RAW_DIR = SRC_DIR.parent / "data" / "raw"
STEPS = ["create_tables", "stage", "clean", "check"]

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


def connect():
    """Open the database with foreign keys on (SQLite leaves them off by default)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def latest_run_dir():
    """The newest run folder in data/raw. Run ids are UTC times, so they sort by name."""
    return max(path for path in RAW_DIR.iterdir() if path.is_dir())


def run_sql_file(name):
    """Run every statement in src/<name> and commit."""
    with closing(connect()) as conn:
        conn.executescript((SRC_DIR / name).read_text())
        conn.commit()


MIGRATIONS = [
    ("users", "first_name", "TEXT"),
    ("users", "last_name", "TEXT"),
    ("users", "image_url", "TEXT"),
    ("albums", "image_url", "TEXT"),
    ("artists", "image_url", "TEXT"),
]


def create_tables():
    """Step 02: create the final tables if they're missing, and add any new columns."""
    run_sql_file("02_schema.sql")
    # CREATE TABLE IF NOT EXISTS won't add columns to an existing database
    with closing(connect()) as conn:
        for table, column, col_type in MIGRATIONS:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()


def stage(run_dir):
    """Step 03: flatten one run's raw JSON into the stg_* tables."""
    with closing(connect()) as conn:
        load_step("03_stage").main(run_dir, conn)
        conn.commit()


def clean():
    """Step 04: clean the staging tables into the final tables."""
    run_sql_file("04_clean.sql")


def check():
    """Step 05: run each check in 05_checks.sql, print PASS or FAIL and the row counts.

    Returns how many checks failed.
    """
    checks = re.split(r"^-- check:", (SRC_DIR / "05_checks.sql").read_text(), flags=re.MULTILINE)
    failed = 0
    with closing(connect()) as conn:
        for check_text in checks[1:]:  # the text before the first check is the file's header
            name, query = check_text.split("\n", 1)
            bad_rows = conn.execute(query).fetchone()[0]
            print(f"{'PASS' if bad_rows == 0 else 'FAIL'}  {name.strip()}  ({bad_rows})")
            failed += bad_rows != 0
        for table in FINAL_TABLES:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table:<15} {count:>7}")
    return failed


def main():
    """Run the whole pipeline, or one step by name.

        python src/pipeline.py                     everything, starting with a new pull
        python src/pipeline.py data/raw/<run_id>   everything, from a saved raw folder
        python src/pipeline.py stage               one step (the Airflow DAG runs these)
    """
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg in STEPS:
        steps, run_dir = [arg], None  # a single stage step uses the newest raw folder
    elif arg:
        steps, run_dir = STEPS, Path(arg)
    else:
        # Only imported on a full run, so a rebuild from raw files never needs a Spotify login
        steps, run_dir = STEPS, load_step("01_ingest").main()

    if "create_tables" in steps:
        create_tables()
    if "stage" in steps:
        stage(run_dir or latest_run_dir())
    if "clean" in steps:
        clean()
    if "check" in steps:
        failed = check()
        if failed:
            sys.exit(f"{failed} check(s) failed")


if __name__ == "__main__":
    main()
