# date 
import os
import json
from utils import load_config, init_logger
from fetch_ACLED import get_newest_date
from datetime import datetime
from dateutil.relativedelta import relativedelta


def main():
    log = init_logger("Latest Dates")

    # get configurations
    config = load_config()

    # Get the dictionary with country names
    # path to db
    cn_path = config["country_name"]["path"]
    db_path = config["database"]["path"]

    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(base_dir, cn_path)
    full_path_db = os.path.join(base_dir, db_path)

    
    
    with open(full_path, "r", encoding="utf-8") as f:
        acled_coverage = json.load(f)

    all_dates = []  # store all latest dates

    # for all countries
    for country_name in acled_coverage.keys():

        ###################################
        # Get the latest date in the database
        latest_date = get_newest_date(country_name, full_path_db)
        all_dates.append(latest_date)

        print(f"Latest date for {country_name}: {latest_date}")

    
    # Compute and print the overall latest date
    if all_dates:
        overall_latest = max(all_dates)
        print("###############################")
        print(" ")
        print(f"Overall latest date: {overall_latest}")
        print(" ")
        print("###############################")
    else:
        print("No dates found in the database.")


if __name__ == "__main__":
    main()


