"""Twice-daily Spotify pipeline in Airflow, one task per step:

    ingest -> create_tables -> stage -> clean -> check

Each task runs one command, the same one you can run in the terminal. Airflow
records how long each task took and whether it passed, measuring the pipeline
from start to finish.
"""

from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

REPO = "/opt/project"  # where docker-compose.yml mounts the repo

with DAG(
    dag_id="spotify_daily",
    start_date=datetime(2026, 11, 20),
    schedule="0 9,21 * * *",  # twice a day at 9 AM and 9 PM (Chicago time, set in docker-compose.yml)
    catchup=False,  # after the Mac was off, run once, not once per missed day
    max_active_runs=1,  # two runs at once would write to the same database
    default_args={
        "owner": "airflow",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["spotify"],
) as dag:
    ingest = BashOperator(
        task_id="ingest",
        bash_command=f"cd {REPO} && python src/01_ingest.py",
    )

    create_tables = BashOperator(
        task_id="create_tables",
        bash_command=f"cd {REPO} && python src/pipeline.py create_tables",
    )

    stage = BashOperator(
        task_id="stage",
        bash_command=f"cd {REPO} && python src/pipeline.py stage",
    )

    clean = BashOperator(
        task_id="clean",
        bash_command=f"cd {REPO} && python src/pipeline.py clean",
    )

    check = BashOperator(
        task_id="check",
        bash_command=f"cd {REPO} && python src/pipeline.py check",
        retries=0,  # a failed check won't pass by running it again
    )

    ingest >> create_tables >> stage >> clean >> check
