# AI Conflict Data Pipeline - Quick Start Checklist

## Pre-Execution Setup ✓

- [ ] **Install dependencies**
  ```bash
  pip install -r requirements.txt
  # Required: sqlite3, transformers, torch, simhash, requests, scikit-learn
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
  mkdir -p data logs reports
  ```

- [ ] **Verify all pipeline scripts exist**
  ```bash
  ls -la *.py  # Should show pipelineGNEWS.py, pipelineACLED.py, etc.
  ```

---

## Execution Steps (In Order)

### 1️⃣ Fetch & Process GNews Articles
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

### 2️⃣ Fetch & Process ACLED Conflict Data
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

### 3️⃣ Sentiment & NLP Enrichment
```bash
python pipelineSentimentAnalysis.py
```
**Output:** `data/deleted_dupgnews2023.db::enriched_articles`
**Time:** 30-60 minutes (GPU: 10-20 minutes)
**What it does:**
- Analyzes sentiment/emotion
- Detects domestic vs. international content
- Classifies article event types
- Performs topic modeling (LDA)
- Detects duplicate clusters

**Check success:**
```bash
sqlite3 data/deleted_dupgnews2023.db "SELECT COUNT(*) FROM enriched_articles;"
```

---

### 4️⃣ Country-Level Matching
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

### 5️⃣ Conflict-Level Matching
```bash
python pipelineMatching.py
```
**Output:** `data/matched_conflict.db::match_conflict_*`
**Time:** 10-20 minutes
**What it does:**
- Matches articles to specific conflicts
- Uses temporal proximity (±N days)
- Creates wide and slim output tables

**Check success:**
```bash
sqlite3 data/matched_conflict.db "SELECT COUNT(*) FROM match_conflict_wide;"
```

---

## Automated Execution

**Run all stages with orchestrator:**
```bash
python pipeline_orchestrator.py
```

**Output:**
- Execution log: `logs/pipeline_execution_YYYYMMDD_HHMMSS.log`
- Results report: `reports/pipeline_results_YYYYMMDD_HHMMSS.json`

---

## Post-Execution Validation ✓

### Database Integrity
```bash
sqlite3 data/matched_conflict.db "PRAGMA integrity_check;"
```
Expected: `ok`

### Table Completeness
```bash
sqlite3 data/deleted_dupgnews2023.db << EOF
SELECT 'articles' as table_name, COUNT(*) as records FROM articles
UNION ALL
SELECT 'article_without_duplicates', COUNT(*) FROM article_without_duplicates
UNION ALL
SELECT 'articles_eng', COUNT(*) FROM articles_eng
UNION ALL
SELECT 'enriched_articles', COUNT(*) FROM enriched_articles;
EOF
```

### Match Coverage
```bash
sqlite3 data/matched_conflict.db << EOF
SELECT 
  (SELECT COUNT(*) FROM match_country_wide) as country_matches,
  (SELECT COUNT(*) FROM match_conflict_wide) as conflict_matches,
  (SELECT COUNT(DISTINCT conf_conflict_id) FROM match_conflict_wide) as unique_conflicts;
EOF
```

---

## Key Outputs & Their Purpose

| Output | Location | Purpose | Query Example |
|--------|----------|---------|---|
| Raw Articles | `gnews_articles_from2023.db::articles` | German news from API | `SELECT COUNT(*) FROM articles;` |
| Deduplicated Articles | `deleted_dupgnews2023.db::article_without_duplicates` | After SimHash dedup | `SELECT COUNT(*) FROM article_without_duplicates;` |
| English Articles | `deleted_dupgnews2023.db::articles_eng` | Translated + classified | `SELECT COUNT(*) FROM articles_eng WHERE article_country != 'NA';` |
| NLP Features | `deleted_dupgnews2023.db::enriched_articles` | Sentiment, topics, clustering | `SELECT emotion_label, COUNT(*) FROM enriched_articles GROUP BY emotion_label;` |
| Conflict Data | `conflict_data.db` | ACLED events (7 tables) | `SELECT COUNT(*) FROM events;` |
| Country Matches | `matched_conflict.db::match_country_*` | Articles → Countries | `SELECT article_country, COUNT(*) FROM match_country_wide GROUP BY article_country;` |
| Conflict Matches | `matched_conflict.db::match_conflict_*` | Articles → Conflicts | `SELECT conf_conflict_id, COUNT(*) FROM match_conflict_wide GROUP BY conf_conflict_id;` |

---

## Sample Queries After Execution

### 1. Countries with Most Articles
```sql
SELECT article_country, COUNT(*) as article_count
FROM matched_conflict.db::match_country_wide
GROUP BY article_country
ORDER BY article_count DESC
LIMIT 20;
```

### 2. Sentiment Distribution by Country
```sql
SELECT 
  ae.article_country,
  ea.emotion_label,
  COUNT(*) as count
FROM articles_eng ae
JOIN enriched_articles ea ON ae.id = ea.id
WHERE ae.article_country IN ('SYR', 'PSE', 'UKR')
GROUP BY ae.article_country, ea.emotion_label
ORDER BY ae.article_country, COUNT(*) DESC;
```

### 3. Conflicts with Most Media Coverage
```sql
SELECT 
  conf_conflict_id,
  conf_country,
  COUNT(*) as article_count,
  conf_total_fatalities,
  conf_n_events
FROM match_conflict_wide
GROUP BY conf_conflict_id
ORDER BY article_count DESC
LIMIT 20;
```

### 4. Articles About Major Conflicts (>100 fatalities)
```sql
SELECT 
  art_id,
  art_publishedAt,
  art_title_en,
  conf_conflict_id,
  conf_total_fatalities
FROM match_conflict_wide
WHERE conf_total_fatalities > 100
ORDER BY art_publishedAt DESC
LIMIT 50;
```

### 5. Coverage Lag (Time from conflict to article)
```sql
SELECT 
  time_proximity_days,
  COUNT(*) as article_count,
  AVG(conf_total_fatalities) as avg_fatalities
FROM match_conflict_wide
WHERE time_proximity_days BETWEEN -30 AND 30
GROUP BY time_proximity_days
ORDER BY time_proximity_days;
```

---

## Troubleshooting Quick Links

| Problem | Solution File | Section |
|---------|---------------|---------|
| API key error | TROUBLESHOOTING.md | 1. API Connection Issues |
| Database locked | TROUBLESHOOTING.md | 2. Database Issues |
| Out of memory | TROUBLESHOOTING.md | 3. Translation Issues |
| Too slow | TROUBLESHOOTING.md | Performance Tuning |
| High duplicate rate | TROUBLESHOOTING.md | 5. Deduplication Issues |
| Low country match rate | TROUBLESHOOTING.md | 6. Country Classification |

---

## Configuration Adjustments

### For Faster Execution (GPU available)
```yaml
processing:
  device: "cuda"
  translation_batch_size: 256  # Increase for GPU
  process_batch_size: 500
```

### For Lower Resource Usage (CPU only)
```yaml
processing:
  device: "cpu"
  translation_batch_size: 16
  process_batch_size: 50
  
# Optional: Skip slow NLP stages
skip_topic_modeling: true
skip_emotion_analysis: false
```

### For Production (Parallel Processing)
```yaml
parallel_stages: true  # Run independent stages in parallel
num_workers: 4
batch_size: 1000
create_indices: true  # Speed up queries
enable_caching: true
```

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

## Data Backup

### Before Running Pipeline
```bash
# Backup existing data (if any)
mkdir -p backups/pre-execution-$(date +%Y%m%d_%H%M%S)
cp -r data/*.db backups/pre-execution-*/
```

### During Execution
```bash
# Create checkpoints
# (Automated by pipelineMatching.py if enabled)
```

### After Successful Execution
```bash
# Archive results
tar -czf results-$(date +%Y%m%d).tar.gz data/ logs/
```

---

## Next Steps

1. ✅ Execute pipeline (see steps 1-5 above)
2. 📊 Analyze results using sample queries
3. 📈 Build visualizations from `match_conflict_wide`/`match_country_wide`
4. 📝 Generate research insights (coverage bias, sentiment framing, etc.)
5. 🔄 Schedule automatic pipeline updates (daily/weekly)

---

## Documentation Index

| Document | Purpose | When to Use |
|----------|---------|------------|
| **PIPELINE_GUIDE.md** | Complete architecture & design | Understanding overall system |
| **DATABASE_SCHEMA.md** | Table structures & field definitions | Writing custom queries |
| **TROUBLESHOOTING.md** | Common issues & solutions | Debugging problems |
| **pipeline_orchestrator.py** | Automated execution script | Running full pipeline |
| **This checklist** | Quick start & status tracking | Daily operations |

---

## Support

### Getting Help
1. Check **TROUBLESHOOTING.md** for your error
2. Review logs: `logs/pipeline_execution_*.log`
3. Verify database: `sqlite3 data/*.db ".schema"`
4. Test APIs manually with curl
5. Check dependencies: `pip list`

### Reporting Issues
Include:
- Full error message (last 50 lines of log)
- Hardware/OS info
- Which stage failed
- Configuration settings

---

## Research Applications

Your pipeline enables:

### 📊 **Coverage Analysis**
- Which countries get media attention?
- How does coverage differ between Global South and Global North?
- Are conflicts proportionally covered based on fatalities?

### 💬 **Sentiment Analysis**
- How are different actors framed emotionally?
- Does sentiment correlate with conflict severity?
- Language-specific framing differences?

### ⏱️ **Temporal Analysis**
- How fast do news outlets respond to conflicts?
- Is there a reporting lag for non-Western conflicts?
- How does seasonal events (holidays) affect coverage?

### 🔗 **Conflict-Article Network**
- Which conflicts get the most articles?
- Do major conflicts get disproportionate coverage?
- How are conflicts clustered by media narratives?

### 🌍 **Global South Focus (For Your Research)**
- Labor-related terminology in conflict articles
- Supply chain mentions in conflict contexts
- BPO/contractor involvement in conflict regions
- Economic exploitation narratives

---

## Version & Changelog

- **Pipeline Version:** 1.0
- **Last Updated:** 2026-01-16
- **Python Version:** 3.8+
- **Database:** SQLite 3.30+

### Key Components
- GNews API Integration
- ACLED API Integration
- SimHash Deduplication
- Helsinki-NLP Translation (M2M-100)
- Hugging Face Transformers (Sentiment/NER)
- LDA Topic Modeling
- Entity Matching with TF-IDF

---

**Ready to execute? Start with:**
```bash
python 
```

Monitor with:
```bash
tail -f logs/pipeline_execution_*.log
```

Verify with:
```bash
sqlite3 data/matched_conflict.db "SELECT COUNT(*) FROM match_conflict_wide;"
```

Happy researching! 🚀
