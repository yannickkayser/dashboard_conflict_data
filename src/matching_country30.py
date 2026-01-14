# src/matching_conflict_time.py
import sqlite3
from pathlib import Path

from utils import get_db_connection, init_logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

# Inputs
ART_DB = DATA_DIR / "deleted_dupgnews2023.db"
CONFLICT_DB = DATA_DIR / "conflict_data.db"

# Output
OUT_DB = DATA_DIR / "matching_conflict.db"

ART_TABLE = "articles_eng"
CONFLICT_TABLE = "unique_conflict"
CONFLICT_FEATURES_TABLE = "conflict_features"
CONFLICT_TIME_TABLE = "conflict_time"

# Outputs
OUT_TABLE_WIDE = "match_conflict_wide"
OUT_TABLE_SLIM = "match_conflict_slim"

# Time window for matching (days)
TIME_WINDOW_DAYS = 30


def table_cols(cur: sqlite3.Cursor, db_alias: str, table: str) -> list[str]:
    rows = cur.execute(f"PRAGMA {db_alias}.table_info({table});").fetchall()
    return [r[1] for r in rows]


def main():
    logger = init_logger("matching_conflict_time")
    logger.info("Starting conflict-article matching with time window.")
    logger.info("ART_DB=%s", ART_DB)
    logger.info("CONFLICT_DB=%s", CONFLICT_DB)
    logger.info("OUT_DB=%s", OUT_DB)
    logger.info("TIME_WINDOW_DAYS=%d", TIME_WINDOW_DAYS)

    conn = get_db_connection(str(OUT_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA temp_store=MEMORY;")
        cur.execute("PRAGMA cache_size=-2000000;")  # 2GB cache

        # Attach sources
        cur.execute("ATTACH DATABASE ? AS art;", (str(ART_DB),))
        cur.execute("ATTACH DATABASE ? AS conf;", (str(CONFLICT_DB),))

        # Validate schema
        art_cols = table_cols(cur, "art", ART_TABLE)
        conf_cols = table_cols(cur, "conf", CONFLICT_TABLE)
        conf_features_cols = table_cols(cur, "conf", CONFLICT_FEATURES_TABLE)
        conf_time_cols = table_cols(cur, "conf", CONFLICT_TIME_TABLE)

        if not art_cols:
            raise RuntimeError(f"Table not found: art.{ART_TABLE}")
        if not conf_cols:
            raise RuntimeError(f"Table not found: conf.{CONFLICT_TABLE}")
        if not conf_features_cols:
            raise RuntimeError(f"Table not found: conf.{CONFLICT_FEATURES_TABLE}")
        if not conf_time_cols:
            raise RuntimeError(f"Table not found: conf.{CONFLICT_TIME_TABLE}")

        # Validate required columns
        if "article_country" not in art_cols:
            raise RuntimeError("Missing column: art.articles_eng.article_country")
        if "publishedAt" not in art_cols:
            raise RuntimeError("Missing column: art.articles_eng.publishedAt")
        if "conflict_id" not in conf_cols:
            raise RuntimeError("Missing column: conf.unique_conflict.conflict_id")
        if "conflict_id" not in conf_features_cols:
            raise RuntimeError("Missing column: conf.conflict_features.conflict_id")
        if "country" not in conf_features_cols:
            raise RuntimeError("Missing column: conf.conflict_features.country")
        if "conflict_id" not in conf_time_cols:
            raise RuntimeError("Missing column: conf.conflict_time.conflict_id")
        if "start_date" not in conf_time_cols:
            raise RuntimeError("Missing column: conf.conflict_time.start_date")
        if "end_date" not in conf_time_cols:
            raise RuntimeError("Missing column: conf.conflict_time.end_date")
        if "mid_date" not in conf_time_cols:
            raise RuntimeError("Missing column: conf.conflict_time.mid_date")

        # Note: We can't create indexes on attached databases from here
        # If source tables don't have indexes, performance may be slower
        # Consider creating indexes directly on source databases beforehand
        
        # Analyze tables to update query planner statistics
        logger.info("Running ANALYZE on source tables...")
        try:
            cur.execute("ANALYZE art.articles_eng;")
            cur.execute("ANALYZE conf.conflict_features;")
            cur.execute("ANALYZE conf.conflict_time;")
            cur.execute("ANALYZE conf.unique_conflict;")
            conn.commit()
            logger.info("ANALYZE completed successfully")
        except sqlite3.OperationalError as e:
            logger.warning("Could not run ANALYZE on attached databases: %s", e)
            logger.warning("Query performance may be suboptimal")

        # Exclude unwanted columns from articles
        EXCLUDE_ART_COLS = {"event_type", "tfidf_terms_de", "tfidf_terms_en"}
        art_cols_filtered = [c for c in art_cols if c not in EXCLUDE_ART_COLS]

        # Prefix columns
        art_select = ",\n            ".join([f'a."{c}" AS art_{c}' for c in art_cols_filtered])
        conf_select_all = ",\n            ".join([f'uc."{c}" AS conf_{c}' for c in conf_cols])
        feat_select_all = ",\n            ".join([f'cf."{c}" AS feat_{c}' for c in conf_features_cols])
        time_select = ",\n            ".join([f't."{c}" AS time_{c}' for c in conf_time_cols if c != "conflict_id"])

        # Slim version: only essential columns
        conf_select_slim = """uc.conflict_id,
            uc.n_events,
            uc.total_fatalities"""
        feat_select_slim = """TRIM(cf.country) AS country,
            cf.conflict_key"""
        time_select_slim = """t.start_date,
            t.end_date,
            t.mid_date"""

        # -------------------------
        # Output 1: WIDE (all columns)
        logger.info("Rebuilding %s (matched rows with time window)...", OUT_TABLE_WIDE)
        logger.info("This may take 1-3 minutes depending on your disk speed...")
        cur.execute(f"DROP TABLE IF EXISTS {OUT_TABLE_WIDE};")
        
        import time
        start_time = time.time()
        
        cur.execute(f"""
            CREATE TABLE {OUT_TABLE_WIDE} AS
            SELECT
            {art_select},
            {conf_select_all},
            {feat_select_all},
            {time_select}
            FROM art.{ART_TABLE} a
            INNER JOIN conf.{CONFLICT_FEATURES_TABLE} cf
              ON TRIM(a.article_country) = TRIM(cf.country)
            INNER JOIN conf.{CONFLICT_TABLE} uc
              ON cf.conflict_id = uc.conflict_id
            INNER JOIN conf.{CONFLICT_TIME_TABLE} t
              ON uc.conflict_id = t.conflict_id
            WHERE 
              -- Article published within time window around conflict mid_date
              DATE(a.publishedAt) BETWEEN 
                DATE(t.mid_date, '-{TIME_WINDOW_DAYS} days') 
                AND DATE(t.mid_date, '+{TIME_WINDOW_DAYS} days')
              -- Alternative: use start_date and end_date with buffer
              -- DATE(a.publishedAt) BETWEEN 
              --   DATE(t.start_date, '-{TIME_WINDOW_DAYS} days')
              --   AND DATE(t.end_date, '+{TIME_WINDOW_DAYS} days')
        """)
        conn.commit()
        
        elapsed_wide = time.time() - start_time
        logger.info("Wide table created in %.2f seconds", elapsed_wide)

        # -------------------------
        # Output 2: SLIM (essential columns only)
        logger.info("Rebuilding %s (essential columns only)...", OUT_TABLE_SLIM)
        cur.execute(f"DROP TABLE IF EXISTS {OUT_TABLE_SLIM};")
        
        start_time = time.time()
        
        cur.execute(f"""
            CREATE TABLE {OUT_TABLE_SLIM} AS
            SELECT
            {art_select},
            {conf_select_slim},
            {feat_select_slim},
            {time_select_slim}
            FROM art.{ART_TABLE} a
            INNER JOIN conf.{CONFLICT_FEATURES_TABLE} cf
              ON TRIM(a.article_country) = TRIM(cf.country)
            INNER JOIN conf.{CONFLICT_TABLE} uc
              ON cf.conflict_id = uc.conflict_id
            INNER JOIN conf.{CONFLICT_TIME_TABLE} t
              ON uc.conflict_id = t.conflict_id
            WHERE 
              DATE(a.publishedAt) BETWEEN 
                DATE(t.mid_date, '-{TIME_WINDOW_DAYS} days') 
                AND DATE(t.mid_date, '+{TIME_WINDOW_DAYS} days')
        """)
        conn.commit()
        
        elapsed_slim = time.time() - start_time
        logger.info("Slim table created in %.2f seconds", elapsed_slim)

        logger.info("Creating indexes...")
        # Wide table indexes
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_WIDE}_conflict_id ON {OUT_TABLE_WIDE}(conf_conflict_id);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_WIDE}_country ON {OUT_TABLE_WIDE}(feat_country);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_WIDE}_published ON {OUT_TABLE_WIDE}(art_publishedAt);")
        
        # Slim table indexes
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_SLIM}_conflict_id ON {OUT_TABLE_SLIM}(conflict_id);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_SLIM}_country ON {OUT_TABLE_SLIM}(country);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_SLIM}_published ON {OUT_TABLE_SLIM}(art_publishedAt);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_SLIM}_mid_date ON {OUT_TABLE_SLIM}(mid_date);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_SLIM}_conflict_key ON {OUT_TABLE_SLIM}(conflict_key);")
        conn.commit()

        n_wide = cur.execute(f"SELECT COUNT(*) FROM {OUT_TABLE_WIDE};").fetchone()[0]
        n_slim = cur.execute(f"SELECT COUNT(*) FROM {OUT_TABLE_SLIM};").fetchone()[0]
        
        total_time = elapsed_wide + elapsed_slim
        logger.info("=" * 60)
        logger.info("PERFORMANCE SUMMARY:")
        logger.info("  Wide table: %.2f seconds (%d rows)", elapsed_wide, n_wide)
        logger.info("  Slim table: %.2f seconds (%d rows)", elapsed_slim, n_slim)
        logger.info("  Total matching time: %.2f seconds", total_time)
        logger.info("=" * 60)

        # Optional: Log some statistics
        stats = cur.execute(f"""
            SELECT 
                COUNT(DISTINCT conflict_id) as unique_conflicts,
                COUNT(DISTINCT art_id) as unique_articles,
                COUNT(DISTINCT country) as unique_countries,
                MIN(art_publishedAt) as earliest_article,
                MAX(art_publishedAt) as latest_article
            FROM {OUT_TABLE_SLIM}
        """).fetchone()
        
        if stats:
            logger.info("Matching statistics:")
            logger.info("  Unique conflicts matched: %d", stats[0])
            logger.info("  Unique articles matched: %d", stats[1])
            logger.info("  Unique countries: %d", stats[2])
            logger.info("  Date range: %s to %s", stats[3], stats[4])

    except Exception:
        logger.exception("matching_conflict_time failed.")
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