"""Compare tab: tastes and habits across all connected users (Gloria).

Everything the page shows is computed here from data/spotify.db and handed to
compare.html as one JSON blob; the script in compare.html draws it. All the rules
(listener profiles, head-to-head results, similarity scores) live in
this file so they're deterministic and easy to check.

Where the data comes from (checked against src/):
  - liked songs, plays, top artists, explicit flag, album release year: Spotify
  - audio features (danceability, energy, ...): ReccoBeats, not Spotify, and not
    every track has them
  - popularity: Deezer's rank, matched by ISRC (higher = more popular)
  - genres: not available (Spotify's Dev Mode no longer returns them)

How differences are judged, everywhere on the page:
  - song-level values (release year, popularity, audio features) use Cliff's delta,
    the chance a random song from A beats a random song from B minus the reverse.
    |delta| < 0.147 counts as negligible (Romano et al., 2006).
  - shares (explicit, artists per song, night plays) use Cohen's h;
    |h| < 0.2 counts as negligible (Cohen, 1988).
  A difference below those cut-offs is shown as "too close to call".
"""

import math
import sqlite3
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
from statistics import mean, median, quantiles

from flask import Blueprint, render_template

from db import DB_PATH, LOCAL_TZ, get_users, query

bp = Blueprint("compare", __name__, url_prefix="/compare")

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

NEGLIGIBLE_DELTA = 0.147  # Cliff's delta below this = too close to call
NEGLIGIBLE_H = 0.2        # Cohen's h below this = too close to call
MIN_SONGS = 20            # songs with a value needed before comparing a listener
MIN_PLAYS = 20            # recorded plays needed before judging listening times
MIN_DAYS = 3              # ...and the plays must cover at least this many days

# Audio features from ReccoBeats: (column, label, what high and low mean)
FEATURES = [
    ("danceability", "Danceability", "How suitable a track is for dancing, from rhythm and beat strength."),
    ("energy", "Energy", "How intense and active a track feels: fast, loud and noisy scores high."),
    ("valence", "Positivity", "How positive a track sounds (Spotify calls this valence): high is cheerful, low is darker or sadder-sounding."),
    ("acousticness", "Acousticness", "How likely a track is acoustic rather than electronic."),
    ("speechiness", "Speechiness", "How much of a track is spoken words, like rap verses."),
    ("instrumentalness", "Instrumentalness", "How likely a track has no vocals."),
]
FEATURE_KEYS = [f[0] for f in FEATURES]

# Every metric the page compares. "dist" metrics are one value per liked song,
# "prop" metrics are a share (count / total).
METRICS = {
    "year": {"kind": "dist", "stat": "median", "label": "Median release year",
             "more": "newer", "less": "older", "source": "Spotify album release dates"},
    "popularity": {"kind": "dist", "stat": "median", "label": "Median Deezer popularity",
                   "more": "more popular", "less": "less popular",
                   "source": "Deezer rank matched by ISRC (higher = more popular right now)"},
    **{
        key: {"kind": "dist", "stat": "mean", "label": f"Average {label.lower()}",
              "more": f"higher in {label.lower()}", "less": f"lower in {label.lower()}",
              "source": "ReccoBeats audio features"}
        for key, label, _ in FEATURES
    },
    "explicit": {"kind": "prop", "label": "Explicit liked songs", "min": MIN_SONGS,
                 "source": "Spotify explicit flag"},
    "diversity": {"kind": "prop", "label": "Different artists per 100 liked songs", "min": MIN_SONGS,
                  "source": "Spotify (main credited artist of each liked song)"},
    "night": {"kind": "prop", "label": "Recorded plays from 10 PM to 5 AM", "min": MIN_PLAYS,
              "source": "Spotify recently played (only a few days so far)"},
}

# Listener profiles: (metric, +1 = clearly higher than the rest / -1 = lower, label, description).
# A listener gets the label for the metric where they differ most from everyone
# else combined, and only if that difference is beyond the negligible cut-off.
TRAITS = [
    ("year", -1, "Older catalog", "Liked songs skew toward older releases than the rest of the group."),
    ("year", +1, "Recent releases", "Liked songs skew toward newer releases than the rest of the group."),
    ("popularity", -1, "Niche-leaning", "Liked songs are less popular on Deezer than the rest of the group's."),
    ("popularity", +1, "Mainstream-leaning", "Liked songs are more popular on Deezer than the rest of the group's."),
    ("danceability", +1, "High danceability", "Liked songs score higher on danceability than the rest of the group's."),
    ("danceability", -1, "Low danceability", "Liked songs score lower on danceability than the rest of the group's."),
    ("energy", +1, "High energy", "Liked songs score higher on energy than the rest of the group's."),
    ("energy", -1, "Low energy", "Liked songs score lower on energy than the rest of the group's."),
    ("valence", +1, "Upbeat sound", "Liked songs sound more positive (higher valence) than the rest of the group's."),
    ("valence", -1, "Darker sound", "Liked songs sound less positive (lower valence) than the rest of the group's."),
    ("acousticness", +1, "High acousticness", "Liked songs are more acoustic than the rest of the group's."),
    ("speechiness", +1, "Rap and spoken word", "Liked songs contain more spoken words, such as rap, than the rest of the group's."),
    ("instrumentalness", +1, "Instrumental-leaning", "Liked songs have fewer vocals than the rest of the group's."),
    ("explicit", +1, "High explicit share", "A larger share of liked songs are marked explicit."),
    ("explicit", -1, "Low explicit share", "A smaller share of liked songs are marked explicit."),
    ("diversity", +1, "Broad artist range", "Liked songs are spread across more different artists."),
    ("diversity", -1, "Artist-concentrated", "Liked songs are concentrated on fewer artists."),
    ("night", +1, "Late-night listening", "A larger share of recorded plays fall between 10 PM and 5 AM."),
]
FALLBACK_TRAIT = ("Balanced profile", "No metric differs clearly from the rest of the group.")

# Head-to-head categories: (metric, which side is reported: +1 higher / -1 lower, question, result label)
BATTLES = [
    ("popularity", -1, "Which library is less mainstream?", "Less mainstream"),
    ("year", -1, "Which library is older?", "Older releases"),
    ("danceability", +1, "Which library is more danceable?", "More danceable"),
    ("energy", +1, "Which library has higher energy?", "Higher energy"),
    ("valence", +1, "Which library sounds happier?", "Happier sound"),
    ("explicit", +1, "Which library has more explicit songs?", "More explicit"),
    ("diversity", +1, "Which library has more artist variety?", "More artist variety"),
]

# Compatibility score: weights of the parts (renormalised over the parts that have data)
COMPAT_WEIGHTS = {"artists": 0.4, "sound": 0.2, "era": 0.2, "mainstream": 0.2}

# Listening windows, Chicago time, for the listening-by-hour labels
WINDOWS = [
    ("night", "Night", [22, 23, 0, 1, 2, 3, 4], "10 PM–5 AM"),
    ("morning", "Morning", list(range(5, 12)), "5 AM–noon"),
    ("afternoon", "Afternoon", list(range(12, 17)), "noon–5 PM"),
    ("evening", "Evening", list(range(17, 22)), "5–10 PM"),
]
WINDOW_LABEL_SHARE = 0.40  # a window needs 40%+ of plays before it becomes a label

GALAXY_SHARED = 60  # most shared artists sent to the artist overlap network
GALAXY_SOLO = 10    # each listener's top artists only they like
TRACKS_PER_FAN = 5  # liked tracks listed per listener in the artist details panel


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def cliffs_delta(a, b):
    """P(a > b) - P(a < b) for a random value from each list, in O(n log n)."""
    if not a or not b:
        return None
    bs = sorted(b)
    n = len(bs)
    more = less = 0
    for x in a:
        more += bisect_left(bs, x)        # values in b below x
        less += n - bisect_right(bs, x)   # values in b above x
    return (more - less) / (len(a) * n)


def cohens_h(p1, p2):
    return 2 * math.asin(math.sqrt(p1)) - 2 * math.asin(math.sqrt(p2))


def to_local(ts):
    """'2026-11-20T18:19:16.410Z' (UTC) -> datetime in Chicago time."""
    return datetime.fromisoformat(ts).astimezone(LOCAL_TZ)


def fmt(key, v):
    """A metric value as text, e.g. 2019, 412,000, 0.62, 34%."""
    if v is None:
        return "—"
    if key == "year":
        return str(round(v))
    if key == "popularity":
        return f"{round(v):,}"
    if key in FEATURE_KEYS:
        return f"{v:.2f}"
    if key == "diversity":
        return f"{round(100 * v)}"
    return f"{round(100 * v)}%"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def new_listener(i, user):
    return {
        "i": i,
        "id": user["user_id"],
        "name": user["display_name"],
        "country": user.get("country"),
        "tracks": {},                    # liked track_id -> "Track – Artist"
        "dist": defaultdict(list),       # metric -> values, one per liked song
        "explicit": [0, 0],              # [explicit, songs with a known flag]
        "primary": Counter(),            # main artist_id -> liked songs
        "primary_total": 0,
        "artists": Counter(),            # any credited artist_id -> liked songs
        "artist_tracks": defaultdict(list),
        "decades": Counter(),
        "hours": [0] * 24,
        "days": set(),
        "plays": 0,
        "first_play": None,
        "last_play": None,
        "top": {},                       # time_range -> [artist names] from Spotify's top artists
    }


def load(users):
    """Read everything the page needs in a handful of queries."""
    people = [new_listener(i, u) for i, u in enumerate(users)]
    by_id = {p["id"]: p for p in people}
    names = {}  # artist_id -> name

    cols = ", ".join(f"af.{k}" for k in FEATURE_KEYS)
    for r in query(
        f"""
        SELECT s.user_id, s.track_id, COALESCE(t.name, '(removed from Spotify)') AS track,
               t.explicit, a.release_year AS year, d.rank AS popularity,
               ta.artist_id, COALESCE(ar.name, '') AS artist, {cols}
        FROM saved_tracks s
        JOIN tracks t ON t.track_id = s.track_id
        LEFT JOIN albums a ON a.album_id = t.album_id
        LEFT JOIN deezer_tracks d ON d.track_id = s.track_id
        LEFT JOIN track_artists ta ON ta.track_id = s.track_id AND ta.artist_order = 0
        LEFT JOIN artists ar ON ar.artist_id = ta.artist_id
        LEFT JOIN audio_features af ON af.track_id = s.track_id
        """
    ):
        p = by_id.get(r["user_id"])
        if not p:
            continue
        p["tracks"][r["track_id"]] = r["track"] + (f" – {r['artist']}" if r["artist"] else "")
        for key in ["year", "popularity", *FEATURE_KEYS]:
            if r[key] is not None:
                p["dist"][key].append(r[key])
        if r["year"] is not None:
            p["decades"][r["year"] // 10 * 10] += 1
        if r["explicit"] is not None:
            p["explicit"][0] += r["explicit"]
            p["explicit"][1] += 1
        if r["artist_id"]:
            p["primary"][r["artist_id"]] += 1
            p["primary_total"] += 1

    # Every credited artist, newest likes first so the details panel shows recent ones
    for r in query(
        """
        SELECT s.user_id, ta.artist_id, ar.name AS artist,
               COALESCE(t.name, '(removed from Spotify)') AS track
        FROM saved_tracks s
        JOIN track_artists ta ON ta.track_id = s.track_id
        JOIN artists ar ON ar.artist_id = ta.artist_id
        JOIN tracks t ON t.track_id = s.track_id
        ORDER BY s.added_at DESC
        """
    ):
        p = by_id.get(r["user_id"])
        if not p:
            continue
        names[r["artist_id"]] = r["artist"] or "(unknown artist)"
        p["artists"][r["artist_id"]] += 1
        if len(p["artist_tracks"][r["artist_id"]]) < TRACKS_PER_FAN:
            p["artist_tracks"][r["artist_id"]].append(r["track"])

    for r in query("SELECT user_id, played_at FROM plays"):
        p = by_id.get(r["user_id"])
        if not p:
            continue
        local = to_local(r["played_at"])
        p["hours"][local.hour] += 1
        p["days"].add(local.date())
        p["plays"] += 1
        p["first_play"] = min(p["first_play"] or local, local)
        p["last_play"] = max(p["last_play"] or local, local)

    # Spotify's own top artists, latest daily snapshot per time range
    for r in query(
        """
        SELECT ta.user_id, ta.time_range, ta.rank, a.name
        FROM top_artists ta
        JOIN artists a ON a.artist_id = ta.artist_id
        WHERE ta.snapshot_date = (SELECT MAX(x.snapshot_date) FROM top_artists x
                                  WHERE x.user_id = ta.user_id AND x.time_range = ta.time_range)
        ORDER BY ta.rank
        """
    ):
        p = by_id.get(r["user_id"])
        if p:
            p["top"].setdefault(r["time_range"], []).append(r["name"])

    return people, names


# ---------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------

def values(p, key):
    """A listener's data for one metric: a list (dist) or (count, total) (prop)."""
    if METRICS[key]["kind"] == "dist":
        return p["dist"][key]
    if key == "explicit":
        return tuple(p["explicit"])
    if key == "diversity":
        return (len(p["primary"]), p["primary_total"])
    if key == "night":
        night = sum(p["hours"][h] for h in WINDOWS[0][2])
        return (night, p["plays"])
    raise KeyError(key)


def summary(p, key):
    """One number for a listener: median/mean for dist metrics, share for prop ones."""
    m = METRICS[key]
    v = values(p, key)
    if m["kind"] == "dist":
        if len(v) < MIN_SONGS:
            return None
        return median(v) if m["stat"] == "median" else mean(v)
    count, total = v
    if key == "night" and len(p["days"]) < MIN_DAYS:
        return None
    return count / total if total >= m["min"] else None


def pooled(people):
    """Everyone in `people` merged into one pseudo-listener, for 'vs. everyone else'."""
    out = new_listener(-1, {"user_id": "", "display_name": "everyone else"})
    for p in people:
        for k, vs in p["dist"].items():
            out["dist"][k].extend(vs)
        out["explicit"][0] += p["explicit"][0]
        out["explicit"][1] += p["explicit"][1]
        out["primary_total"] += p["primary_total"]
        for h in range(24):
            out["hours"][h] += p["hours"][h]
        out["plays"] += p["plays"]
        out["days"] |= {(p["i"], d) for d in p["days"]}
    # Diversity of the group = sum of each person's distinct artists / their songs,
    # so merging libraries doesn't hide repeats across people
    out["primary"] = Counter({f"{p['i']}:{a}": n for p in people for a, n in p["primary"].items()})
    return out


def compare(a, b, key):
    """Compare two listeners on one metric.

    Returns their values, the effect size, whether it's beyond the negligible
    cut-off, and which one is higher (0 = a, 1 = b, None = too close or no data).
    """
    va, vb = summary(a, key), summary(b, key)
    out = {"key": key, "a": va, "b": vb, "a_text": fmt(key, va), "b_text": fmt(key, vb),
           "effect": None, "clear": False, "higher": None, "enough": va is not None and vb is not None}
    if not out["enough"]:
        return out
    if METRICS[key]["kind"] == "dist":
        d = cliffs_delta(values(a, key), values(b, key))
        out["effect"] = round(d, 3)
        out["clear"] = abs(d) >= NEGLIGIBLE_DELTA
        # P(random song from the higher one beats a random song from the other)
        out["superiority"] = round((1 + abs(d)) / 2, 3)
    else:
        h = cohens_h(va, vb)
        out["effect"] = round(h, 3)
        out["clear"] = abs(h) >= NEGLIGIBLE_H
    if out["clear"]:
        out["higher"] = 0 if out["effect"] > 0 else 1
    return out


def evidence(key, cmp, who, other):
    """A plain sentence explaining a comparison, from the first listener's side."""
    m = METRICS[key]
    line = f"{m['label']}: {cmp['a_text']} for {who} vs. {cmp['b_text']} for {other}."
    if m["kind"] == "dist" and cmp["clear"]:
        word = m["more"] if cmp["effect"] > 0 else m["less"]
        line += f" A random song of {who}'s is {word} than one of {other}'s {round(100 * cmp['superiority'])}% of the time."
    return line


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def top_artist(p, names):
    if not p["artists"]:
        return None
    aid, n = max(p["artists"].items(), key=lambda kv: (kv[1], names[kv[0]]))
    return {"name": names[aid], "songs": n}


def personalities(people, names):
    """Give each listener the profile label where they differ most from everyone else."""
    candidates = []
    rest_cache = {}
    for p in people:
        rest = rest_cache.setdefault(p["i"], pooled([q for q in people if q is not p]))
        for key, direction, title, blurb in TRAITS:
            c = compare(p, rest, key)
            if not c["clear"] or (c["effect"] > 0) != (direction > 0):
                continue
            threshold = NEGLIGIBLE_DELTA if METRICS[key]["kind"] == "dist" else NEGLIGIBLE_H
            candidates.append((abs(c["effect"]) / threshold, p["i"], key, title, blurb, c))

    # Largest difference first; each listener gets one label and each label goes to one listener
    candidates.sort(key=lambda c: (-c[0], c[1], c[3]))
    chosen, used = {}, set()
    for strength, i, key, title, blurb, c in candidates:
        if i in chosen or title in used:
            continue
        chosen[i] = (strength, key, title, blurb, c)
        used.add(title)

    cards = []
    for p in people:
        fav = top_artist(p, names)
        stats = []
        if fav:
            stats.append({"label": "Most-liked artist", "value": fav["name"], "note": f"{fav['songs']} songs"})
        if p["i"] in chosen:
            strength, key, title, blurb, c = chosen[p["i"]]
            stats.insert(0, {"label": METRICS[key]["label"], "value": c["a_text"],
                             "note": f"others: {c['b_text']}"})
            why = evidence(key, c, p["name"], "everyone else")
            rule = (f"Assigned because this is the metric where {p['name']} differs most from the rest of the group "
                    f"({'Cliff’s delta' if METRICS[key]['kind'] == 'dist' else 'Cohen’s h'} = {abs(c['effect']):.2f}).")
            if key == "night":
                why += f" Based on {p['plays']} plays over {len(p['days'])} days only."
        else:
            title, blurb = FALLBACK_TRAIT
            key = None
            yr = summary(p, "year")
            if yr is not None:
                stats.insert(0, {"label": "Median release year", "value": fmt("year", yr)})
            why = "No metric differs from the rest of the group by more than the negligible threshold."
            rule = "Default label when no metric clearly differs."
        cards.append({"i": p["i"], "name": p["name"], "initial": (p["name"].strip()[:1] or "?").upper(),
                      "title": title, "blurb": blurb,
                      "metric": key, "stats": stats[:2], "why": why, "rule": rule})
    return cards


def pair_report(a, b, names):
    """Everything the pairwise similarity section shows for one pair."""
    sa, sb = set(a["artists"]), set(b["artists"])
    shared = sa & sb
    either = sa | sb
    jaccard = len(shared) / len(either) if either else 0
    overlap = len(shared) / min(len(sa), len(sb)) if sa and sb else 0

    parts = {"artists": round(100 * overlap, 1)}
    sound = [compare(a, b, k) for k in FEATURE_KEYS]
    sound_ok = [c for c in sound if c["enough"]]
    if len(sound_ok) >= 3:
        parts["sound"] = round(100 * (1 - mean(abs(c["effect"]) for c in sound_ok)), 1)
    era = compare(a, b, "year")
    if era["enough"]:
        parts["era"] = round(100 * (1 - abs(era["effect"])), 1)
    pop = compare(a, b, "popularity")
    if pop["enough"]:
        parts["mainstream"] = round(100 * (1 - abs(pop["effect"])), 1)
    total_w = sum(COMPAT_WEIGHTS[k] for k in parts)
    score = round(sum(COMPAT_WEIGHTS[k] * v for k, v in parts.items()) / total_w) if total_w else None

    # Artists both like most: by the smaller of their two song counts
    common = sorted(shared, key=lambda x: (-min(a["artists"][x], b["artists"][x]),
                                           -(a["artists"][x] + b["artists"][x]), names[x].lower()))
    shared_tracks = sorted(a["tracks"][t] for t in set(a["tracks"]) & set(b["tracks"]))
    top_a = {n for r in a["top"].values() for n in r}
    top_b = {n for r in b["top"].values() for n in r}

    def signature(p, other):
        only = [x for x, _ in p["artists"].most_common() if x not in other["artists"]][:3]
        return [{"artist": names[x], "songs": p["artists"][x]} for x in only]

    # Where they differ most / agree most, among comparisons with enough data
    comps = [c for c in [*sound, era, pop, compare(a, b, "explicit")] if c["enough"]]
    diffs = sorted((c for c in comps if c["clear"]), key=lambda c: -abs(c["effect"]) / (
        NEGLIGIBLE_DELTA if METRICS[c["key"]]["kind"] == "dist" else NEGLIGIBLE_H))
    sames = [c for c in comps if not c["clear"]]

    def diff_line(c):
        hi, lo = (a, b) if c["higher"] == 0 else (b, a)
        m = METRICS[c["key"]]
        hv, lv = (c["a_text"], c["b_text"]) if c["higher"] == 0 else (c["b_text"], c["a_text"])
        if m["kind"] == "dist":
            return f"{hi['name']}'s songs are {m['more']}: {hv} vs. {lv}"
        return f"{m['label']}: {hi['name']} {hv} vs. {lo['name']} {lv}"

    return {
        "a": a["i"], "b": b["i"],
        "score": score,
        "parts": parts,
        "jaccard": round(jaccard, 4),
        "overlap": round(overlap, 4),
        "shared_count": len(shared),
        "artists_a": len(sa), "artists_b": len(sb),
        "common": [{"artist": names[x], "a": a["artists"][x], "b": b["artists"][x]} for x in common[:8]],
        "shared_tracks": shared_tracks[:12],
        "shared_tracks_count": len(shared_tracks),
        "top_overlap": sorted(top_a & top_b)[:8],
        "signature_a": signature(a, b),
        "signature_b": signature(b, a),
        "differences": [diff_line(c) for c in diffs[:2]],
        "similarities": [f"{METRICS[c['key']]['label']}: {c['a_text']} vs. {c['b_text']}" for c in sames[:4]],
        "description": describe_pair(a, b, score, parts, len(shared), len(shared_tracks)),
    }


def describe_pair(a, b, score, parts, shared, shared_tracks):
    """A one-line summary that only restates the numbers."""
    if score is None:
        return "Not enough data for this pair yet."
    if score >= 70:
        band = "High similarity."
    elif score >= 55:
        band = "Moderately high similarity."
    elif score >= 40:
        band = "Moderate similarity."
    else:
        band = "Low similarity."
    labels = {"artists": "shared artists", "sound": "audio features", "era": "release years",
              "mainstream": "popularity"}
    best = max(parts, key=parts.get)
    return f"{band} Most similar in {labels[best]}."


def battles(people):
    """Every pair × every category, so the page can switch pairs instantly."""
    cats = []
    for key, side, question, award in BATTLES:
        # A fixed bar scale per category so every pair is drawn the same way
        vals = [summary(p, key) for p in people]
        have = [v for v in vals if v is not None]
        if key == "year" and have:
            scale = [min(have) - 5, max(have) + 3]
        elif key == "popularity":
            scale = [0, max(1_000_000, max(have, default=0))]
        else:
            scale = [0, 1]
        cats.append({"key": key, "side": side, "question": question, "award": award,
                     "label": METRICS[key]["label"], "source": METRICS[key]["source"],
                     "scale": scale})
    results = {}
    for a, b in combinations(people, 2):
        rows = []
        for cat in cats:
            c = compare(a, b, cat["key"])
            winner = None
            if c["clear"]:
                # side +1: the higher value wins, side -1: the lower one does
                winner = c["higher"] if cat["side"] > 0 else 1 - c["higher"]
            note = ""
            if not c["enough"]:
                if cat["key"] == "night":
                    note = f"Needs {MIN_DAYS}+ days of plays each."
                else:
                    note = "Not enough data."
            if cat["key"] == "night" and c["enough"]:
                note = f"Only {len(a['days'])} and {len(b['days'])} days of plays."
            rows.append({"a": c["a"], "b": c["b"], "a_text": c["a_text"], "b_text": c["b_text"],
                         "winner": winner, "enough": c["enough"], "note": note.strip()})
        results[f"{a['i']}-{b['i']}"] = rows
    return {"categories": cats, "results": results}


def music_dna(people):
    """Average audio features per listener, with coverage and an insight sentence."""
    rows = []
    for p in people:
        n = len(p["dist"]["energy"])
        rows.append({
            "i": p["i"], "songs": n, "liked": len(p["tracks"]),
            "values": {k: (round(mean(p["dist"][k]), 3) if len(p["dist"][k]) >= MIN_SONGS else None)
                       for k in FEATURE_KEYS},
        })
    available = any(r["songs"] >= MIN_SONGS for r in rows)

    # Biggest clear gap between any two listeners on one feature
    best = None
    for a, b in combinations(people, 2):
        for k in FEATURE_KEYS:
            c = compare(a, b, k)
            if c["clear"] and (best is None or abs(c["effect"]) > abs(best[2]["effect"])):
                best = (a, b, c)
    if best:
        a, b, c = best
        hi, lo = (a, b) if c["higher"] == 0 else (b, a)
        label = METRICS[c["key"]]["label"].replace("Average ", "")
        hv, lv = (c["a_text"], c["b_text"]) if c["higher"] == 0 else (c["b_text"], c["a_text"])
        insight = f"Biggest gap: {label} ({hi['name']} {hv} vs. {lo['name']} {lv})."
    elif available:
        insight = "Everyone sounds alike: no clear gaps."
    else:
        insight = ""
    return {"features": [{"key": k, "label": lbl, "desc": d} for k, lbl, d in FEATURES],
            "listeners": rows, "available": available, "insight": insight}


def galaxy(people, names):
    """Artists for the overlap network: the most shared ones plus each listener's top unshared ones."""
    fans = defaultdict(dict)
    for p in people:
        for aid, n in p["artists"].items():
            fans[aid][p["i"]] = n
    shared = [a for a, f in fans.items() if len(f) >= 2]
    shared.sort(key=lambda a: (-len(fans[a]), -sum(fans[a].values()), names[a].lower()))
    picked = shared[:GALAXY_SHARED]
    for p in people:
        solo = [a for a, _ in p["artists"].most_common() if len(fans[a]) == 1][:GALAXY_SOLO]
        picked.extend(solo)
    by_i = {p["i"]: p for p in people}
    return {
        "artists": [
            {
                "id": a, "name": names[a], "songs": sum(fans[a].values()),
                "fans": [{"i": i, "songs": n, "tracks": by_i[i]["artist_tracks"][a]}
                         for i, n in sorted(fans[a].items())],
            }
            for a in picked
        ],
        "shared_total": len(shared),
        "table": [
            {"artist": names[a], "songs": [fans[a].get(p["i"], 0) for p in people]}
            for a in shared[:30]
        ],
    }


def listening_clock(people):
    out = []
    for p in people:
        windows = []
        for key, label, hours, span in WINDOWS:
            n = sum(p["hours"][h] for h in hours)
            windows.append({"key": key, "label": label, "span": span,
                            "plays": n, "share": round(n / p["plays"], 3) if p["plays"] else 0})
        peak = max(p["hours"]) if p["plays"] else 0
        tag = None
        if p["plays"] >= MIN_PLAYS and len(p["days"]) >= MIN_DAYS:
            top = max(windows, key=lambda w: w["share"])
            if top["share"] >= WINDOW_LABEL_SHARE:
                tag = {"label": top["label"], "share": top["share"], "span": top["span"]}
        out.append({
            "i": p["i"], "hours": p["hours"], "plays": p["plays"], "days": len(p["days"]),
            "first": f"{p['first_play']:%b} {p['first_play'].day}" if p["first_play"] else None,
            "last": f"{p['last_play']:%b} {p['last_play'].day}" if p["last_play"] else None,
            "peak_hours": [h for h in range(24) if peak and p["hours"][h] == peak],
            "peak_plays": peak, "windows": windows, "tag": tag,
        })
    return out


def eras(people):
    """Liked songs per decade and the popularity spread, per listener."""
    decades = sorted({d for p in people for d in p["decades"]})
    rows = []
    for p in people:
        total = sum(p["decades"].values())
        pops = p["dist"]["popularity"]
        q = None
        if len(pops) >= MIN_SONGS:
            q = [round(x) for x in quantiles(pops, n=4, method="inclusive")]
        rows.append({"i": p["i"], "total": total,
                     "decades": [p["decades"].get(d, 0) for d in decades],
                     "median_year": summary(p, "year"),
                     "popularity": q, "matched": len(pops)})
    return {"decades": decades, "listeners": rows}


def insights(people, pairs, names):
    """Short, data-grounded sentences for the top of the page."""
    out = []
    if pairs:
        best = max(pairs, key=lambda r: r["jaccard"])
        a, b = people[best["a"]]["name"], people[best["b"]]["name"]
        if len(pairs) > 1:
            out.append(f"{a} and {b} share the most artists ({best['shared_count']}) but only {best['shared_tracks_count']} songs.")

    def extreme(key, higher=True):
        have = [(summary(p, key), p) for p in people if summary(p, key) is not None]
        if len(have) < 2:
            return None
        have.sort(key=lambda t: t[0], reverse=higher)
        c = compare(have[0][1], have[1][1], key)
        return (have[0], have[1]) if c["clear"] else None

    r = extreme("year", higher=False)
    if r:
        (v1, p1), (v2, p2) = r
        out.append(f"{p1['name']}'s liked songs are the oldest (median release year {fmt('year', v1)} vs. {fmt('year', v2)} for {p2['name']}).")
    r = extreme("danceability")
    if r:
        (v1, p1), _ = r
        out.append(f"{p1['name']}'s library has the highest average danceability ({fmt('danceability', v1)}).")
    r = extreme("explicit")
    if r:
        (v1, p1), (v2, p2) = r
        out.append(f"{p1['name']} has the highest share of explicit songs ({fmt('explicit', v1)}).")
    r = extreme("popularity")
    if r:
        (v1, p1), _ = r
        out.append(f"{p1['name']}'s liked songs have the highest median Deezer popularity ({fmt('popularity', v1)}).")
    return out[:3]


def advanced(people):
    """Per-listener numbers for the 'Show the numbers' tables."""
    rows = []
    for p in people:
        r = {"name": p["name"], "liked": len(p["tracks"]), "artists": len(p["artists"]),
             "with_features": len(p["dist"]["energy"]), "with_popularity": len(p["dist"]["popularity"]),
             "plays": p["plays"], "days": len(p["days"])}
        for key in METRICS:
            r[key] = fmt(key, summary(p, key))
        rows.append(r)
    songs = [
        {"year": sorted(p["dist"]["year"]), "popularity": sorted(p["dist"]["popularity"])}
        for p in people
    ]
    return {"rows": rows, "songs": songs}


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def build():
    users = get_users()
    if len(users) < 2:
        return {"listeners": [{"i": i, "name": u["display_name"]} for i, u in enumerate(users)], "enough": False}
    people, names = load(users)
    pairs = [pair_report(a, b, names) for a, b in combinations(people, 2)]
    return {
        "enough": True,
        "listeners": [{"i": p["i"], "name": p["name"], "liked": len(p["tracks"]),
                       "initial": (p["name"].strip()[:1] or "?").upper()} for p in people],
        "insights": insights(people, pairs, names),
        "personalities": personalities(people, names),
        "pairs": pairs,
        "compat_weights": COMPAT_WEIGHTS,
        "battles": battles(people),
        "dna": music_dna(people),
        "galaxy": galaxy(people, names),
        "clock": listening_clock(people),
        "eras": eras(people),
        "advanced": advanced(people),
        "metrics": {k: {"label": m["label"], "source": m["source"]} for k, m in METRICS.items()},
        "thresholds": {"delta": NEGLIGIBLE_DELTA, "h": NEGLIGIBLE_H, "songs": MIN_SONGS,
                       "plays": MIN_PLAYS, "days": MIN_DAYS},
    }


# Rebuilding takes a moment, so keep the result until the database file changes
_cache = {"key": None, "data": None}


def _db_version():
    parts = []
    for path in (DB_PATH, DB_PATH.with_name(DB_PATH.name + "-wal")):
        if path.exists():
            st = path.stat()
            parts.append((st.st_mtime_ns, st.st_size))
    return tuple(parts)


def cached_build():
    key = _db_version()
    if _cache["key"] != key or _cache["data"] is None:
        _cache["data"] = build()
        _cache["key"] = key
    return _cache["data"]


@bp.route("/")
def index():
    try:
        data = cached_build()
        error = None
    except FileNotFoundError as e:
        data, error = None, str(e)
    except sqlite3.Error as e:
        data, error = None, f"Couldn't read the database: {e}"
    return render_template("compare.html", data=data, error=error)
