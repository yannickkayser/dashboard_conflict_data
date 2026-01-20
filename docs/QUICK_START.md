# AI Conflict Data Pipeline - Quick Start Checklist

## Pre-Execution Setup ✓

- [ ] **Install dependencies**
  ```bash
  pip install -r requirements.txt
  # Required: sqlite3, transformers, torch, simhash, requests, scikit-learn, ....
  ```

- [ ] **Create configuration file (`config.yaml`)**
  ```yaml
  gnews:
    api_key: "YOUR_GNEWS_API_KEY"
  acled:
    email: "your_email@example.com"
    password: "your_password"
  ```

- [ ] **Create data directory**
  ```bash
  mkdir -p data logs raw src 
  ```

- [ ] **Verify all pipeline scripts exist**
  ```bash
  ls -la *.py  # Should show pipelineGNEWS.py, pipelineACLED.py, etc.
  ```

---

## Execution Steps (In Order)

### 1 Fetch & Process GNews Articles
```bash
python pipelineGNEWS.py
```
**Output:** `data/deleted_dupgnews2023.db::articles_eng` (1000-5000 articles)
**Time:** 20-40 minutes (depending on date range)
**What it does:**
- Fetches German news articles from GNews API
- Removes duplicates using SimHash algorithm
- Translates to English using M2M-100
- Classifies countries using TF-IDF

**Check success:**
```bash
sqlite3 data/deleted_dupgnews2023.db "SELECT COUNT(*) FROM articles_eng;"
```

---

### 2 Fetch & Process ACLED Conflict Data
```bash
python pipelineACLED.py
```
**Output:** `data/conflict_data.db` (7 tables with conflict events)
**Time:** 5-15 minutes
**What it does:**
- Fetches conflict events from ACLED API
- Aggregates events into unique conflicts
- Extracts conflict features and metadata

**Check success:**
```bash
sqlite3 data/conflict_data.db "SELECT COUNT(*) FROM events;"
```

---

### 3 Sentiment & NLP Enrichment
```bash
python pipelineSentimentAnalysis.py
```
**Output:** `data/processed_conflict_articles.csv`
**Time:** 30-60 minutes (GPU: 10-20 minutes)
**What it does:**
- Analyzes sentiment/emotion
- Detects domestic vs. international content
- Extracts country
- Classifies article event types
- Performs topic modeling (LDA)
- Detects duplicate clusters


---

### 4 Country-Level Matching
```bash
python pipelinematchingCountry.py
```
**Output:** `data/matched_conflict.db::match_country_*`
**Time:** 5-10 minutes
**What it does:**
- Matches articles to countries
- Creates wide (all fields) and slim (minimal) versions
- Builds coverage indices

**Check success:**
```bash
sqlite3 data/matched_conflict.db "SELECT COUNT(*) FROM match_country_wide;"
```


---

## Key Outputs & Their Purpose

| Output | Location | Purpose | Query Example |
|--------|----------|---------|---|
| Raw Articles | `gnews_articles_from2023.db::articles` | German news from API | `SELECT COUNT(*) FROM articles;` |
| Deduplicated Articles | `deleted_dupgnews2023.db::article_without_duplicates` | After SimHash dedup | `SELECT COUNT(*) FROM article_without_duplicates;` |
| English Articles | `deleted_dupgnews2023.db::articles_eng` | Translated + classified | `SELECT COUNT(*) FROM articles_eng WHERE article_country != 'NA';` |
| NLP Features | `processed_conflict_articles.csv` | Sentiment, topics, clustering | `` |
| Conflict Data | `conflict_data.db` | ACLED events (7 tables) | `SELECT COUNT(*) FROM events;` |
| Country Matches | `matched_conflict.db::match_country_*` | Articles → Countries | `SELECT article_country, COUNT(*) FROM match_country_wide GROUP BY article_country;` |


---

## Monitoring Pipeline Execution

### Check Current Progress
```bash
# Watch logs in real-time
tail -f logs/pipeline_execution_*.log

# Count processed articles
watch "sqlite3 data/deleted_dupgnews2023.db 'SELECT COUNT(*) FROM articles_eng;'"

# Monitor disk usage
watch "du -sh data/"

# Check memory/CPU
top -p $(pgrep -f pipelineGNEWS.py)
```

### Expected Execution Times

| Stage | CPU Only | GPU | Total Data |
|-------|----------|-----|-----------|
| GNews (1-10 days) | 20-40 min | 15-25 min | 1-10 MB |
| ACLED (all data) | 5-15 min | 5-15 min | 50-100 MB |
| Sentiment (slow) | 30-60 min | 10-20 min | depends on articles |
| Country match | 5-10 min | 5-10 min | 10-20 MB |
| Conflict match | 10-20 min | 10-20 min | 20-30 MB |
| **TOTAL** | **70-145 min** | **40-80 min** | **100-160 MB** |

---

## Documentation Index

| Document | Purpose | When to Use |
|----------|---------|------------|
| **PIPELINE_GUIDE.md** | Complete architecture & design | Understanding overall system |
| **DATABASE_SCHEMA.md** | Table structures & field definitions | Writing custom queries |
| **TROUBLESHOOTING.md** | Common issues & solutions | Debugging problems |
| **AUTOMATION.md** | Automated execution script | Running full pipeline |


---



