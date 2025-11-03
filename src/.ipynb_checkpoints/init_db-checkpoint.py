# init.py 

## import packages
import requests
import json
import argparse
from datetime import datetime
import pandas as pd
import sqlite3
import time
import os
import random
import glob
import typing_extensions
from utils import load_config, init_logger


# LOG 
log = init_logger("INITIALISE")

config = load_config()

# Function to get access token using username and password
def get_access_token(username, password, token_url):
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    data = {
            'username': username,
            'password': password,
            'grant_type': "password",
            'client_id': "acled"
    }

    response = requests.post(token_url, headers=headers, data=data)

    if response.status_code == 200:
        token_data = response.json()
        return token_data['access_token']
    else:
        raise Exception(f"Failed to get access token: {response.status_code} {response.text}")
    
# ----------------------------
#  Function to get data
# ----------------------------
def fetch_acled_data(country, year, my_token):
    BASE_URL = "https://acleddata.com/api/acled/read?_format=json"
    params = {
        "country": country,
        "year": year,
        "limit": 5000,  # explicit limit
    }

    print(f"\n[{datetime.now()}] Fetching ACLED data for {country} ({year})")

    all_data = []
    page = 1

    while True:
        params["page"] = page

        response = requests.get(
            BASE_URL,
            params=params,
            headers={
                "Authorization": f"Bearer {my_token}",
                "Content-Type": "application/json"
            }
        )

        if response.status_code != 200:
            print(f"❌ Request failed for page {page}: {response.status_code}")
            print(response.text)
            break

        page_data = response.json().get("data", [])
        print(f"Page {page}: received {len(page_data)} rows")

        if not page_data:
            print("No data returned — stopping pagination.")
            break

        all_data.extend(page_data)

        # Stop if we got fewer than the limit → last page
        if len(page_data) < params["limit"]:
            print("Reached final page.")
            break

        page += 1
        time.sleep(random.uniform(0.5, 1.5))  # polite delay

    # Save all combined data
    if all_data:
        folder_path = "../raw"
        os.makedirs(folder_path, exist_ok=True)

        output_filename = os.path.join(folder_path, f"ACLEDoutput_{country}_{year}.json")

        with open(output_filename, "w", encoding="utf-8") as f:
            json.dump({"data": all_data}, f, ensure_ascii=False, indent=4)

        print(f"✅ Saved {len(all_data)} total rows to {output_filename}")
    else:
        print("⚠️ No data retrieved.")

# ----------------------------
# Build database on the shema above
# ----------------------------
def create_database(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Events table
    c.execute('''
        CREATE TABLE IF NOT EXISTS events (
            event_id_cnty TEXT PRIMARY KEY,
            event_date TEXT,
            year INTEGER,
            time_precision TEXT,
            disorder_type TEXT,
            event_type TEXT,
            sub_event_type TEXT,
            actor1 TEXT,
            assoc_actor_1 TEXT,
            inter1 TEXT,
            actor2 TEXT, 
            assoc_actor_2 TEXT,
            inter2 TEXT,
            interaction TEXT,
            civilian_targeting TEXT,
            iso INTEGER,
            region TEXT,
            country TEXT,
            admin1 TEXT,
            admin2 TEXT,
            admin3 TEXT,
            location TEXT,
            latitude REAL,
            longitude REAL,
            geo_precision INTEGER,
            source TEXT,
            source_scale TEXT,
            notes TEXT,
            fatalities INTEGER,
            tags TEXT,
            timestamp INTEGER
        )
    ''')
    conn.commit()
    conn.close()

# ----------------------------
# Function to load json in db
# ----------------------------
def load_json_to_db(json_path, db_path):
    # Load JSON
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    for event in data["data"]:
        # Insert event
        c.execute('''
            INSERT OR IGNORE INTO events VALUES (
                :event_id_cnty, :event_date, :year, :time_precision, :disorder_type,
                :event_type, :sub_event_type, :actor1, :assoc_actor_1, :inter1, :actor2, 
                :assoc_actor_2, :inter2, :interaction, :civilian_targeting,
                :iso, :region, :country, :admin1, :admin2, :admin3, :location,
                :latitude, :longitude, :geo_precision, :source, :source_scale,
                :notes, :fatalities, :tags, :timestamp
            )
        ''', event)

    conn.commit()

    conn.close()





# Get the dictionary with country names
with open("../data/acled_coverage.json", "r", encoding="utf-8") as f:
    acled_coverage = json.load(f)


# ----------------------------
#  Run for all countries
# ----------------------------
# get access token

#access_token = get_access_token(config["acled"]["username"], config["acled"]["password"], config["acled"]["token_url"])
#for country_name in acled_coverage.keys():
#    print(f"Analyzing {country_name}")
#    fetch_acled_data(country_name, 2024, access_token)
#    wait_time = random.uniform(1, 3)  # wait between 1 and 3 seconds
#    time.sleep(wait_time)

# took 36 mins !!!

# Path to your raw JSON folder
raw_folder = "../raw"

# 1. Create database (only needs to be done once)
create_database(config["database"]["path"])

# 2. Get all JSON files in the folder
json_files = glob.glob(os.path.join(raw_folder, "*.json"))

# 3. Loop over each file and load it into the database
for json_file in json_files:
     print(f"Loading {json_file} into database...")
     load_json_to_db(json_file,config["database"]["path"])

print("All JSON files loaded successfully.")
# took 3.6 s