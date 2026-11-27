# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two audiences, equally important:

- The professor and classmates in MLDS 400 (Northwestern, Fall 2026), seeing the dashboard
  during the final presentation and while grading. They don't know our data and need each
  view to explain itself quickly.
- The four of us on the team (Alex, Gloria, Lily, Yash), coming back to it to look at our own
  listening as the pipeline keeps collecting data.

## Product Purpose

A dashboard on top of our Spotify listening pipeline. It has two jobs:

- Insight: show each person's listening habits (Individual tab) and how the listeners differ
  from each other: who is similar, who is unique, what they share (Compare tab).
- Proof: make the data engineering underneath visible. The data is real, refreshed by runs
  twice a day, cleaned and checked, and joined across Spotify, ReccoBeats and Deezer.

Success means a grader understands both the findings and the pipeline behind them without
us having to explain every chart.

## Operating Context

- Shown live on a classroom projector during the final presentation, and opened on laptops
  by the team.
- Runs locally with `python dashboard/app.py` and reads `data/spotify.db` read-only. There is
  no hosting and no login.
- The data comes from an Airflow DAG (`spotify_daily`) that runs at 9 AM and 9 PM Chicago time.

## Capabilities and Constraints

- Flask with Blueprints (one per tab in `dashboard/views/`), Jinja templates, and Plotly
  charts. Keep this stack: no new frontend framework and no build step.
- It must be readable on a projector: large enough text and strong contrast, and charts that
  can be read from the back of a room.
- Data limits we have to work with:
  - Spotify Development Mode gives no genres, no popularity and no audio features. Audio
    features come from ReccoBeats and popularity rank from Deezer, and not every track has
    either one.
  - Spotify only returns the last 50 plays per pull, so play history only starts from when
    each account was added.
  - The app allows at most 5 accounts; there are 4 right now.
- Times are stored in UTC and shown in Chicago time.

## Evidence on Hand

- The real database: `data/spotify.db` (4 listeners and 524 plays as of 2026-11-24).
- Nothing else exists yet (no screenshots, write-up or user feedback). Don't invent findings
  or numbers; every figure on the page comes from the database.

## Product Principles

1. Every chart answers a question someone would actually ask, and its title says what that
   question is.
2. Be honest about the data: show where coverage is thin (missing audio features, short play
   history) instead of hiding it.
3. It has to work from across a room: put readability ahead of density.
4. Keep the pipeline visible: show where the data came from and how fresh it is.

## Accessibility & Inclusion

Must be readable on a projector (high contrast and legible type sizes). No other specific
requirement has been set.
