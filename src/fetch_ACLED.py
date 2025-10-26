# fetch_ACLED.py 
# Script for fetching ACLED Data
# the function can be called weekly to update the database, call the newest data available at ACLED

#################################
# import packages 

import requests
import json
import time
import os
import datetime
import sqlite3
from utils import load_config, init_logger

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
        print("getting token", token_data['access_token'])
        return token_data['access_token']
    else:
        raise Exception(f"Failed to get access token: {response.status_code} {response.text}")


################################## 
# 2. fetching ACLED data (newest available, last date in the database until now => gives us the newest data available)
def fetch_acled_data (country, year, event_date): 

    # Define API endpoint and parameters
    BASE_URL = "https://acleddata.com/api/acled/read?_format=json"
    params = {
        "country": country,
        "year": year,
        "event_date": event_date
    }

    print(f"\n[{datetime.now()}] Fetching ACLED data for {country} ({year} - {event_date})")

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
        print(f"Total number of entries: {number_entries} for {country}, {year}")

        # Call limit 5000 entries, if there are more than 5000 entries need a different approach
        if number_entries >= 5000: # error -> needs to be bigger 
            print(f"------- Attention --------\n{country}:{year} has more entries than, need to perfom pagination")

        else:
            # save results as json file
            data = response.json()

            # filename
            folder_path = "../raw"
            os.makedirs(folder_path, exist_ok=True)  # create folder if it doesn't exist

            output_filename = os.path.join(folder_path, f"ACLEDouput_{country}_{year}_{event_date}.json")

            # Save JSON to the specified file
            with open(output_filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)


            print(f"Saving data to {output_filename}")


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



# Calll 

log = init_logger("ACLED")

# Get an access token
config = load_config()

my_token = get_access_token(
    username=config["acled"]["username"],
    password=config["acled"]["password"],
    token_url=config["acled"]["token_url"]
)

latest_date = get_newest_date(config["database"]["path"])
print(f"Latest date : {latest_date}")