# aggregation
import logging
from pathlib import Path
import pandas as pd
from utils import get_db_connection, init_logger

CONFLICT_SCHEME_NAME = "country_actor1_primaryassoc_v1"
# <-- NEW: compute DB path relative to this file, not the current working dir
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "conflict_data.db"

### Build empyt table if not exists
# src/build_conflict_mapping.py

def create_event_conflict_table(conn):
    """
    Create a mapping table that connects each event to a conflict_id
    within a given classification scheme.
    """
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS event_conflict (
        event_id_cnty TEXT,
        conflict_scheme TEXT,
        conflict_id INTEGER,
        PRIMARY KEY (event_id_cnty, conflict_scheme)
    );
    """

    cur = conn.cursor()
    cur.execute(create_table_sql)
    conn.commit()

def create_conflict_lookup_table(conn):
    """
    Persistent lookup: conflict_key -> conflict_id (stable across runs).
    """
    sql = """
    CREATE TABLE IF NOT EXISTS conflict_lookup (
        conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
        conflict_key TEXT  UNIQUE
    );
    """
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()


def primary_assoc_actor_first(value) -> str:
    if value is None:
        return "NA"
    s = str(value).strip()
    if not s or s.upper() == "NA":
        return "NA"
    parts = [p.strip() for p in s.split(";") if p.strip()]
    return parts[0] if parts else "NA"


def build_conflict_mapping(conn, logger):
    """
    Incremental mapping:
    - Load only events not yet mapped for this scheme
    - Ensure conflict_key exists in conflict_lookup
    - Fetch stable conflict_id from conflict_lookup
    - Insert new rows into event_conflict
    """
    # Load only NEW events (not mapped yet for this scheme)
    query = """
    SELECT e.event_id_cnty, e.actor1, e.assoc_actor_1, e.country
    FROM events e
    LEFT JOIN event_conflict ec
      ON ec.event_id_cnty = e.event_id_cnty
     AND ec.conflict_scheme = ?
    WHERE ec.event_id_cnty IS NULL
    """
    events = pd.read_sql(query, conn, params=(CONFLICT_SCHEME_NAME,))
    events["event_id_cnty"] = events["event_id_cnty"].astype(str).str.strip()
    logger.info("Loaded %d NEW events to map for scheme '%s'.", len(events), CONFLICT_SCHEME_NAME)

    if events.empty:
        logger.info("No new events found. Nothing to do.")
        return

    # Canonicalize (stability!)
    events["actor1"] = events["actor1"].fillna("NA").astype(str).str.strip()
    events["assoc_actor_1"] = events["assoc_actor_1"].fillna("NA").astype(str).str.strip()
    events["country"] = events["country"].fillna("NA").astype(str).str.strip()

    primary_assoc = events["assoc_actor_1"].apply(primary_assoc_actor_first)

    events["conflict_key"] = (
        events["country"] + "|" + events["actor1"] + "|" + primary_assoc
    )

    cur = conn.cursor()

    # 1) Ensure keys exist in conflict_lookup
    unique_keys = events["conflict_key"].drop_duplicates().tolist()
    cur.executemany(
        "INSERT OR IGNORE INTO conflict_lookup (conflict_key) VALUES (?);",
        [(k,) for k in unique_keys],
    )
    conn.commit()

    # 2) Fetch conflict_id for keys (chunked)
    key_to_id = {}
    CHUNK = 900
    for i in range(0, len(unique_keys), CHUNK):
        chunk = unique_keys[i:i + CHUNK]
        placeholders = ",".join(["?"] * len(chunk))
        rows = cur.execute(
            f"SELECT conflict_key, conflict_id FROM conflict_lookup WHERE conflict_key IN ({placeholders});",
            chunk,
        ).fetchall()
        key_to_id.update({k: cid for k, cid in rows})

    events["conflict_id"] = events["conflict_key"].map(key_to_id)
    if events["conflict_id"].isna().any():
        raise RuntimeError("Some conflict_keys did not get a conflict_id (unexpected).")

    # 3) Insert new event->conflict mappings
    mapping_rows = [
        (str(r.event_id_cnty), CONFLICT_SCHEME_NAME, int(r.conflict_id))
        for r in events[["event_id_cnty", "conflict_id"]].itertuples(index=False)
    ]

    cur.executemany(
        "INSERT OR IGNORE INTO event_conflict (event_id_cnty, conflict_scheme, conflict_id) VALUES (?, ?, ?);",
        mapping_rows
    )
    conn.commit()

    logger.info("Wrote %d new rows to event_conflict.", len(mapping_rows))



def log_counts(conn, logger):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM events;")
    events_count = cur.fetchone()[0]
    logger.info(f"events table rows: {events_count}")

    cur.execute("SELECT COUNT(*) FROM event_conflict;")
    ec_count = cur.fetchone()[0]
    logger.info(f"event_conflict rows: {ec_count}")


def main():
    logger = init_logger("conflict_mapping")
    logger.info(f"Using DB at {DB_PATH}")

    conn = get_db_connection(str(DB_PATH))

    logger.info("Ensuring event_conflict table exists...")
    create_event_conflict_table(conn)

    logger.info("Ensuring conflict_lookup table exists...")
    create_conflict_lookup_table(conn)

    logger.info("Building conflict mapping...")
    build_conflict_mapping(conn, logger)

    logger.info("Final row counts:")
    log_counts(conn, logger)

    logger.info("Done. Closing connection.")
    conn.close()


if __name__ == "__main__":
    main()

