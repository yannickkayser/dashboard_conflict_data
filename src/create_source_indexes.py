# create_source_indexes.py
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

ART_DB = DATA_DIR / "deleted_dupgnews2023.db"
CONFLICT_DB = DATA_DIR / "conflict_data.db"

# Index articles database
print(f"Creating indexes on {ART_DB}...")
conn_art = sqlite3.connect(ART_DB)
cur_art = conn_art.cursor()
cur_art.execute("CREATE INDEX IF NOT EXISTS idx_article_country ON articles_eng(article_country);")
cur_art.execute("CREATE INDEX IF NOT EXISTS idx_publishedAt ON articles_eng(publishedAt);")
conn_art.commit()
conn_art.close()
print("Articles database indexed.")

# Index conflict database
print(f"Creating indexes on {CONFLICT_DB}...")
conn_conf = sqlite3.connect(CONFLICT_DB)
cur_conf = conn_conf.cursor()
cur_conf.execute("CREATE INDEX IF NOT EXISTS idx_feat_country ON conflict_features(country);")
cur_conf.execute("CREATE INDEX IF NOT EXISTS idx_feat_conflict_id ON conflict_features(conflict_id);")
cur_conf.execute("CREATE INDEX IF NOT EXISTS idx_time_conflict_id ON conflict_time(conflict_id);")
cur_conf.execute("CREATE INDEX IF NOT EXISTS idx_time_mid_date ON conflict_time(mid_date);")
cur_conf.execute("CREATE INDEX IF NOT EXISTS idx_conf_conflict_id ON unique_conflict(conflict_id);")
conn_conf.commit()
conn_conf.close()
print("Conflict database indexed.")

print("All indexes created successfully!")