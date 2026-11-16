# This script pulls hourly PJM power data from the EIA API and hourly weather from the Open-Meteo API.
# It saves everything to data/raw as is, and the cleaning happens in clean_data.py.
# Run it from the project folder with python src/read_api.py

import os
from datetime import date, timedelta

import pandas as pd
import requests

# This reads the EIA api key from the .env file. The line in .env should look like EIA_API_KEY=yourkey
for line in open(".env"):
    if line.startswith("EIA_API_KEY="):
        os.environ["EIA_API_KEY"] = line.split("=", 1)[1].strip()
api_key = os.environ["EIA_API_KEY"]

# This is the date range we pull. The weather archive is about 5 days behind, so we stop 6 days ago.
start = "2019-01-01"
end = str(date.today() - timedelta(days=6))

# These are the cities we get weather for. PJM covers a lot of states, so I picked three big cities in it.
cities = [("chicago", 41.88, -87.63), ("philadelphia", 39.95, -75.17), ("washington_dc", 38.91, -77.04)]
weather_vars = ("temperature_2m,relative_humidity_2m,dew_point_2m,apparent_temperature,"
                "precipitation,snowfall,cloud_cover,wind_speed_10m,shortwave_radiation")


# This gets all the hourly PJM data from one EIA endpoint. EIA only gives 5000 rows at a time,
# so it keeps asking for the next 5000 until it gets a page that isn't full.
def get_eia(endpoint):
    rows = []
    while True:
        url = "https://api.eia.gov/v2/electricity/rto/" + endpoint + "/data/"
        params = {
            "api_key": api_key,
            "frequency": "hourly",
            "data[0]": "value",
            "facets[respondent][]": "PJM",
            "start": start + "T00",
            "end": end + "T23",
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "offset": len(rows),
            "length": 5000,
        }
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        page = response.json()["response"]["data"]
        rows = rows + page
        if len(page) < 5000:
            return pd.DataFrame(rows)


# This gets the hourly weather for one city. Open-Meteo doesn't need an api key.
def get_weather(city, lat, lon):
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "hourly": weather_vars,
        "temperature_unit": "fahrenheit",
        "timezone": "GMT",
    }
    response = requests.get("https://archive-api.open-meteo.com/v1/archive", params=params, timeout=120)
    response.raise_for_status()
    weather = pd.DataFrame(response.json()["hourly"])
    weather["city"] = city
    return weather


os.makedirs("data/raw", exist_ok=True)

# This is the demand data. It has actual demand, EIA's day-ahead forecast, net generation and interchange.
demand = get_eia("region-data")
demand.to_csv("data/raw/eia_demand.csv", index=False)
print("demand:", len(demand), "rows")

# This is the fuel mix data. It has how much power came from each fuel every hour.
fuel = get_eia("fuel-type-data")
fuel.to_csv("data/raw/eia_fuel_mix.csv", index=False)
print("fuel mix:", len(fuel), "rows")

# This is the weather data for all three cities stacked together.
weather = pd.concat([get_weather(city, lat, lon) for city, lat, lon in cities])
weather.to_csv("data/raw/weather.csv", index=False)
print("weather:", len(weather), "rows")
