# aggregation
import logging
from pathlib import Path
import pandas as pd
from utils import get_db_connection, init_logger

CONFLICT_SCHEME_NAME = "actor1_assoc1_v1"
# <-- NEW: compute DB path relative to this file, not the current working dir
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "conflict_data.db"

### Build empyt table if not exists
# src/build_conflict_mapping.py

from utils import load_config, get_db_connection, init_logger

def create_event_conflict_table(conn):
    """
    Create a mapping table that connects each event to a conflict_id
    within a given classification scheme.
    """
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS event_conflict (
        event_id_cnty INTEGER,
        conflict_scheme TEXT,
        conflict_id INTEGER,
        PRIMARY KEY (event_id_cnty, conflict_scheme)
    );
    """

    cur = conn.cursor()
    cur.execute(create_table_sql)
    conn.commit()


def build_conflict_mapping(conn, logger):
    """
    1. Read events (event_id_cnty, actor_1, assoc_actor_1) from the events table.
    2. Group events by (actor_1, assoc_actor_1).
    3. Assign each unique pair a numeric conflict_id.
    4. Write the mapping into event_conflict.
    """

    # 1) Load data from the events table
    query = """
        SELECT event_id_cnty, actor1, assoc_actor_1
        FROM events
    """
    events = pd.read_sql(query, conn)
    logger.info(f"Loaded {len(events)} events from 'events' table.")

    # 2) Clean & create a grouping key
    events["actor1"] = events["actor1"].fillna("NA").astype(str).str.strip()
    events["assoc_actor_1"] = events["assoc_actor_1"].fillna("NA").astype(str).str.strip()

    events["conflict_key"] = events["actor1"] + "|" + events["assoc_actor_1"]

    # 3) Turn each unique conflict_key into an integer id
    # pd.factorize returns (codes, unique_values)
    events["conflict_id"], uniques = pd.factorize(events["conflict_key"])
    n_conflicts = len(uniques)
    logger.info(f"Identified {n_conflicts} unique conflicts for scheme '{CONFLICT_SCHEME_NAME}'.")

    # 4) Build the mapping dataframe
    mapping_df = events[["event_id_cnty", "conflict_id"]].copy()
    mapping_df["conflict_scheme"] = CONFLICT_SCHEME_NAME
    mapping_df = mapping_df[["event_id_cnty", "conflict_scheme", "conflict_id"]]

    # Optional: clear old rows for this scheme so you can safely rerun
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM event_conflict WHERE conflict_scheme = ?",
        (CONFLICT_SCHEME_NAME,),
    )
    conn.commit()

    # 5) Write mapping into event_conflict
    mapping_df.to_sql(
        "event_conflict",
        conn,
        if_exists="append",  # we already deleted this scheme's rows
        index=False,
    )

    logger.info(
        f"Wrote {len(mapping_df)} rows to event_conflict for scheme '{CONFLICT_SCHEME_NAME}'."
    )


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

    logger.info("Building conflict mapping...")
    build_conflict_mapping(conn, logger)

    logger.info("Final row counts:")
    log_counts(conn, logger)

    logger.info("Done. Closing connection.")
    conn.close()


if __name__ == "__main__":
    main()

# ----------------------------------------------

### 

