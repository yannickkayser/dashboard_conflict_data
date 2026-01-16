# pipelineGNEWS.py
#
# Automated pipeline to fetch GNews data and process it through:
# 1. Fetching raw articles (fetch_GNEWS.py)
# 2. Deduplication (delete_duplicates_gnews_articles.py)
# 3. Translation and country classification (gnews_update_tfidf_and_article.py)
# with data validation and performance metrics

import os
import time
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Tuple
from pathlib import Path

from utils import load_config, init_logger

# Import from existing scripts
from fetch_GNEWS import fetch_articles_for_day, save_articles
from delete_duplicates_gnews_articles import (
    norm_url, norm_text, fp_text, better,
    band_keys, SIMHASH_DISTANCE, NUM_BANDS, BAND_BITS, MASK_16
)
from gnews_update_tfidf_and_article import (
    load_conflict_countries,
    build_country_aliases_from_conflicts,
    choose_country_strict,
    translate_texts,
    ensure_articles_eng_schema,
    TRANSLATION_MODEL,
    DEVICE
)

from simhash import Simhash
from collections import defaultdict
from transformers import pipeline
import torch


# =============================
# CONFIGURATION
# =============================
class PipelineConfig:
    """Configuration for GNews pipeline"""
    
    def __init__(self, config_dict: Dict):
        # Database paths
        base_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(base_dir, "..", "data")
        data_dir = os.path.abspath(data_dir)
        os.makedirs(data_dir, exist_ok=True)
        
        self.raw_db = os.path.join(data_dir, "gnews_articles_from2023.db")
        self.dedup_db = os.path.join(data_dir, "deleted_dupgnews2023.db")
        self.conflict_db = os.path.join(data_dir, "conflict_data.db")
        self.matched_db = os.path.join(data_dir, "matched_country.db")
        
        # Table names
        self.raw_table = "articles"
        self.dedup_table = "article_without_duplicates"
        self.final_table = "articles_eng"
        
        # API Configuration
        self.api_key = config_dict.get("gnews", {}).get("api_key","cbc7d3f5fe399cb90da7301863ecf370")
        self.base_url = "https://gnews.io/api/v4/search"
        self.lang = "de"
        self.country = "de"
        self.query = (
            "Protest OR Demonstration OR Unruhen OR Ausschreitungen OR Gewalt OR "
            "Angriff OR Anschlag OR Terror OR Extremismus OR Krieg OR Konflikt OR Korruption"
        )
        
        # Processing parameters
        self.translate_batch_size = 128
        self.process_batch_size = 200
        
        # Date range
        self.start_date = datetime(2025, 12, 21)
        self.end_date = datetime(2025, 12, 31)


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
    
    def validate_raw_articles(self, db_path: str, table: str) -> Dict:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        checks = {
            "total_articles": 0,
            "missing_url": 0,
            "missing_title": 0,
            "duplicate_urls": 0
        }
        
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        checks["total_articles"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE url IS NULL OR TRIM(url) = '';")
        checks["missing_url"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE title IS NULL OR TRIM(title) = '';")
        checks["missing_title"] = cur.fetchone()[0]
        
        cur.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT url, COUNT(*) as cnt FROM {table} GROUP BY url HAVING cnt > 1
            );
        """)
        checks["duplicate_urls"] = cur.fetchone()[0]
        
        conn.close()
        return checks
    
    def validate_deduplication(self, db_path: str, table: str, original_count: int) -> Dict:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        checks = {
            "total_articles": 0,
            "duplicates_removed": 0,
            "removal_percentage": 0.0,
            "duplicate_urls": 0
        }
        
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        checks["total_articles"] = cur.fetchone()[0]
        
        checks["duplicates_removed"] = original_count - checks["total_articles"]
        if original_count > 0:
            checks["removal_percentage"] = (checks["duplicates_removed"] / original_count) * 100
        
        cur.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT url, COUNT(*) as cnt FROM {table} WHERE url IS NOT NULL 
                GROUP BY url HAVING cnt > 1
            );
        """)
        checks["duplicate_urls"] = cur.fetchone()[0]
        
        conn.close()
        return checks
    
    def validate_translation(self, db_path: str, table: str) -> Dict:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        checks = {
            "total_articles": 0,
            "missing_translation": 0,
            "country_na": 0,
            "articles_with_high_confidence": 0
        }
        
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        checks["total_articles"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE title_en IS NULL OR TRIM(title_en) = '';")
        checks["missing_translation"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE article_country = 'NA';")
        checks["country_na"] = cur.fetchone()[0]
        
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE article_country_score = 100;")
        checks["articles_with_high_confidence"] = cur.fetchone()[0]
        
        conn.close()
        return checks
    
    def log_validation_results(self, step_name: str, checks: Dict):
        self.logger.info(f"\n--- Validation: {step_name} ---")
        
        errors = []
        warnings = []
        
        for key, value in checks.items():
            if "missing" in key.lower() and isinstance(value, int) and value > 0:
                warnings.append(f"  ⚠️  {key}: {value:,}")
            elif "duplicate" in key.lower() and isinstance(value, int) and value > 0:
                errors.append(f"  ❌ {key}: {value:,}")
            elif isinstance(value, (int, float)):
                self.logger.info(f"  ✓ {key}: {value:,.2f}" if isinstance(value, float) else f"  ✓ {key}: {value:,}")
            else:
                self.logger.info(f"  ✓ {key}: {value}")
        
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

        
# =============================
# STEP 0: GET OLDEST MATCHED DATE
# =============================
def get_oldest_date(logger, gnews_db: str):
    """Get the oldest article date from the matched database"""
    
    logger.info("=" * 60)
    logger.info("STEP 0: GETTING OLDEST MATCHED DATE")
    logger.info("=" * 60)
    
    if not os.path.exists(gnews_db):
        logger.warning(f"Gnews database not found: {gnews_db}")
        logger.info("Returning default date: 2024-01-01")
        return "2024-01-01"
    
    try:
        conn = sqlite3.connect(gnews_db)
        cur = conn.cursor()
        
        # Check if the table exists
        tables = cur.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='articles';
        """).fetchall()
        
        if not tables:
            logger.warning("Table 'articles' not found in matched database")
            logger.info("Returning default date: 2024-01-01")
            conn.close()
            return "2024-01-01"
        
        # Get the oldest article date
        result = cur.execute("""
            SELECT MIN(publishedAt)
            FROM articles
        """).fetchone()
        
        conn.close()
        
        if result and result[0]:
            oldest_date = result[0][:10]  # Extract YYYY-MM-DD
            logger.info(f"Oldest matched article date: {oldest_date}")
            return oldest_date
        else:
            logger.warning("No articles found in gnews database")
            logger.info("Returning default date: 2024-01-01")
            return "2024-01-01"
            
    except Exception as e:
        logger.error(f"Error reading gnews database: {e}")
        logger.info("Returning default date: 2024-01-01")
        return "2024-01-01"

# =============================
# STEP 1: FETCH GNEWS DATA
# =============================
def get_newest_date(logger, db_path):

    # connect to the database
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # query the newest date (most up to date)
    c.execute("""
            SELECT publishedAt
            FROM articles
            ORDER BY publishedAT DESC
            LIMIT 1
            """)
    result = c.fetchone()
    
    logger.info("=" * 60)
    logger.info("STEP 0: Getting latest date")
    logger.info("=" * 60)
    logger.info(f"Date range: {result}")

    c.close()
    conn.close()  # also good practice to close the connection
    return result[0] if result else "2000-01-01"

def fetch_new_data(config: PipelineConfig, logger, metrics: PerformanceMetrics) -> Tuple[int, bool]:
    """Step 1: Fetch new GNews data using fetch_GNEWS.py functions"""
    metrics.start_step("1. Fetch GNews Data")
    
    logger.info("=" * 60)
    logger.info("STEP 1: FETCHING NEW GNEWS DATA")
    logger.info("=" * 60)
    
    # Use the existing fetch_articles_monthly logic but adapted
    from datetime import datetime, timedelta
    import requests
    
    total_articles = 0
    current = config.start_date
    
    # Create database and table if needed
    conn = sqlite3.connect(config.raw_db)
    cur = conn.cursor()

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {config.raw_table} (
            id TEXT PRIMARY KEY,
            publishedAt TEXT,
            title TEXT,
            description TEXT,
            content TEXT,
            url TEXT,
            source_name TEXT,
            source_url TEXT
        );
    """)
    conn.commit()
    conn.close()
    
    while current < config.end_date:
        logger.info(f"\nProcessing {current.date()}...")
        
        # Fetch articles for this day (reusing existing logic)
        next_date = current + timedelta(days=1)
        page = 1
        day_count = 0
        
        conn = sqlite3.connect(config.raw_db)
        cur = conn.cursor()
        
        while True:
            params = {
                "q": config.query,
                "token": config.api_key,
                "lang": config.lang,
                "country": config.country,
                "from": current.strftime("%Y-%m-%dT00:00:00Z"),
                "to": next_date.strftime("%Y-%m-%dT00:00:00Z"),
                "sortby": "relevance",
                "page": page,
                "max": 100,
            }
            
            r = requests.get(config.base_url, params=params)
            
            if r.status_code in [403, 429]:
                logger.error(f"Rate limit reached at {current.date()} page {page}")
                conn.close()
                raise SystemExit("Pipeline stopped due to rate limit.")
            
            if r.status_code != 200:
                logger.error(f"API error {r.status_code} for {current.date()}: {r.text}")
                conn.close()
                raise SystemExit("Pipeline stopped due to API error.")
            
            res = r.json()
            articles = res.get("articles", [])
            
            if not articles:
                break
            
            # Save articles using existing save logic
            for article in articles:
                data = {
                    "id": article.get("url"),
                    "publishedAt": article["publishedAt"],
                    "title": article.get("title"),
                    "description": article.get("description"),
                    "content": article.get("content"),
                    "url": article.get("url"),
                    "source_name": article["source"].get("name"),
                    "source_url": article["source"].get("url"),
                }
                cur.execute(f"""
                    INSERT OR IGNORE INTO {config.raw_table} 
                    (id, publishedAt, title, description, content, url, source_name, source_url)
                    VALUES (:id, :publishedAt, :title, :description, :content, :url, :source_name, :source_url)
                """, data)
            
            conn.commit()
            day_count += len(articles)
            logger.info(f"  Fetched {len(articles)} articles from page {page}")
            
            if len(articles) < 25:
                break
            
            page += 1
            time.sleep(1)
        
        conn.close()
        logger.info(f"Total {day_count} articles for {current.date()}")
        total_articles += day_count
        current += timedelta(days=1)
    
    logger.info(f"\n✓ Fetch complete. Total articles: {total_articles:,}")
    
    metrics.end_step()
    return total_articles, total_articles > 0


# =============================
# STEP 2: DEDUPLICATION
# =============================
def deduplicate_articles(config: PipelineConfig, logger, metrics: PerformanceMetrics, original_count: int) -> int:
    """Step 2: Deduplicate using delete_duplicates_gnews_articles.py functions"""
    metrics.start_step("2. Deduplication")
    
    logger.info("=" * 60)
    logger.info("STEP 2: DEDUPLICATING ARTICLES")
    logger.info("=" * 60)
    
    # Load articles from raw DB
    src_conn = sqlite3.connect(config.raw_db)
    src_conn.row_factory = sqlite3.Row
    src_cur = src_conn.cursor()
    
    logger.info("Loading articles from raw database...")
    rows = src_cur.execute(f"""
        SELECT * FROM {config.raw_table}
        WHERE (url IS NOT NULL AND TRIM(url) <> '')
           OR (title IS NOT NULL AND TRIM(title) <> '')
           OR (description IS NOT NULL AND TRIM(description) <> '')
    """).fetchall()
    
    logger.info(f"Loaded {len(rows):,} articles")
    
    # Pass 1: URL deduplication using imported norm_url and better functions
    logger.info("\nPass 1: URL deduplication...")
    by_url = {}
    no_url = []
    
    for r in rows:
        d = dict(r)
        u = norm_url(d.get("url"))
        if u:
            by_url[u] = better(by_url[u], d) if u in by_url else d
        else:
            no_url.append(d)
    
    candidates = list(by_url.values()) + no_url
    logger.info(f"After URL dedup: {len(candidates):,} articles")
    
    # Pass 2: SimHash near-duplicate detection using imported functions
    logger.info("\nPass 2: Near-duplicate detection (SimHash)...")
    buckets = defaultdict(list)
    kept = []
    kept_hash = []
    n_near_dups = 0
    
    for i, d in enumerate(candidates):
        text = fp_text(d.get("title"), d.get("description"))
        if not text:
            kept.append(d)
            kept_hash.append(None)
        else:
            h = Simhash(text).value
            
            cand_idx = set()
            for k in band_keys(h):
                for idx in buckets.get(k, []):
                    cand_idx.add(idx)
            
            dup_of = None
            for idx in cand_idx:
                h2 = kept_hash[idx]
                if h2 is None:
                    continue
                if Simhash(h).distance(Simhash(h2)) <= SIMHASH_DISTANCE:
                    dup_of = idx
                    break
            
            if dup_of is None:
                new_idx = len(kept)
                kept.append(d)
                kept_hash.append(h)
                for k in band_keys(h):
                    buckets[k].append(new_idx)
            else:
                n_near_dups += 1
                kept[dup_of] = better(kept[dup_of], d)
        
        if (i + 1) % 10000 == 0:
            logger.info(f"  Processed {i+1:,}/{len(candidates):,} articles...")
    
    logger.info(f"Near-duplicates merged: {n_near_dups:,}")
    logger.info(f"Final unique articles: {len(kept):,}")
    
    # Write to dedup database
    logger.info("\nWriting to deduplication database...")
    
    col_info = src_cur.execute(f"PRAGMA table_info({config.raw_table});").fetchall()
    cols = [c[1] for c in col_info]
    col_defs = [f"{c[1]} {c[2]}" for c in col_info]
    
    dedup_conn = sqlite3.connect(config.dedup_db)
    dedup_cur = dedup_conn.cursor()
    
    dedup_cur.execute(f"DROP TABLE IF EXISTS {config.dedup_table};")
    dedup_cur.execute(f"CREATE TABLE {config.dedup_table} ({', '.join(col_defs)});")
    
    placeholders = ",".join(["?"] * len(cols))
    dedup_cur.executemany(
        f"INSERT INTO {config.dedup_table} ({', '.join(cols)}) VALUES ({placeholders});",
        [tuple(r.get(c) for c in cols) for r in kept]
    )
    dedup_conn.commit()
    
    logger.info(f"✓ Deduplication complete: {len(rows):,} → {len(kept):,} articles")
    
    src_conn.close()
    dedup_conn.close()
    
    metrics.end_step()
    return len(kept)


# =============================
# STEP 3: TRANSLATION & CLASSIFICATION
# =============================
def translate_and_classify(config: PipelineConfig, logger, metrics: PerformanceMetrics):
    """Step 3: Translate and classify using gnews_update_tfidf_and_article.py functions"""
    metrics.start_step("3. Translation & Classification")
    
    logger.info("=" * 60)
    logger.info("STEP 3: TRANSLATION & COUNTRY CLASSIFICATION")
    logger.info("=" * 60)
    
    # Load country aliases using existing function
    logger.info("Loading country aliases from conflict database...")
    conflict_countries = load_conflict_countries(Path(config.conflict_db))
    country_aliases = build_country_aliases_from_conflicts(conflict_countries)
    logger.info(f"Loaded {len(country_aliases)} countries")
    
    # Connect to database
    conn = sqlite3.connect(config.dedup_db)
    
    # Create target table using existing function
    logger.info("Creating target table...")
    ensure_articles_eng_schema(conn)
    
    # Find articles needing translation
    logger.info("Finding articles needing translation...")
    rows = conn.execute(f"""
        SELECT a.id, a.publishedAt, a.title, a.description, a.url, a.source_name, a.source_url
        FROM {config.dedup_table} a
        LEFT JOIN {config.final_table} e ON a.id = e.id
        WHERE e.id IS NULL
        ORDER BY a.rowid
    """).fetchall()
    
    logger.info(f"Found {len(rows):,} articles to process")
    
    if len(rows) == 0:
        logger.info("No articles to process")
        conn.close()
        metrics.end_step()
        return
    
    # Load translation model
    logger.info("Loading translation model...")
    translator = pipeline("translation_de_to_en", model=TRANSLATION_MODEL, device=DEVICE)
    logger.info(f"Using device: {'GPU' if DEVICE >= 0 else 'CPU'}")
    
    # Process in batches
    logger.info("\nProcessing articles...")
    for start in range(0, len(rows), config.process_batch_size):
        batch = rows[start:start + config.process_batch_size]
        
        ids = [r[0] for r in batch]
        dates = [r[1][:10] if r[1] and len(r[1]) >= 10 else None for r in batch]
        titles_de = [r[2] or "" for r in batch]
        descs_de = [r[3] or "" for r in batch]
        urls = [r[4] for r in batch]
        source_names = [r[5] for r in batch]
        source_urls = [r[6] for r in batch]
        
        # Translate using existing function
        titles_en = translate_texts(translator, titles_de, config.translate_batch_size, num_beams=2)
        descs_en = translate_texts(translator, descs_de, config.translate_batch_size, num_beams=2)
        
        # Classify country using existing function
        payload = []
        for i in range(len(batch)):
            analysis_text = f"{titles_en[i]} {descs_en[i]}".strip()
            country, score = choose_country_strict(analysis_text, country_aliases)
            
            payload.append((
                ids[i], dates[i], urls[i], source_names[i], source_urls[i],
                titles_en[i], descs_en[i], country, score
            ))
        
        # Insert
        conn.executemany(f"""
            INSERT INTO {config.final_table} 
            (id, publishedAt, url, source_name, source_url, title_en, description_en, 
             article_country, article_country_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                publishedAt=excluded.publishedAt,
                url=excluded.url,
                source_name=excluded.source_name,
                source_url=excluded.source_url,
                title_en=excluded.title_en,
                description_en=excluded.description_en,
                article_country=excluded.article_country,
                article_country_score=excluded.article_country_score;
        """, payload)
        conn.commit()
        
        logger.info(f"  Processed batch {start+len(batch):,}/{len(rows):,} articles")
    
    conn.close()
    logger.info("✓ Translation and classification complete")
    
    metrics.end_step()


# =============================
# STATISTICS
# =============================
def log_final_statistics(config: PipelineConfig, logger):
    """Log final statistics about the databases"""
    logger.info("=" * 60)
    logger.info("FINAL DATABASE STATISTICS")
    logger.info("=" * 60)
    
    # Raw articles
    conn_raw = sqlite3.connect(config.raw_db)
    cur = conn_raw.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {config.raw_table};")
    raw_count = cur.fetchone()[0]
    logger.info(f"Raw articles: {raw_count:,}")
    conn_raw.close()
    
    # Deduplicated and translated articles
    conn_dedup = sqlite3.connect(config.dedup_db)
    cur = conn_dedup.cursor()
    
    cur.execute(f"SELECT COUNT(*) FROM {config.dedup_table};")
    dedup_count = cur.fetchone()[0]
    logger.info(f"Deduplicated articles: {dedup_count:,}")
    
    cur.execute(f"SELECT COUNT(*) FROM {config.final_table};")
    translated_count = cur.fetchone()[0]
    logger.info(f"Translated articles: {translated_count:,}")
    
    # Date range
    cur.execute(f"SELECT MIN(publishedAt), MAX(publishedAt) FROM {config.final_table};")
    min_date, max_date = cur.fetchone()
    logger.info(f"Date range: {min_date} to {max_date}")
    
    # Country distribution
    logger.info("\nTop 10 countries by article count:")
    cur.execute(f"""
        SELECT article_country, COUNT(*) as cnt
        FROM {config.final_table}
        WHERE article_country != 'NA'
        GROUP BY article_country
        ORDER BY cnt DESC
        LIMIT 10;
    """)
    for i, (country, cnt) in enumerate(cur.fetchall(), 1):
        logger.info(f"  {i}. {country}: {cnt:,} articles")
    
    # NA articles
    cur.execute(f"SELECT COUNT(*) FROM {config.final_table} WHERE article_country = 'NA';")
    na_count = cur.fetchone()[0]
    logger.info(f"\nArticles without country classification: {na_count:,}")
    
    conn_dedup.close()
    logger.info("=" * 60)


# =============================
# MAIN PIPELINE
# =============================
# Update the main() function section where configuration is set:
def main():
    """Main pipeline execution"""
    logger = init_logger("gnews_pipeline")
    metrics = PerformanceMetrics()
    validator = DataValidator(logger)
    
    pipeline_start = time.time()
    
    logger.info("=" * 60)
    logger.info("GNEWS DATA PIPELINE - STARTING")
    logger.info(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    try:
        # Load configuration
        config_dict = load_config()
        config = PipelineConfig(config_dict)
        
        logger.info(f"\nConfiguration:")
        logger.info(f"  Raw DB: {config.raw_db}")
        logger.info(f"  Dedup DB: {config.dedup_db}")
        logger.info(f"  Conflict DB: {config.conflict_db}")

        # Step 0: Get oldest matched date and set date range
        
        oldest_matched = get_oldest_date(logger, config.raw_db)
        
        # Parse the oldest date and go back 2 weeks
        oldest_dt = datetime.strptime(oldest_matched, "%Y-%m-%d")
        config.start_date = oldest_dt - timedelta(weeks=4)
        config.end_date = oldest_dt  # Fetch up to the oldest matched date
        
        logger.info(f"\nDynamic date range calculated:")
        logger.info(f"  Oldest matched article: {oldest_matched}")
        logger.info(f"  Fetch from: {config.start_date.date()} (2 weeks before)")
        logger.info(f"  Fetch to: {config.end_date.date()}")
        
        # Check if start date is reasonable
        if config.start_date.year < 2020:
            logger.warning(f"Start date {config.start_date.date()} seems too early.")
            logger.info("Using default start date: 2023-01-01")
            config.start_date = datetime(2023, 1, 1)
        
        if config.start_date >= config.end_date:
            logger.warning("Start date is after or equal to end date. No data to fetch.")
            logger.info("Pipeline will exit.")
            return
        
        # Step 1: Fetch new data
        original_count, data_updated = fetch_new_data(config, logger, metrics)
        
        if original_count == 0:
            logger.info("No new articles fetched. Pipeline will exit.")
            return
        
        # Validate raw articles
        checks = validator.validate_raw_articles(config.raw_db, config.raw_table)
        validator.log_validation_results("Raw Articles", checks)
        
        # Step 2: Deduplicate
        dedup_count = deduplicate_articles(config, logger, metrics, original_count)
        
        # Validate deduplication
        checks = validator.validate_deduplication(config.dedup_db, config.dedup_table, original_count)
        validator.log_validation_results("Deduplication", checks)
        
        # Step 3: Translate and classify
        translate_and_classify(config, logger, metrics)
        
        # Validate translation
        checks = validator.validate_translation(config.dedup_db, config.final_table)
        validator.log_validation_results("Translation & Classification", checks)
        
        # Final statistics
        log_final_statistics(config, logger)
        
        # Print summaries
        logger.info(validator.get_validation_summary())
        logger.info(metrics.get_summary())
        
        pipeline_duration = time.time() - pipeline_start
        
        logger.info("\n" + "=" * 60)
        logger.info("✓ PIPELINE COMPLETED SUCCESSFULLY")
        logger.info(f"Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Total duration: {pipeline_duration:.2f}s ({pipeline_duration/60:.2f} minutes)")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.exception("\n✗ PIPELINE FAILED")
        logger.error(f"Error: {str(e)}")
        raise

if __name__ == "__main__":
    main()