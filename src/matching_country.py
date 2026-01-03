# src/matching_country.py
import sqlite3
from pathlib import Path

from utils import get_db_connection, init_logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

# Inputs
ART_DB = DATA_DIR / "deleted_dupgnews2023.db"
CONFLICT_DB = DATA_DIR / "conflict_data.db"  # contains conflict_country

# Output
OUT_DB = DATA_DIR / "matching_country.db"

ART_TABLE = "articles_eng"
CONFLICT_TABLE = "conflict_country"

# Outputs
OUT_TABLE_WIDE = "match_country_wide"   # articles_eng cols + ALL conflict_country cols (legacy behavior)
OUT_TABLE_SLIM = "match_country_slim"   # articles_eng cols + ONLY conflict_country.country


def table_cols(cur: sqlite3.Cursor, db_alias: str, table: str) -> list[str]:
    rows = cur.execute(f"PRAGMA {db_alias}.table_info({table});").fetchall()
    return [r[1] for r in rows]


def main():
    logger = init_logger("matching_country")
    logger.info("Starting country matching (articles -> conflict_country).")
    logger.info("ART_DB=%s", ART_DB)
    logger.info("CONFLICT_DB=%s", CONFLICT_DB)
    logger.info("OUT_DB=%s", OUT_DB)

    conn = get_db_connection(str(OUT_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")

        # Attach sources (cross-db join). [web:124]
        cur.execute("ATTACH DATABASE ? AS art;", (str(ART_DB),))
        cur.execute("ATTACH DATABASE ? AS conf;", (str(CONFLICT_DB),))

        # Validate schema
        art_cols = table_cols(cur, "art", ART_TABLE)
        conf_cols = table_cols(cur, "conf", CONFLICT_TABLE)

        if not art_cols:
            raise RuntimeError(f"Table not found or empty schema: art.{ART_TABLE}")
        if not conf_cols:
            raise RuntimeError(f"Table not found or empty schema: conf.{CONFLICT_TABLE}")

        if "article_country" not in art_cols:
            raise RuntimeError("Missing column: art.articles_eng.article_country")
        if "country" not in conf_cols:
            raise RuntimeError("Missing column: conf.conflict_country.country")

        # --- Exclude columns you don't want to propagate even if they still exist physically
        EXCLUDE_ART_COLS = {"event_type", "tfidf_terms_de", "tfidf_terms_en"}
        art_cols_filtered = [c for c in art_cols if c not in EXCLUDE_ART_COLS]

        # Prefix columns to avoid name collisions in outputs
        art_select = ",\n            ".join([f'a."{c}" AS art_{c}' for c in art_cols_filtered])

        # Original behavior (wide): keep all conflict columns
        conf_select_all = ",\n            ".join([f'c."{c}" AS conf_{c}' for c in conf_cols])

        # New slim behavior: keep only country
        conf_select_country_only = 'TRIM(c."country") AS country'

        # -------------------------
        # Output 1: WIDE (legacy)
        logger.info("Rebuilding %s (matched rows, includes ALL conflict columns)...", OUT_TABLE_WIDE)
        cur.execute(f"DROP TABLE IF EXISTS {OUT_TABLE_WIDE};")
        cur.execute(f"""
            CREATE TABLE {OUT_TABLE_WIDE} AS
            SELECT
            {art_select},
            {conf_select_all}
            FROM art.{ART_TABLE} a
            INNER JOIN conf.{CONFLICT_TABLE} c
              ON TRIM(a.article_country) = TRIM(c.country);
        """)  # CREATE TABLE AS SELECT creates+populates from query. [web:76]
        conn.commit()

        # -------------------------
        # Output 2: SLIM (requested)
        logger.info("Rebuilding %s (matched rows, ONLY articles_eng + country)...", OUT_TABLE_SLIM)
        cur.execute(f"DROP TABLE IF EXISTS {OUT_TABLE_SLIM};")
        cur.execute(f"""
            CREATE TABLE {OUT_TABLE_SLIM} AS
            SELECT
            {art_select},
            {conf_select_country_only}
            FROM art.{ART_TABLE} a
            INNER JOIN conf.{CONFLICT_TABLE} c
              ON TRIM(a.article_country) = TRIM(c.country);
        """)
        conn.commit()

        logger.info("Creating indexes...")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_WIDE}_art_country ON {OUT_TABLE_WIDE}(art_article_country);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_WIDE}_conf_country ON {OUT_TABLE_WIDE}(conf_country);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_SLIM}_art_country ON {OUT_TABLE_SLIM}(art_article_country);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_SLIM}_country ON {OUT_TABLE_SLIM}(country);")
        conn.commit()

        n_wide = cur.execute(f"SELECT COUNT(*) FROM {OUT_TABLE_WIDE};").fetchone()[0]
        n_slim = cur.execute(f"SELECT COUNT(*) FROM {OUT_TABLE_SLIM};").fetchone()[0]
        logger.info("Done. Rows in %s: %d", OUT_TABLE_WIDE, n_wide)
        logger.info("Done. Rows in %s: %d", OUT_TABLE_SLIM, n_slim)

    except Exception:
        logger.exception("matching_country failed.")
        raise
    finally:
        try:
            cur.execute("DETACH DATABASE art;")
            cur.execute("DETACH DATABASE conf;")
        except Exception:
            pass
        conn.close()
        logger.info("Closed DB connection.")


if __name__ == "__main__":
    main()
