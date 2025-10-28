import yaml
import sqlite3
import logging
from datetime import datetime

def load_config(path="../config/config.yaml"):
    with open(path, "r") as file:
        return yaml.safe_load(file)

def init_logger(name="pipeline"):
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()]
    )
    return logging.getLogger(name)

def get_db_connection(db_path):
    return sqlite3.connect(db_path)

def get_latest_event_date(conn):
    query = """
        SELECT event_date
        FROM events
        ORDER BY event_date DESC
        LIMIT 1;
    """
    cur = conn.cursor()
    cur.execute(query)
    result = cur.fetchone()
    return result[0] if result else "2000-01-01"
