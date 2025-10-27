# fetch_GNEWS.py 
# Script for fetching GNEWS Data
# the function can be called weekly to update the database, based on the Keywords from ACLED

#################################
# import package
import json
import urllib.request
import urllib.parse  
import sqlite3
import pandas as pd

#################################
# 1. Get Keywords + dates + location
def get_querywords(country, db_path):
    """Extract keywords/entities from database."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("""
        SELECT event_id_cnty, event_date, country, location, actor, keywords, entities,
        FROM events
        WHERE country = :country 
    """, {"country": country})

    rows = c.fetchall()

    columns = [col[0] for col in c.description]  # get column names dynamically

    conn.close()

    # ✅ Convert to DataFrame
    df = pd.DataFrame(rows, columns=columns)

    # ✅ Parse JSON fields (keywords and entities)
    df["keywords"] = df["keywords"].apply(lambda x: json.loads(x) if x else [])
    df["entities"] = df["entities"].apply(lambda x: json.loads(x) if x else [])

    return df





# Replace with your real API key
#apikey = "XXXXXXXXXXXX"

# Define search parameters
query = 'protest OR konflikt OR demonstration OR aufstand OR streik'  # keywords in German
lang = "de"       # German language
country = "de"    # Germany as country of publication
max_results = 10  # number of articles to retrieve

# Define date range for January 2024 (ISO 8601 format)
date_from = "2024-01-01T00:00:00Z"
date_to = "2024-01-31T23:59:59Z"

# URL encode the query string to handle spaces and special characters
encoded_query = urllib.parse.quote(query)

# Construct API URL
url = (
    f"https://gnews.io/api/v4/search?"
    f"q={encoded_query}"
    f"&lang={lang}"
    f"&country={country}"
    f"&from={date_from}"
    f"&to={date_to}"
    f"&max={max_results}"
    f"&sortby=relevance"
    f"&apikey={apikey}"
)

# Fetch and parse JSON response
with urllib.request.urlopen(url) as response:
    data = json.loads(response.read().decode("utf-8"))
    articles = data.get("articles", [])

    if not articles:
        print("No articles found for the specified parameters.")
    else:
        for i, article in enumerate(articles, 1):
            print(f"\nArticle {i}")
            print(f"Title: {article.get('title')}")
            print(f"Description: {article.get('description')}")
            print(f"Published At: {article.get('publishedAt')}")
            print(f"Source: {article.get('source', {}).get('name')}")
            print(f"URL: {article.get('url')}")
