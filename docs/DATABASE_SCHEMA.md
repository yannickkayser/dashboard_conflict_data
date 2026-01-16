# Complete Database Schema Reference

## Database Overview

| Database | Purpose | Tables | Usage |
|----------|---------|--------|-------|
| `gnews_articles_from2023.db` | Raw article data | 1 | Input source |
| `deleted_dupgnews2023.db` | Processed articles | 3 | Article pipeline |
| `conflict_data.db` | Conflict data | 7+ | Conflict dataset |
| `matched_conflict.db` | Matched data | 5+ | Final output |

---

## Detailed Schema

### Database 1: `gnews_articles_from2023.db`

#### Table: `articles`
**Purpose:** Raw articles from GNews API (German language)

```sql
CREATE TABLE articles (
    id TEXT PRIMARY KEY,                 -- Article URL
    publishedAt DATETIME,               -- Publication timestamp
    title TEXT,                         -- Article title (German)
    description TEXT,                   -- Summary (German)
    content TEXT,                       -- Full content (German)
    url TEXT,                          -- Source URL
    source_name TEXT,                  -- News outlet name
    source_url TEXT                    -- News outlet website
);

-- Typical queries:
-- Count articles: SELECT COUNT(*) FROM articles;
-- By date: SELECT * FROM articles WHERE publishedAt >= '2025-12-21' ORDER BY publishedAt DESC;
-- By source: SELECT DISTINCT source_name, COUNT(*) FROM articles GROUP BY source_name;
```

**Sample Data:**
- id: `https://example.com/article-123`
- publishedAt: `2025-12-25 14:30:00`
- title: `Proteste in Berlin eskalieren`
- source_name: `Der Spiegel`

---

### Database 2: `deleted_dupgnews2023.db`

#### Table: `article_without_duplicates`
**Purpose:** Articles after SimHash deduplication (German)

```sql
CREATE TABLE article_without_duplicates (
    id TEXT PRIMARY KEY,               -- Article URL
    publishedAt DATETIME,              -- Publication timestamp
    title TEXT,                        -- Title (German)
    description TEXT,                 -- Description (German)
    url TEXT,                         -- Source URL
    source_name TEXT,                 -- News outlet
    source_url TEXT                   -- Source website
);

-- Comparison query:
-- SELECT 'original' as source, COUNT(*) FROM articles
-- UNION ALL
-- SELECT 'after_dedup', COUNT(*) FROM article_without_duplicates;
```

**Deduplication Details:**
- Algorithm: SimHash (16-bit fingerprints)
- Removes near-identical content
- Keeps best article per cluster (lowest centroid distance)
- Typical dedup rate: 20-40%

---

#### Table: `articles_eng`
**Purpose:** Deduplicated articles with translations and country classification

```sql
CREATE TABLE articles_eng (
    id TEXT PRIMARY KEY,                       -- Article URL
    publishedAt DATETIME,                      -- Publication date
    url TEXT,                                 -- Source URL
    source_name TEXT,                         -- News outlet
    source_url TEXT,                          -- Source website
    title_en TEXT,                            -- English translation
    description_en TEXT,                      -- English translation
    article_country TEXT,                     -- Detected country (ISO code)
    article_country_score INTEGER             -- Confidence score (0-100)
);

-- Most useful queries:
-- SELECT * FROM articles_eng WHERE article_country = 'SYR';
-- SELECT article_country, COUNT(*) FROM articles_eng GROUP BY article_country 
--   ORDER BY COUNT(*) DESC LIMIT 20;
-- SELECT * FROM articles_eng WHERE article_country_score < 50;
```

**Sample Data:**
- article_country: `SYR`, `COL`, `PSE`, `UKR`, `NA` (for unclassified)
- article_country_score: `100`, `95`, `45`, `0`
- High score = confidence in country classification

**Country Code Notes:**
- `NA` = Not Assigned (confidence too low)
- ISO 3166-1 alpha-3 codes used
- May include cross-border conflicts (uses primary country)

---

#### Table: `enriched_articles`
**Purpose:** Articles with NLP features (sentiment, topics, clusters)

```sql
CREATE TABLE enriched_articles (
    id TEXT PRIMARY KEY,                       -- Article URL
    is_domestic INTEGER,                       -- 1=domestic, 0=international
    detected_locations TEXT,                   -- Named entity locations
    emotion_label TEXT,                        -- Sentiment: positive, negative, neutral
    acled_event_type TEXT,                     -- ACLED classification
    narrative_topic_id INTEGER,                -- Topic model cluster
    article_cluster_id TEXT,                   -- Duplicate cluster ID
    is_duplicate INTEGER                       -- 1=duplicate, 0=unique
);

-- Analysis queries:
-- SELECT emotion_label, COUNT(*) FROM enriched_articles GROUP BY emotion_label;
-- SELECT is_domestic, COUNT(*) FROM enriched_articles GROUP BY is_domestic;
-- SELECT narrative_topic_id, COUNT(*) FROM enriched_articles WHERE narrative_topic_id IS NOT NULL 
--   GROUP BY narrative_topic_id ORDER BY COUNT(*) DESC LIMIT 10;

-- Find clusters of similar articles:
-- SELECT article_cluster_id, COUNT(*) as cluster_size FROM enriched_articles
--   WHERE is_duplicate = 1 GROUP BY article_cluster_id ORDER BY cluster_size DESC;
```

**Field Details:**
- `is_domestic`: Geographic scope (country-internal vs. international coverage)
- `emotion_label`: Output from emotion classifier (e.g., "angry", "fearful", "sad")
- `acled_event_type`: Predicted ACLED classification (Violence, Protest, Riot, etc.)
- `narrative_topic_id`: LDA topic model cluster (0-19 typically)
- `article_cluster_id`: Groups semantically similar/duplicate articles together

---

### Database 3: `conflict_data.db`

#### Table: `events` (Raw ACLED Data)
**Purpose:** Individual conflict events from ACLED database

```sql
CREATE TABLE events (
    event_id_cnty TEXT PRIMARY KEY,            -- Unique ACLED event ID
    event_date DATE,                           -- Event date (YYYY-MM-DD)
    year INTEGER,                              -- Year extracted
    time_precision INTEGER,                    -- Date precision (1=exact, 2=month, 3=year)
    event_type TEXT,                           -- ACLED type
    sub_event_type TEXT,                       -- ACLED subtype
    actor1 TEXT,                               -- Primary actor/group
    actor2 TEXT,                               -- Secondary actor
    assoc_actor_1 TEXT,                        -- Associated actors
    country TEXT,                              -- Country (full name)
    iso INTEGER,                               -- ISO numeric code
    region TEXT,                               -- Continent/region
    location TEXT,                             -- Location description
    latitude REAL,                             -- Coordinates
    longitude REAL,                            --
    fatalities INTEGER,                        -- Death toll
    source TEXT,                               -- Data source
    notes TEXT                                 -- Event notes
);

-- Key queries:
-- SELECT COUNT(*) FROM events;  -- Total events
-- SELECT COUNT(DISTINCT event_id_cnty) FROM events;  -- Unique events
-- SELECT SUM(fatalities) FROM events;  -- Total fatalities
-- SELECT country, COUNT(*) FROM events GROUP BY country ORDER BY COUNT(*) DESC;
-- SELECT * FROM events WHERE event_type LIKE '%Protest%' AND fatalities > 0;
-- SELECT * FROM events WHERE YEAR(event_date) = 2025 AND country = 'Syria';
```

**Event Types (Sample):**
- Protests
- Violence against civilians
- Riots
- Explosions/Remote violence
- Strategic developments
- Battles
- Armed clashes

---

#### Table: `unique_conflict`
**Purpose:** Aggregated conflicts from grouped events

```sql
CREATE TABLE unique_conflict (
    conflict_id TEXT PRIMARY KEY,              -- Unique conflict identifier
    n_events INTEGER,                          -- Number of events in conflict
    total_fatalities INTEGER                   -- Sum of fatalities
);

-- Queries:
-- SELECT * FROM unique_conflict WHERE total_fatalities > 100 
--   ORDER BY total_fatalities DESC LIMIT 20;
-- SELECT * FROM unique_conflict WHERE n_events > 50;
-- SELECT AVG(n_events) as avg_events_per_conflict FROM unique_conflict;
```

---

#### Table: `conflict_features`
**Purpose:** Enriched metadata for each conflict

```sql
CREATE TABLE conflict_features (
    conflict_id TEXT PRIMARY KEY,              -- FK to unique_conflict
    country TEXT,                              -- Affected country
    conflict_key TEXT,                         -- Human-readable conflict identifier
    actor1 TEXT,                               -- Primary actor (modal)
    start_date DATE,                           -- Conflict start
    end_date DATE,                             -- Conflict end (or latest event)
    duration_days INTEGER,                     -- Days from start to end
    event_type_mode TEXT                       -- Most common event type
);

-- Queries:
-- SELECT * FROM conflict_features WHERE start_date >= '2025-01-01';
-- SELECT country, COUNT(*) FROM conflict_features GROUP BY country 
--   ORDER BY COUNT(*) DESC LIMIT 20;
-- SELECT * FROM conflict_features WHERE duration_days > 365 
--   ORDER BY duration_days DESC;
-- SELECT conflict_key, actor1 FROM conflict_features WHERE country = 'Syria';
```

---

#### Table: `conflict_time`
**Purpose:** Temporal aggregations (month/year counts, trends)

```sql
CREATE TABLE conflict_time (
    conflict_id TEXT,                          -- FK to unique_conflict
    year_month TEXT,                           -- YYYY-MM format
    event_count INTEGER,                       -- Events in this period
    fatality_count INTEGER                     -- Deaths in this period
    -- Typically multi-row per conflict
);

-- Time series queries:
-- SELECT year_month, SUM(event_count) FROM conflict_time 
--   WHERE conflict_id = 'SOME_CONFLICT_ID'
--   GROUP BY year_month ORDER BY year_month;
-- SELECT year_month, SUM(fatality_count) FROM conflict_time 
--   GROUP BY year_month ORDER BY year_month;
```

---

#### Table: `conflict_country`
**Purpose:** Country-level aggregations of conflicts

```sql
CREATE TABLE conflict_country (
    country TEXT PRIMARY KEY,                  -- Country name
    total_conflicts INTEGER,                   -- Number of conflicts
    total_events INTEGER,                      -- All events combined
    total_fatalities INTEGER                   -- All deaths combined
);

-- Country ranking:
-- SELECT * FROM conflict_country ORDER BY total_fatalities DESC LIMIT 30;
-- SELECT * FROM conflict_country WHERE total_conflicts > 5;
```

---

### Database 4: `matched_conflict.db`

#### Table: `match_country_wide`
**Purpose:** Articles matched to countries (complete data)

```sql
CREATE TABLE match_country_wide (
    -- Article fields (prefixed: art_)
    art_id TEXT,                               -- Article URL
    art_publishedAt DATETIME,                  -- Article date
    art_title_en TEXT,                         -- English title
    art_description_en TEXT,                   -- English description
    art_url TEXT,                              -- Source URL
    art_source_name TEXT,                      -- News outlet
    art_source_url TEXT,                       -- News website
    
    -- Country fields (from articles_eng)
    article_country TEXT,                      -- Country code
    article_country_score INTEGER              -- Classification confidence
);

-- Match statistics:
-- SELECT COUNT(DISTINCT art_id) FROM match_country_wide;  -- Total articles
-- SELECT article_country, COUNT(*) FROM match_country_wide 
--   GROUP BY article_country ORDER BY COUNT(*) DESC;
-- SELECT article_country, article_country_score, COUNT(*) FROM match_country_wide
--   GROUP BY article_country, article_country_score;
```

---

#### Table: `match_country_slim`
**Purpose:** Articles matched to countries (minimal fields for fast access)

```sql
CREATE TABLE match_country_slim (
    art_id TEXT PRIMARY KEY,                   -- Article URL
    art_publishedAt DATETIME,                  -- Article date
    art_url TEXT,                              -- Source URL
    article_country TEXT                       -- Country code
);

-- Fast queries:
-- SELECT COUNT(*) FROM match_country_slim WHERE article_country = 'SYR';
-- SELECT * FROM match_country_slim WHERE article_country IN ('SYR', 'IRQ', 'YEM')
--   ORDER BY art_publishedAt DESC LIMIT 100;
```

---

#### Table: `match_conflict_wide`
**Purpose:** Articles matched to specific conflicts (complete data)

```sql
CREATE TABLE match_conflict_wide (
    -- Article fields
    art_id TEXT,
    art_publishedAt DATETIME,
    art_title_en TEXT,
    art_description_en TEXT,
    art_url TEXT,
    art_source_name TEXT,
    
    -- Conflict fields (prefixed: conf_)
    conf_conflict_id TEXT,                     -- Conflict ID
    conf_country TEXT,                         -- Conflict country
    conf_start_date DATE,                      -- Conflict start
    conf_end_date DATE,                        -- Conflict end
    conf_n_events INTEGER,                     -- Event count
    conf_total_fatalities INTEGER,             -- Fatalities
    conf_duration_days INTEGER,                -- Duration
    
    -- Matching metadata
    time_proximity_days INTEGER                -- Days between article & conflict
);

-- Detailed conflict coverage:
-- SELECT conf_conflict_id, COUNT(*) as article_count 
-- FROM match_conflict_wide GROUP BY conf_conflict_id 
-- ORDER BY article_count DESC LIMIT 30;

-- Articles near major conflicts:
-- SELECT * FROM match_conflict_wide 
-- WHERE conf_total_fatalities > 1000 
-- ORDER BY art_publishedAt DESC;

-- Temporal lag analysis:
-- SELECT time_proximity_days, COUNT(*) FROM match_conflict_wide
--   GROUP BY time_proximity_days ORDER BY time_proximity_days;
```

---

#### Table: `match_conflict_slim`
**Purpose:** Articles matched to conflicts (minimal fields)

```sql
CREATE TABLE match_conflict_slim (
    art_id TEXT PRIMARY KEY,                   -- Article
    conf_conflict_id TEXT,                     -- Conflict ID
    conf_n_events INTEGER,                     -- Event count
    conf_total_fatalities INTEGER,             -- Fatalities
    conf_country TEXT,                         -- Conflict country
    conf_start_date DATE,                      -- Conflict start
    conf_end_date DATE                         -- Conflict end
);

-- Fast conflict lookups:
-- SELECT * FROM match_conflict_slim WHERE conf_conflict_id = 'CONFLICT_ID';
-- SELECT COUNT(*) FROM match_conflict_slim WHERE conf_total_fatalities > 100;
```

---

## Data Dictionary: Key Fields

### Temporal Fields
- `publishedAt`, `event_date`: DateTime or Date (YYYY-MM-DD HH:MM:SS)
- `start_date`, `end_date`: Date format (YYYY-MM-DD)
- `year`: Integer (2023, 2024, 2025)
- `year_month`: String (2025-12)

### Categorical Fields
- `event_type`: ACLED classification (Protests, Violence, Battles, etc.)
- `emotion_label`: NLP output (positive, negative, neutral, angry, sad, fearful)
- `article_country`: ISO 3166-1 alpha-3 codes (SYR, PSE, UKR, COL, etc.)
- `is_domestic`: Binary (0 or 1)
- `is_duplicate`: Binary (0 or 1)

### Numeric Fields
- `fatalities`, `total_fatalities`: Integer (≥0)
- `article_country_score`: Integer (0-100)
- `n_events`, `total_events`: Integer count
- `duration_days`: Integer (≥0)
- `latitude`, `longitude`: Float (-180 to 180)

### Text Fields
- URLs: Stored as unique identifiers
- Titles/Descriptions: Full text (English in enriched tables)
- `detected_locations`: Comma-separated NER output
- `conflict_key`: Human-readable identifier

---

## Common SQL Patterns

### 1. Coverage Analysis
```sql
-- Countries with highest article coverage
SELECT article_country, COUNT(*) as article_count
FROM articles_eng
WHERE article_country != 'NA'
GROUP BY article_country
ORDER BY article_count DESC
LIMIT 20;
```

### 2. Sentiment by Country
```sql
-- Emotional tone of conflict coverage by country
SELECT ae.article_country, ea.emotion_label, COUNT(*) as count
FROM articles_eng ae
JOIN enriched_articles ea ON ae.id = ea.id
GROUP BY ae.article_country, ea.emotion_label
ORDER BY ae.article_country, COUNT(*) DESC;
```

### 3. Conflict-Article Lag
```sql
-- How quickly do news articles appear after conflict events?
SELECT 
    time_proximity_days,
    COUNT(*) as article_count,
    AVG(conf_total_fatalities) as avg_fatalities
FROM match_conflict_wide
WHERE time_proximity_days BETWEEN -30 AND 30
GROUP BY time_proximity_days
ORDER BY time_proximity_days;
```

### 4. High-Fatality Conflicts
```sql
-- Articles about deadliest conflicts
SELECT DISTINCT
    conf_conflict_id,
    conf_country,
    conf_total_fatalities,
    COUNT(*) as article_count
FROM match_conflict_wide
WHERE conf_total_fatalities > 100
GROUP BY conf_conflict_id
ORDER BY conf_total_fatalities DESC;
```

### 5. Data Quality Checks
```sql
-- Check for missing values
SELECT 
    (SELECT COUNT(*) FROM articles_eng WHERE title_en IS NULL) as missing_titles,
    (SELECT COUNT(*) FROM articles_eng WHERE article_country IS NULL) as missing_country,
    (SELECT COUNT(*) FROM enriched_articles WHERE emotion_label IS NULL) as missing_emotion;
```

---

## Index Recommendations

For better query performance, consider adding:

```sql
-- For date-range queries
CREATE INDEX idx_articles_eng_publishedAt ON articles_eng(publishedAt);
CREATE INDEX idx_events_event_date ON events(event_date);

-- For country lookups
CREATE INDEX idx_articles_eng_country ON articles_eng(article_country);
CREATE INDEX idx_conflict_features_country ON conflict_features(country);

-- For matching queries
CREATE INDEX idx_match_conflict_wide_conflict_id ON match_conflict_wide(conf_conflict_id);
CREATE INDEX idx_match_conflict_wide_article_id ON match_conflict_wide(art_id);

-- For source tracking
CREATE INDEX idx_articles_eng_source ON articles_eng(source_name);

-- For deduplication lookups
CREATE INDEX idx_enriched_articles_cluster ON enriched_articles(article_cluster_id);
```

---

## Backup & Maintenance

**Database Backup:**
```bash
# Backup all databases
mkdir -p backups
for db in data/*.db; do
    cp "$db" "backups/$(basename $db).backup.$(date +%Y%m%d_%H%M%S)"
done
```

**Integrity Check:**
```sql
-- Run in SQLite
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

**Space Analysis:**
```bash
ls -lh data/*.db
sqlite3 data/matched_conflict.db "SELECT page_count * page_size as bytes FROM pragma_page_count(), pragma_page_size();"
```
