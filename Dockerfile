# Airflow plus the pipeline's packages, the project's only image. The pipeline code
# isn't copied in: docker-compose.yml mounts src/, DAG/, data/ and tokens/ so edits
# show up live.
FROM apache/airflow:slim-3.3.2-python3.13
COPY requirements.txt /requirements.txt
# Naming Airflow's own version stops pip from upgrading or downgrading it
RUN pip install --no-cache-dir "apache-airflow==3.3.2" -r /requirements.txt
# Folder for Airflow's run history, created as the airflow user so it can write there
RUN mkdir -p /opt/airflow/db
