"""Pull all hourly PJM data (EIA) and weather (Open-Meteo) into data/raw/*.csv, uncleaned. Times are UTC."""

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

# Load EIA_API_KEY from .env if present.
if (ROOT / ".env").exists():
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

EIA_KEY = os.environ.get("EIA_API_KEY", "DEMO_KEY")
START = "2019-01-01"
END = str(date.today() - timedelta(days=6))  # Open-Meteo archive lags ~5 days

# PJM load centers (name, lat, lon)
CITIES = [("chicago", 41.88, -87.63), ("philadelphia", 39.95, -75.17), ("washington_dc", 38.91, -77.04)]
WEATHER_VARS = ("temperature_2m,relative_humidity_2m,dew_point_2m,apparent_temperature,"
                "precipitation,snowfall,cloud_cover,wind_speed_10m,shortwave_radiation")


def eia(route):
    """Every hourly PJM row from an EIA v2 route, 5000 per page."""
    rows = []
    while True:
        r = requests.get(f"https://api.eia.gov/v2/electricity/rto/{route}/data/", params={
            "api_key": EIA_KEY, "frequency": "hourly", "data[0]": "value",
            "facets[respondent][]": "PJM", "start": f"{START}T00", "end": f"{END}T23",
            "sort[0][column]": "period", "sort[0][direction]": "asc",
            "offset": len(rows), "length": 5000,
        }, timeout=60)
        r.raise_for_status()
        page = r.json()["response"]["data"]
        rows += page
        if len(page) < 5000:
            return pd.DataFrame(rows)


def weather(city, lat, lon):
    """Hourly weather for one city from the Open-Meteo archive (no key needed)."""
    r = requests.get("https://archive-api.open-meteo.com/v1/archive", params={
        "latitude": lat, "longitude": lon, "start_date": START, "end_date": END,
        "hourly": WEATHER_VARS, "temperature_unit": "fahrenheit", "timezone": "GMT",
    }, timeout=120)
    r.raise_for_status()
    return pd.DataFrame(r.json()["hourly"]).assign(city=city)


if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    eia("region-data").to_csv(RAW / "eia_demand.csv", index=False)  # D, DF, NG, TI
    eia("fuel-type-data").to_csv(RAW / "eia_fuel_mix.csv", index=False)
    pd.concat(weather(*c) for c in CITIES).to_csv(RAW / "weather.csv", index=False)
