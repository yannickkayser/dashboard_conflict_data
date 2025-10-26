# fetch_ACLED.py 
# Script for fetching ACLED Data
# the function can be called weekly to update the database, call the newest data available at ACLED

#################################
# import packages 

import requests
import json
import time
import os
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
        "event_date_where": "BETWEEN"
    }

    print(f"\n[{datetime.now()}] Fetching ACLED data for {country} from {time_period}")

    response = requests.get("https://acleddata.com/api/acled/read?_format=json",
        params=params,
        headers={"Authorization": f"Bearer {my_token}", "Content-Type": "application/json"}
    )

    ### Retrieve data and save in json file
    if response.json()["status"] == 200:
        print(
            "Request successful"
        )

        number_entries = response.json()["total_count"]
        print(f"Total number of entries: {number_entries} for {country}")

        # Call limit 5000 entries, if there are more than 5000 entries need a different approach
        if number_entries >= 5000: # error -> needs to be bigger 
            print(f"------- Attention --------\n{country} has more entries than, need to perfom pagination")

        else:
            # save results as json file
            data = response.json()

            # filename
            folder_path = "../raw"
            os.makedirs(folder_path, exist_ok=True)  # create folder if it doesn't exist

            output_filename = os.path.join(folder_path, f"ACLEDouput_{country}_{time_period}.json")

            # Save JSON to the specified file
            with open(output_filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)


            print(f"Saving data to {output_filename}")
            return(output_filename)


    else:
        print("Request unsucessfull, try again")



################################## 
# 3. Newest dates in the database

def get_newest_date(db_path):

    # connect to the database
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # query the newest date (most up to date)
    query ="""
            SELECT event_date 
            FROM events
            ORDER BY event_date DESC
            LIMIT 1
            """
    c.execute(query)
    result = c.fetchone()
    c.close()
    return result[0] if result else "2000-01-01"


################################## 
# 4. Load into the dates in the database
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
                :event_type, :sub_event_type, :interaction, :civilian_targeting,
                :iso, :region, :country, :admin1, :admin2, :admin3, :location,
                :latitude, :longitude, :geo_precision, :source, :source_scale,
                :notes, :fatalities, :tags, :timestamp
            )
        ''', event)

        # Insert actor1
        if event.get("actor1"):
            c.execute('INSERT OR IGNORE INTO actors (actor_name) VALUES (?)', (event["actor1"],))
            c.execute('SELECT actor_id FROM actors WHERE actor_name = ?', (event["actor1"],))
            actor1_id = c.fetchone()[0]
            c.execute('INSERT OR IGNORE INTO event_actors VALUES (?, ?, ?)',
            (event["event_id_cnty"], actor1_id, "actor1"))

        # Insert actor2 if exists
        if event.get("actor2"):
            c.execute('INSERT OR IGNORE INTO actors (actor_name) VALUES (?)', (event["actor2"],))
            c.execute('SELECT actor_id FROM actors WHERE actor_name = ?', (event["actor2"],))
            actor2_id = c.fetchone()[0]
            c.execute('INSERT OR IGNORE INTO event_actors VALUES (?, ?, ?)',
                    (event["event_id_cnty"], actor2_id, "actor2"))

    conn.commit()
    conn.close()