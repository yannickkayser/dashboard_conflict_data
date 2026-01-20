# Pipeline Troubleshooting & Optimization Guide

## Common Issues & Solutions

### 1. API Connection Issues

#### Problem: "GNews API Key Invalid"
```
Error: 401 Unauthorized from GNews API
```

**Solutions:**
- Verify API key in `config.yaml` is correct
- Check API key hasn't expired
- Ensure GNews account is active
- Test API key directly:
  ```bash
  curl "https://gnews.io/api/v4/search?q=test&token=YOUR_KEY"
  ```

#### Problem: "ACLED API Rate Limit Exceeded"
```
Error: 429 Too Many Requests
```

**Solutions:**
- Implement backoff/retry logic:
  ```python
  import time
  for attempt in range(3):
      try:
          response = fetch_acled_data()
          break
      except RateLimitError:
          wait_time = 2 ** attempt  # Exponential backoff
          print(f"Rate limited. Waiting {wait_time}s...")
          time.sleep(wait_time)
  ```
- Reduce batch size in pipeline configuration
- Spread requests over longer time period
- Contact ACLED for higher rate limit quota

---

### 2. Database Issues

#### Problem: "Database is Locked"
```
sqlite3.OperationalError: database is locked
```

**Causes:**
- Multiple processes writing to same database simultaneously
- Previous process crashed without closing connection
- Long-running transaction blocking writes

**Solutions:**
```python
# Set longer timeout for database operations
conn = sqlite3.connect(db_path, timeout=30.0)

# Properly close connections
try:
    # operations
finally:
    conn.close()

# Check for locked processes
lsof | grep ".db"
# Kill if needed
kill -9 PID
```

#### Problem: "Disk Space Full"
```
sqlite3.OperationalError: disk I/O error
```

**Solutions:**
- Check available space:
  ```bash
  df -h data/
  ```

- Compress old databases:
  ```bash
  gzip data/gnews_articles_from2023.db
  ```
- Use external storage if needed

#### Problem: "Table Not Found"
```
sqlite3.OperationalError: no such table: articles_eng
```

**Solutions:**
- Verify pipeline stage completed successfully
- Check database exists:
  ```bash
  sqlite3 data/deleted_dupgnews2023.db ".tables"
  ```
- Re-run pipeline stage that creates table

---

### 3. Translation Issues

#### Problem: "CUDA Out of Memory"
```
RuntimeError: CUDA out of memory
```

**Solutions:**
```python
# Option 1: Use CPU instead
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
device = torch.device('cpu')

# Option 2: Reduce batch size in config
translation_batch_size: 64  # from 128

# Option 3: Clear GPU cache
torch.cuda.empty_cache()

# Option 4: Use smaller model
TRANSLATION_MODEL = "Helsinki-NLP/Opus-MT-de-en-tiny"
```

#### Problem: "Translation Model Too Large"
```
Connection timeout downloading model
```

**Solutions:**
```bash
# Pre-download model
python -c "from transformers import AutoTokenizer, AutoModelForSeq2SeqLM; \
  tokenizer = AutoTokenizer.from_pretrained('Helsinki-NLP/Opus-MT-de-en'); \
  model = AutoModelForSeq2SeqLM.from_pretrained('Helsinki-NLP/Opus-MT-de-en')"

# Or set cache directory to external drive
export HF_HOME=/large_drive/huggingface
python pipelineGNEWS.py
```

---

### 4. NLP Pipeline Issues

#### Problem: "Sentiment Model Not Found"
```
OSError: Can't find model 'distilbert-base-multilingual-uncased-sentiment'
```

**Solutions:**
- Download model manually
- Check internet connection
- Try alternative model:
  ```python
  nlp = pipeline("sentiment-analysis", 
                 model="cardiffnlp/twitter-xlm-roberta-base-sentiment")
  ```

#### Problem: "Topic Modeling (LDA) Too Slow"
```
LDA fitting takes > 1 hour
```

**Solutions:**
```python
# Reduce number of topics
n_topics = 10  # from 20

# Reduce iterations
lda_model = LatentDirichletAllocation(
    n_components=10,
    max_iter=5,  # from 20
    random_state=42,
    n_jobs=-1    # Use all cores
)

# Or skip LDA entirely if not needed
skip_topic_modeling = True
```

---

### 5. Deduplication Issues

#### Problem: "Too Many Duplicates Found (>80%)"
```
Deduplication removed 90% of articles
```

**Causes:**
- Aggregator websites republishing same content
- Query keywords too narrow
- Date range includes repeated coverage

**Solutions:**
```python
# Increase SimHash distance threshold (more lenient)
SIMHASH_DISTANCE = 5  # from 3 (less deduplication)

# Expand date range for diversity
start_date = datetime(2025, 12, 1)  # from Dec 21
end_date = datetime(2026, 1, 31)    # from Dec 31

# Diversify search query
query = (
    "Protest OR Demonstration OR Unruhen OR Ausschreitungen OR "
    "Gewalt OR Angriff OR Anschlag OR Terror OR Konflikt"
    # Add more specific terms
    " OR Streik OR Arbeit OR Arbeitskampf"
)
```

---

### 6. Country Classification Issues

#### Problem: "Low Country Classification Rate (<30%)"
```
article_country_score: average 45/100
Country NA: 70% of articles
```

**Causes:**
- Articles lack explicit country mentions
- TF-IDF model needs training data
- Content too generic or translated poorly

**Solutions:**
```python
# Use Named Entity Recognition to extract countries
from spacy import load
nlp = load("de_core_news_sm")
for doc in nlp.pipe(article_texts):
    for ent in doc.ents:
        if ent.label_ == "LOC":
            print(ent.text)

# Improve TF-IDF with custom vocabulary
from sklearn.feature_extraction.text import TfidfVectorizer
custom_vocabulary = {
    'syria': 0, 'damascus': 1, 'aleppo': 2,  # Syria
    'palestine': 3, 'gaza': 4, 'israel': 5,  # Palestine
    # ... add more
}
vectorizer = TfidfVectorizer(vocabulary=custom_vocabulary)

# Lower classification threshold
if country_score < 30:
    article_country = choose_country_lenient(text, threshold=30)
```

---

### 7. Memory Issues

#### Problem: "Python Process Growing > 16GB RAM"
```
MemoryError: Unable to allocate X GB
```

**Solutions:**
```python
# Process in smaller batches
batch_size = 50  # from 200

# Stream processing instead of loading all data
import sqlite3
conn = sqlite3.connect('data/deleted_dupgnews2023.db')
cursor = conn.cursor()
cursor.execute("SELECT * FROM articles_eng")
for row in cursor.fetchmany(100):  # Fetch 100 at a time
    process_article(row)

# Monitor memory usage
import psutil
process = psutil.Process()
print(f"Memory: {process.memory_info().rss / 1024**2:.2f} MB")

# Clear caches periodically
import gc
gc.collect()
torch.cuda.empty_cache()
```

---

### 8. Performance Issues

#### Problem: "Pipeline Taking 12+ Hours"
```
Total execution time: 43200+ seconds
```

**Optimization Steps (Priority Order):**

1. **Use GPU for translation (2-3x speedup)**
   ```yaml
   device: "cuda"
   translation_batch_size: 256
   ```

2. **Increase parallelization**
   ```python
   from concurrent.futures import ThreadPoolExecutor
   with ThreadPoolExecutor(max_workers=4) as executor:
       futures = [executor.submit(translate_batch, batch) 
                  for batch in batches]
   ```

3. **Reduce redundant processing**
   ```python
   # Skip already-processed records
   existing = get_processed_ids(db)
   new_articles = [a for a in articles if a['id'] not in existing]
   ```

4. **Profile bottlenecks**
   ```python
   import cProfile
   profiler = cProfile.Profile()
   profiler.enable()
   # ... pipeline code ...
   profiler.disable()
   profiler.print_stats(sort='cumulative')
   ```

5. **Cache model loads**
   ```python
   # Load once, reuse for all batches
   model = load_translation_model()
   for batch in batches:
       outputs = model(batch)
   ```

---

## Performance Tuning

### Configuration for Different Hardware

#### Low-Resource Machine (4GB RAM, CPU Only)
```yaml
processing:
  translation_batch_size: 16
  process_batch_size: 50
  device: "cpu"
  
pipeline_gnews:
  start_date: datetime(2025, 12, 25)  # Shorter date range
  end_date: datetime(2025, 12, 31)
  
skip_sentiment_analysis: true  # Optional
```

#### Mid-Range Machine (16GB RAM, GPU)
```yaml
processing:
  translation_batch_size: 128
  process_batch_size: 200
  device: "cuda"
  
num_workers: 2
```

#### High-Performance Machine (64GB RAM, Multi-GPU)
```yaml
processing:
  translation_batch_size: 512
  process_batch_size: 1000
  device: "cuda:0,cuda:1"  # Multiple GPUs
  
num_workers: 8
parallel_stages: true  # Run stages in parallel if independent
```

---

### Database Optimization

#### Create Indices for Fast Queries
```sql
-- Read-heavy queries
CREATE INDEX idx_articles_eng_publishedAt ON articles_eng(publishedAt);
CREATE INDEX idx_articles_eng_country ON articles_eng(article_country);
CREATE INDEX idx_match_conflict_wide_conflict_id ON match_conflict_wide(conf_conflict_id);

-- Coverage queries
CREATE INDEX idx_enriched_articles_emotion ON enriched_articles(emotion_label);
CREATE INDEX idx_enriched_articles_domestic ON enriched_articles(is_domestic);
```

#### Vacuum & Analyze Regularly
```python
import sqlite3

def optimize_database(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")      # Write-Ahead Logging
    conn.execute("PRAGMA synchronous=NORMAL")    # Faster writes
    conn.execute("PRAGMA cache_size=10000")      # Larger cache
    conn.execute("VACUUM")                        # Reclaim space
    conn.execute("ANALYZE")                       # Update statistics
    conn.close()
    print(f"Optimized {db_path}")

# Run after major operations
optimize_database("data/matched_conflict.db")
```

---

## Monitoring & Logging

### Enable Detailed Logging
```python
import logging

# Set detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('pipeline.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)
logger.info("Pipeline started")
logger.debug("Processing article: " + article_id)
logger.warning("Low confidence classification: " + str(score))
logger.error("Failed to process: " + error_msg)
```

### Monitor Resource Usage
```python
import psutil
import time

class ResourceMonitor:
    def __init__(self):
        self.process = psutil.Process()
        self.initial_memory = self.process.memory_info().rss
    
    def log_status(self, stage_name):
        current_mem = self.process.memory_info().rss / 1024**2
        cpu_pct = self.process.cpu_percent(interval=1)
        
        print(f"{stage_name}: {current_mem:.0f}MB RAM, {cpu_pct}% CPU")

monitor = ResourceMonitor()
monitor.log_status("After dedup")
monitor.log_status("After translation")
```

---

## Data Quality Checks

### Pre-Pipeline Validation
```python
def validate_raw_articles(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    checks = {}
    
    # Total articles
    cursor.execute("SELECT COUNT(*) FROM articles")
    checks['total'] = cursor.fetchone()[0]
    
    # Missing fields
    cursor.execute("SELECT COUNT(*) FROM articles WHERE url IS NULL")
    checks['missing_url'] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM articles WHERE title IS NULL")
    checks['missing_title'] = cursor.fetchone()[0]
    
    # Duplicates
    cursor.execute("""
        SELECT COUNT(*) FROM (
            SELECT url, COUNT(*) FROM articles 
            GROUP BY url HAVING COUNT(*) > 1
        )
    """)
    checks['duplicate_urls'] = cursor.fetchone()[0]
    
    conn.close()
    
    # Report
    print(f"Total articles: {checks['total']}")
    print(f"Missing URLs: {checks['missing_url']} ({100*checks['missing_url']/checks['total']:.1f}%)")
    print(f"Missing titles: {checks['missing_title']} ({100*checks['missing_title']/checks['total']:.1f}%)")
    print(f"Duplicate URLs: {checks['duplicate_urls']}")
    
    return checks

validate_raw_articles('data/gnews_articles_from2023.db')
```

### Post-Pipeline Validation
```python
def validate_matched_conflicts(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Coverage
    cursor.execute("SELECT COUNT(DISTINCT conf_conflict_id) FROM match_conflict_wide")
    unique_conflicts = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(DISTINCT art_id) FROM match_conflict_wide")
    matched_articles = cursor.fetchone()[0]
    
    # Match quality
    cursor.execute("SELECT AVG(ABS(time_proximity_days)) FROM match_conflict_wide")
    avg_lag = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM match_conflict_wide WHERE time_proximity_days > 30")
    distant_matches = cursor.fetchone()[0]
    
    print(f"Unique conflicts matched: {unique_conflicts}")
    print(f"Articles matched to conflicts: {matched_articles}")
    print(f"Average temporal lag: ±{avg_lag:.1f} days")
    print(f"Matches >30 days away: {distant_matches} ({100*distant_matches/matched_articles:.1f}%)")
    
    conn.close()

validate_matched_conflicts('data/matched_conflict.db')
```

---

## Backup & Recovery

### Automated Backup Strategy
```python
import shutil
import os
from datetime import datetime

def backup_databases(backup_dir='backups'):
    os.makedirs(backup_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    databases = [
        'data/gnews_articles_from2023.db',
        'data/deleted_dupgnews2023.db',
        'data/conflict_data.db',
        'data/matched_conflict.db'
    ]
    
    for db in databases:
        if os.path.exists(db):
            backup_name = f"{backup_dir}/{os.path.basename(db)}.{timestamp}.bak"
            shutil.copy2(db, backup_name)
            print(f"Backed up: {backup_name}")

# Schedule daily backups
# Linux: Add to crontab (crontab -e)
# 0 2 * * * python /path/to/backup_script.py
```

### Recovery Procedure
```bash
# List backups
ls -la backups/

# Restore from backup
cp backups/matched_conflict.db.20251225_020000.bak data/matched_conflict.db

# Verify integrity
sqlite3 data/matched_conflict.db "PRAGMA integrity_check;"
```

---

## Debugging Workflow

### Step 1: Enable Detailed Logging
```python
logging.basicConfig(level=logging.DEBUG)
```

### Step 2: Run with Subset of Data
```python
# Process only first 100 articles
conn = sqlite3.connect('data/gnews_articles_from2023.db')
cursor = conn.cursor()
cursor.execute("SELECT * FROM articles LIMIT 100")
sample_articles = cursor.fetchall()
# Process sample_articles
```

### Step 3: Add Breakpoints
```python
import pdb

for article in articles:
    try:
        result = process_article(article)
    except Exception as e:
        print(f"Error on article: {article['id']}")
        print(f"Exception: {e}")
        pdb.post_mortem()  # Drop into debugger
```

### Step 4: Check Intermediate Outputs
```sql
-- Query intermediate table to verify processing
SELECT COUNT(*) FROM articles;
SELECT COUNT(*) FROM article_without_duplicates;
SELECT COUNT(*) FROM articles_eng WHERE article_country != 'NA';
SELECT COUNT(*) FROM enriched_articles WHERE emotion_label IS NOT NULL;
```

---

## Getting Help

1. **Check logs:** `cat logs/pipeline_execution_*.log`
2. **Review database:** `sqlite3 data/matched_conflict.db ".schema"`
3. **Test APIs:** `curl -v "https://gnews.io/api/v4/search?q=test&token=KEY"`
4. **Verify dependencies:** `pip list | grep transformers`
5. **Check disk space:** `df -h`
6. **Monitor processes:** `ps aux | grep python`
