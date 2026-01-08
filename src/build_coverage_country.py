# src/build_coverage_country.py
from pathlib import Path
import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_MATCH = PROJECT_ROOT / "data" / "matching_country.db"

def main():
    if not DB_MATCH.exists():
        raise FileNotFoundError(f"Missing DB: {DB_MATCH}")

    con = sqlite3.connect(DB_MATCH)
    cur = con.cursor()

    # Rebuild table
    cur.execute("DROP TABLE IF EXISTS coverage_country;")

    # Assumption: match_country_wide has a column country
    cur.execute("""
        CREATE TABLE coverage_country AS
        SELECT
            TRIM(conf_country) AS country,
            COUNT(*) AS n_articles
        FROM match_country_wide
        WHERE conf_country IS NOT NULL
          AND TRIM(conf_country) != ''
        GROUP BY TRIM(conf_country);
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_coverage_country_country ON coverage_country(country);")
    con.commit()
    con.close()

    print(f"Built coverage_country in {DB_MATCH}")

if __name__ == "__main__":
    main()
