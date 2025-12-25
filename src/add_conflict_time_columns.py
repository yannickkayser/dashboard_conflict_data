import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "conflict_data.db"


# Tables you already have
EVENTS_TABLE = "events"
MAP_TABLE = "event_conflict"          # event_id_cnty -> conflict_id
FEATURES_TABLE = "conflict_features"  # per conflict_id features (from unique_conflicts.py)

# Adjust if your events date column is named differently:
EVENT_DATE_COL = "event_date"         # typical name; change if needed


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.cursor()
    rows = cur.execute(f"PRAGMA table_info({table});").fetchall()
    return {r[1] for r in rows}


def ensure_columns(conn: sqlite3.Connection, table: str, cols: list[tuple[str, str]]) -> None:
    existing = table_columns(conn, table)
    cur = conn.cursor()
    for name, coltype in cols:
        if name not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype};")  # add column [web:195]
    conn.commit()


def ensure_conflict_time_table(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conflict_time (
            conflict_id INTEGER PRIMARY KEY,
            start_date TEXT,
            end_date TEXT,
            mid_date TEXT,
            duration_days INTEGER
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_time_start ON conflict_time(start_date);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_time_end ON conflict_time(end_date);")
    conn.commit()


def rebuild_conflict_time(conn: sqlite3.Connection) -> None:
    """
    Build conflict_time using MIN/MAX dates across events linked to each conflict_id.
    Assumes EVENT_DATE_COL is ISO-like text; if it includes time, we take substr(,1,10).
    MIN/MAX are standard SQLite aggregate functions. [web:502]
    """
    cur = conn.cursor()

    # Clear and rebuild (safe because it's derived)
    cur.execute("DELETE FROM conflict_time;")
    conn.commit()

    # Start/end from events joined with event_conflict
    cur.execute(f"""
        INSERT INTO conflict_time(conflict_id, start_date, end_date)
        SELECT
            ec.conflict_id,
            MIN(substr(e.{EVENT_DATE_COL}, 1, 10)) AS start_date,
            MAX(substr(e.{EVENT_DATE_COL}, 1, 10)) AS end_date
        FROM {MAP_TABLE} ec
        JOIN {EVENTS_TABLE} e
          ON e.event_id_cnty = ec.event_id_cnty
        WHERE e.{EVENT_DATE_COL} IS NOT NULL AND TRIM(e.{EVENT_DATE_COL}) <> ''
        GROUP BY ec.conflict_id;
    """)
    conn.commit()

    # Derive mid_date and duration_days using SQLite date functions
    cur.execute("""
        UPDATE conflict_time
        SET
          duration_days = CAST((julianday(end_date) - julianday(start_date)) AS INTEGER),
          mid_date = date(julianday(start_date) + (julianday(end_date) - julianday(start_date)) / 2.0);
    """)
    conn.commit()


def push_time_into_conflict_features(conn: sqlite3.Connection) -> None:
    """
    Optional: copy time columns into conflict_features for easier dashboard joins.
    Uses UPDATE ... SET ... = (SELECT ...) which is standard in SQLite. [web:251]
    """
    # Add columns if missing
    ensure_columns(conn, FEATURES_TABLE, [
        ("start_date", "TEXT"),
        ("end_date", "TEXT"),
        ("mid_date", "TEXT"),
        ("duration_days", "INTEGER"),
    ])

    cur = conn.cursor()
    cur.execute(f"""
        UPDATE {FEATURES_TABLE}
        SET
          start_date = (SELECT ct.start_date FROM conflict_time ct WHERE ct.conflict_id = {FEATURES_TABLE}.conflict_id),
          end_date   = (SELECT ct.end_date   FROM conflict_time ct WHERE ct.conflict_id = {FEATURES_TABLE}.conflict_id),
          mid_date   = (SELECT ct.mid_date   FROM conflict_time ct WHERE ct.conflict_id = {FEATURES_TABLE}.conflict_id),
          duration_days = (SELECT ct.duration_days FROM conflict_time ct WHERE ct.conflict_id = {FEATURES_TABLE}.conflict_id)
        WHERE conflict_id IN (SELECT conflict_id FROM conflict_time);
    """)
    conn.commit()


def ensure_dashboard_indexes(conn: sqlite3.Connection) -> None:
    """
    Indexes that usually matter for the dashboard (filters by country/actor/time).
    """
    cur = conn.cursor()
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{FEATURES_TABLE}_country ON {FEATURES_TABLE}(country);")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{FEATURES_TABLE}_actor1 ON {FEATURES_TABLE}(actor1);")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{FEATURES_TABLE}_start_date ON {FEATURES_TABLE}(start_date);")
    conn.commit()


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Missing DB: {DB_PATH}")

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    try:
        ensure_conflict_time_table(conn)
        rebuild_conflict_time(conn)
        push_time_into_conflict_features(conn)
        ensure_dashboard_indexes(conn)

        cur = conn.cursor()
        n = cur.execute("SELECT COUNT(*) FROM conflict_time;").fetchone()[0]
        print(f"Done. conflict_time rows: {n}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
