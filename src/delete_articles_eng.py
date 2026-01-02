from pathlib import Path
import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # <- one level above src/
GNEWS_DB = PROJECT_ROOT / "data" / "deleted_dupgnews2023.db"

def main() -> None:
    print("GNEWS_DB =", GNEWS_DB)
    if not GNEWS_DB.exists():
        raise FileNotFoundError(f"DB not found: {GNEWS_DB}")

    with sqlite3.connect(str(GNEWS_DB)) as con:
        con.execute("DROP TABLE IF EXISTS article_without_duplicates;")
        #con.execute("DROP TABLE IF EXISTS tresholds;")
        con.commit()

    print("Dropped table (if it existed): articles_eng")

if __name__ == "__main__":
    main()




