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
OUT_DB = DATA_DIR / "matching_conflict_2days.db"

ART_TABLE = "articles_eng"
CONFLICT_TABLE = "unique_conflict"
CONFLICT_FEATURES_TABLE = "conflict_features"
CONFLICT_TIME_TABLE = "conflict_time"

# Outputs
OUT_TABLE_WIDE = "match_conflict_wide"
OUT_TABLE_SLIM = "match_conflict_slim"

# Time window for matching (days)
TIME_WINDOW_DAYS = 2


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
        #cur.execute("PRAGMA cache_size=-2000000;")  # 2GB cache

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

        # Exclude unwanted columns from articles
        EXCLUDE_ART_COLS = {"event_type", "tfidf_terms_de", "tfidf_terms_en"}
        art_cols_filtered = [c for c in art_cols if c not in EXCLUDE_ART_COLS]

        # Prefix columns
        art_select = ", ".join([f'a."{c}"' for c in art_cols_filtered])
        conf_select_all = ", ".join([f'uc."{c}"' for c in conf_cols])
        feat_select_all = ", ".join([f'cf."{c}"' for c in conf_features_cols])
        time_select = ", ".join([f't."{c}"' for c in conf_time_cols if c != "conflict_id"])

        # Slim version columns
        conf_select_slim = "uc.conflict_id, uc.n_events, uc.total_fatalities"
        feat_select_slim = "TRIM(cf.country) AS country, cf.conflict_key"
        time_select_slim = "t.start_date, t.end_date, t.mid_date"

        import time
        
        # Get list of countries that have both articles and conflicts
        logger.info("Finding countries with both articles and conflicts...")
        countries = cur.execute("""
            SELECT DISTINCT TRIM(cf.country) as country
            FROM conf.conflict_features cf
            WHERE EXISTS (
                SELECT 1 FROM art.articles_eng a 
                WHERE TRIM(a.article_country) = TRIM(cf.country)
            )
            ORDER BY country
        """).fetchall()
        
        n_countries = len(countries)
        logger.info("Found %d countries to process", n_countries)
        
        if n_countries == 0:
            logger.warning("No matching countries found! Check your country names.")
            return

        # -------------------------
        # Output 1: WIDE table - process by country
        logger.info("=" * 60)
        logger.info("Building WIDE table (all columns)...")
        cur.execute(f"DROP TABLE IF EXISTS {OUT_TABLE_WIDE};")
        
        # Create table structure first
        cur.execute(f"""
            CREATE TABLE {OUT_TABLE_WIDE} (
                {', '.join([f'art_{c} TEXT' for c in art_cols_filtered])},
                {', '.join([f'conf_{c} TEXT' for c in conf_cols])},
                {', '.join([f'feat_{c} TEXT' for c in conf_features_cols])},
                {', '.join([f'time_{c} TEXT' for c in conf_time_cols if c != 'conflict_id'])}
            )
        """)
        
        total_inserted = 0
        start_time = time.time()
        
        for idx, (country,) in enumerate(countries, 1):
            if idx % 10 == 0 or idx == 1:
                elapsed = time.time() - start_time
                rate = idx / elapsed if elapsed > 0 else 0
                eta = (n_countries - idx) / rate if rate > 0 else 0
                logger.info("Progress: %d/%d countries (%.1f%%) - %.1f countries/sec - ETA: %.1f min", 
                           idx, n_countries, 100*idx/n_countries, rate, eta/60)
            
            cur.execute(f"""
                INSERT INTO {OUT_TABLE_WIDE}
                SELECT
                    {art_select},
                    {conf_select_all},
                    {feat_select_all},
                    {time_select}
                FROM art.{ART_TABLE} a
                INNER JOIN conf.{CONFLICT_FEATURES_TABLE} cf
                  ON TRIM(cf.country) = ?
                INNER JOIN conf.{CONFLICT_TABLE} uc
                  ON cf.conflict_id = uc.conflict_id
                INNER JOIN conf.{CONFLICT_TIME_TABLE} t
                  ON uc.conflict_id = t.conflict_id
                WHERE 
                  TRIM(a.article_country) = ?
                  AND DATE(a.publishedAt) BETWEEN 
                    DATE(t.mid_date, '-{TIME_WINDOW_DAYS} days') 
                    AND DATE(t.mid_date, '+{TIME_WINDOW_DAYS} days')
            """, (country, country))
            
            inserted = cur.rowcount
            total_inserted += inserted
            
            # Commit every 10 countries to avoid huge transaction
            if idx % 10 == 0:
                conn.commit()
        
        conn.commit()
        elapsed_wide = time.time() - start_time
        logger.info("Wide table completed in %.2f seconds (%.2f min)", elapsed_wide, elapsed_wide/60)
        logger.info("Total rows inserted: %d", total_inserted)

        # -------------------------
        # Output 2: SLIM table - process by country
        logger.info("=" * 60)
        logger.info("Building SLIM table (essential columns only)...")
        cur.execute(f"DROP TABLE IF EXISTS {OUT_TABLE_SLIM};")
        
        # Create table structure
        cur.execute(f"""
            CREATE TABLE {OUT_TABLE_SLIM} (
                {', '.join([f'art_{c} TEXT' for c in art_cols_filtered])},
                conflict_id TEXT,
                n_events TEXT,
                total_fatalities TEXT,
                country TEXT,
                conflict_key TEXT,
                start_date TEXT,
                end_date TEXT,
                mid_date TEXT
            )
        """)
        
        total_inserted = 0
        start_time = time.time()
        
        for idx, (country,) in enumerate(countries, 1):
            if idx % 10 == 0 or idx == 1:
                elapsed = time.time() - start_time
                rate = idx / elapsed if elapsed > 0 else 0
                eta = (n_countries - idx) / rate if rate > 0 else 0
                logger.info("Progress: %d/%d countries (%.1f%%) - %.1f countries/sec - ETA: %.1f min", 
                           idx, n_countries, 100*idx/n_countries, rate, eta/60)
            
            cur.execute(f"""
                INSERT INTO {OUT_TABLE_SLIM}
                SELECT
                    {art_select},
                    {conf_select_slim},
                    {feat_select_slim},
                    {time_select_slim}
                FROM art.{ART_TABLE} a
                INNER JOIN conf.{CONFLICT_FEATURES_TABLE} cf
                  ON TRIM(cf.country) = ?
                INNER JOIN conf.{CONFLICT_TABLE} uc
                  ON cf.conflict_id = uc.conflict_id
                INNER JOIN conf.{CONFLICT_TIME_TABLE} t
                  ON uc.conflict_id = t.conflict_id
                WHERE 
                  TRIM(a.article_country) = ?
                  AND DATE(a.publishedAt) BETWEEN 
                    DATE(t.mid_date, '-{TIME_WINDOW_DAYS} days') 
                    AND DATE(t.mid_date, '+{TIME_WINDOW_DAYS} days')
            """, (country, country))
            
            inserted = cur.rowcount
            total_inserted += inserted
            
            if idx % 10 == 0:
                conn.commit()
        
        conn.commit()
        elapsed_slim = time.time() - start_time
        logger.info("Slim table completed in %.2f seconds (%.2f min)", elapsed_slim, elapsed_slim/60)
        logger.info("Total rows inserted: %d", total_inserted)

        # Create indexes on output tables
        logger.info("=" * 60)
        logger.info("Creating indexes...")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_WIDE}_conflict_id ON {OUT_TABLE_WIDE}(conf_conflict_id);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_WIDE}_country ON {OUT_TABLE_WIDE}(feat_country);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE_WIDE}_published ON {OUT_TABLE_WIDE}(art_publishedAt);")
        
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
        logger.info("  Total matching time: %.2f seconds (%.2f minutes)", total_time, total_time/60)
        logger.info("=" * 60)

        # Statistics
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