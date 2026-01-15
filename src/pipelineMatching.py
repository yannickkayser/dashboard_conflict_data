# pipeline_matching.py
#
# Automated pipeline to match articles with conflicts and build country-level indices

import time
import sqlite3
from datetime import datetime
from pathlib import Path

from utils import get_db_connection, init_logger


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


def match_conflicts_to_articles(art_db: str, conflict_db: str, out_db: str, 
                                time_window_days: int, logger):
    """
    Match conflicts to articles with time window.
    Adapted from matching_country_time.py to work with the pipeline.
    """
    logger.info(f"Matching articles to conflicts (±{time_window_days} days)")
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
        conf_cols = table_cols(cur, "conf", "unique_conflict")
        conf_features_cols = table_cols(cur, "conf", "conflict_features")
        conf_time_cols = table_cols(cur, "conf", "conflict_time")
        
        if not art_cols:
            raise RuntimeError("Table not found: art.articles_eng")
        if not conf_cols:
            raise RuntimeError("Table not found: conf.unique_conflict")
        if not conf_features_cols:
            raise RuntimeError("Table not found: conf.conflict_features")
        if not conf_time_cols:
            raise RuntimeError("Table not found: conf.conflict_time")
        
        logger.info("Schema validation passed")
        
        # Exclude unwanted columns
        EXCLUDE_ART_COLS = {"event_type", "tfidf_terms_de", "tfidf_terms_en"}
        art_cols_filtered = [c for c in art_cols if c not in EXCLUDE_ART_COLS]
        
        # Build column select strings
        art_select = ", ".join([f'a."{c}"' for c in art_cols_filtered])
        conf_select_all = ", ".join([f'uc."{c}"' for c in conf_cols])
        feat_select_all = ", ".join([f'cf."{c}"' for c in conf_features_cols])
        time_select = ", ".join([f't."{c}"' for c in conf_time_cols if c != "conflict_id"])
        
        # Slim version columns
        conf_select_slim = "uc.conflict_id, uc.n_events, uc.total_fatalities"
        feat_select_slim = "TRIM(cf.country) AS country, cf.conflict_key"
        time_select_slim = "t.start_date, t.end_date, t.mid_date"
        
        # Get countries with both articles and conflicts
        logger.info("Finding matching countries...")
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
        logger.info(f"Found {n_countries} countries to process")
        
        if n_countries == 0:
            raise ValueError("No matching countries found!")
        
        # Build WIDE table
        logger.info("Building WIDE table...")
        cur.execute("DROP TABLE IF EXISTS match_conflict_wide;")
        
        wide_cols = []
        wide_cols.extend([f'art_{c} TEXT' for c in art_cols_filtered])
        wide_cols.extend([f'conf_{c} TEXT' for c in conf_cols])
        wide_cols.extend([f'feat_{c} TEXT' for c in conf_features_cols])
        wide_cols.extend([f'time_{c} TEXT' for c in conf_time_cols if c != 'conflict_id'])
        
        cur.execute(f"CREATE TABLE match_conflict_wide ({', '.join(wide_cols)})")
        
        total_inserted = 0
        start_time = time.time()
        
        for idx, (country,) in enumerate(countries, 1):
            if idx % 10 == 0 or idx == 1:
                elapsed = time.time() - start_time
                rate = idx / elapsed if elapsed > 0 else 0
                eta = (n_countries - idx) / rate if rate > 0 else 0
                logger.info(f"  Wide: {idx}/{n_countries} ({100*idx/n_countries:.1f}%) - ETA: {eta/60:.1f} min")
            
            cur.execute(f"""
                INSERT INTO match_conflict_wide
                SELECT {art_select}, {conf_select_all}, {feat_select_all}, {time_select}
                FROM art.articles_eng a
                INNER JOIN conf.conflict_features cf ON TRIM(cf.country) = ?
                INNER JOIN conf.unique_conflict uc ON cf.conflict_id = uc.conflict_id
                INNER JOIN conf.conflict_time t ON uc.conflict_id = t.conflict_id
                WHERE TRIM(a.article_country) = ?
                  AND DATE(a.publishedAt) BETWEEN 
                    DATE(t.mid_date, '-{time_window_days} days') 
                    AND DATE(t.mid_date, '+{time_window_days} days')
            """, (country, country))
            
            total_inserted += cur.rowcount
            if idx % 10 == 0:
                conn.commit()
        
        conn.commit()
        logger.info(f"Wide table: {total_inserted:,} rows in {time.time() - start_time:.1f}s")
        
        # Build SLIM table
        logger.info("Building SLIM table...")
        cur.execute("DROP TABLE IF EXISTS match_conflict_slim;")
        
        slim_cols = []
        slim_cols.extend([f'art_{c} TEXT' for c in art_cols_filtered])
        slim_cols.extend(['conflict_id TEXT', 'n_events TEXT', 'total_fatalities TEXT',
                         'country TEXT', 'conflict_key TEXT', 'start_date TEXT', 
                         'end_date TEXT', 'mid_date TEXT'])
        
        cur.execute(f"CREATE TABLE match_conflict_slim ({', '.join(slim_cols)})")
        
        total_inserted = 0
        start_time = time.time()
        
        for idx, (country,) in enumerate(countries, 1):
            if idx % 10 == 0 or idx == 1:
                elapsed = time.time() - start_time
                rate = idx / elapsed if elapsed > 0 else 0
                eta = (n_countries - idx) / rate if rate > 0 else 0
                logger.info(f"  Slim: {idx}/{n_countries} ({100*idx/n_countries:.1f}%) - ETA: {eta/60:.1f} min")
            
            cur.execute(f"""
                INSERT INTO match_conflict_slim
                SELECT {art_select}, {conf_select_slim}, {feat_select_slim}, {time_select_slim}
                FROM art.articles_eng a
                INNER JOIN conf.conflict_features cf ON TRIM(cf.country) = ?
                INNER JOIN conf.unique_conflict uc ON cf.conflict_id = uc.conflict_id
                INNER JOIN conf.conflict_time t ON uc.conflict_id = t.conflict_id
                WHERE TRIM(a.article_country) = ?
                  AND DATE(a.publishedAt) BETWEEN 
                    DATE(t.mid_date, '-{time_window_days} days') 
                    AND DATE(t.mid_date, '+{time_window_days} days')
            """, (country, country))
            
            total_inserted += cur.rowcount
            if idx % 10 == 0:
                conn.commit()
        
        conn.commit()
        logger.info(f"Slim table: {total_inserted:,} rows in {time.time() - start_time:.1f}s")
        
        # Create indexes
        logger.info("Creating indexes...")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wide_conflict_id ON match_conflict_wide(conf_conflict_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wide_country ON match_conflict_wide(feat_country);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_slim_conflict_id ON match_conflict_slim(conflict_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_slim_country ON match_conflict_slim(country);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_slim_mid_date ON match_conflict_slim(mid_date);")
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
    """
    Build coverage_country table.
    Adapted from build_coverage_country.py to work with the pipeline.
    """
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
            FROM match_conflict_slim
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


def build_country_indices(out_db: str, conflict_db: str, logger):
    """
    Build country_indices table with conflict and coverage metrics.
    Adapted from build_country_indices.py to work with the pipeline.
    """
    logger.info("Building country_indices table...")
    
    try:
        import pandas as pd
        import numpy as np
        import country_converter as coco
        import pycountry
        from functools import lru_cache
    except ImportError as e:
        logger.error("Missing required libraries: pandas, numpy, country_converter, pycountry")
        raise
    
    conn = sqlite3.connect(out_db)
    cur = conn.cursor()
    
    try:
        # Attach conflict database
        cur.execute("ATTACH DATABASE ? AS conf_temp;", (conflict_db,))
        
        # Get conflict data
        conflict_df = pd.read_sql_query("""
            SELECT TRIM(country) AS country, SUM(n_events) AS n_events,
                   SUM(total_fatalities) AS total_fatalities
            FROM conf_temp.conflict_country
            WHERE country IS NOT NULL AND TRIM(country) != ''
            GROUP BY TRIM(country);
        """, conn)
        
        # Get coverage data
        coverage_df = pd.read_sql_query("""
            SELECT TRIM(country) AS country, SUM(n_articles) AS n_articles
            FROM coverage_country
            GROUP BY TRIM(country);
        """, conn)
        
        cur.execute("DETACH DATABASE conf_temp;")
        
        # Merge and fill
        df = conflict_df.merge(coverage_df, on="country", how="outer")
        df["n_events"] = df["n_events"].fillna(0)
        df["total_fatalities"] = df["total_fatalities"].fillna(0)
        df["n_articles"] = df["n_articles"].fillna(0)
        
        # Country code mapping
        _cc = coco.CountryConverter()
        ALIASES = {"UK": "GBR", "UAE": "ARE", "Russia": "RUS", 
                   "South Korea": "KOR", "North Korea": "PRK"}
        
        @lru_cache(maxsize=10_000)
        def country_name_to_iso3(name: str):
            if not name:
                return None
            s = str(name).strip()
            if s in ALIASES:
                return ALIASES[s]
            iso3 = _cc.convert(names=s, to="ISO3", not_found=None)
            if iso3 and iso3 != "not found":
                return iso3
            try:
                hit = pycountry.countries.search_fuzzy(s)[0]
                return getattr(hit, "alpha_3", None)
            except LookupError:
                return None
        
        df["iso_a3"] = df["country"].map(country_name_to_iso3)
        
        # Calculate shares
        df["share_events"] = df["n_events"] / df["n_events"].sum()
        df["share_fatalities"] = df["total_fatalities"] / df["total_fatalities"].sum()
        df["share_articles"] = df["n_articles"] / df["n_articles"].sum()
        
        # Helper functions
        EPS = 1e-9
        
        def minmax01(x):
            x = x.astype(float)
            lo, hi = x.min(), x.max()
            if (hi - lo) < EPS:
                return x * 0.0
            return (x - lo) / (hi - lo)
        
        def harmonic_mean(a: pd.Series, b: pd.Series):
            a = a.fillna(0).astype(float)
            b = b.fillna(0).astype(float)
            hm = np.where((a > 0) & (b > 0), 2.0 / ((1.0 / (a + EPS)) + (1.0 / (b + EPS))), 0.0)
            return pd.Series(hm, index=a.index)
        
        # Calculate indices
        df["conflict_index_raw"] = harmonic_mean(df["n_events"], df["total_fatalities"])
        df["conflict_index_scaled"] = minmax01(np.log1p(df["conflict_index_raw"]))
        
        total_articles = float(df["n_articles"].sum())
        df["coverage_index"] = (df["n_articles"] / total_articles) if total_articles > 0 else 0.0
        
        # Write to database
        df_out = df[["country", "iso_a3", "share_events", "share_fatalities", "share_articles",
                     "n_events", "total_fatalities", "n_articles", "conflict_index_raw",
                     "conflict_index_scaled", "coverage_index"]].copy()
        
        cur.execute("DROP TABLE IF EXISTS country_indices;")
        df_out.to_sql("country_indices", conn, index=False)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_country_indices_country ON country_indices(country);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_country_indices_iso ON country_indices(iso_a3);")
        conn.commit()
        
        n_countries = len(df_out)
        missing_iso = df_out["iso_a3"].isna().sum()
        logger.info(f"Indices: {n_countries} countries ({n_countries - missing_iso} with ISO codes)")
        logger.info("✓ Indices table completed")
        
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
        SELECT COUNT(*) as total, COUNT(DISTINCT conflict_id) as conflicts,
               COUNT(DISTINCT art_id) as articles, COUNT(DISTINCT country) as countries
        FROM match_conflict_slim
    """).fetchone()
    
    logger.info(f"Total matches: {stats[0]:,}")
    logger.info(f"Unique conflicts: {stats[1]:,}")
    logger.info(f"Unique articles: {stats[2]:,}")
    logger.info(f"Countries: {stats[3]:,}")
    
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
    logger = init_logger("matching_pipeline")
    metrics = PerformanceMetrics()
    
    pipeline_start = time.time()
    
    logger.info("=" * 60)
    logger.info("ARTICLE-CONFLICT MATCHING PIPELINE")
    logger.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    # Configuration
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    DATA_DIR = PROJECT_ROOT / "data"
    
    ART_DB = str(DATA_DIR / "deleted_dupgnews2023.db")
    CONFLICT_DB = str(DATA_DIR / "conflict_data.db")
    OUT_DB = str(DATA_DIR / "matching_conflict_2days.db")
    TIME_WINDOW_DAYS = 2
    
    logger.info(f"Configuration:")
    logger.info(f"  Article DB: {ART_DB}")
    logger.info(f"  Conflict DB: {CONFLICT_DB}")
    logger.info(f"  Output DB: {OUT_DB}")
    logger.info(f"  Time window: ±{TIME_WINDOW_DAYS} days")
    
    try:
        # Step 1: Match conflicts to articles
        logger.info("\n" + "=" * 60)
        logger.info("STEP 1: MATCHING")
        logger.info("=" * 60)
        metrics.start_step("1. Match Conflicts to Articles")
        match_conflicts_to_articles(ART_DB, CONFLICT_DB, OUT_DB, TIME_WINDOW_DAYS, logger)
        metrics.end_step()
        
        # Step 2: Build coverage table
        logger.info("\n" + "=" * 60)
        logger.info("STEP 2: COVERAGE")
        logger.info("=" * 60)
        metrics.start_step("2. Build Coverage Country")
        build_coverage_country(OUT_DB, logger)
        metrics.end_step()
        
        # Step 3: Build indices
        logger.info("\n" + "=" * 60)
        logger.info("STEP 3: INDICES")
        logger.info("=" * 60)
        metrics.start_step("3. Build Country Indices")
        build_country_indices(OUT_DB, CONFLICT_DB, logger)
        metrics.end_step()
        
        # Final statistics
        log_final_statistics(OUT_DB, logger)
        logger.info(metrics.get_summary())
        
        duration = time.time() - pipeline_start
        logger.info("\n" + "=" * 60)
        logger.info("✓ PIPELINE COMPLETED")
        logger.info(f"Duration: {duration:.2f}s ({duration/60:.2f} min)")
        logger.info(f"Output: {OUT_DB}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.exception("\n✗ PIPELINE FAILED")
        raise


if __name__ == "__main__":
    main()