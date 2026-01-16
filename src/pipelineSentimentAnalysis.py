# pipelineSentimentAnalysis.py
#
# Automated pipeline to process German news articles with NLP enrichment:
# - Geographic detection (domestic vs. international)
# - Sentiment/emotion analysis
# - Event type classification
# - Topic modeling
# - Article clustering and deduplication

import os
import json
import time
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, Tuple, Optional

import geonamescache
import spacy
from transformers import pipeline
from bertopic import BERTopic
from sklearn.feature_extraction.text import CountVectorizer

from utils import load_config, get_db_connection, init_logger


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
        self.output_art = os.path.join(data_dir, "processed_conflict_articles_test.csv")
        self.output_evt = os.path.join(data_dir, "processed_conflict_events_test.csv")
        
        # Table names
        self.raw_table = "articles"
        self.enriched_table = "enriched_articles"

        # Variables
        self.similarity_treshold = 0.7



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
    
    def validate_raw_articles(self) -> Dict:
        """Validate raw articles table"""
        cur = self.conn.cursor()
        
        checks = {
            "total_articles": 0,
            "articles_with_null_title": 0,
            "articles_with_null_date": 0,
            "duplicate_articles": 0,
            "date_range_start": None,
            "date_range_end": None
        }
        
        # Total articles
        cur.execute("SELECT COUNT(*) FROM articles;")
        checks["total_articles"] = cur.fetchone()[0]
        
        # Articles with NULL titles
        cur.execute("SELECT COUNT(*) FROM articles WHERE title IS NULL OR TRIM(title) = '';")
        checks["articles_with_null_title"] = cur.fetchone()[0]
        
        # Articles with NULL dates
        cur.execute("SELECT COUNT(*) FROM articles WHERE publishedAt IS NULL;")
        checks["articles_with_null_date"] = cur.fetchone()[0]
        
        # Check for duplicates (same title and date)
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT title, publishedAt, COUNT(*) as cnt 
                FROM articles 
                GROUP BY title, publishedAt 
                HAVING cnt > 1
            );
        """)
        checks["duplicate_articles"] = cur.fetchone()[0]
        
        # Date range
        cur.execute("SELECT MIN(publishedAt), MAX(publishedAt) FROM articles;")
        start, end = cur.fetchone()
        checks["date_range_start"] = start
        checks["date_range_end"] = end
        
        return checks
    
    def validate_enrichment(self) -> Dict:
        """Validate enriched articles"""
        cur = self.conn.cursor()
        
        checks = {
            "total_enriched": 0,
            "domestic_articles": 0,
            "international_articles": 0,
            "missing_sentiment": 0,
            "missing_event_type": 0,
            "missing_topic": 0,
            "articles_with_locations": 0,
            "domestic_percentage": 0.0
        }
        
        # Total enriched
        cur.execute("SELECT COUNT(*) FROM enriched_articles;")
        checks["total_enriched"] = cur.fetchone()[0]
        
        # Domestic vs international
        cur.execute("SELECT COUNT(*) FROM enriched_articles WHERE is_domestic = 1;")
        checks["domestic_articles"] = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM enriched_articles WHERE is_domestic = 0;")
        checks["international_articles"] = cur.fetchone()[0]
        
        # Missing enrichment fields
        cur.execute("SELECT COUNT(*) FROM enriched_articles WHERE emotion_label IS NULL;")
        checks["missing_sentiment"] = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM enriched_articles WHERE acled_event_type IS NULL;")
        checks["missing_event_type"] = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM enriched_articles WHERE narrative_topic_id IS NULL;")
        checks["missing_topic"] = cur.fetchone()[0]
        
        # Articles with detected locations
        cur.execute("""
            SELECT COUNT(*) FROM enriched_articles 
            WHERE detected_locations IS NOT NULL AND TRIM(detected_locations) != '';
        """)
        checks["articles_with_locations"] = cur.fetchone()[0]
        
        # Calculate domestic percentage
        if checks["total_enriched"] > 0:
            checks["domestic_percentage"] = (checks["domestic_articles"] / checks["total_enriched"]) * 100
        
        return checks
    
    def validate_clustering(self) -> Dict:
        """Validate article clustering"""
        cur = self.conn.cursor()
        
        checks = {
            "total_clusters": 0,
            "duplicate_articles": 0,
            "unique_articles": 0,
            "avg_cluster_size": 0.0,
            "max_cluster_size": 0,
            "singleton_clusters": 0
        }
        
        # Total clusters
        cur.execute("SELECT COUNT(DISTINCT article_cluster_id) FROM enriched_articles;")
        checks["total_clusters"] = cur.fetchone()[0]
        
        # Duplicates vs unique
        cur.execute("SELECT COUNT(*) FROM enriched_articles WHERE is_duplicate = 1;")
        checks["duplicate_articles"] = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM enriched_articles WHERE is_duplicate = 0;")
        checks["unique_articles"] = cur.fetchone()[0]
        
        # Cluster size statistics
        cur.execute("""
            SELECT AVG(cluster_size), MAX(cluster_size)
            FROM (
                SELECT article_cluster_id, COUNT(*) as cluster_size
                FROM enriched_articles
                GROUP BY article_cluster_id
            );
        """)
        avg_size, max_size = cur.fetchone()
        checks["avg_cluster_size"] = float(avg_size) if avg_size else 0.0
        checks["max_cluster_size"] = max_size if max_size else 0
        
        # Singleton clusters
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT article_cluster_id, COUNT(*) as cluster_size
                FROM enriched_articles
                GROUP BY article_cluster_id
                HAVING cluster_size = 1
            );
        """)
        checks["singleton_clusters"] = cur.fetchone()[0]
        
        return checks
    
    def log_validation_results(self, step_name: str, checks: Dict):
        """Log validation results"""
        self.logger.info(f"\n--- Validation: {step_name} ---")
        
        errors = []
        warnings = []
        
        for key, value in checks.items():
            # Identify errors and warnings
            if "null" in key.lower() and isinstance(value, int) and value > 0:
                warnings.append(f"  ⚠️  {key}: {value}")
            elif "missing" in key.lower() and isinstance(value, int) and value > 0:
                warnings.append(f"  ⚠️  {key}: {value}")
            elif "duplicate" in key.lower() and isinstance(value, int) and value > 10:
                warnings.append(f"  ⚠️  {key}: {value}")
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
            status = "✓ PASS" if errors == 0 else "✗ FAIL"
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


class GermanLocationDetector:
    """Handle German location detection"""
    
    def __init__(self, logger):
        self.logger = logger
        self.logger.info("Initializing German location dictionary...")
        
        # Build location set
        gc = geonamescache.GeonamesCache()
        cities = gc.get_cities()
        
        self.german_locations = {
            city["name"].lower()
            for city in cities.values()
            if city["countrycode"] == "DE"
        }
        
        # Add common German location names
        german_extras = {
            "deutschland", "germany", "bundesrepublik", "berlin", "münchen", "munich",
            "hamburg", "köln", "cologne", "frankfurt", "stuttgart", "düsseldorf",
            "dortmund", "essen", "leipzig", "bremen", "dresden", "hannover", "nürnberg",
            "duisburg", "bochum", "wuppertal", "bielefeld", "bonn", "münster", "karlsruhe",
            "mannheim", "augsburg", "wiesbaden", "gelsenkirchen", "mönchengladbach",
            "braunschweig", "chemnitz", "aachen", "kiel", "halle", "magdeburg", "freiburg",
            "oberhausen", "lübeck", "erfurt", "mainz", "rostock", "kassel", "hagen",
            "saarbrücken", "hamm", "potsdam", "ludwigshafen", "oldenburg", "leverkusen",
            "osnabrück", "solingen", "heidelberg", "herne", "neuss", "darmstadt", "paderborn",
            "remscheid", "regensburg", "ingolstadt", "würzburg", "wolfsburg", "fürth", "ulm", "offenbach"
        }
        self.german_locations.update(german_extras)
        
        self.german_suffixes = ["straße", "strasse", "platz", "tor", "allee", "bahnhof", "weg", "brücke"]
        
        self.logger.info(f"Loaded {len(self.german_locations)} German locations")
    
    def detect_domestic(self, text: str, nlp) -> Tuple[bool, str]:
        """
        Detect if article is about Germany (domestic) or international
        Returns: (is_domestic, detected_locations)
        """
        if not text:
            return False, ""
        
        doc = nlp(text)
        found_locs = [ent.text.lower() for ent in doc.ents if ent.label_ in ["GPE", "LOC"]]
        
        is_domestic = any(loc in self.german_locations for loc in found_locs)
        
        if not is_domestic:
            is_domestic = any(any(s in loc for s in self.german_suffixes) for loc in found_locs)
            if not is_domestic:
                is_domestic = any(kw in text.lower() for kw in self.german_locations)
        
        return is_domestic, ", ".join(set(found_locs))


class NLPModels:
    """Initialize and manage NLP models"""
    
    def __init__(self, logger):
        self.logger = logger
        self.logger.info("Initializing NLP Models... (This may take a few minutes)")
        
        # Load spaCy German model
        try:
            self.nlp = spacy.load("de_core_news_sm")
        except:
            self.logger.info("Downloading spaCy German model...")
            os.system("python -m spacy download de_core_news_sm")
            self.nlp = spacy.load("de_core_news_sm")
        
        # German emotion classifier
        self.logger.info("Loading German emotion model...")
        self.emotion_pipe = pipeline(
            "text-classification",
            model="ChrisLalk/German-Emotions",
            tokenizer="ChrisLalk/German-Emotions",
            return_all_scores=True,
            truncation=True,
            top_k=None
        )
        
        # Multilingual zero-shot classifier
        self.logger.info("Loading multilingual zero-shot model...")
        self.zero_shot = pipeline(
            "zero-shot-classification",
            model="MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
        )
        
        self.logger.info("✓ All NLP models loaded successfully")
    
    def get_emotion(self, text: str) -> Tuple[str, float]:
        """
        Get emotion label and numeric sentiment from German text
        Returns: (emotion_label, sentiment_numeric)
        """
        try:
            results = self.emotion_pipe(text[:512])[0]
            top_result = max(results, key=lambda x: x['score'])
            label = top_result['label'].lower()
            score = top_result['score']
            
            # Map to numeric sentiment
            if label in ['joy', 'surprise']:
                val = score
            elif label in ['anger', 'fear', 'disgust', 'sadness']:
                val = -score
            else:
                val = 0.0
            
            return label, val
        except Exception as e:
            return "neutral", 0.0
    
    def classify_event_type(self, text: str, labels: list) -> str:
        """
        Classify text into ACLED event types
        """
        try:
            result = self.zero_shot(text[:512], labels)
            return result['labels'][0]
        except Exception as e:
            return "Unknown"


def title_similarity(a: str, b: str) -> float:
    """Calculate similarity between two titles"""
    return SequenceMatcher(None, a, b).ratio()


def load_raw_articles(full_path_db, logger, metrics: PerformanceMetrics) -> Tuple[pd.DataFrame, sqlite3.Connection]:
    """
    Step 1: Load raw articles from database
    """
    metrics.start_step("1. Load Raw Articles")
    
    logger.info("=" * 60)
    logger.info("STEP 1: LOADING RAW ARTICLES")
    logger.info("=" * 60)
    
    conn = get_db_connection(full_path_db)
    
    logger.info(f"Reading from database: {full_path_db}")
    df = pd.read_sql_query("SELECT * FROM articles", conn)
    
    logger.info(f"Loaded {len(df):,} articles")
    
    # Process datetime fields
    df['published_at_dt'] = pd.to_datetime(df['publishedAt'])
    df['published_date'] = df['published_at_dt'].dt.date.astype(str)
    df['published_month'] = df['published_at_dt'].dt.to_period('M').astype(str)
    
    logger.info("✓ Articles loaded and preprocessed")
    
    metrics.end_step()
    return df, conn


def geographic_detection(df: pd.DataFrame, location_detector: GermanLocationDetector, 
                        nlp_models: NLPModels, logger, metrics: PerformanceMetrics) -> pd.DataFrame:
    """
    Step 2: Detect geographic context (domestic vs international)
    """
    metrics.start_step("2. Geographic Detection")
    
    logger.info("=" * 60)
    logger.info("STEP 2: GEOGRAPHIC DETECTION")
    logger.info("=" * 60)
    
    logger.info(f"Processing {len(df):,} articles for geographic detection...")
    
    def detect_for_row(row):
        text = f"{row['title']} {row['description']}"
        return location_detector.detect_domestic(text, nlp_models.nlp)
    
    geo_results = df.apply(detect_for_row, axis=1)
    df['is_domestic'] = geo_results.apply(lambda x: x[0])
    df['detected_locations'] = geo_results.apply(lambda x: x[1])
    
    domestic_count = df['is_domestic'].sum()
    logger.info(f"✓ Detected {domestic_count:,} domestic articles ({domestic_count/len(df)*100:.1f}%)")
    
    metrics.end_step()
    return df


def cluster_articles(df: pd.DataFrame, similarity_threshold: float, logger, 
                     metrics: PerformanceMetrics) -> pd.DataFrame:
    """
    Step 3: Cluster similar articles to identify unique events
    """
    metrics.start_step("3. Article Clustering")
    
    logger.info("=" * 60)
    logger.info("STEP 3: CLUSTERING ARTICLES")
    logger.info("=" * 60)
    
    logger.info(f"Clustering with similarity threshold: {similarity_threshold}")
    
    # Sort by date and title for sequential comparison
    df = df.sort_values(['published_date', 'title'])
    df['article_cluster_id'] = range(len(df))
    df['is_duplicate'] = False
    
    duplicates = 0
    for i in range(1, len(df)):
        if df.iloc[i]['published_date'] == df.iloc[i-1]['published_date']:
            if title_similarity(df.iloc[i]['title'], df.iloc[i-1]['title']) >= similarity_threshold:
                df.iloc[i, df.columns.get_loc('is_duplicate')] = True
                df.iloc[i, df.columns.get_loc('article_cluster_id')] = df.iloc[i-1]['article_cluster_id']
                duplicates += 1
    
    unique_clusters = df['article_cluster_id'].nunique()
    logger.info(f"✓ Found {duplicates:,} duplicate articles")
    logger.info(f"✓ Created {unique_clusters:,} unique article clusters")
    
    metrics.end_step()
    return df


def sentiment_analysis(df: pd.DataFrame, nlp_models: NLPModels, logger, 
                       metrics: PerformanceMetrics) -> pd.DataFrame:
    """
    Step 4: Perform sentiment and emotion analysis on unique events
    """
    metrics.start_step("4. Sentiment Analysis")
    
    logger.info("=" * 60)
    logger.info("STEP 4: SENTIMENT & EMOTION ANALYSIS")
    logger.info("=" * 60)
    
    # Get first article from each cluster as representative
    df_events = df.sort_values(by=["article_cluster_id", "published_at_dt"]) \
                  .groupby("article_cluster_id").first().reset_index()
    
    logger.info(f"Analyzing sentiment for {len(df_events):,} unique events...")
    
    def analyze_sentiment(row):
        text = f"{row['title']} {row['description']}"
        return nlp_models.get_emotion(text)
    
    sent_results = df_events.apply(analyze_sentiment, axis=1)
    df_events['emotion_label'] = sent_results.apply(lambda x: x[0])
    df_events['sentiment_numeric'] = sent_results.apply(lambda x: x[1])
    
    # Log sentiment distribution
    emotion_dist = df_events['emotion_label'].value_counts()
    logger.info("\nEmotion distribution:")
    for emotion, count in emotion_dist.items():
        logger.info(f"  {emotion}: {count:,} ({count/len(df_events)*100:.1f}%)")
    
    logger.info("✓ Sentiment analysis completed")
    
    metrics.end_step()
    return df_events


def event_classification(df_events: pd.DataFrame, nlp_models: NLPModels, logger, 
                         metrics: PerformanceMetrics) -> pd.DataFrame:
    """
    Step 5: Classify events using ACLED taxonomy
    """
    metrics.start_step("5. Event Classification")
    
    logger.info("=" * 60)
    logger.info("STEP 5: EVENT TYPE CLASSIFICATION")
    logger.info("=" * 60)
    
    acled_labels = [
        "Protests",
        "Battles",
        "Strategic developments",
        "Violence against civilians",
        "Riots",
        "Explosions"
    ]
    
    logger.info(f"Classifying {len(df_events):,} events into ACLED categories...")
    
    def classify_event(row):
        text = f"{row['title']} {row['description']}"
        return nlp_models.classify_event_type(text, acled_labels)
    
    df_events['acled_event_type'] = df_events.apply(classify_event, axis=1)
    
    # Log classification distribution
    type_dist = df_events['acled_event_type'].value_counts()
    logger.info("\nEvent type distribution:")
    for event_type, count in type_dist.items():
        logger.info(f"  {event_type}: {count:,} ({count/len(df_events)*100:.1f}%)")
    
    logger.info("✓ Event classification completed")
    
    metrics.end_step()
    return df_events


def topic_modeling(df_events: pd.DataFrame, logger, metrics: PerformanceMetrics) -> pd.DataFrame:
    """
    Step 6: Extract narrative topics using BERTopic
    """
    metrics.start_step("6. Topic Modeling")
    
    logger.info("=" * 60)
    logger.info("STEP 6: TOPIC MODELING")
    logger.info("=" * 60)
    
    logger.info(f"Running BERTopic on {len(df_events):,} events...")
    
    # Prepare texts
    texts = (df_events['title'] + " " + df_events['description'].fillna("")).tolist()
    
    # Initialize BERTopic with multilingual support
    topic_model = BERTopic(
        language="multilingual",
        vectorizer_model=CountVectorizer(stop_words=None, min_df=3)
    )
    
    # Fit and transform
    topics, probabilities = topic_model.fit_transform(texts)
    df_events['narrative_topic_id'] = topics
    
    # Log topic statistics
    topic_dist = pd.Series(topics).value_counts()
    n_topics = len(topic_dist[topic_dist.index != -1])  # Exclude outlier topic
    logger.info(f"✓ Identified {n_topics} topics")
    logger.info(f"  Outliers (topic -1): {(topics == -1).sum():,}")
    
    metrics.end_step()
    return df_events


def merge_and_save(df_all: pd.DataFrame, df_events: pd.DataFrame, conn: sqlite3.Connection,
                   config: dict, logger, metrics: PerformanceMetrics):
    """
    Step 7: Merge results and save to database and CSV
    """
    metrics.start_step("7. Save Results")
    
    logger.info("=" * 60)
    logger.info("STEP 7: SAVING RESULTS")
    logger.info("=" * 60)
    
    # Merge event-level analysis back to all articles
    nlp_results = df_events[[
        'article_cluster_id',
        'emotion_label',
        'sentiment_numeric',
        'acled_event_type',
        'narrative_topic_id'
    ]]
    
    df_enriched = df_all.merge(nlp_results, on='article_cluster_id', how='left')
    
    # Remove original publishedAt column to avoid conflicts
    if "publishedAt" in df_enriched.columns:
        df_enriched.drop(columns=["publishedAt"], inplace=True)
    
    # Save to database
    logger.info("Writing to database table: enriched_articles")
    df_enriched.to_sql('enriched_articles', conn, if_exists='replace', index=False)
    
    # Save CSVs
    articles_output = config["output"]["articles_csv"]
    events_output = config["output"]["events_csv"]
    
    logger.info(f"Saving all articles to: {articles_output}")
    df_enriched.to_csv(articles_output, index=False, encoding='utf-8-sig')
    
    logger.info(f"Saving unique events to: {events_output}")
    df_events.to_csv(events_output, index=False, encoding='utf-8-sig')
    
    logger.info(f"✓ Saved {len(df_enriched):,} enriched articles")
    logger.info(f"✓ Saved {len(df_events):,} unique events")
    
    metrics.end_step()


def log_final_statistics(conn, logger):
    """
    Log final statistics about the enriched database
    """
    logger.info("=" * 60)
    logger.info("FINAL DATABASE STATISTICS")
    logger.info("=" * 60)
    
    cur = conn.cursor()
    
    # Total articles
    cur.execute("SELECT COUNT(*) FROM enriched_articles;")
    total = cur.fetchone()[0]
    logger.info(f"Total enriched articles: {total:,}")
    
    # Domestic vs international
    cur.execute("SELECT is_domestic, COUNT(*) FROM enriched_articles GROUP BY is_domestic;")
    for is_domestic, count in cur.fetchall():
        category = "Domestic" if is_domestic else "International"
        logger.info(f"  {category}: {count:,} ({count/total*100:.1f}%)")
    
    # Date range
    cur.execute("SELECT MIN(published_date), MAX(published_date) FROM enriched_articles;")
    min_date, max_date = cur.fetchone()
    logger.info(f"Date range: {min_date} to {max_date}")
    
    # Top emotions
    logger.info("\nTop 3 emotions:")
    cur.execute("""
        SELECT emotion_label, COUNT(*) as cnt
        FROM enriched_articles
        GROUP BY emotion_label
        ORDER BY cnt DESC
        LIMIT 3;
    """)
    for i, (emotion, count) in enumerate(cur.fetchall(), 1):
        logger.info(f"  {i}. {emotion}: {count:,}")
    
    # Top event types
    logger.info("\nTop 3 event types:")
    cur.execute("""
        SELECT acled_event_type, COUNT(*) as cnt
        FROM enriched_articles
        GROUP BY acled_event_type
        ORDER BY cnt DESC
        LIMIT 3;
    """)
    for i, (event_type, count) in enumerate(cur.fetchall(), 1):
        logger.info(f"  {i}. {event_type}: {count:,}")
    
    logger.info("=" * 60)


def main():
    """
    Main pipeline execution with validation and metrics
    """
    logger = init_logger("sentiment_pipeline")
    metrics = PerformanceMetrics()
    
    pipeline_start = time.time()
    
    logger.info("=" * 60)
    logger.info("SENTIMENT ANALYSIS PIPELINE - STARTING")
    logger.info(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    try:
        # Load configuration
        config_dict = load_config()
        config = PipelineConfig(config_dict)
        
        logger.info(f"\nConfiguration:")
        logger.info(f"  Raw DB: {config.raw_db}")
        logger.info(f"  Dedup DB: {config.dedup_db}")
        logger.info(f"  Articles CSV: {config.output_art}")
        logger.info(f"  Events CSV: {config.output_evt}")
        
        # Configuration with defaults
        similarity_threshold = config.similarity_treshold
        
        # Step 1: Load raw articles
        df, conn = load_raw_articles(config.raw_db, logger, metrics)
        
        try:
            validator = DataValidator(conn, logger)
            
            # Validate raw data
            logger.info("\n" + "=" * 60)
            logger.info("INITIAL DATA VALIDATION")
            logger.info("=" * 60)
            checks = validator.validate_raw_articles()
            validator.log_validation_results("Raw Articles", checks)
            
            # Initialize NLP models
            nlp_models = NLPModels(logger)
            location_detector = GermanLocationDetector(logger)
            
            # Step 2: Geographic detection
            df = geographic_detection(df, location_detector, nlp_models, logger, metrics)
            
            # Step 3: Cluster articles
            df = cluster_articles(df, similarity_threshold, logger, metrics)
            
            # Validate clustering
            checks = validator.validate_clustering()
            validator.log_validation_results("Article Clustering", checks)
            
            # Step 4: Sentiment analysis
            df_events = sentiment_analysis(df, nlp_models, logger, metrics)
            
            # Step 5: Event classification
            df_events = event_classification(df_events, nlp_models, logger, metrics)
            
            # Step 6: Topic modeling
            df_events = topic_modeling(df_events, logger, metrics)
            
            # Step 7: Merge and save
            merge_and_save(df, df_events, conn, config, logger, metrics)
            
            # Validate final enrichment
            checks = validator.validate_enrichment()
            validator.log_validation_results("Enriched Articles", checks)
            
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
        logger.exception("\n✗ PIPELINE FAILED")
        logger.error(f"Error: {str(e)}")
        raise


if __name__ == "__main__":
    main()