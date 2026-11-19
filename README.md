# Spotify Listening Pipeline

🚧 Work in progress. MLDS 400 final project, Northwestern University, Fall 2026.

A data pipeline that pulls listening data from the Spotify API, adds audio features
(ReccoBeats) and popularity (Deezer), and stores it all in a SQLite database for analysis.

## Status

- Done: Project setup (Docker, pinned packages)
- Working: Ingestion and cleaning
- Todo: Scheduling, analysis and dashboard

## Data sources

- **Spotify Web API**: recently played, top tracks and artists, liked songs (needs a free developer app)
- **ReccoBeats**: audio features like energy, mood and tempo (no key)
- **Deezer**: popularity rank (no key)

## Run it

1. Create an app in the [Spotify developer dashboard](https://developer.spotify.com/dashboard)
   with the redirect URI `http://127.0.0.1:8888/callback`.
2. Copy `.env.example` to `.env` and add your app's client id and secret.
3. Log in once (opens a browser):
   ```bash
   pip install -r requirements.txt
   python src/ingest.py
   ```
4. Run the pipeline:
   ```bash
   docker build -t spotify-pipeline .
   docker run --rm --env-file .env -v "$PWD/data:/app/data" -v "$PWD/.cache:/app/.cache" spotify-pipeline
   ```

The database ends up in `data/spotify.db`. Your keys and data stay out of git.

## Team

Alex Miller, Gloria Ye, Lily Trautman, Yash Ahlawat
