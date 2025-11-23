# aggregation

# src/build_conflict_mapping.py

from utils import load_config, get_db_connection, init_logger

def create_event_conflict_table(conn):
    """
    Create a mapping table that connects each event to a conflict_id
    within a given classification scheme.
    """
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS event_conflict (
        event_id INTEGER,
        conflict_scheme TEXT,
        conflict_id INTEGER,
        PRIMARY KEY (event_id, conflict_scheme)
    );
    """

    cur = conn.cursor()
    cur.execute(create_table_sql)
    conn.commit()

def main():
    logger = init_logger("conflict_mapping")

    # Option A: use config
    config = load_config()
    db_path = config["database"]["path"]

    # Option B (if you don’t want config yet):
    # db_path = "../data/conflict_data.db"

    logger.info(f"Connecting to database at {db_path}...")
    conn = get_db_connection(db_path)

    logger.info("Creating event_conflict table if it does not exist...")
    create_event_conflict_table(conn)

    logger.info("Done. Closing connection.")
    conn.close()

if __name__ == "__main__":
    main()

