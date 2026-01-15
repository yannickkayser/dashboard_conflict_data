# piplinematchingCountry.py
#
# Automated pipeline to match articles with countries and build indices

import os
import time
import sqlite3
from datetime import datetime
from pathlib import Path

from utils import get_db_connection, init_logger

# =============================
# CONFIGURATION
# =============================
class PipelineConfig:
    """Configuration for GNews pipeline"""
    
    def __init__(self):
        # Database paths
        base_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(base_dir, "..", "data")
        data_dir = os.path.abspath(data_dir)
        os.makedirs(data_dir, exist_ok=True)
        
        self.raw_db = os.path.join(data_dir, "gnews_articles_from2023.db")
        self.dedup_db = os.path.join(data_dir, "deleted_dupgnews2023.db")
        self.conflict_db = os.path.join(data_dir, "conflict_data.db")
        self.matched_db = os.path.join(data_dir, "matched_conflict.db")
        
        # Table names
        self.raw_table = "articles"
        self.dedup_table = "article_without_duplicates"
        self.final_table = "articles_eng"

class PerformanceMetrics:
    """Track performance metrics for each pipeline step"""
    
    def __init__(self):
        self.metrics = {}
        self.start_time = None
    
    def start_step(self, step_name: str):
        self.current_step = step_name
        self.start_time = time.time()
    
    def end_step(self):
        if self.start_time:
            self.metrics[self.current_step] = time.time() - self.start_time
            self.start_time = None
    
    def get_summary(self) -> str:
        if not self.metrics:
            return "No metrics recorded"
        
        lines = ["\n" + "=" * 60, "PERFORMANCE METRICS", "=" * 60]
        total = sum(self.metrics.values())
        
        for step, duration in self.metrics.items():
            pct = (duration / total * 100) if total > 0 else 0
            lines.append(f"{step:.<45} {duration:>8.2f}s ({pct:>5.1f}%)")
        
        lines.extend(["-" * 60, f"{'TOTAL TIME':.<45} {total:>8.2f}s", "=" * 60])
        return "\n".join(lines)


def table_cols(cur: sqlite3.Cursor, db_alias: str, table: str) -> list:
    """Get column names from a table"""
    rows = cur.execute(f"PRAGMA {db_alias}.table_info({table});").fetchall()
    return [r[1] for r in rows]


def match_countries_to_articles(art_db: str, conflict_db: str, out_db: str, logger):
    """
    Match countries to articles with date restriction.
    Articles must be published on or before the latest conflict date.
    """
    logger.info("Matching articles to countries")
    logger.info(f"  Article DB: {art_db}")
    logger.info(f"  Conflict DB: {conflict_db}")
    logger.info(f"  Output DB: {out_db}")
    
    conn = get_db_connection(out_db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    try:
        # Set performance pragmas
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA temp_store=MEMORY;")
        
        # Attach source databases
        cur.execute("ATTACH DATABASE ? AS art;", (art_db,))
        cur.execute("ATTACH DATABASE ? AS conf;", (conflict_db,))
        
        # Validate tables exist
        art_cols = table_cols(cur, "art", "articles_eng")
        conf_cols = table_cols(cur, "conf", "conflict_country")
        events_cols = table_cols(cur, "conf", "events")
        
        if not art_cols:
            raise RuntimeError("Table not found: art.articles_eng")
        if not conf_cols:
            raise RuntimeError("Table not found: conf.conflict_country")
        if not events_cols:
            raise RuntimeError("Table not found: conf.events")
            
        if "article_country" not in art_cols:
            raise RuntimeError("Missing column: art.articles_eng.article_country")
        if "country" not in conf_cols:
            raise RuntimeError("Missing column: conf.conflict_country.country")
        if "event_date" not in events_cols:
            raise RuntimeError("Missing column: conf.events.event_date")
        
        logger.info("Schema validation passed")
        
        # Get the latest conflict date per country
        logger.info("Finding latest conflict dates per country...")
        latest_dates = cur.execute("""
            SELECT TRIM(country) as country, MAX(DATE(event_date)) as latest_date
            FROM events
            WHERE country IS NOT NULL AND TRIM(country) != ''
            GROUP BY TRIM(country)
        """).fetchall()
        
        logger.info(f"Found latest dates for {len(latest_dates)} countries")
        
        # Exclude unwanted columns
        EXCLUDE_ART_COLS = {"event_type", "tfidf_terms_de", "tfidf_terms_en"}
        art_cols_filtered = [c for c in art_cols if c not in EXCLUDE_ART_COLS]
        
        # Build column select strings
        art_select = ", ".join([f'a."{c}"' for c in art_cols_filtered])
        conf_select_all = ", ".join([f'c."{c}"' for c in conf_cols])
        conf_select_country = 'TRIM(c.country) AS country'
        
        # Build WIDE table
        logger.info("Building WIDE table...")
        cur.execute("DROP TABLE IF EXISTS match_country_wide;")
        
        wide_cols = []
        wide_cols.extend([f'art_{c} TEXT' for c in art_cols_filtered])
        wide_cols.extend([f'conf_{c} TEXT' for c in conf_cols])
        
        cur.execute(f"CREATE TABLE match_country_wide ({', '.join(wide_cols)})")
        
        total_inserted = 0
        start_time = time.time()
        
        for idx, row in enumerate(latest_dates, 1):
            country = row[0]
            latest_date = row[1]
            
            if idx % 10 == 0 or idx == 1:
                elapsed = time.time() - start_time
                rate = idx / elapsed if elapsed > 0 else 0
                eta = (len(latest_dates) - idx) / rate if rate > 0 else 0
                logger.info(f"  Wide: {idx}/{len(latest_dates)} ({100*idx/len(latest_dates):.1f}%) - ETA: {eta/60:.1f} min")
            
            cur.execute(f"""
                INSERT INTO match_country_wide
                SELECT {art_select}, {conf_select_all}
                FROM art.articles_eng a
                INNER JOIN conf.conflict_country c ON TRIM(c.country) = ?
                WHERE TRIM(a.article_country) = ?
                  AND DATE(a.publishedAt) <= DATE(?)
            """, (country, country, latest_date))
            
            total_inserted += cur.rowcount
            if idx % 10 == 0:
                conn.commit()
        
        conn.commit()
        logger.info(f"Wide table: {total_inserted:,} rows in {time.time() - start_time:.1f}s")
        
        # Build SLIM table
        logger.info("Building SLIM table...")
        cur.execute("DROP TABLE IF EXISTS match_country_slim;")
        
        slim_cols = []
        slim_cols.extend([f'art_{c} TEXT' for c in art_cols_filtered])
        slim_cols.append('country TEXT')
        
        cur.execute(f"CREATE TABLE match_country_slim ({', '.join(slim_cols)})")
        
        total_inserted = 0
        start_time = time.time()
        
        for idx, row in enumerate(latest_dates, 1):
            country = row[0]
            latest_date = row[1]
            
            if idx % 10 == 0 or idx == 1:
                elapsed = time.time() - start_time
                rate = idx / elapsed if elapsed > 0 else 0
                eta = (len(latest_dates) - idx) / rate if rate > 0 else 0
                logger.info(f"  Slim: {idx}/{len(latest_dates)} ({100*idx/len(latest_dates):.1f}%) - ETA: {eta/60:.1f} min")
            
            # FIXED: Now consistent with WIDE table - using <= instead of >=
            cur.execute(f"""
                INSERT INTO match_country_slim
                SELECT {art_select}, {conf_select_country}
                FROM art.articles_eng a
                INNER JOIN conf.conflict_country c ON TRIM(c.country) = ?
                WHERE TRIM(a.article_country) = ?
                  AND DATE(a.publishedAt) <= DATE(?)
            """, (country, country, latest_date))
            
            total_inserted += cur.rowcount
            if idx % 10 == 0:
                conn.commit()
        
        conn.commit()
        logger.info(f"Slim table: {total_inserted:,} rows in {time.time() - start_time:.1f}s")
        
        # Create indexes
        logger.info("Creating indexes...")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wide_art_country ON match_country_wide(art_article_country);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wide_conf_country ON match_country_wide(conf_country);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_slim_art_country ON match_country_slim(art_article_country);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_slim_country ON match_country_slim(country);")
        conn.commit()
        
        logger.info("✓ Matching completed")
        
    finally:
        try:
            cur.execute("DETACH DATABASE art;")
            cur.execute("DETACH DATABASE conf;")
        except:
            pass
        conn.close()


def build_coverage_country(out_db: str, logger):
    """Build coverage_country table"""
    logger.info("Building coverage_country table...")
    
    conn = sqlite3.connect(out_db)
    cur = conn.cursor()
    
    try:
        cur.execute("DROP TABLE IF EXISTS coverage_country;")
        
        cur.execute("""
            CREATE TABLE coverage_country AS
            SELECT
                TRIM(country) AS country,
                COUNT(*) AS n_articles
            FROM match_country_slim
            WHERE country IS NOT NULL AND TRIM(country) != ''
            GROUP BY TRIM(country);
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_coverage_country_country ON coverage_country(country);")
        conn.commit()
        
        n_countries = cur.execute("SELECT COUNT(*) FROM coverage_country;").fetchone()[0]
        total_articles = cur.execute("SELECT SUM(n_articles) FROM coverage_country;").fetchone()[0]
        
        logger.info(f"Coverage: {n_countries} countries, {total_articles:,} articles")
        logger.info("✓ Coverage table completed")
        
    finally:
        conn.close()


def log_final_statistics(out_db: str, logger):
    """Log final statistics"""
    logger.info("=" * 60)
    logger.info("FINAL DATABASE STATISTICS")
    logger.info("=" * 60)
    
    conn = sqlite3.connect(out_db)
    cur = conn.cursor()
    
    # Matching statistics
    stats = cur.execute("""
        SELECT COUNT(*) as total, COUNT(DISTINCT art_id) as articles,
               COUNT(DISTINCT country) as countries
        FROM match_country_slim
    """).fetchone()
    
    logger.info(f"Total matches: {stats[0]:,}")
    logger.info(f"Unique articles: {stats[1]:,}")
    logger.info(f"Countries: {stats[2]:,}")
    
    # Top 5 countries
    logger.info("\nTop 5 countries by coverage:")
    top = cur.execute("""
        SELECT country, n_articles FROM coverage_country
        ORDER BY n_articles DESC LIMIT 5
    """).fetchall()
    
    for i, (country, n_articles) in enumerate(top, 1):
        logger.info(f"  {i}. {country}: {n_articles:,} articles")
    
    logger.info("=" * 60)
    conn.close()


def main():
    """Main pipeline execution"""
    logger = init_logger("matching_country_pipeline")
    metrics = PerformanceMetrics()
    
    pipeline_start = time.time()
    
    logger.info("=" * 60)
    logger.info("ARTICLE-COUNTRY MATCHING PIPELINE")
    logger.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    try:
        # Load configuration
        config = PipelineConfig()

        logger.info(f"\nConfiguration:")
        logger.info(f"  Raw DB: {config.raw_db}")
        logger.info(f"  Dedup DB: {config.dedup_db}")
        logger.info(f"  Conflict DB: {config.conflict_db}")
        logger.info(f"  Matching DB: {config.matched_db}")
        logger.info(f"  Restriction: Articles <= latest conflict date per country")
        
        # Step 1: Match countries to articles
        logger.info("\n" + "=" * 60)
        logger.info("STEP 1: MATCHING")
        logger.info("=" * 60)
        metrics.start_step("1. Match Countries to Articles")
        match_countries_to_articles(config.dedup_db, config.conflict_db, config.matched_db, logger)
        metrics.end_step()
        
        # Step 2: Build coverage table
        logger.info("\n" + "=" * 60)
        logger.info("STEP 2: COVERAGE")
        logger.info("=" * 60)
        metrics.start_step("2. Build Coverage Country")
        build_coverage_country(config.matched_db, logger)
        metrics.end_step()
        
        # Final statistics
        log_final_statistics(config.matched_db, logger)
        logger.info(metrics.get_summary())
        
        duration = time.time() - pipeline_start
        logger.info("\n" + "=" * 60)
        logger.info("✓ PIPELINE COMPLETED")
        logger.info(f"Duration: {duration:.2f}s ({duration/60:.2f} min)")
        logger.info(f"Output: {config.matched_db}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.exception("\n✗ PIPELINE FAILED")
        raise


if __name__ == "__main__":
    main()