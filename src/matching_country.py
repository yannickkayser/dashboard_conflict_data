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

ART_TABLE = "article_eng"
CONFLICT_TABLE = "conflict_country"
OUT_TABLE = "match_country_wide"


def table_cols(cur: sqlite3.Cursor, db_alias: str, table: str) -> list[str]:
    # PRAGMA table_info returns one row per column; column name is in field [1]. [web:86]
    rows = cur.execute(f"PRAGMA {db_alias}.table_info({table});").fetchall()
    return [r[1] for r in rows]


def main():
    logger = init_logger("matching_country")
    logger.info("Starting country matching (articles -> conflict_country).")
    logger.info("ART_DB=%s", ART_DB)
    logger.info("CONFLICT_DB=%s", CONFLICT_DB)
    logger.info("OUT_DB=%s", OUT_DB)

    # OUT_DB is the main db we write to; inputs are attached for cross-db join. [web:82][web:80]
    conn = get_db_connection(str(OUT_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        # Optional speed pragmas
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")

        # Attach sources
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
            raise RuntimeError("Missing column: art.article_eng.article_country")
        if "country" not in conf_cols:
            raise RuntimeError("Missing column: conf.conflict_country.country")

        # Prefix columns to avoid name collisions in the output table
        art_select = ",\n            ".join([f'a."{c}" AS art_{c}' for c in art_cols])
        conf_select = ",\n            ".join([f'c."{c}" AS conf_{c}' for c in conf_cols])

        logger.info("Rebuilding output table %s (ONLY matched rows via INNER JOIN)...", OUT_TABLE)
        cur.execute(f"DROP TABLE IF EXISTS {OUT_TABLE};")

        # ONLY matched rows: INNER JOIN. [web:99]
        cur.execute(f"""
            CREATE TABLE {OUT_TABLE} AS
            SELECT
            {art_select},
            {conf_select}
            FROM art.{ART_TABLE} a
            INNER JOIN conf.{CONFLICT_TABLE} c
              ON TRIM(a.article_country) = TRIM(c.country);
        """)
        conn.commit()

        logger.info("Creating indexes...")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE}_art_country ON {OUT_TABLE}(art_article_country);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE}_conf_country ON {OUT_TABLE}(conf_country);")
        conn.commit()

        n = cur.execute(f"SELECT COUNT(*) FROM {OUT_TABLE};").fetchone()[0]
        logger.info("Done. Rows in %s: %d", OUT_TABLE, n)

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
