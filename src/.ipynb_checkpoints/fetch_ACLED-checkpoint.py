# fetch_ACLED.py 
# Script for fetching ACLED Data
# the function can be called weekly to update the database, call the newest data available at ACLED

#################################
# import packages 

import requests
import json
import time
import os
import random
from datetime import datetime
from dateutil.relativedelta import relativedelta
import sqlite3
from utils import load_config, init_logger


# LOG 
log = init_logger("ACLED")
##################################
# 1. Access Token

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
        #print("getting token", token_data['access_token'])
        return token_data['access_token']
    else:
        raise Exception(f"Failed to get access token: {response.status_code} {response.text}")


################################## 
# 2. fetching ACLED data (newest available, last date in the database until now => gives us the newest data available)
def fetch_acled_data (country, time_period): 

    # Get an access token
    config = load_config()

    my_token = get_access_token(
        username=config["acled"]["username"],
        password=config["acled"]["password"],
        token_url=config["acled"]["token_url"]
    )

    # Define API endpoint and parameters
    BASE_URL = "https://acleddata.com/api/acled/read?_format=json"
    params = {
        "country": country,
        "event_date": time_period,
        "event_date_where": "BETWEEN",
        "limit": 5000,  # explicit limit
    }

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
        folder_path = "raw/"
        os.makedirs(folder_path, exist_ok=True)

        safe_period = time_period.replace("|", "_to_")
        output_filename = os.path.join(
            folder_path, 
            f"ACLEDoutput_{country}_{safe_period}.json"
        )

        with open(output_filename, "w", encoding="utf-8") as f:
            json.dump({"data": all_data}, f, ensure_ascii=False, indent=4)

        print(f"✅ Saved {len(all_data)} total rows to {output_filename}")
    else:
        print("⚠️ No data retrieved.")




################################## 
# 3. Newest dates in the database

def get_newest_date(country, db_path):

    # connect to the database
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # query the newest date (most up to date)
    c.execute("""
            SELECT event_date 
            FROM events
            WHERE country = :country
            ORDER BY event_date DESC
            LIMIT 1
            """, {"country":country})
    result = c.fetchone()
    c.close()
    conn.close()  # also good practice to close the connection
    return result[0] if result else "2000-01-01"


################################## 
# 4. Load into the dates in the database
def load_json_to_db(json_path, db_path):
    # Load JSON
    if not json_path or not os.path.exists(json_path):
        log.warning(f"JSON file not found: {json_path}")
        return
        
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