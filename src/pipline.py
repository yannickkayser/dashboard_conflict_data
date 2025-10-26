

from fetch_ACLED import fetch_acled_data, get_newest_date, load_json_to_db
from datetime import datetime
from dateutil.relativedelta import relativedelta

from src.fetch_gnews import fetch_gnews_articles
from src.match_and_load import match_and_load
from src.utils import load_config, get_db_connection, get_latest_event_date

def main():
    config = load_config()

    ###################################
    # Get the latest date in the database
    latest_date = get_newest_date(config["database"]["path"])

    country = "Germany"
    event_date = latest_date

    dt = datetime.now()
    #Extract just the date
    one_year_ago = dt - relativedelta(years=1)
    date_time = one_year_ago.date()

    time_period = f"{event_date}|{date_time}"

    # Fetch the data 
    acled_file = fetch_acled_data(country, time_period)

    # Update the database
    load_json_to_db(acled_file, config["database"]["path"])

    ###################################
    # Get the keywords from the json



    gnews_file = fetch_gnews_articles(keywords)

    match_and_load(acled_file, gnews_file, db_path)

if __name__ == "__main__":
    main()