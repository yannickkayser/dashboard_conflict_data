
from utils import load_config
from fetch_ACLED import fetch_acled_data, get_newest_date, load_json_to_db
from datetime import datetime
from dateutil.relativedelta import relativedelta

from extract_keywords import load_keywords_database

#from src.fetch_gnews import fetch_gnews_articles
#from src.match_and_load import match_and_load
#from src.utils import load_config, get_db_connection, get_latest_event_date

def main():
    config = load_config()

    # Specify country
    country = "Germany"

    ###################################
    # Get the latest date in the database
    latest_date = get_newest_date(country, config["database"]["path"])

    dt = datetime.now()
    #Extract just the date one year ago -1 
    one_year_ago = dt - relativedelta(years=1)
    date_time = one_year_ago.date()

    # Query parameter for new timeframe that available for fetching
    time_period = f"{latest_date}|{date_time}"

    ###################################
    # Fetch the data 
    acled_file = fetch_acled_data(country, time_period)

    # Update the database
    load_json_to_db(acled_file, config["database"]["path"])

    ###################################
    # Get the keywords from the json put them into the database
    load_keywords_database(country, config["database"]["path"])


    ###################################
    # Fetch NEWS Data based on keywords


    #gnews_file = fetch_gnews_articles(keywords)

    #match_and_load(acled_file, gnews_file, db_path)

if __name__ == "__main__":
    main()