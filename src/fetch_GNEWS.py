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
    "Protest OR Demonstration OR Streik OR Unruhen OR Ausschreitungen OR Gewalt OR Angriff OR Anschlag OR Terror OR Extremismus OR Polizei OR Festnahme OR Krieg OR Konflikt OR Wahl OR Korruption"
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
engine = db.create_engine(DATABASE_URI)

# =============================
# 3. HELPER FUNCTIONS
# =============================
def save_articles(article_list):
    with engine.begin() as conn:   # <-- automatisch commit
        for article in article_list:
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
            stmt = db.insert(articles_table).prefix_with("OR IGNORE")
            conn.execute(stmt, [data])


def fetch_articles_for_day(date):
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
            "max": 100,
        }

        r = requests.get(BASE_URL, params=params)

        # 💥 Rate Limit erreicht → Skript vollständig stoppen
        if r.status_code in [403, 429]:
            print(f"❌ RATE LIMIT erreicht bei {date.date()} page {page}")
            print(f"   Antwort: {r.text}")
            raise SystemExit("⛔ Skript gestoppt wegen Rate Limit.")

        # Andere API-Fehler
        if r.status_code != 200:
            print(f"❌ Error {r.status_code} für {date.date()}: {r.text}")
            raise SystemExit("⛔ Skript gestoppt wegen API-Fehler.")

        res = r.json()
        articles = res.get("articles", [])
        if not articles:
            break

        save_articles(articles)
        total_fetched += len(articles)
        print(f"✅ {len(articles)} articles fetched from page {page} ({date.date()})")

        if len(articles) < 25:
            break

        page += 1
        time.sleep(1)

    print(f"📅 Total {total_fetched} articles saved for {date.date()}")


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
    start_date = datetime(2020, 10, 21)
    end_date = datetime(2025, 11, 18)

    print(f"🚀 Fetching articles from {start_date.date()} to {end_date.date()} ...")
    fetch_articles_monthly(start_date, end_date)
    print("✅ All articles saved in gnews_articles.db.")


import os
print("Working directory:", os.getcwd())
print("DB path:", os.path.abspath("gnews_articles.db"))
print("Size (bytes):", os.path.getsize("gnews_articles.db"))
