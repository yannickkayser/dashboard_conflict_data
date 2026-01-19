# pipeline.py
#
# Automated pipeline to fetch ACLED data and process conflict aggregations
# with data validation and performance metrics

import os
import json
import time
from datetime import datetime
from dateutil.relativedelta import relativedelta
from typing import Dict, Tuple, Optional

from utils import load_config, get_db_connection, init_logger
from fetch_ACLED import fetch_acled_data, get_newest_date, load_json_to_db

# Import processing modules
from aggregation import (
    create_event_conflict_table,
    create_conflict_lookup_table,
    build_conflict_mapping,
    CONFLICT_SCHEME_NAME
)
from unique_conflicts import (
    create_unique_conflict_table,
    ensure_indexes,
    populate_unique_conflict_table,
    ensure_conflict_type_count_tables,
    ensure_conflict_features_schema,
    rebuild_conflict_features_base,
    rebuild_conflict_type_counts,
    fill_event_type_modes,
    fill_assoc_actor_1_mode,
    ensure_conflict_time_table,
    rebuild_conflict_time,
    push_time_into_conflict_features,
    ensure_dashboard_indexes
)
from aggregate_country_conflict import ensure_conflict_country_table

from build_fts_index import build_fts


class PerformanceMetrics:
    """Track performance metrics for each pipeline step"""
    
    def __init__(self):
        self.metrics = {}
        self.current_step = None
        self.start_time = None
    
    def start_step(self, step_name: str):
        """Start timing a step"""
        self.current_step = step_name
        self.start_time = time.time()
    
    def end_step(self):
        """End timing the current step"""
        if self.current_step and self.start_time:
            elapsed = time.time() - self.start_time
            self.metrics[self.current_step] = elapsed
            self.current_step = None
            self.start_time = None
    
    def get_summary(self) -> str:
        """Get a formatted summary of all metrics"""
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


class DataValidator:
    """Validate data integrity at each step"""
    
    def __init__(self, conn, logger):
        self.conn = conn
        self.logger = logger
        self.validations = []
    
    def validate_events_table(self) -> Dict:
        """Validate events table"""
        cur = self.conn.cursor()
        
        checks = {
            "total_events": 0,
            "events_with_nulls": 0,
            "duplicate_ids": 0,
            "invalid_dates": 0,
            "negative_fatalities": 0,
            "missing_countries": 0
        }
        
        # Total events
        cur.execute("SELECT COUNT(*) FROM events;")
        checks["total_events"] = cur.fetchone()[0]
        
        # Events with critical NULL values
        cur.execute("""
            SELECT COUNT(*) FROM events 
            WHERE event_id_cnty IS NULL 
               OR event_date IS NULL 
               OR country IS NULL;
        """)
        checks["events_with_nulls"] = cur.fetchone()[0]
        
        # Duplicate event IDs
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT event_id_cnty, COUNT(*) as cnt 
                FROM events 
                GROUP BY event_id_cnty 
                HAVING cnt > 1
            );
        """)
        checks["duplicate_ids"] = cur.fetchone()[0]
        
        # Invalid dates (not in YYYY-MM-DD format or future dates)
        cur.execute("""
            SELECT COUNT(*) FROM events 
            WHERE event_date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
               OR event_date > date('now');
        """)
        checks["invalid_dates"] = cur.fetchone()[0]
        
        # Negative fatalities
        cur.execute("SELECT COUNT(*) FROM events WHERE fatalities < 0;")
        checks["negative_fatalities"] = cur.fetchone()[0]
        
        # Missing country names
        cur.execute("""
            SELECT COUNT(*) FROM events 
            WHERE country IS NULL OR TRIM(country) = '';
        """)
        checks["missing_countries"] = cur.fetchone()[0]
        
        return checks
    
    def validate_conflict_mapping(self) -> Dict:
        """Validate conflict mapping"""
        cur = self.conn.cursor()
        
        checks = {
            "total_mappings": 0,
            "unmapped_events": 0,
            "conflicts_created": 0,
            "orphaned_conflicts": 0,
            "events_mapped_percentage": 0.0
        }
        
        # Total mappings
        cur.execute("SELECT COUNT(*) FROM event_conflict;")
        checks["total_mappings"] = cur.fetchone()[0]
        
        # Events without mapping
        cur.execute("""
            SELECT COUNT(*) FROM events e
            LEFT JOIN event_conflict ec ON e.event_id_cnty = ec.event_id_cnty
            WHERE ec.event_id_cnty IS NULL;
        """)
        checks["unmapped_events"] = cur.fetchone()[0]
        
        # Total conflicts created
        cur.execute("SELECT COUNT(DISTINCT conflict_id) FROM event_conflict;")
        checks["conflicts_created"] = cur.fetchone()[0]
        
        # Conflicts without events (shouldn't happen)
        cur.execute("""
            SELECT COUNT(*) FROM conflict_lookup cl
            LEFT JOIN event_conflict ec ON cl.conflict_id = ec.conflict_id
            WHERE ec.conflict_id IS NULL;
        """)
        checks["orphaned_conflicts"] = cur.fetchone()[0]
        
        # Calculate mapping percentage
        cur.execute("SELECT COUNT(*) FROM events;")
        total_events = cur.fetchone()[0]
        if total_events > 0:
            checks["events_mapped_percentage"] = (
                (total_events - checks["unmapped_events"]) / total_events * 100
            )
        
        return checks
    
    def validate_unique_conflicts(self) -> Dict:
        """Validate unique conflicts table"""
        cur = self.conn.cursor()
        
        checks = {
            "total_unique_conflicts": 0,
            "conflicts_zero_events": 0,
            "conflicts_zero_fatalities": 0,
            "mismatched_event_counts": 0,
            "avg_events_per_conflict": 0.0,
            "avg_fatalities_per_conflict": 0.0
        }
        
        # Total unique conflicts
        cur.execute("SELECT COUNT(*) FROM unique_conflict;")
        checks["total_unique_conflicts"] = cur.fetchone()[0]
        
        # Conflicts with zero events (error!)
        cur.execute("SELECT COUNT(*) FROM unique_conflict WHERE n_events = 0;")
        checks["conflicts_zero_events"] = cur.fetchone()[0]
        
        # Conflicts with zero fatalities (valid, but worth tracking)
        cur.execute("SELECT COUNT(*) FROM unique_conflict WHERE total_fatalities = 0;")
        checks["conflicts_zero_fatalities"] = cur.fetchone()[0]
        
        # Check for mismatched counts between unique_conflict and actual event_conflict
        cur.execute("""
            SELECT COUNT(*) FROM unique_conflict uc
            WHERE uc.n_events != (
                SELECT COUNT(DISTINCT ec.event_id_cnty)
                FROM event_conflict ec
                WHERE ec.conflict_id = uc.conflict_id
            );
        """)
        checks["mismatched_event_counts"] = cur.fetchone()[0]
        
        # Average events per conflict
        cur.execute("SELECT AVG(n_events) FROM unique_conflict;")
        result = cur.fetchone()[0]
        checks["avg_events_per_conflict"] = float(result) if result else 0.0
        
        # Average fatalities per conflict
        cur.execute("SELECT AVG(total_fatalities) FROM unique_conflict;")
        result = cur.fetchone()[0]
        checks["avg_fatalities_per_conflict"] = float(result) if result else 0.0
        
        return checks
    
    def validate_conflict_features(self) -> Dict:
        """Validate conflict features table"""
        cur = self.conn.cursor()
        
        checks = {
            "total_features": 0,
            "missing_country": 0,
            "missing_actor1": 0,
            "missing_dates": 0,
            "invalid_duration": 0,
            "features_completeness_pct": 0.0
        }
        
        # Total features
        cur.execute("SELECT COUNT(*) FROM conflict_features;")
        checks["total_features"] = cur.fetchone()[0]
        
        # Missing country
        cur.execute("""
            SELECT COUNT(*) FROM conflict_features 
            WHERE country IS NULL OR TRIM(country) = '';
        """)
        checks["missing_country"] = cur.fetchone()[0]
        
        # Missing actor1
        cur.execute("""
            SELECT COUNT(*) FROM conflict_features 
            WHERE actor1 IS NULL OR TRIM(actor1) = '';
        """)
        checks["missing_actor1"] = cur.fetchone()[0]
        
        # Missing dates
        cur.execute("""
            SELECT COUNT(*) FROM conflict_features 
            WHERE start_date IS NULL OR end_date IS NULL;
        """)
        checks["missing_dates"] = cur.fetchone()[0]
        
        # Invalid duration (negative or NULL when dates exist)
        cur.execute("""
            SELECT COUNT(*) FROM conflict_features 
            WHERE (duration_days < 0)
               OR (start_date IS NOT NULL AND end_date IS NOT NULL AND duration_days IS NULL);
        """)
        checks["invalid_duration"] = cur.fetchone()[0]
        
        # Calculate completeness
        if checks["total_features"] > 0:
            complete = checks["total_features"] - max(
                checks["missing_country"],
                checks["missing_actor1"],
                checks["missing_dates"]
            )
            checks["features_completeness_pct"] = (complete / checks["total_features"]) * 100
        
        return checks
    
    def validate_country_aggregation(self) -> Dict:
        """Validate country aggregation table"""
        cur = self.conn.cursor()
        
        checks = {
            "total_countries": 0,
            "countries_zero_events": 0,
            "missing_top_actors": 0,
            "sum_events_match": True,
            "total_events_aggregated": 0
        }
        
        # Total countries
        cur.execute("SELECT COUNT(*) FROM conflict_country;")
        checks["total_countries"] = cur.fetchone()[0]
        
        # Countries with zero events
        cur.execute("SELECT COUNT(*) FROM conflict_country WHERE n_events = 0;")
        checks["countries_zero_events"] = cur.fetchone()[0]
        
        # Missing top actors (should be filled)
        cur.execute("""
            SELECT COUNT(*) FROM conflict_country 
            WHERE top1_actor1 IS NULL OR TRIM(top1_actor1) = '';
        """)
        checks["missing_top_actors"] = cur.fetchone()[0]
        
        # Verify sum of events matches
        cur.execute("SELECT SUM(n_events) FROM conflict_country;")
        country_sum = cur.fetchone()[0] or 0
        checks["total_events_aggregated"] = country_sum
        
        cur.execute("SELECT SUM(n_events) FROM conflict_features;")
        features_sum = cur.fetchone()[0] or 0
        
        # Allow small discrepancy due to potential filtering
        checks["sum_events_match"] = abs(country_sum - features_sum) < (features_sum * 0.01)
        
        return checks
    
    def log_validation_results(self, step_name: str, checks: Dict):
        """Log validation results"""
        self.logger.info(f"\n--- Validation: {step_name} ---")
        
        errors = []
        warnings = []
        
        for key, value in checks.items():
            # Identify errors and warnings
            if "zero" in key.lower() and isinstance(value, int) and value > 0:
                if "fatalities" not in key.lower():
                    errors.append(f"  ❌ {key}: {value}")
                else:
                    warnings.append(f"  ⚠️  {key}: {value}")
            elif "missing" in key.lower() and isinstance(value, int) and value > 0:
                warnings.append(f"  ⚠️  {key}: {value}")
            elif "invalid" in key.lower() and isinstance(value, int) and value > 0:
                errors.append(f"  ❌ {key}: {value}")
            elif "duplicate" in key.lower() and isinstance(value, int) and value > 0:
                errors.append(f"  ❌ {key}: {value}")
            elif "mismatch" in key.lower() and isinstance(value, int) and value > 0:
                errors.append(f"  ❌ {key}: {value}")
            elif "match" in key.lower() and isinstance(value, bool) and not value:
                errors.append(f"  ❌ {key}: {value}")
            elif isinstance(value, (int, float)):
                self.logger.info(f"  ✓ {key}: {value:,.2f}" if isinstance(value, float) else f"  ✓ {key}: {value:,}")
            else:
                self.logger.info(f"  ✓ {key}: {value}")
        
        # Log warnings and errors separately
        if warnings:
            self.logger.warning("\nWarnings:")
            for w in warnings:
                self.logger.warning(w)
        
        if errors:
            self.logger.error("\nErrors:")
            for e in errors:
                self.logger.error(e)
            raise ValueError(f"Data validation failed for {step_name}. See errors above.")
        
        self.validations.append((step_name, checks, len(warnings), len(errors)))
    
    def get_validation_summary(self) -> str:
        """Get summary of all validations"""
        if not self.validations:
            return "No validations performed"
        
        lines = ["\n" + "=" * 60]
        lines.append("VALIDATION SUMMARY")
        lines.append("=" * 60)
        
        total_warnings = 0
        total_errors = 0
        
        for step, checks, warnings, errors in self.validations:
            status = "✓ PASS" if errors == 0 else "❌ FAIL"
            lines.append(f"{step:.<40} {status}")
            if warnings > 0:
                lines.append(f"  └─ Warnings: {warnings}")
            if errors > 0:
                lines.append(f"  └─ Errors: {errors}")
            total_warnings += warnings
            total_errors += errors
        
        lines.append("-" * 60)
        lines.append(f"Total Warnings: {total_warnings}")
        lines.append(f"Total Errors: {total_errors}")
        lines.append("=" * 60)
        
        return "\n".join(lines)


def fetch_new_data(config, logger, metrics: PerformanceMetrics) -> Tuple[str, bool]:
    """
    Step 1: Fetch new ACLED data for all countries
    """
    metrics.start_step("1. Fetch ACLED Data")
    
    logger.info("=" * 60)
    logger.info("STEP 1: FETCHING NEW ACLED DATA")
    logger.info("=" * 60)
    
    db_path = config["database"]["path"]
    cn_path = config["country_name"]["path"]

    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_path_db = os.path.join(base_dir, db_path)
    full_path_cn = os.path.join(base_dir, cn_path)

    with open(full_path_cn, "r", encoding="UTF-8") as f:
        acled_coverage = json.load(f)

    data_updated = False
    countries_updated = []
    
    for country_name in acled_coverage.keys():
        logger.info(f"Processing {country_name}...")
        
        # Get the latest date in the database
        latest_date = get_newest_date(country_name, full_path_db)
        logger.info(f"  Latest date in DB: {latest_date}")

        # Calculate time period
        dt = datetime.now()
        one_year_ago = dt - relativedelta(years=1)
        date_time = one_year_ago.date()

        time_period = f"{latest_date}|{date_time}"

        # Fetch the data
        acled_file = fetch_acled_data(country_name, time_period)
        
        if acled_file:
            logger.info(f"  File saved to: {acled_file}")
            logger.info(f"  File exists: {os.path.exists(acled_file)}")
            logger.info(f"  File size: {os.path.getsize(acled_file) / 1024:.2f} KB")
            # Update the database
            load_json_to_db(acled_file, full_path_db)
            logger.info(f"  ✓ Updated database for {country_name}")
            data_updated = True
            countries_updated.append(country_name)
        else:
            logger.info(f"  - No new data for {country_name}")

    logger.info(f"\nData fetch complete. Updated {len(countries_updated)} countries.")
    
    metrics.end_step()
    return full_path_db, data_updated


def process_conflict_mapping(conn, logger, metrics: PerformanceMetrics, validator: DataValidator):
    """
    Step 2: Build conflict mapping (aggregation.py logic)
    """
    metrics.start_step("2. Conflict Mapping")
    
    logger.info("=" * 60)
    logger.info("STEP 2: BUILDING CONFLICT MAPPING")
    logger.info("=" * 60)
    
    logger.info("Ensuring event_conflict table exists...")
    create_event_conflict_table(conn)

    logger.info("Ensuring conflict_lookup table exists...")
    create_conflict_lookup_table(conn)

    logger.info("Building conflict mapping...")
    build_conflict_mapping(conn, logger)
    
    logger.info("✓ Conflict mapping completed")
    
    # Validate
    checks = validator.validate_conflict_mapping()
    validator.log_validation_results("Conflict Mapping", checks)
    
    metrics.end_step()


def process_unique_conflicts(conn, logger, metrics: PerformanceMetrics, validator: DataValidator):
    """
    Step 3: Generate unique conflicts and features (unique_conflicts.py logic)
    """
    metrics.start_step("3. Unique Conflicts")
    
    logger.info("=" * 60)
    logger.info("STEP 3: PROCESSING UNIQUE CONFLICTS")
    logger.info("=" * 60)
    
    create_unique_conflict_table(conn)
    ensure_indexes(conn, logger)
    populate_unique_conflict_table(conn, logger)

    ensure_conflict_type_count_tables(conn, logger)
    ensure_conflict_features_schema(conn, logger)

    rebuild_conflict_features_base(conn, logger)
    rebuild_conflict_type_counts(conn, logger)
    fill_event_type_modes(conn, logger)
    fill_assoc_actor_1_mode(conn, logger)

    ensure_conflict_time_table(conn, logger)
    rebuild_conflict_time(conn, logger)
    push_time_into_conflict_features(conn, logger)
    ensure_dashboard_indexes(conn, logger)
    
    logger.info("✓ Unique conflicts processing completed")
    
    # Validate
    checks_uc = validator.validate_unique_conflicts()
    validator.log_validation_results("Unique Conflicts", checks_uc)
    
    checks_cf = validator.validate_conflict_features()
    validator.log_validation_results("Conflict Features", checks_cf)
    
    metrics.end_step()


def process_country_aggregation(conn, logger, metrics: PerformanceMetrics, validator: DataValidator):
    """
    Step 4: Aggregate by country (aggregate_country_conflict.py logic)
    """
    metrics.start_step("4. Country Aggregation")
    
    logger.info("=" * 60)
    logger.info("STEP 4: AGGREGATING BY COUNTRY")
    logger.info("=" * 60)
    
    ensure_conflict_country_table(conn, logger, topk_actor1=5, topk_eventmode=3)
    
    logger.info("✓ Country aggregation completed")
    
    # Validate
    checks = validator.validate_country_aggregation()
    validator.log_validation_results("Country Aggregation", checks)
    
    metrics.end_step()

def process_fts_index(conn, logger, metrics: PerformanceMetrics):
    """
    Step 5: Build Full-Text Search index
    """
    metrics.start_step("5. FTS Index")
    
    logger.info("=" * 60)
    logger.info("STEP 5: BUILDING FULL-TEXT SEARCH INDEX")
    logger.info("=" * 60)
    
    # Close the connection temporarily since build_fts creates its own
    conn.close()
    
    build_fts()
    
    logger.info("✓ FTS index build completed")
    
    metrics.end_step()


def log_final_statistics(conn, logger):
    """
    Log final statistics about the database
    """
    logger.info("=" * 60)
    logger.info("FINAL DATABASE STATISTICS")
    logger.info("=" * 60)
    
    cur = conn.cursor()
    
    # Events count
    cur.execute("SELECT COUNT(*) FROM events;")
    events_count = cur.fetchone()[0]
    logger.info(f"Total events: {events_count:,}")
    
    # Conflicts count
    cur.execute("SELECT COUNT(*) FROM unique_conflict;")
    conflicts_count = cur.fetchone()[0]
    logger.info(f"Total conflicts: {conflicts_count:,}")
    
    # Countries count
    cur.execute("SELECT COUNT(*) FROM conflict_country;")
    countries_count = cur.fetchone()[0]
    logger.info(f"Total countries: {countries_count:,}")
    
    # Date range
    cur.execute("SELECT MIN(event_date), MAX(event_date) FROM events;")
    min_date, max_date = cur.fetchone()
    logger.info(f"Date range: {min_date} to {max_date}")
    
    # Top 5 countries by events
    logger.info("\nTop 5 countries by event count:")
    cur.execute("""
        SELECT country, n_events, total_fatalities 
        FROM conflict_country 
        ORDER BY n_events DESC 
        LIMIT 5;
    """)
    for i, (country, n_events, fatalities) in enumerate(cur.fetchall(), 1):
        logger.info(f"  {i}. {country}: {n_events:,} events, {fatalities:,} fatalities")
    
    logger.info("=" * 60)


def main():
    """
    Main pipeline execution with validation and metrics
    """
    logger = init_logger("pipeline")
    metrics = PerformanceMetrics()
    
    pipeline_start = time.time()
    
    logger.info("=" * 60)
    logger.info("ACLED DATA PIPELINE - STARTING")
    logger.info(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    try:
        # Load configuration
        config = load_config()
        
        # Step 1: Fetch new data
        db_path, data_updated = fetch_new_data(config, logger, metrics)
        
        if not data_updated:
            logger.info("\n⚠️  No new data fetched.")
            logger.info("To reprocess existing data, modify the code to force processing.")
            # Uncomment to always reprocess:
            # data_updated = True
        
        # Connect to database for processing steps
        conn = get_db_connection(db_path)
        validator = DataValidator(conn, logger)
        
        try:
            # Validate initial state
            logger.info("\n" + "=" * 60)
            logger.info("INITIAL DATA VALIDATION")
            logger.info("=" * 60)
            checks = validator.validate_events_table()
            validator.log_validation_results("Events Table", checks)
            
            # Step 2: Build conflict mapping
            process_conflict_mapping(conn, logger, metrics, validator)
            
            # Step 3: Process unique conflicts
            process_unique_conflicts(conn, logger, metrics, validator)
            
            # Step 4: Aggregate by country
            process_country_aggregation(conn, logger, metrics, validator)

            # Step 5: Build FTS index
            process_fts_index(conn, logger, metrics)

            # Reconnect after FTS build (since we closed it)
            conn = get_db_connection(db_path)

            # Final statistics
            log_final_statistics(conn, logger)
            
            # Print summaries
            logger.info(validator.get_validation_summary())
            logger.info(metrics.get_summary())
            
            pipeline_duration = time.time() - pipeline_start
            
            logger.info("\n" + "=" * 60)
            logger.info("✓ PIPELINE COMPLETED SUCCESSFULLY")
            logger.info(f"Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"Total duration: {pipeline_duration:.2f}s ({pipeline_duration/60:.2f} minutes)")
            logger.info("=" * 60)
            
        finally:
            conn.close()
            logger.info("Database connection closed")
            
    except Exception as e:
        logger.exception("\n❌ PIPELINE FAILED")
        logger.error(f"Error: {str(e)}")
        raise


if __name__ == "__main__":
    main()