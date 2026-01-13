# pipelineMATCHING.py
#
# Automated pipeline to match GNews articles with ACLED conflicts:
# 1. Country-level matching (matching_country.py)
# 2. Conflict-level matching (build_conflict_article_matches.py)
# 3. Coverage aggregation (build_coverage_country.py)
# 4. Index calculation (build_country_indices.py)
# with data validation and performance metrics

import os
import time
import sqlite3
from datetime import datetime
from typing import Dict, Tuple
from pathlib import Path

from utils import load_config, init_logger, get_db_connection

# Import from existing scripts
from matching_country import table_cols
from build_conflict_article_matches import (
    compute_actor_score,
    compute_kw_score,
    ensure_conflict_has_bestmatch_columns,
    EXTRA_DAYS,
    ACTOR_WEIGHT,
    KW_WEIGHT,
    MATCH_THRESHOLD
)


# =============================
# CONFIGURATION
# =============================
class PipelineConfig:
    """Configuration for Matching pipeline"""
    
    def __init__(self, config_dict: Dict):
        # Database paths
        base_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(base_dir, "..", "data")
        data_dir = os.path.abspath(data_dir)
        
        self.gnews_db = os.path.join(data_dir, "deleted_dupgnews2023.db")
        self.conflict_db = os.path.join(data_dir, "conflict_data.db")
        self.match_db = os.path.join(data_dir, "matching_country.db")
        
        # Table names
        self.articles_table = "articles_eng"
        self.conflict_country_table = "conflict_country"
        self.conflict_features_table = "conflict_features"
        
        # Output tables
        self.match_country_wide = "match_country_wide"
        self.match_country_slim = "match_country_slim"
        self.conflict_article_bestmatch = "conflict_article_bestmatch"
        self.conflict_article_bestmatch_wide = "conflict_article_bestmatch_wide"
        self.coverage_country = "coverage_country"
        self.country_indices = "country_indices"


# =============================
# PERFORMANCE METRICS
# =============================
class PerformanceMetrics:
    """Track performance metrics for each pipeline step"""
    
    def __init__(self):
        self.metrics = {}
        self.current_step = None
        self.start_time = None
    
    def start_step(self, step_name: str):
        self.current_step = step_name
        self.start_time = time.time()
    
    def end_step(self):
        if self.current_step and self.start_time:
            elapsed = time.time() - self.start_time
            self.metrics[self.current_step] = elapsed
            self.current_step = None
            self.start_time = None
    
    def get_summary(self) -> str:
        if not self.metrics:
            return "No metrics recorded"
        
        lines = ["\n" + "=" * 60]
        lines.append("PERFORMANCE METRICS")
        lines.append("=" * 60]
        
        total_time = sum(self.metrics.values())
        
        for step, duration in self.metrics.items():
            percentage = (duration / total_time * 100) if total_time > 0 else 0
            lines.append(f"{step:.<45} {duration:>8.2f}s ({percentage:>5.1f}%)")
        
        lines.append("-" * 60)
        lines.append(f"{'TOTAL TIME':.<45} {total_time:>8.2f}s")
        lines.append("=" * 60)
        
        return "\n".join(lines)


# =============================
# DATA VALIDATOR
# =============================
class DataValidator:
    """Validate data integrity at each step"""
    
    def __init__(self, logger):
        self.logger = logger
        self.validations = []
    
    def validate_country_matching(self, db_path: str, table: str) -> Dict:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        checks = {
            "total_matches": 0,
            "unique_articles": 0,
            "unique_countries": 0
        }
        
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        checks["total_matches"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT COUNT(DISTINCT art_id) FROM {table};")
        checks["unique_articles"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT COUNT(DISTINCT art_article_country) FROM {table};")
        checks["unique_countries"] = cur.fetchone()[0]
        
        conn.close()
        return checks
    
    def validate_conflict_matching(self, db_path: str, wide_table: str, best_table: str) -> Dict:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        checks = {
            "total_wide_matches": 0,
            "unique_conflicts_matched": 0,
            "unique_articles_matched": 0,
            "best_matches": 0,
            "avg_score": 0.0
        }
        
        cur.execute(f"SELECT COUNT(*) FROM {wide_table};")
        checks["total_wide_matches"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT COUNT(DISTINCT conflict_rowid) FROM {wide_table};")
        checks["unique_conflicts_matched"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT COUNT(DISTINCT matched_article_rowid) FROM {wide_table};")
        checks["unique_articles_matched"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT COUNT(*) FROM {best_table};")
        checks["best_matches"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT AVG(match_total_score) FROM {wide_table};")
        result = cur.fetchone()[0]
        checks["avg_score"] = float(result) if result else 0.0
        
        conn.close()
        return checks
    
    def validate_coverage(self, db_path: str, table: str) -> Dict:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        checks = {
            "total_countries": 0,
            "total_articles": 0,
            "avg_articles_per_country": 0.0
        }
        
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        checks["total_countries"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT SUM(n_articles) FROM {table};")
        result = cur.fetchone()[0]
        checks["total_articles"] = int(result) if result else 0
        
        if checks["total_countries"] > 0:
            checks["avg_articles_per_country"] = checks["total_articles"] / checks["total_countries"]
        
        conn.close()
        return checks
    
    def validate_indices(self, db_path: str, table: str) -> Dict:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        checks = {
            "total_countries": 0,
            "countries_with_iso": 0,
            "avg_conflict_index": 0.0,
            "avg_coverage_index": 0.0
        }
        
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        checks["total_countries"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE iso_a3 IS NOT NULL;")
        checks["countries_with_iso"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT AVG(conflict_index_scaled) FROM {table};")
        result = cur.fetchone()[0]
        checks["avg_conflict_index"] = float(result) if result else 0.0
        
        cur.execute(f"SELECT AVG(coverage_index) FROM {table};")
        result = cur.fetchone()[0]
        checks["avg_coverage_index"] = float(result) if result else 0.0
        
        conn.close()
        return checks
    
    def log_validation_results(self, step_name: str, checks: Dict):
        self.logger.info(f"\n--- Validation: {step_name} ---")
        
        warnings = []
        
        for key, value in checks.items():
            if isinstance(value, (int, float)):
                self.logger.info(f"  ✓ {key}: {value:,.2f}" if isinstance(value, float) else f"  ✓ {key}: {value:,}")
            else:
                self.logger.info(f"  ✓ {key}: {value}")
        
        if warnings:
            self.logger.warning("\nWarnings:")
            for w in warnings:
                self.logger.warning(w)
        
        self.validations.append((step_name, checks, len(warnings), 0))
    
    def get_validation_summary(self) -> str:
        if not self.validations:
            return "No validations performed"
        
        lines = ["\n" + "=" * 60]
        lines.append("VALIDATION SUMMARY")
        lines.append("=" * 60)
        
        for step, checks, warnings, errors in self.validations:
            status = "✓ PASS"
            lines.append(f"{step:.<40} {status}")
            if warnings > 0:
                lines.append(f"  └─ Warnings: {warnings}")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)


# =============================
# STEP 1: COUNTRY-LEVEL MATCHING
# =============================
def match_articles_to_countries(config: PipelineConfig, logger, metrics: PerformanceMetrics):
    """Step 1: Match articles to countries using matching_country.py logic"""
    metrics.start_step("1. Country-Level Matching")
    
    logger.info("=" * 60)
    logger.info("STEP 1: COUNTRY-LEVEL MATCHING")
    logger.info("=" * 60)
    
    conn = get_db_connection(str(config.match_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    try:
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        
        # Attach source databases
        logger.info("Attaching source databases...")
        cur.execute("ATTACH DATABASE ? AS art;", (str(config.gnews_db),))
        cur.execute("ATTACH DATABASE ? AS conf;", (str(config.conflict_db),))
        
        # Validate schema
        art_cols = table_cols(cur, "art", config.articles_table)
        conf_cols = table_cols(cur, "conf", config.conflict_country_table)
        
        if not art_cols or not conf_cols:
            raise RuntimeError("Source tables not found")
        
        logger.info(f"Articles table columns: {len(art_cols)}")
        logger.info(f"Conflict country table columns: {len(conf_cols)}")
        
        # Exclude unwanted columns
        EXCLUDE_ART_COLS = {"event_type", "tfidf_terms_de", "tfidf_terms_en"}
        art_cols_filtered = [c for c in art_cols if c not in EXCLUDE_ART_COLS]
        
        # Build select statements
        art_select = ",\n            ".join([f'a."{c}" AS art_{c}' for c in art_cols_filtered])
        conf_select_all = ",\n            ".join([f'c."{c}" AS conf_{c}' for c in conf_cols])
        conf_select_country_only = 'TRIM(c."country") AS country'
        
        # Create WIDE table (all columns)
        logger.info(f"Creating {config.match_country_wide} table...")
        cur.execute(f"DROP TABLE IF EXISTS {config.match_country_wide};")
        cur.execute(f"""
            CREATE TABLE {config.match_country_wide} AS
            SELECT
            {art_select},
            {conf_select_all}
            FROM art.{config.articles_table} a
            INNER JOIN conf.{config.conflict_country_table} c
              ON TRIM(a.article_country) = TRIM(c.country);
        """)
        conn.commit()
        
        # Create SLIM table (articles + country only)
        logger.info(f"Creating {config.match_country_slim} table...")
        cur.execute(f"DROP TABLE IF EXISTS {config.match_country_slim};")
        cur.execute(f"""
            CREATE TABLE {config.match_country_slim} AS
            SELECT
            {art_select},
            {conf_select_country_only}
            FROM art.{config.articles_table} a
            INNER JOIN conf.{config.conflict_country_table} c
              ON TRIM(a.article_country) = TRIM(c.country);
        """)
        conn.commit()
        
        # Create indexes
        logger.info("Creating indexes...")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{config.match_country_wide}_art_country ON {config.match_country_wide}(art_article_country);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{config.match_country_wide}_conf_country ON {config.match_country_wide}(conf_country);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{config.match_country_slim}_art_country ON {config.match_country_slim}(art_article_country);")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{config.match_country_slim}_country ON {config.match_country_slim}(country);")
        conn.commit()
        
        # Get counts
        n_wide = cur.execute(f"SELECT COUNT(*) FROM {config.match_country_wide};").fetchone()[0]
        n_slim = cur.execute(f"SELECT COUNT(*) FROM {config.match_country_slim};").fetchone()[0]
        
        logger.info(f"✓ Country matching complete")
        logger.info(f"  Wide table rows: {n_wide:,}")
        logger.info(f"  Slim table rows: {n_slim:,}")
        
    finally:
        try:
            cur.execute("DETACH DATABASE art;")
            cur.execute("DETACH DATABASE conf;")
        except:
            pass
        conn.close()
    
    metrics.end_step()


# =============================
# STEP 2: CONFLICT-LEVEL MATCHING
# =============================
def match_articles_to_conflicts(config: PipelineConfig, logger, metrics: PerformanceMetrics):
    """Step 2: Match articles to specific conflicts using build_conflict_article_matches.py logic"""
    metrics.start_step("2. Conflict-Level Matching")
    
    logger.info("=" * 60)
    logger.info("STEP 2: CONFLICT-LEVEL MATCHING")
    logger.info("=" * 60)
    
    with sqlite3.connect(str(config.conflict_db)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        
        # Attach GNews database
        logger.info("Attaching GNews database...")
        con.execute("ATTACH DATABASE ? AS gnews", (str(config.gnews_db),))
        
        # Ensure conflict_features has bestmatch columns
        logger.info("Ensuring bestmatch columns in conflict_features...")
        ensure_conflict_has_bestmatch_columns(con)
        
        # Drop and recreate output tables
        logger.info("Creating output tables...")
        con.executescript(f"""
        DROP TABLE IF EXISTS {config.conflict_article_bestmatch};
        DROP TABLE IF EXISTS {config.conflict_article_bestmatch_wide};
        
        CREATE TABLE {config.conflict_article_bestmatch} (
            conflict_rowid INTEGER,
            article_rowid  INTEGER,
            total_score    INTEGER,
            actor_score    INTEGER,
            kw_score       INTEGER,
            matched_actors TEXT,
            matched_keywords TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_bestmatch_conflict ON {config.conflict_article_bestmatch}(conflict_rowid);
        """)
        
        # Hard-filter candidates (country + publishedAt window)
        logger.info("Loading candidate matches...")
        cand_sql = f"""
        SELECT
            c.rowid AS conflict_rowid,
            a.rowid AS article_rowid,
            c.*,
            a.publishedAt, a.url, a.source_name, a.source_url,
            a.title_en, a.description_en, a.content, a.content_en,
            a.article_country, a.article_country_score,
            a.kw_1, a.kw_2, a.kw_3
        FROM {config.conflict_features_table} c
        JOIN gnews.{config.articles_table} a
          ON a.article_country = c.country
         AND date(a.publishedAt) BETWEEN date(c.start_date)
                                   AND date(c.end_date, '+{EXTRA_DAYS} days')
        ORDER BY c.rowid, a.publishedAt, a.rowid
        """
        candidates = con.execute(cand_sql).fetchall()
        logger.info(f"Found {len(candidates):,} candidate matches")
        
        # Score candidates
        logger.info("Scoring matches...")
        import re
        import unicodedata
        from collections import defaultdict
        
        def norm(s):
            return "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) 
                          if not unicodedata.combining(c)).strip()
        
        def collapse_ws(s):
            return re.sub(r"\s+", " ", (s or "")).strip()
        
        best_by_conflict = {}
        wide_rows = []
        
        for i, r in enumerate(candidates):
            conflict_rowid = r["conflict_rowid"]
            
            article_text = collapse_ws(f"{r['title_en'] or ''} {r['description_en'] or ''} {r['content_en'] or ''}")
            article_text_norm = norm(article_text)
            
            actor_score, matched_actors = compute_actor_score(r, article_text_norm)
            kw_score, matched_keywords = compute_kw_score(r, r)
            total_score = ACTOR_WEIGHT * actor_score + KW_WEIGHT * kw_score
            
            if total_score < MATCH_THRESHOLD:
                continue
            
            # Store for wide output
            wide_rows.append((
                conflict_rowid,
                r["article_rowid"],
                total_score,
                actor_score,
                kw_score,
                matched_actors,
                matched_keywords,
            ))
            
            # Track best match per conflict
            if conflict_rowid not in best_by_conflict:
                best_by_conflict[conflict_rowid] = (r, total_score, actor_score, kw_score, matched_actors, matched_keywords)
            else:
                old_r, old_total, *_ = best_by_conflict[conflict_rowid]
                old_pub = old_r["publishedAt"] or ""
                pub = r["publishedAt"] or ""
                
                better = False
                if total_score > old_total:
                    better = True
                elif total_score == old_total and pub < old_pub:
                    better = True
                
                if better:
                    best_by_conflict[conflict_rowid] = (r, total_score, actor_score, kw_score, matched_actors, matched_keywords)
            
            if (i + 1) % 10000 == 0:
                logger.info(f"  Processed {i+1:,}/{len(candidates):,} candidates...")
        
        logger.info(f"Found {len(wide_rows):,} matches above threshold")
        logger.info(f"Found {len(best_by_conflict):,} conflicts with at least one match")
        
        # Write bestmatch table
        logger.info("Writing bestmatch table...")
        rows_best = []
        for conflict_rowid, (r, total_score, actor_score, kw_score, matched_actors, matched_keywords) in best_by_conflict.items():
            rows_best.append((
                conflict_rowid,
                r["article_rowid"],
                total_score,
                actor_score,
                kw_score,
                matched_actors,
                matched_keywords
            ))
        
        con.executemany(
            f"""
            INSERT INTO {config.conflict_article_bestmatch}(
                conflict_rowid, article_rowid,
                total_score, actor_score, kw_score,
                matched_actors, matched_keywords
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows_best
        )
        con.commit()
        
        # Create wide table
        logger.info("Creating wide table...")
        con.executescript("""
        DROP TABLE IF EXISTS _tmp_matches;
        CREATE TABLE _tmp_matches (
            conflict_rowid INTEGER,
            article_rowid  INTEGER,
            total_score    INTEGER,
            actor_score    INTEGER,
            kw_score       INTEGER,
            matched_actors TEXT,
            matched_keywords TEXT
        );
        CREATE INDEX IF NOT EXISTS idx__tmp_matches_conflict ON _tmp_matches(conflict_rowid);
        CREATE INDEX IF NOT EXISTS idx__tmp_matches_article  ON _tmp_matches(article_rowid);
        """)
        
        con.executemany(
            """
            INSERT INTO _tmp_matches(
                conflict_rowid, article_rowid,
                total_score, actor_score, kw_score,
                matched_actors, matched_keywords
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            wide_rows
        )
        con.commit()
        
        con.executescript(f"""
        DROP TABLE IF EXISTS {config.conflict_article_bestmatch_wide};
        
        CREATE TABLE {config.conflict_article_bestmatch_wide} AS
        SELECT
            c.rowid AS conflict_rowid,
            m.article_rowid AS matched_article_rowid,
            m.total_score   AS match_total_score,
            m.actor_score   AS match_actor_score,
            m.kw_score      AS match_kw_score,
            m.matched_actors,
            m.matched_keywords,
            c.*,
            a.publishedAt AS article_publishedAt,
            a.url         AS article_url,
            a.source_name AS article_source_name,
            a.source_url  AS article_source_url,
            a.title_en    AS article_title_en,
            a.description_en AS article_description_en,
            a.content     AS article_content,
            a.content_en  AS article_content_en,
            a.article_country,
            a.article_country_score,
            a.kw_1 AS article_kw_1,
            a.kw_2 AS article_kw_2,
            a.kw_3 AS article_kw_3
        FROM _tmp_matches m
        JOIN {config.conflict_features_table} c
          ON c.rowid = m.conflict_rowid
        JOIN gnews.{config.articles_table} a
          ON a.rowid = m.article_rowid
        ;
        CREATE INDEX IF NOT EXISTS idx_wide_conflict ON {config.conflict_article_bestmatch_wide}(conflict_rowid);
        CREATE INDEX IF NOT EXISTS idx_wide_country ON {config.conflict_article_bestmatch_wide}(country);
        """)
        con.commit()
        
        con.execute("DROP TABLE IF EXISTS _tmp_matches;")
        con.commit()
        
        logger.info("✓ Conflict matching complete")
    
    metrics.end_step()


# =============================
# STEP 3: COVERAGE AGGREGATION
# =============================
def build_coverage_country(config: PipelineConfig, logger, metrics: PerformanceMetrics):
    """Step 3: Build coverage_country table"""
    metrics.start_step("3. Coverage Aggregation")
    
    logger.info("=" * 60)
    logger.info("STEP 3: COVERAGE AGGREGATION")
    logger.info("=" * 60)
    
    con = sqlite3.connect(config.match_db)
    cur = con.cursor()
    
    logger.info("Building coverage_country table...")
    cur.execute("DROP TABLE IF EXISTS coverage_country;")
    cur.execute(f"""
        CREATE TABLE coverage_country AS
        SELECT
            TRIM(conf_country) AS country,
            COUNT(*) AS n_articles
        FROM {config.match_country_wide}
        WHERE conf_country IS NOT NULL
          AND TRIM(conf_country) != ''
        GROUP BY TRIM(conf_country);
    """)
    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_coverage_country_country ON coverage_country(country);")
    con.commit()
    
    n_countries = cur.execute("SELECT COUNT(*) FROM coverage_country;").fetchone()[0]
    logger.info(f"✓ Coverage aggregation complete: {n_countries:,} countries")
    
    con.close()
    metrics.end_step()


# =============================
# STEP 4: COUNTRY INDICES
# =============================
def build_country_indices(config: PipelineConfig, logger, metrics: PerformanceMetrics):
    """Step 4: Calculate country indices"""
    metrics.start_step("4. Country Indices")
    
    logger.info("=" * 60)
    logger.info("STEP 4: COUNTRY INDICES CALCULATION")
    logger.info("=" * 60)
    
    import pandas as pd
    import numpy as np
    import country_converter as coco
    import pycountry
    from functools import lru_cache
    
    EPS = 1e-9
    
    def minmax01(x):
        x = x.astype(float)
        lo, hi = x.min(), x.max()
        if (hi - lo) < EPS:
            return x * 0.0
        return (x - lo) / (hi - lo)
    
    def harmonic_mean(a: pd.Series, b: pd.Series) -> pd.Series:
        a = a.fillna(0).astype(float)
        b = b.fillna(0).astype(float)
        hm = np.where((a > 0) & (b > 0), 2.0 / ((1.0 / (a + EPS)) + (1.0 / (b + EPS))), 0.0)
        return pd.Series(hm, index=a.index)
    
    _cc = coco.CountryConverter()
    
    ALIASES = {
        "UK": "GBR",
        "UAE": "ARE",
        "Russia": "RUS",
        "South Korea": "KOR",
        "North Korea": "PRK",
    }
    
    @lru_cache(maxsize=10_000)
    def country_name_to_iso3(name: str):
        if name is None:
            return None
        s = str(name).strip()
        if not s:
            return None
        
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
    
    logger.info("Loading data from databases...")
    con_conf = sqlite3.connect(config.conflict_db)
    con_match = sqlite3.connect(config.match_db)
    
    conflict = pd.read_sql_query("""
        SELECT
            TRIM(country) AS country,
            SUM(n_events) AS n_events,
            SUM(total_fatalities) AS total_fatalities
        FROM conflict_country
        WHERE country IS NOT NULL AND TRIM(country) != ''
        GROUP BY TRIM(country);
    """, con_conf)
    
    coverage = pd.read_sql_query("""
        SELECT
            TRIM(country) AS country,
            SUM(n_articles) AS n_articles
        FROM coverage_country
        GROUP BY TRIM(country);
    """, con_match)
    
    con_match.close()
    
    logger.info("Merging and calculating indices...")
    df = conflict.merge(coverage, on="country", how="outer")
    
    df["n_events"] = df["n_events"].fillna(0)
    df["total_fatalities"] = df["total_fatalities"].fillna(0)
    df["n_articles"] = df["n_articles"].fillna(0)
    df["iso_a3"] = df["country"].map(country_name_to_iso3)
    
    # Shares
    df["share_events"] = df["n_events"] / df["n_events"].sum()
    df["share_fatalities"] = df["total_fatalities"] / df["total_fatalities"].sum()
    df["share_articles"] = df["n_articles"] / df["n_articles"].sum()
    
    # Indices
    df["conflict_index_raw"] = harmonic_mean(df["n_events"], df["total_fatalities"])
    df["conflict_index_scaled"] = minmax01(np.log1p(df["conflict_index_raw"]))
    
    total_articles = float(df["n_articles"].sum())
    df["coverage_index"] = (df["n_articles"] / total_articles) if total_articles > 0 else 0.0
    
    # Write to database
    logger.info("Writing country_indices table...")
    cur = con_conf.cursor()
    cur.execute("DROP TABLE IF EXISTS country_indices;")
    
    df_out = df[[
        "country",
        "iso_a3",
        "share_events",
        "share_fatalities",
        "share_articles",
        "n_events",
        "total_fatalities",
        "n_articles",
        "conflict_index_raw",
        "conflict_index_scaled",
        "coverage_index",
    ]].copy()
    
    df_out.to_sql("country_indices", con_conf, index=False)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_country_indices_country ON country_indices(country);")
    
    con_conf.commit()
    con_conf.close()
    
    logger.info(f"✓ Country indices complete: {len(df_out):,} countries")
    
    metrics.end_step()


# =============================
# STATISTICS
# =============================
def log_final_statistics(config: PipelineConfig, logger):
    """Log final statistics about the matching results"""
    logger.info("=" * 60)
    logger.info("FINAL MATCHING STATISTICS")
    logger.info("=" * 60)
    
    # Country-level matches
    conn_match = sqlite3.connect(config.match_db)
    cur = conn_match.cursor()
    
    cur.execute(f"SELECT COUNT(*) FROM {config.match_country_wide};")
    country_matches = cur.fetchone()[0]
    logger.info(f"Country-level matches: {country_matches:,}")
    
    cur.execute("SELECT COUNT(*) FROM coverage_country;")
    countries = cur.fetchone()[0]
    logger.info(f"Countries with coverage: {countries_count:,}")
    
    conn_match.close()
    
    # Conflict-level matches
    con_conf = sqlite3.connect(config.conflict_db)
    cur = con_conf.cursor()
    
    cur.execute(f"SELECT COUNT(*) FROM {config.conflict_article_bestmatch};")
    best_matches = cur.fetchone()[0]
    
    cur.execute(f"SELECT COUNT(*) FROM {config.conflict_article_bestmatch_wide};")
    wide_matches = cur.fetchone()[0]
    
    logger.info(f"\n✓ Matching pipeline statistics:")
    logger.info(f"  Best matches: {best_matches:,}")
    logger.info(f"  Wide matches: {wide_matches:,}")
    
    con_conf.close()
    metrics.end_step()


# =============================
# STATISTICS
# =============================
def log_final_statistics(config: PipelineConfig, logger):
    """Log final statistics"""
    logger.info("=" * 60)
    logger.info("FINAL MATCHING STATISTICS")
    logger.info("=" * 60)
    
    # Country matches
    conn = sqlite3.connect(config.match_db)
    cur = conn.cursor()
    
    cur.execute(f"SELECT COUNT(*) FROM {config.match_country_slim};")
    country_matches = cur.fetchone()[0]
    logger.info(f"Country-level matches: {country_matches:,}")
    
    cur.execute("SELECT COUNT(*) FROM coverage_country;")
    countries_covered = cur.fetchone()[0]
    logger.info(f"Countries with coverage: {countries_covered:,}")
    
    conn.close()
    
    # Conflict matches
    con_conf = sqlite3.connect(config.conflict_db)
    cur = con_conf.cursor()
    
    cur.execute(f"SELECT COUNT(*) FROM {config.conflict_article_bestmatch};")
    best_matches = cur.fetchone()[0]
    
    cur.execute(f"SELECT COUNT(*) FROM {config.conflict_article_bestmatch_wide};")
    wide_matches = cur.fetchone()[0]
    
    logger.info(f"✓ Indices calculated for {len(df):,} countries")
    logger.info(f"  Countries with ISO codes: {df['iso_a3'].notna().sum():,}")
    
    con_conf.close()
    metrics.end_step()


# =============================
# STATISTICS
# =============================
def log_final_statistics(config: PipelineConfig, logger):
    """Log final statistics about all databases"""
    logger.info("=" * 60)
    logger.info("FINAL MATCHING STATISTICS")
    logger.info("=" * 60)
    
    # Country matching
    conn_match = sqlite3.connect(config.match_db)
    cur = conn_match.cursor()
    
    cur.execute(f"SELECT COUNT(*) FROM {config.match_country_wide};")
    country_matches = cur.fetchone()[0]
    logger.info(f"Country-level matches: {country_matches:,}")
    
    cur.execute("SELECT COUNT(*) FROM coverage_country;")
    countries_covered = cur.fetchone()[0]
    logger.info(f"Countries with article coverage: {countries_covered:,}")
    
    conn_match.close()
    
    # Conflict matches
    con_conf = sqlite3.connect(config.conflict_db)
    cur = con_conf.cursor()
    
    cur.execute(f"SELECT COUNT(*) FROM {config.conflict_article_bestmatch_wide};")
    wide_matches = cur.fetchone()[0]
    
    cur.execute(f"SELECT COUNT(*) FROM {config.conflict_article_bestmatch};")
    best_matches = cur.fetchone()[0]
    
    logger.info(f"✓ Indices calculated for all countries")
    logger.info(f"  Total matches (wide): {wide_matches:,}")
    logger.info(f"  Best matches: {best_matches:,}")
    
    con_conf.close()
    metrics.end_step()


# =============================
# STATISTICS
# =============================
def log_final_statistics(config: PipelineConfig, logger):
    """Log final statistics"""
    logger.info("=" * 60)
    logger.info("FINAL MATCHING STATISTICS")
    logger.info("=" * 60)
    
    # Country-level matches
    conn_match = sqlite3.connect(config.match_db)
    cur = conn_match.cursor()
    
    cur.execute(f"SELECT COUNT(*) FROM {config.match_country_slim};")
    country_matches = cur.fetchone()[0]
    logger.info(f"Country-level matches: {country_matches:,}")
    
    cur.execute("SELECT COUNT(*) FROM coverage_country;")
    countries_covered = cur.fetchone()[0]
    logger.info(f"Countries with coverage: {countries_covered:,}")
    
    conn_match.close()
    
    # Conflict-level matches
    con_conf = sqlite3.connect(config.conflict_db)
    cur = con_conf.cursor()
    
    cur.execute(f"SELECT COUNT(*) FROM {config.conflict_article_bestmatch};")
    best_matches = cur.fetchone()[0]
    
    cur.execute(f"SELECT COUNT(*) FROM {config.conflict_article_bestmatch_wide};")
    wide_matches = cur.fetchone()[0]
    
    logger.info(f"✓ Country indices calculated")
    logger.info(f"  Best conflict matches: {len(best_by_conflict):,}")
    logger.info(f"  Total conflict-article pairs: {len(wide_rows):,}")
    
    con_conf.close()
    metrics.end_step()


# =============================
# STATISTICS
# =============================
def log_final_statistics(config: PipelineConfig, logger):
    """Log final statistics"""
    logger.info("=" * 60)
    logger.info("FINAL MATCHING STATISTICS")
    logger.info("=" * 60)
    
    # Country matching
    conn_match = sqlite3.connect(config.match_db)
    cur = conn_match.cursor()
    
    cur.execute(f"SELECT COUNT(*) FROM {config.match_country_wide};")
    country_matches = cur.fetchone()[0]
    logger.info(f"Country-level matches: {country_matches:,}")
    
    cur.execute("SELECT COUNT(*) FROM coverage_country;")
    countries_covered = cur.fetchone()[0]
    logger.info(f"Countries with coverage: {countries_covered:,}")
    
    conn_dedup.close()
    
    # Conflict matches
    con_conf = sqlite3.connect(config.conflict_db)
    cur = con_conf.cursor()
    
    cur.execute(f"SELECT COUNT(*) FROM {config.conflict_article_bestmatch};")
    best_matches = cur.fetchone()[0]
    logger.info(f"Conflict best matches: {best_matches:,}")
    
    cur.execute(f"SELECT COUNT(*) FROM {config.conflict_article_bestmatch_wide};")
    all_matches = cur.fetchone()[0]
    logger.info(f"All conflict matches (many-to-many): {all_matches:,}")
    
    cur.execute("SELECT COUNT(*) FROM country_indices;")
    indices_count = cur.fetchone()[0]
    logger.info(f"Countries with indices: {indices_count:,}")
    
    con_conf.close()
    logger.info("=" * 60)


# =============================
# MAIN PIPELINE
# =============================
def main():
    """Main pipeline execution"""
    logger = init_logger("matching_pipeline")
    metrics = PerformanceMetrics()
    validator = DataValidator(logger)
    
    pipeline_start = time.time()
    
    logger.info("=" * 60)
    logger.info("MATCHING PIPELINE - STARTING")
    logger.info(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    try:
        # Load configuration
        config_dict = load_config()
        config = PipelineConfig(config_dict)
        
        logger.info(f"\nConfiguration:")
        logger.info(f"  GNews DB: {config.gnews_db}")
        logger.info(f"  Conflict DB: {config.conflict_db}")
        logger.info(f"  Match DB: {config.match_db}")
        
        # Step 1: Country-level matching
        match_articles_to_countries(config, logger, metrics)
        
        # Validate country matching
        checks = validator.validate_country_matching(config.match_db, config.match_country_slim)
        validator.log_validation_results("Country Matching", checks)
        
        # Step 2: Conflict-level matching
        match_articles_to_conflicts(config, logger, metrics)
        
        # Validate conflict matching
        checks = validator.validate_conflict_matching(
            config.conflict_db, 
            config.conflict_article_bestmatch_wide,
            config.conflict_article_bestmatch
        )
        validator.log_validation_results("Conflict Matching", checks)
        
        # Step 3: Coverage aggregation
        build_coverage_country(config, logger, metrics)
        
        # Validate coverage
        checks = validator.validate_coverage(config.match_db, config.coverage_country)
        validator.log_validation_results("Coverage Aggregation", checks)
        
        # Step 4: Country indices
        build_country_indices(config, logger, metrics)
        
        # Validate indices
        checks = validator.validate_indices(config.conflict_db, config.country_indices)
        validator.log_validation_results("Country Indices", checks)
        
        # Final statistics
        log_final_statistics(config, logger)
        
        # Print summaries
        logger.info(validator.get_validation_summary())
        logger.info(metrics.get_summary())
        
        pipeline_duration = time.time() - pipeline_start
        
        logger.info("\n" + "=" * 60)
        logger.info("✓ MATCHING PIPELINE COMPLETED SUCCESSFULLY")
        logger.info(f"Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Total duration: {pipeline_duration:.2f}s ({pipeline_duration/60:.2f} minutes)")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.exception("\n❌ MATCHING PIPELINE FAILED")
        logger.error(f"Error: {str(e)}")
        raise


if __name__ == "__main__":
    main()