# unique_conflicts
import logging
import time
from pathlib import Path

from utils import get_db_connection, init_logger

# compute DB path relative to this file
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "conflict_data.db"


def create_unique_conflict_table(conn):
    """
    Create a table that contains information about all unique conflicts.
    """
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS unique_conflict (
        conflict_id INTEGER PRIMARY KEY,
        n_events INTEGER,
        total_fatalities INTEGER
    );
    """

    cur = conn.cursor()
    cur.execute(create_table_sql)
    conn.commit()


def ensure_indexes(conn, logger):
    """
    Ensure indexes exist on the columns we join/group on.
    This makes the aggregation much faster and more predictable.
    """
    logger.info("Ensuring indexes exist on events and event_conflict...")
    cur = conn.cursor()

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_event_id_cnty
        ON events(event_id_cnty);
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_event_conflict_event_id_cnty
        ON event_conflict(event_id_cnty);
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_event_conflict_conflict_id
        ON event_conflict(conflict_id);
    """)

    conn.commit()
    logger.info("Indexes ensured.")


def populate_unique_conflict_table(conn, logger):
    """
    Populate the unique_conflict table with one row per conflict_id:

        conflict_id | n_events | total_fatalities

    n_events: number of distinct events mapped to that conflict_id
    total_fatalities: sum of fatalities across those events.

    Assumes:
      - mapping table: event_conflict(event_id_cnty, conflict_scheme, conflict_id)
      - events table:  events(event_id_cnty, fatalities)
    """
    cur = conn.cursor()

    logger.info("Clearing existing data from unique_conflict table...")
    cur.execute("DELETE FROM unique_conflict;")
    conn.commit()

    logger.info(
        "Aggregating events and fatalities per conflict_id "
        "(this may take a few seconds)..."
    )

    # INSERT INTO unique_conflict (conflict_id, n_events, total_fatalities)
    test_sql = """
    SELECT
        m.conflict_id,
        COUNT(DISTINCT m.event_id_cnty) AS n_events,
        COALESCE(SUM(e.fatalities), 0) AS total_fatalities
    FROM event_conflict AS m
    LEFT JOIN events AS e
        ON e.event_id_cnty = m.event_id_cnty
    GROUP BY m.conflict_id
    ORDER BY m.conflict_id;
    """

    
    start = time.perf_counter()
    rows = list(conn.execute(test_sql))
    elapsed = time.perf_counter() - start
    logger.info("Read-only aggregation returned %d rows in %.2f seconds.",
                len(rows), elapsed)



def main():
    logger = init_logger("unique_conflicts")
    logger.info("Starting unique_conflicts script.")
    logger.info("Connecting to database at %s", DB_PATH)

    conn = get_db_connection(DB_PATH)

    try:
        logger.info("Creating unique_conflict table if it does not exist...")
        create_unique_conflict_table(conn)

        ensure_indexes(conn, logger)

        logger.info("Populating unique_conflict table...")
        populate_unique_conflict_table(conn, logger)

        logger.info("Finished updating unique_conflict table.")

    except Exception:
        logger.exception("unique_conflicts script failed.")
        raise

    finally:
        conn.close()
        logger.info("Closed database connection.")


if __name__ == "__main__":
    main()
