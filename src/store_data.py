# This script puts the data we pulled from the APIs into a SQLite database at data/pjm.db.
# Each csv in data/raw becomes its own table. Run read_api.py first, then run this
# from the project folder with python src/store_data.py

import sqlite3

import pandas as pd

conn = sqlite3.connect("data/pjm.db")

# This loads each raw csv into a table with the same name. If the table is already
# there it gets replaced, so running this again after a new pull just updates it.
for table in ["eia_demand", "eia_fuel_mix", "weather"]:
    df = pd.read_csv("data/raw/" + table + ".csv")
    df.to_sql(table, conn, if_exists="replace", index=False)
    print(table + ":", len(df), "rows")

# This is a quick check that the tables are in the database and have data
print(pd.read_sql("SELECT type, COUNT(*) AS hours FROM eia_demand GROUP BY type", conn))

conn.close()
