# AI Conflict Data Pipeline - Comprehensive Guide

## Overview

Your system implements a sophisticated multi-stage data pipeline for extracting, processing, and matching conflict-related news articles to structured conflict datasets. The pipeline integrates news data (GNews) with conflict data (ACLED) through NLP processing and entity matching.

---

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         COMPLETE DATA FLOW                                  │
└─────────────────────────────────────────────────────────────────────────────┘

1. DATA ACQUISITION
   ├── GNews API (pipelineGNEWS.py)
   │   ├── Fetch articles (German language)
   │   ├── Query: Protest, Demonstration, Violence, Conflict, Corruption
   │   └── Time range: Dec 21, 2025 - Dec 31, 2025
   │
   └── ACLED API (pipelineACLED.py)
       ├── Fetch conflict events worldwide
       ├── Standardized event classification
       └── Geolocated conflict data

2. ARTICLE PROCESSING (pipelineGNEWS.py)
   ├── Deduplication (SimHash algorithm)
   ├── Translation (German → English)
   └── Country Classification (TF-IDF matching)

3. NLP ENRICHMENT (pipelineSentimentAnalysis.py)
   ├── Geographic Detection (domestic vs. international)
   ├── Sentiment/Emotion Analysis
   ├── Event Type Classification
   ├── Topic Modeling (LDA)
   └── Duplicate Detection & Clustering

4. CONFLICT DATA PROCESSING (pipelineACLED.py)
   ├── Event aggregation
   ├── Unique conflict identification
   ├── Feature extraction
   ├── Temporal indexing
   └── Country-level aggregation

5. COUNTRY MATCHING (pipelinematchingCountry.py)
   ├── Match articles to countries
   ├── Build coverage indices
   └── Country-level aggregation

6. OUTPUT DATABASE
   └── matched_country.db (Article-Country mappings)
```

---

## Database Structure

### Core Databases

#### 1. `gnews_articles_from2023.db`
**Purpose:** Raw article data from GNews API

**Table: `articles`**
| Field | Type | Description |
|-------|------|-------------|
| id | String (PK) | Article URL |
| publishedAt | DateTime | Publication timestamp |
| title | String | German title |
| description | String | German description |
| content | String | Article body text |
| url | String | Source URL |
| source_name | String | News outlet name |
| source_url | String | Source website |

---

#### 2. `deleted_dupgnews2023.db`
**Purpose:** Processed article data with deduplication and enrichment

**Table: `article_without_duplicates`**
- Raw articles after SimHash deduplication
- Removes near-duplicate content
- Preserves: id, publishedAt, title, description, url, source_name, source_url

**Table: `articles_eng`**
- Articles with English translations and country classification
- Key enrichments:
  - `title_en`: Translated title (German → English)
  - `description_en`: Translated description
  - `article_country`: Detected country (TF-IDF classifier)
  - `article_country_score`: Confidence score (0-100)

**Table: `enriched_articles`** (Added by Sentiment Analysis Pipeline)
- NLP-enriched articles
- Fields:
  - `is_domestic`: Boolean (1 = domestic, 0 = international)
  - `detected_locations`: Named entities found
  - `emotion_label`: Sentiment classification
  - `acled_event_type`: Predicted ACLED event type
  - `narrative_topic_id`: Topic model cluster ID
  - `article_cluster_id`: Duplicate cluster ID
  - `is_duplicate`: Boolean (True if near-duplicate)

---

#### 3. `conflict_data.db`
**Purpose:** Structured conflict data from ACLED

**Table: `events`** (Raw ACLED events)
| Field | Description |
|-------|-------------|
| event_id_cnty (PK) | Unique event identifier |
| event_date | Date of occurrence |
| event_type | ACLED classification (e.g., Violence against civilians, Protests) |
| sub_event_type | Detailed event classification |
| actor1, actor2 | Involved parties/groups |
| country | Country code or name |
| location | Geographic location |
| latitude, longitude | Coordinates |
| fatalities | Death toll |
| source | Data source |

**Table: `unique_conflict`** (Aggregated conflicts)
- `conflict_id` (PK): Unique conflict identifier
- `n_events`: Number of events in this conflict
- `total_fatalities`: Aggregated fatality count

**Table: `conflict_features`** (Conflict metadata)
- `conflict_id` (FK)
- `conflict_key`: Unique identifier
- `country`: Affected country
- `actor1`: Primary actor (modal)
- `start_date`, `end_date`: Temporal bounds
- `duration_days`: Conflict duration
- `event_type_mode`: Most common event type

**Table: `conflict_time`**
- Temporal aggregations by conflict

**Table: `conflict_country`**
- Country-level conflict aggregations

---

#### 4. `matched_conflict.db`
**Purpose:** Matched articles to conflicts and countries

**Table: `match_country_wide`**
- Complete article-country matches
- Combines article fields (`art_*`) with country data

**Table: `match_country_slim`**
- Minimal fields: article ID, publishedAt, URL, article_country
- Fast query access

---

## Pipeline Execution Steps

### Step 1: Fetch GNews Data
```bash
python pipelineGNEWS.py
```

**Process:**
1. Calls `fetch_GNEWS.py` → Retrieves articles from GNews API
2. Stores raw articles in `gnews_articles_from2023.db`
3. Validation: Checks for missing URLs, titles, and duplicates
4. Performance logging: Tracks fetch time and article count

**Configuration:**
- Language: German (de)
- Country: Germany (de)
- Keywords: Protest, Demonstration, Violence, Conflict, Corruption, etc.
- Date range: 2025-12-21 to 2025-12-31
- Batch size: 200 articles

---

### Step 2: Deduplication
**Integrated in:** `pipelineGNEWS.py`

**Algorithm:** SimHash
- Normalizes URLs and text content
- Creates 16-bit SimHash fingerprints
- Groups similar articles using locality-sensitive hashing
- Keeps best article per cluster (lower hash distance to group centroid)

**Output:** `deleted_dupgnews2023.db::article_without_duplicates`

**Metrics Tracked:**
- Total articles before deduplication
- Duplicate URLs detected
- Percent of duplicates removed

---

### Step 3: Translation and Country Classification
**Integrated in:** `pipelineGNEWS.py`

**Translation:**
- Model: `Helsinki-NLP/Opus-MT-de-en`
- Translates German titles/descriptions to English
- Batch processing (128 articles per batch)

**Country Classification:**
- Method: TF-IDF matching against ACLED conflict countries
- Compares article content to country-specific vocabulary
- Outputs: Country code + confidence score (0-100)

**Output:** `deleted_dupgnews2023.db::articles_eng`

---

### Step 4: Sentiment and NLP Analysis
```bash
python pipelineSentimentAnalysis.py
```

**Components:**
1. **Geographic Detection:** Classifies articles as domestic vs. international
2. **Sentiment Analysis:** Emotion classification (positive, negative, neutral)
3. **Event Type Classification:** Maps article content to ACLED event types
4. **Topic Modeling:** LDA topic extraction
5. **Duplicate Detection:** Clustering and deduplication of semantically similar articles

**Output:** `deleted_dupgnews2023.db::enriched_articles`

**Key Features Added:**
- `is_domestic`: Identifies articles about domestic conflicts
- `emotion_label`: Sentiment classification
- `acled_event_type`: Predicted conflict event type
- `narrative_topic_id`: Topic cluster
- `article_cluster_id`: Duplicate cluster membership

---

### Step 5: Fetch and Process ACLED Data
```bash
python pipelineACLED.py
```

**Process:**
1. Fetches conflict events from ACLED API
2. Creates event-to-conflict mappings
3. Aggregates events into unique conflicts
4. Builds conflict feature tables
5. Creates temporal and country-level indices

**Aggregations:**
- Event count per conflict
- Total fatalities
- Date range (start to end)
- Primary actors
- Event types
- Geographic coverage

**Output:** `conflict_data.db` (7 tables)

---

### Step 6: Country-Level Matching
```bash
python pipelinematchingCountry.py
```

**Process:**
1. Matches articles to countries using `article_country` field
2. Joins article metadata with country context
3. Builds wide (all fields) and slim (minimal) versions
4. Creates coverage indices for fast query access

**Output Tables:**
- `matched_conflict.db::match_country_wide` (complete data)
- `matched_conflict.db::match_country_slim` (minimal fields)
- `matched_conflict.db::coverage_country` (coverage indices)


---

## Quick Start Guide

### 1. Environment Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Required packages (inferred from imports):
# - sqlite3
# - transformers (M2M-100 translation, sentiment models)
# - torch
# - simhash
# - requests (for APIs)
# - python-dateutil
# - scikit-learn (TF-IDF)
```

### 2. Configuration

**Create `config.yaml`:**
```yaml
gnews:
  api_key: "your_gnews_api_key"
  base_url: "https://gnews.io/api/v4/search"

acled:
  base_url: "https://api.acleddata.com"
  email: "your_email"
  password: "your_password"

database:
  data_dir: "./data"

processing:
  translation_batch_size: 128
  process_batch_size: 200
  device: "cuda"  # or "cpu"
```

### 3. Execution Order

```bash
# Stage 1: Fetch and process GNews
python pipelineGNEWS.py

# Stage 2: Fetch and process ACLED
python pipelineACLED.py

# Stage 3: Sentiment analysis enrichment
python pipelineSentimentAnalysis.py

# Stage 4: Country-level matching
python pipelinematchingCountry.py

```

### 4. Monitoring

Each pipeline outputs:
- **Performance metrics** (time per stage)
- **Data validation reports** (quality checks)
- **Summary statistics** (counts, error rates)

Example output:
```
============================================================
PERFORMANCE METRICS
============================================================
Fetch articles............................... 45.23s (23.5%)
Deduplication............................... 78.92s (41.2%)
Translation................................. 35.67s (18.5%)
Country classification....................... 32.18s (16.8%)
============================================================
TOTAL TIME................................. 192.00s
============================================================
```

---

## Data Flow Diagram: Article → Conflict Match

```
GNews API Article
├── [Raw fields: URL, title, description, content, source, date]
│
├─→ DEDUPLICATION (SimHash)
│   └─→ article_without_duplicates
│
├─→ TRANSLATION (M2M-100)
│   └─→ articles_eng (+ title_en, description_en)
│
├─→ COUNTRY CLASSIFICATION (TF-IDF)
│   └─→ articles_eng (+ article_country, article_country_score)
│
├─→ SENTIMENT ANALYSIS (Emotion classifier)
│   └─→ enriched_articles (+ emotion_label)
│
├─→ GEOGRAPHIC DETECTION (NLP)
│   └─→ enriched_articles (+ is_domestic, detected_locations)
│
├─→ EVENT TYPE CLASSIFICATION (ACLED mapper)
│   └─→ enriched_articles (+ acled_event_type)
│
├─→ TOPIC MODELING (LDA)
│   └─→ enriched_articles (+ narrative_topic_id)
│
└─→ COUNTRY MATCHING
    └─→ match_country_wide
        └─→ match_country_slim

```

---

## Key Metrics & Validation

### Data Validation Points

**After Article Fetch:**
- Total articles fetched
- Missing URLs (should be 0)
- Missing titles (should be 0)
- Duplicate URLs (logged as warnings)

**After Deduplication:**
- Articles removed (% of originals)
- Remaining duplicate URLs (should be 0)
- Quality: More than 60% removal indicates low article diversity

**After Translation:**
- Missing translations (should be 0)
- Missing country assignments (warn if > 10%)
- High-confidence classifications (100% score count)

**After Conflict Processing:**
- Unique conflicts identified
- Events per conflict (mean, median)
- Coverage by country
- Temporal span (min to max dates)

---

## Common Queries

### Get matched articles for a specific country:
```sql
SELECT art_id, art_publishedAt, art_title_en, art_url
FROM match_country_wide
WHERE article_country = 'Nigeria'
ORDER BY art_publishedAt DESC;
```

### Get conflicts with most articles:
```sql
SELECT conf_conflict_id, COUNT(*) as article_count, 
       conf_country, conf_n_events, conf_total_fatalities
FROM match_conflict_wide
GROUP BY conf_conflict_id
ORDER BY article_count DESC
LIMIT 20;
```

### Get articles for conflicts in a date range:
```sql
SELECT DISTINCT art_id, art_publishedAt, art_title_en,
       conf_conflict_id, conf_start_date, conf_end_date
FROM match_conflict_wide
WHERE conf_start_date >= '2025-12-21'
  AND conf_end_date <= '2025-12-31'
ORDER BY art_publishedAt DESC;
```

### Get sentiment distribution for articles in specific country:
```sql
SELECT emotion_label, COUNT(*) as count
FROM enriched_articles ea
JOIN articles_eng ae ON ea.id = ae.id
WHERE ae.article_country = 'Syria'
GROUP BY emotion_label;
```

---

## Performance Optimization Tips

1. **Batch Processing:** Increase batch sizes for GPU availability
2. **Indexing:** Add indices on frequently queried fields (country, date, conflict_id)
3. **Slim Tables:** Use `*_slim` tables for faster queries on simple lookups
4. **Caching:** Cache TF-IDF models and translation models between runs
5. **Parallel Execution:** Run Stage 2 (ACLED) in parallel with Stage 1 (GNews) if independent data sources

---

## Troubleshooting

### Issue: Translation Model Download Slow
**Solution:** Pre-download models or use GPU acceleration

### Issue: High Deduplication Rate (>80%)
**Cause:** Possibly fetching redundant content from aggregator sites
**Solution:** Review query terms or increase date range for diversity

### Issue: Low Country Match Rate (<30%)
**Cause:** Articles may not mention country explicitly
**Solution:** Enhance with named entity recognition or manual review

### Issue: Memory Errors During Sentiment Analysis
**Solution:** Reduce batch size, use CPU instead of GPU, or process in smaller date ranges

---

## Schema Relationships

```
gnews_articles_from2023.db::articles
    ↓ (deduplicate)
deleted_dupgnews2023.db::article_without_duplicates
    ↓ (translate + classify country)
deleted_dupgnews2023.db::articles_eng
    ↓ (enrich with NLP)
deleted_dupgnews2023.db::enriched_articles
    ↓
    └─→ (country matching) → matched_conflict.db::match_country_wide
                          ↓
                          matched_conflict.db::match_country_slim


conflict_data.db::events
    ↓ (aggregate)
conflict_data.db::unique_conflict
    ├─→ conflict_data.db::conflict_features
    ├─→ conflict_data.db::conflict_time
    └─→ conflict_data.db::conflict_country
        ↓
        (used in matching)
        ↓
    matched_conflict.db
```

---
