"""
fetch_GNEWS.py
Fetch all GNews articles between 2023-01-01 and 2025-11-08 (paid plan with historical access).
Stores all articles in a local SQLite database.
"""

import time
import requests
import sqlalchemy as db
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# =============================
# 1. API CONFIGURATION
# =============================
API_KEY = "cbc7d3f5fe399cb90da7301863ecf370"   
BASE_URL = "https://gnews.io/api/v4/search"
LANG = "de"
COUNTRY = "de"
QUERY = (
    "Protest OR Demonstration OR Streik OR Blockade OR "
    "Gewalt OR Angriff OR Konflikt OR Aufstand OR Wahl OR Militär OR Politik OR Menschenrechte OR Bürgerkrieg OR Pressefreiheit OR Militär"
    )

# =============================
# 2. DATABASE CONFIGURATION
# =============================
DATABASE_URI = "sqlite:///gnews_articles.db"
engine = db.create_engine(DATABASE_URI)
metadata = db.MetaData()

articles_table = db.Table(
    "articles",
    metadata,
    db.Column("id", db.String, primary_key=True),
    db.Column("publishedAt", db.DateTime),
    db.Column("title", db.String),
    db.Column("description", db.String),
    db.Column("content", db.String),
    db.Column("url", db.String),
    db.Column("source_name", db.String),
    db.Column("source_url", db.String),
)
metadata.create_all(engine)
connection = engine.connect()

# =============================
# 3. HELPER FUNCTIONS
# =============================
def save_articles(article_list):
    """Insert all fetched articles into the database."""
    for article in article_list:
        try:
            data = {
                "id": article.get("url"),
                "publishedAt": datetime.fromisoformat(article["publishedAt"].replace("Z", "+00:00")),
                "title": article.get("title"),
                "description": article.get("description"),
                "content": article.get("content"),
                "url": article.get("url"),
                "source_name": article["source"].get("name"),
                "source_url": article["source"].get("url"),
            }
            connection.execute(articles_table.insert(), [data])
        except Exception as e:
            # Duplicate entries or other DB constraint violations
            print(f"⚠️ Skipping article: {e}")

def fetch_articles_for_day(date):
    """Fetch all paginated articles for a single day."""
    next_date = date + timedelta(days=1)
    page = 1
    total_fetched = 0

    while True:
        params = {
            "q": QUERY,
            "token": API_KEY,
            "lang": LANG,
            "country": COUNTRY,
            "from": date.strftime("%Y-%m-%dT00:00:00Z"),
            "to": next_date.strftime("%Y-%m-%dT00:00:00Z"),
            "sortby": "relevance",
            "page": page,
            "max": 100,  # ignored by some plans, but harmless
        }
        r = requests.get(BASE_URL, params=params)

        if r.status_code != 200:
            print(f"❌ Error {r.status_code} for {date.date()}: {r.text}")
            break

        res = r.json()
        articles = res.get("articles", [])
        if not articles:
            break  # no more pages

        save_articles(articles)
        total_fetched += len(articles)
        print(f"✅ {len(articles)} articles fetched from page {page} ({date.date()})")

        # Stop if fewer than 25 articles (end of pagination)
        if len(articles) < 25:
            break

        page += 1
        time.sleep(1)  # respect API rate limit

    print(f"📅 Total {total_fetched} articles saved for {date.date()}")


def fetch_articles_monthly(start_date, end_date):
    """Iterate month by month and fetch all articles."""
    current = start_date
    while current < end_date:
        next_month = current + relativedelta(months=1)
        print(f"\n=== Fetching month: {current.strftime('%B %Y')} ===")

        day = current
        while day < next_month and day < end_date:
            fetch_articles_for_day(day)
            day += timedelta(days=1)

        current = next_month


# =============================
# 4. RUN SCRIPT
# =============================
if __name__ == "__main__":
    start_date = datetime(2023, 1, 1)
    end_date = datetime(2025, 11, 8)

    print(f"🚀 Fetching articles from {start_date.date()} to {end_date.date()} ...")
    fetch_articles_monthly(start_date, end_date)
    print("✅ All articles saved in gnews_articles.db.")
