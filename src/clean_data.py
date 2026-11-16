# This script cleans the EIA and weather data and puts it all into one table with a row for every hour.
# Run it from the project folder with python src/clean_data.py

import os

import pandas as pd

# This is every hour from the start of 2019 to the last day we pulled, in UTC time
all_hours = pd.date_range("2019-01-01", "2026-11-09 23:00", freq="h", tz="UTC")

# This is the demand data. It has actual demand, EIA's day-ahead forecast, net generation and interchange.
# I pivot it so each one gets its own column, then reindex so any hour EIA skipped shows up as an empty row.
demand = pd.read_csv("data/raw/eia_demand.csv")
demand["period"] = pd.to_datetime(demand["period"], format="%Y-%m-%dT%H", utc=True)
demand = demand.pivot(index="period", columns="type", values="value")
demand = demand.rename(columns={"D": "demand", "DF": "forecast", "NG": "net_generation", "TI": "interchange"})
demand = demand.reindex(all_hours)

# This is the fuel mix data. It has how much power came from each fuel (coal, gas, nuclear, etc) every hour.
fuel = pd.read_csv("data/raw/eia_fuel_mix.csv")
fuel["period"] = pd.to_datetime(fuel["period"], format="%Y-%m-%dT%H", utc=True)
fuel = fuel.pivot(index="period", columns="fueltype", values="value")
fuel = fuel.rename(columns={"COL": "coal", "NG": "gas", "NUC": "nuclear", "OIL": "oil",
                            "OTH": "other", "SUN": "solar", "WAT": "hydro", "WND": "wind"})
fuel = fuel.reindex(all_hours)

# This is the weather data for Chicago, Philadelphia and DC. It has temperature, humidity, wind, etc.
# I give each city its own columns so the names look like temperature_2m_chicago.
weather = pd.read_csv("data/raw/weather.csv")
weather["time"] = pd.to_datetime(weather["time"], utc=True)
weather = weather.pivot(index="time", columns="city")
weather.columns = [var + "_" + city for var, city in weather.columns]

print("missing before:")
print(demand.isna().sum())

# This removes the bad demand readings. EIA puts 2147480000 when something breaks, and there are also
# some spikes that are way off from the rest of the day. Real peaks are never more than about 33% off
# from the day's median, so anything more than 40% off gets removed.
for col in ["demand", "net_generation"]:
    demand.loc[demand[col] > 1000000, col] = None
    daily_median = demand[col].rolling(25, center=True, min_periods=5).median()
    spike = (demand[col] / daily_median - 1).abs() > 0.4
    demand.loc[spike, col] = None

# This fills in gaps that are 3 hours or less using the hours on either side.
# I leave the forecast alone since it's what we compare our model against.
for col in ["demand", "net_generation", "interchange"]:
    demand[col] = demand[col].interpolate(limit=3, limit_area="inside")
fuel = fuel.interpolate(limit=3, limit_area="inside")

print("missing after:")
print(demand.isna().sum())

# This joins everything together into one table and saves it
hourly = demand.join(fuel).join(weather)
hourly.index.name = "time"

assert len(hourly) == len(all_hours)
assert hourly["demand"].max() < 170000

os.makedirs("data/processed", exist_ok=True)
hourly.to_csv("data/processed/pjm_hourly.csv")
print("saved", hourly.shape, "to data/processed/pjm_hourly.csv")
