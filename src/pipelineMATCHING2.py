# pipelineMATCHING2.py
#
# Automated pipeline to match GNews articles with ACLED conflicts:
# 1. Country-level matching (matching_country.py)
# 2. Conflict-level matching (build_conflict_article_matches.py)
# 3. Coverage aggregation (build_coverage_country.py)
# 4. Index calculation (build_country_indices.py)
# with INCREMENTAL updates and data validation

import os
import time
import sqlite3
from datetime import datetime
from typing import Dict, Tuple
from pathlib import Path

from utils import load_config, init_logger, get_db_connection

# Import from existing matching scripts
import matching_country
import build_conflict_article_matches
import build_coverage_country
import build_country_indices


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
        
        # Force rebuild flag (can be set to True to force full rebuild)
        self.force_rebuild = config_dict.get("matching", {}).get("force_rebuild", False)


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
        lines.append("=" * 60)
        
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
# HELPER FUNCTIONS
# =============================
def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check if a table exists in the database"""
    cur = conn.cursor()
    cur.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name=?;
    """, (table_name,))
    return cur.fetchone() is not None


def needs_update(config: PipelineConfig, logger) -> Dict[str, bool]:
    """Check which steps need to be updated based on data freshness"""
    
    needs = {
        "country_matching": False,
        "conflict_matching": False,
        "coverage": False,
        "indices": False
    }
    
    # If force rebuild, update everything
    if config.force_rebuild:
        logger.info("Force rebuild enabled - will rebuild all tables")
        return {k: True for k in needs.keys()}
    
    # Check if output tables exist
    match_conn = sqlite3.connect(config.match_db)
    conflict_conn = sqlite3.connect(config.conflict_db)
    
    # Country matching tables
    if not table_exists(match_conn, config.match_country_wide):
        logger.info("Country matching tables don't exist - will create")
        needs["country_matching"] = True
        needs["coverage"] = True  # Coverage depends on country matching
        needs["indices"] = True   # Indices depend on coverage
    
    # Conflict matching tables
    if not table_exists(conflict_conn, config.conflict_article_bestmatch_wide):
        logger.info("Conflict matching tables don't exist - will create")
        needs["conflict_matching"] = True
    
    # Coverage table
    if not table_exists(match_conn, config.coverage_country):
        logger.info("Coverage table doesn't exist - will create")
        needs["coverage"] = True
        needs["indices"] = True
    
    # Indices table
    if not table_exists(conflict_conn, config.country_indices):
        logger.info("Country indices table doesn't exist - will create")
        needs["indices"] = True
    
    # If all tables exist, check if source data is newer
    if not any(needs.values()):
        logger.info("All tables exist - checking for updates...")
        
        # Check if articles_eng has been updated since last matching
        gnews_conn = sqlite3.connect(config.gnews_db)
        gnews_cur = gnews_conn.cursor()
        
        # Get last article date
        gnews_cur.execute(f"SELECT MAX(publishedAt) FROM {config.articles_table};")
        latest_article = gnews_cur.fetchone()[0]
        
        # Get last matched article date (from country matching)
        match_cur = match_conn.cursor()
        match_cur.execute(f"SELECT MAX(art_publishedAt) FROM {config.match_country_wide};")
        latest_matched = match_cur.fetchone()[0]
        
        if latest_article and latest_matched and latest_article > latest_matched:
            logger.info(f"New articles detected (latest: {latest_article} vs matched: {latest_matched})")
            needs["country_matching"] = True
            needs["conflict_matching"] = True
            needs["coverage"] = True
            needs["indices"] = True
        else:
            logger.info("Data is up to date - no updates needed")
        
        gnews_conn.close()
    
    match_conn.close()
    conflict_conn.close()
    
    return needs


# =============================
# STEP 1: COUNTRY-LEVEL MATCHING
# =============================
def match_articles_to_countries(config: PipelineConfig, logger, metrics: PerformanceMetrics):
    """Step 1: Match articles to countries using matching_country.py"""
    metrics.start_step("1. Country-Level Matching")
    
    logger.info("=" * 60)
    logger.info("STEP 1: COUNTRY-LEVEL MATCHING")
    logger.info("=" * 60)
    
    # Temporarily override the paths in the imported module
    original_out_db = matching_country.OUT_DB
    original_art_db = matching_country.ART_DB
    original_conflict_db = matching_country.CONFLICT_DB
    
    matching_country.OUT_DB = Path(config.match_db)
    matching_country.ART_DB = Path(config.gnews_db)
    matching_country.CONFLICT_DB = Path(config.conflict_db)
    
    try:
        # Run the matching_country main function
        matching_country.main()
        logger.info("✓ Country-level matching complete")
    finally:
        # Restore original paths
        matching_country.OUT_DB = original_out_db
        matching_country.ART_DB = original_art_db
        matching_country.CONFLICT_DB = original_conflict_db
    
    metrics.end_step()


# =============================
# STEP 2: CONFLICT-LEVEL MATCHING
# =============================
def match_articles_to_conflicts(config: PipelineConfig, logger, metrics: PerformanceMetrics):
    """Step 2: Match articles to specific conflicts using build_conflict_article_matches.py"""
    metrics.start_step("2. Conflict-Level Matching")
    
    logger.info("=" * 60)
    logger.info("STEP 2: CONFLICT-LEVEL MATCHING")
    logger.info("=" * 60)
    
    # Temporarily override the paths in the imported module
    original_conflict_db = build_conflict_article_matches.CONFLICT_DB
    original_gnews_db = build_conflict_article_matches.GNEWS_DB
    
    build_conflict_article_matches.CONFLICT_DB = Path(config.conflict_db)
    build_conflict_article_matches.GNEWS_DB = Path(config.gnews_db)
    
    try:
        # Run the conflict matching main function
        build_conflict_article_matches.main()
        logger.info("✓ Conflict-level matching complete")
    finally:
        # Restore original paths
        build_conflict_article_matches.CONFLICT_DB = original_conflict_db
        build_conflict_article_matches.GNEWS_DB = original_gnews_db
    
    metrics.end_step()


# =============================
# STEP 3: COVERAGE AGGREGATION
# =============================
def build_coverage(config: PipelineConfig, logger, metrics: PerformanceMetrics):
    """Step 3: Build coverage_country table using build_coverage_country.py"""
    metrics.start_step("3. Coverage Aggregation")
    
    logger.info("=" * 60)
    logger.info("STEP 3: COVERAGE AGGREGATION")
    logger.info("=" * 60)
    
    # Temporarily override the path in the imported module
    original_db_match = build_coverage_country.DB_MATCH
    
    build_coverage_country.DB_MATCH = Path(config.match_db)
    
    try:
        # Run the coverage building main function
        build_coverage_country.main()
        logger.info("✓ Coverage aggregation complete")
    finally:
        # Restore original path
        build_coverage_country.DB_MATCH = original_db_match
    
    metrics.end_step()


# =============================
# STEP 4: COUNTRY INDICES
# =============================
def build_indices(config: PipelineConfig, logger, metrics: PerformanceMetrics):
    """Step 4: Calculate country indices using build_country_indices.py"""
    metrics.start_step("4. Country Indices")
    
    logger.info("=" * 60)
    logger.info("STEP 4: COUNTRY INDICES CALCULATION")
    logger.info("=" * 60)
    
    # Temporarily override the paths in the imported module
    original_db_conflict = build_country_indices.DB_CONFLICT
    original_db_match = build_country_indices.DB_MATCH
    
    build_country_indices.DB_CONFLICT = Path(config.conflict_db)
    build_country_indices.DB_MATCH = Path(config.match_db)
    
    try:
        # Run the indices building main function
        build_country_indices.main()
        logger.info("✓ Country indices calculation complete")
    finally:
        # Restore original paths
        build_country_indices.DB_CONFLICT = original_db_conflict
        build_country_indices.DB_MATCH = original_db_match
    
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
    
    cur.execute(f"SELECT COUNT(*) FROM {config.conflict_article_bestmatch};")
    best_matches = cur.fetchone()[0]
    logger.info(f"Conflict best matches: {best_matches:,}")
    
    cur.execute(f"SELECT COUNT(*) FROM {config.conflict_article_bestmatch_wide};")
    all_matches = cur.fetchone()[0]
    logger.info(f"All conflict matches (many-to-many): {all_matches:,}")
    
    cur.execute("SELECT COUNT(*) FROM country_indices;")
    indices_count = cur.fetchone()[0]
    logger.info(f"Countries with indices: {indices_count:,}")
    
    # Top 5 countries by coverage
    logger.info("\nTop 5 countries by article coverage:")
    cur.execute("""
        SELECT country, n_articles, conflict_index_scaled, coverage_index
        FROM country_indices
        ORDER BY n_articles DESC
        LIMIT 5;
    """)
    for i, (country, n_articles, conflict_idx, coverage_idx) in enumerate(cur.fetchall(), 1):
        logger.info(f"  {i}. {country}: {n_articles:,} articles (conflict: {conflict_idx:.3f}, coverage: {coverage_idx:.3f})")
    
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
        logger.info(f"  Force rebuild: {config.force_rebuild}")
        
        # Check what needs to be updated
        logger.info("\n" + "=" * 60)
        logger.info("CHECKING FOR UPDATES")
        logger.info("=" * 60)
        needs = needs_update(config, logger)
        
        if not any(needs.values()):
            logger.info("\n✓ All data is up to date - no processing needed")
            logger.info("  To force rebuild, set 'force_rebuild: true' in config")
            return
        
        # Step 1: Country-level matching
        if needs["country_matching"]:
            match_articles_to_countries(config, logger, metrics)
            
            # Validate country matching
            checks = validator.validate_country_matching(config.match_db, config.match_country_slim)
            validator.log_validation_results("Country Matching", checks)
        else:
            logger.info("\n⊘ Skipping country matching (up to date)")
        
        # Step 2: Conflict-level matching
        if needs["conflict_matching"]:
            match_articles_to_conflicts(config, logger, metrics)
            
            # Validate conflict matching
            checks = validator.validate_conflict_matching(
                config.conflict_db, 
                config.conflict_article_bestmatch_wide,
                config.conflict_article_bestmatch
            )
            validator.log_validation_results("Conflict Matching", checks)
        else:
            logger.info("\n⊘ Skipping conflict matching (up to date)")
        
        # Step 3: Coverage aggregation
        if needs["coverage"]:
            build_coverage(config, logger, metrics)
            
            # Validate coverage
            checks = validator.validate_coverage(config.match_db, config.coverage_country)
            validator.log_validation_results("Coverage Aggregation", checks)
        else:
            logger.info("\n⊘ Skipping coverage aggregation (up to date)")
        
        # Step 4: Country indices
        if needs["indices"]:
            build_indices(config, logger, metrics)
            
            # Validate indices
            checks = validator.validate_indices(config.conflict_db, config.country_indices)
            validator.log_validation_results("Country Indices", checks)
        else:
            logger.info("\n⊘ Skipping country indices (up to date)")
        
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