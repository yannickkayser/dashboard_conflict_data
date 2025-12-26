# pipline.py
#
# script to automatically update the ACLED database every 2-7 days 

import os
import json
from utils import load_config
from fetch_ACLED import fetch_acled_data, get_newest_date, load_json_to_db
from datetime import datetime
from dateutil.relativedelta import relativedelta

#from extract_keywords import load_keywords_database

#from src.fetch_gnews import fetch_gnews_articles
#from src.match_and_load import match_and_load
#from src.utils import load_config, get_db_connection, get_latest_event_date

def main():
    config = load_config()

    # path to db
    db_path = config["database"]["path"]
    cn_path = config["country_name"]["path"]

    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(base_dir, db_path)
    
    # Get the dictionary with country names
    full_path_cn = os.path.join(base_dir, cn_path)


    with open(full_path_cn, "r", encoding="UTF-8") as f:
        acled_coverage = json.load(f)

    # for all countries
    for country_name in acled_coverage.keys():
        ###################################
        # Get the latest date in the database
        latest_date = get_newest_date(country_name, full_path)

        dt = datetime.now()
        # Extract just the date one year ago -1 
        one_year_ago = dt - relativedelta(years=1)
        date_time = one_year_ago.date()

        # Query parameter for new timeframe that available for fetching
        time_period = f"{latest_date}|{date_time}"

        ###################################
        # Fetch the data 
        acled_file = fetch_acled_data(country_name, time_period)
    
        # Update the database
        load_json_to_db(acled_file, full_path)
        print(f"Updating database for {country_name}")

        ###################################
        # aggregation
    
        ###################################
        # Get the keywords from the json put them into the database
        # load_keywords_database(country, config["database"]["path"])



if __name__ == "__main__":
    main()