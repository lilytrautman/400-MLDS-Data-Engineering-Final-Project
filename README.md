# Spotify Listening Pipeline

🚧 Work in progress. MLDS 400 final project, Northwestern University, Fall 2026.

A data pipeline that pulls listening data from the Spotify API, adds audio features
(ReccoBeats) and popularity (Deezer), and stores it all in a SQLite database for analysis.

## Status

- Done: Project setup (Docker, pinned packages)
- Done: Ingestion, cleaning and data quality checks
- Done: Pulls for every team member's account
- Done: Daily runs with Airflow
- Todo: Analysis and dashboard
- Maybe later: ML

## Data sources

- **Spotify Web API**: recently played, top tracks and artists, liked songs (needs a free developer app)
- **ReccoBeats**: audio features like energy, mood and tempo (no key)
- **Deezer**: popularity rank (no key)

## Run it

1. Create an app in the [Spotify developer dashboard](https://developer.spotify.com/dashboard)
   with the redirect URI `http://127.0.0.1:8888/callback`. Add every account you want to
   pull under **User Management** (up to 5).
2. Copy `.env.example` to `.env` and add your app's client id and secret.
3. Install the packages and log each account in once:
   ```
   pip install -r requirements.txt
   python src/login.py <name>             # on this computer (opens a browser)
   python src/login.py <name> --remote    # prints a link to send to someone else
   ```
4. Run the pipeline once:
   ```
   python src/pipeline.py
   ```
5. Or run it every day with Airflow:
   ```
   docker compose up -d --build
   ```
   Open http://localhost:8080, unpause `spotify_daily`, and it runs twice a day at 9 AM and 9 PM
   (Chicago time). Stop it with `docker compose down`.

The database ends up in `data/spotify.db`. Tokens (`tokens/`), keys and data stay out of git.

To rebuild the database from saved raw files without calling the APIs:
`python src/pipeline.py data/raw/<run_id>`.

## Dashboard

A Flask app that reads `data/spotify.db` (read-only). It doesn't need API keys or Airflow,
only a copy of the database in `data/`.

```
pip install -r requirements.txt
python dashboard/app.py
```

Open http://127.0.0.1:5000. The **Individual** tab shows one listener's trends and the
**Compare** tab compares listeners. Each tab is a Flask Blueprint in `dashboard/views/`
with its own template; `base.html`, `style.css` and `static/charts.js` hold the shared
layout, colors and Plotly settings.

## Team

Alex Miller, Gloria Ye, Lily Trautman, Yash Ahlawat
