import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFLICT_DB = PROJECT_ROOT / "data" / "conflict_data.db"

CONFLICT_TABLE = "conflict_features"

DROP_TABLES = [
    "conflict_article_bestmatch",
    "conflict_article_bestmatch_wide",
]

DROP_COLS = [
    "matched_article_rowid",
    "match_total_score",
    "match_actor_score",
    "match_kw_score",
    "matched_actors",
    "matched_keywords",
    "article_publishedAt",
    "article_url",
    "article_source_name",
    "article_source_url",
    "article_title_en",
    "article_description_en",
    "article_content",
    "article_content_en",
    "article_country",
    "article_country_score",
    #"article_kw_1",
    #"article_kw_2",
   # "article_kw_3",
]

def main():
    with sqlite3.connect(str(CONFLICT_DB)) as con:
        # 0) Optional: Version anzeigen
        v = con.execute("select sqlite_version();").fetchone()[0]
        print("SQLite version:", v)

        # 1) Tabellen löschen
        for t in DROP_TABLES:
            con.execute(f"DROP TABLE IF EXISTS {t};")  # ok wenn sie nicht existiert
        con.commit()

        # 2) Spalten löschen
        # (funktioniert nur auf neueren SQLite-Versionen und wenn keine Abhängigkeiten bestehen)
        for c in DROP_COLS:
            con.execute(f"ALTER TABLE {CONFLICT_TABLE} DROP COLUMN {c};")
        con.commit()

if __name__ == "__main__":
    main()
