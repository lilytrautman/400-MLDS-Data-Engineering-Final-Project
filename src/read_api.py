"""Pull hourly PJM demand + fuel mix (EIA) and weather (Open-Meteo) into data/raw/*.csv. All times UTC."""

import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

# Load .env if present; real environment variables win.
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

EIA_KEY = os.environ.get("EIA_API_KEY", "DEMO_KEY")  # DEMO_KEY is heavily rate limited
REGION = "PJM"
START = "2019-01-01"
END = (date.today() - timedelta(days=6)).isoformat()  # Open-Meteo archive lags ~5 days

# PJM is big; average a few load centers' weather. (name, lat, lon)
CITIES = [
    ("chicago", 41.88, -87.63),
    ("philadelphia", 39.95, -75.17),
    ("washington_dc", 38.91, -77.04),
]
WEATHER_VARS = ["temperature_2m", "relative_humidity_2m", "dew_point_2m",
                "wind_speed_10m", "cloud_cover", "precipitation"]


def eia(route, facets, start=START, end=END):
    """Page through an EIA v2 hourly route (5000 rows per request)."""
    url = f"https://api.eia.gov/v2/electricity/rto/{route}/data/"
    params = {
        "api_key": EIA_KEY,
        "frequency": "hourly",
        "data[0]": "value",
        "start": f"{start}T00",
        "end": f"{end}T23",
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": 5000,
        **{f"facets[{k}][]": v for k, v in facets.items()},
    }
    rows, offset = [], 0
    while True:
        r = requests.get(url, params={**params, "offset": offset}, timeout=60)
        r.raise_for_status()
        resp = r.json()["response"]
        rows += resp["data"]
        offset += len(resp["data"])
        print(f"  {route}: {offset}/{resp['total']}")
        if not resp["data"] or offset >= int(resp["total"]):
            break
        time.sleep(0.5)
    df = pd.DataFrame(rows)
    df["period"] = pd.to_datetime(df["period"], format="%Y-%m-%dT%H", utc=True)
    df["value"] = pd.to_numeric(df["value"])
    return df


def weather(name, lat, lon, start=START, end=END):
    """Hourly historical weather for one point from the Open-Meteo archive (no key)."""
    r = requests.get("https://archive-api.open-meteo.com/v1/archive", params={
        "latitude": lat, "longitude": lon,
        "start_date": start, "end_date": end,
        "hourly": ",".join(WEATHER_VARS),
        "timezone": "GMT",
        "temperature_unit": "fahrenheit",
    }, timeout=120)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["hourly"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df["city"] = name
    return df


if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    # type: D = demand, DF = EIA day-ahead forecast, NG = net generation, TI = net interchange
    outputs = {
        "eia_demand": eia("region-data", {"respondent": REGION, "type": ["D", "DF", "NG", "TI"]}),
        "eia_fuel_mix": eia("fuel-type-data", {"respondent": REGION}),
        "weather": pd.concat(weather(*c) for c in CITIES),
    }
    for name, df in outputs.items():
        df.to_csv(RAW / f"{name}.csv", index=False)
        print(f"{name}: {len(df)} rows -> data/raw/{name}.csv")
