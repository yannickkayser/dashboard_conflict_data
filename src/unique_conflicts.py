# unique_conflicts.py
import logging
import time
from pathlib import Path
import re
from collections import Counter, defaultdict
import math
import json
import sqlite3

from utils import get_db_connection, init_logger


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "conflict_data.db"


# --------------------
# TOKENIZATION / STOPWORDS
TOKEN_RE = re.compile(r"[A-Za-z]{3,}")  # words >= 3 letters

STOPWORDS = {
    "the","a","an","and","or","to","of","in","on","for","with","at","by","from",
    "is","are","was","were","be","been","being","as","that","this","it","its",
    "their","they","them","he","she","his","her","you","we","our","us",
    "after","before","during","over","under","into","out","up","down",
    "near","around","about","between","within","across",
    "said","report","reports","according","allegedly",
    "killed","injured","attack","attacked","clash","clashes","protest","protests",
    "police","army","soldiers","people","civilians","forces","security",
    "against", "there", "demonstration", "demand", "members", "gathered", "demonstrators",
    "protestors"
}


# --------------------
# TABLE NAMES (time feature part)
EVENTS_TABLE = "events"
MAP_TABLE = "event_conflict"
FEATURES_TABLE = "conflict_features"
EVENT_DATE_COL = "event_date"  # change if needed


# --------------------
# SCHEMA HELPERS

def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.cursor()
    rows = cur.execute(f"PRAGMA table_info({table});").fetchall()
    return {r[1] for r in rows}


def ensure_columns(conn: sqlite3.Connection, table: str, cols: list[tuple[str, str]]) -> None:
    existing = table_columns(conn, table)
    cur = conn.cursor()
    for name, coltype in cols:
        if name not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype};")
    conn.commit()


# --------------------
# UNIQUE CONFLICT BASE TABLE

def create_unique_conflict_table(conn):
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


def populate_unique_conflict_table(conn, logger, batch_size=100):
    cur = conn.cursor()

    logger.info("Clearing existing data from unique_conflict table...")
    cur.execute("DELETE FROM unique_conflict;")
    conn.commit()

    logger.info("Fetching list of conflict_ids...")
    conflict_ids = [row[0] for row in cur.execute(
        "SELECT DISTINCT conflict_id FROM event_conflict ORDER BY conflict_id;"
    ).fetchall()]

    total = len(conflict_ids)
    logger.info("Found %d unique conflict_ids.", total)

    insert_sql = """
    INSERT INTO unique_conflict (conflict_id, n_events, total_fatalities)
    VALUES (?, ?, ?);
    """

    processed = 0
    for i in range(0, total, batch_size):
        batch = conflict_ids[i:i + batch_size]
        placeholders = ",".join(["?"] * len(batch))

        agg_sql = f"""
        SELECT
            m.conflict_id,
            COUNT(DISTINCT m.event_id_cnty) AS n_events,
            COALESCE(SUM(e.fatalities), 0) AS total_fatalities
        FROM event_conflict AS m
        LEFT JOIN events AS e
            ON e.event_id_cnty = m.event_id_cnty
        WHERE m.conflict_id IN ({placeholders})
        GROUP BY m.conflict_id
        ORDER BY m.conflict_id;
        """

        rows = cur.execute(agg_sql, batch).fetchall()
        cur.executemany(insert_sql, rows)
        conn.commit()

        processed += len(batch)
        logger.info(
            "Progress: %d / %d conflicts aggregated + inserted (last conflict_id = %s)",
            processed,
            total,
            batch[-1],
        )

    logger.info("END: Table filled sucessfully (%d conflicts)", total)


# --------------------
# CONFLICT FEATURES TABLE

def ensure_conflict_features_schema(conn, logger):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS conflict_features(
            conflict_id INTEGER PRIMARY KEY,
            conflict_key TEXT,
            n_events INTEGER,
            total_fatalities INTEGER,

            country TEXT,
            actor1 TEXT,
            primary_assoc_actor_1 TEXT,


            disorder_type_mode TEXT,
            event_type_mode TEXT
        );
    """)
    conn.commit()

    # Add missing columns if this table already existed with an older schema
    ensure_columns(conn, FEATURES_TABLE, [
        ("assoc_actor_1", "TEXT"),
        ("disorder_type_mode", "TEXT"),
        ("event_type_mode", "TEXT"),
        ("tfidf_terms_conflict", "TEXT"),
        ("start_date", "TEXT"),
        ("end_date", "TEXT"),
        ("mid_date", "TEXT"),
        ("duration_days", "INTEGER"),
    ])

    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_features_conflict_id ON conflict_features(conflict_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_features_country ON conflict_features(country);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_features_actor1 ON conflict_features(actor1);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_features_start_date ON conflict_features(start_date);")
    conn.commit()

    logger.info("conflict_features schema ensured.")



def ensure_conflict_type_count_tables(conn, logger):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS conflict_disordertype_counts(
            conflict_id INTEGER,
            disorder_type TEXT,
            n_events INTEGER,
            fatalities INTEGER,
            PRIMARY KEY (conflict_id, disorder_type)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS conflict_eventtype_counts(
            conflict_id INTEGER,
            event_type TEXT,
            n_events INTEGER,
            fatalities INTEGER,
            PRIMARY KEY (conflict_id, event_type)
        );
    """)

    conn.commit()
    logger.info("Type count tables ensured.")


def rebuild_conflict_features_base(conn, logger):
    cur = conn.cursor()

    logger.info("Rebuilding conflict features base (clearing table)...")
    cur.execute("DELETE FROM conflict_features;")
    conn.commit()
    logger.info("Table cleared. Now, rebuilding...")

    cur.execute("""
        INSERT INTO conflict_features (conflict_id, conflict_key, n_events, total_fatalities)
        SELECT
            uc.conflict_id,
            cl.conflict_key,
            uc.n_events,
            uc.total_fatalities
        FROM unique_conflict uc
        LEFT JOIN conflict_lookup cl
            ON cl.conflict_id = uc.conflict_id;
    """)
    conn.commit()

    logger.info("Parsing conflict_key -> country/actor1/primary_assoc_actor_1 ...")
    rows = cur.execute("""
        SELECT conflict_id, conflict_key
        FROM conflict_features
        WHERE conflict_key IS NOT NULL AND TRIM(conflict_key) <> '';
    """).fetchall()

    updates = []
    for conflict_id, conflict_key in rows:
        parts = str(conflict_key).split("|")
        country = parts[0].strip() if len(parts) > 0 else None
        actor1 = parts[1].strip() if len(parts) > 1 else None
        primary = parts[2].strip() if len(parts) > 2 else None
        updates.append((country, actor1, primary, conflict_id))

    cur.executemany("""
        UPDATE conflict_features
        SET country = ?, actor1 = ?, primary_assoc_actor_1 = ?
        WHERE conflict_id = ?;
    """, updates)
    conn.commit()

    logger.info("conflict_features base rebuilt (%d conflicts parsed).", len(updates))


def fill_assoc_actor_1_mode(conn, logger, scheme_name=None):
    cur = conn.cursor()
    logger.info("Filling assoc_actor_1 (mode across events)%s ...",
                f" for scheme={scheme_name}" if scheme_name else "")

    if scheme_name:
        cur.execute("""
            WITH ranked AS (
                SELECT
                    ec.conflict_id AS conflict_id,
                    TRIM(e.assoc_actor_1) AS assoc_actor_1,
                    COUNT(*) AS n,
                    ROW_NUMBER() OVER (
                        PARTITION BY ec.conflict_id
                        ORDER BY COUNT(*) DESC, TRIM(e.assoc_actor_1)
                    ) AS rn
                FROM event_conflict ec
                JOIN events e
                  ON e.event_id_cnty = ec.event_id_cnty
                WHERE ec.conflict_scheme = ?
                  AND e.assoc_actor_1 IS NOT NULL
                  AND TRIM(e.assoc_actor_1) <> ''
                  AND UPPER(TRIM(e.assoc_actor_1)) <> 'NA'
                GROUP BY ec.conflict_id, TRIM(e.assoc_actor_1)
            )
            UPDATE conflict_features
            SET assoc_actor_1 = (
                SELECT r.assoc_actor_1
                FROM ranked r
                WHERE r.conflict_id = conflict_features.conflict_id
                  AND r.rn = 1
            )
            WHERE conflict_id IN (SELECT conflict_id FROM ranked WHERE rn = 1);
        """, (scheme_name,))
    else:
        cur.execute("""
            WITH ranked AS (
                SELECT
                    ec.conflict_id AS conflict_id,
                    TRIM(e.assoc_actor_1) AS assoc_actor_1,
                    COUNT(*) AS n,
                    ROW_NUMBER() OVER (
                        PARTITION BY ec.conflict_id
                        ORDER BY COUNT(*) DESC, TRIM(e.assoc_actor_1)
                    ) AS rn
                FROM event_conflict ec
                JOIN events e
                  ON e.event_id_cnty = ec.event_id_cnty
                WHERE e.assoc_actor_1 IS NOT NULL
                  AND TRIM(e.assoc_actor_1) <> ''
                  AND UPPER(TRIM(e.assoc_actor_1)) <> 'NA'
                GROUP BY ec.conflict_id, TRIM(e.assoc_actor_1)
            )
            UPDATE conflict_features
            SET assoc_actor_1 = (
                SELECT r.assoc_actor_1
                FROM ranked r
                WHERE r.conflict_id = conflict_features.conflict_id
                  AND r.rn = 1
            )
            WHERE conflict_id IN (SELECT conflict_id FROM ranked WHERE rn = 1);
        """)
    conn.commit()
    logger.info("assoc_actor_1 filled.")


def fill_event_type_modes(conn, logger, scheme_name=None):
    cur = conn.cursor()
    logger.info("Filling disorder type modes and event type modes")

    scheme_filter = "AND ec.conflict_scheme = ?" if scheme_name else ""

    sql_disorder_type = f"""
        WITH RANKED AS (
            SELECT
                ec.conflict_id,
                TRIM(e.disorder_type) AS disorder_type,
                COUNT(*) AS n,
                ROW_NUMBER() OVER (
                    PARTITION BY ec.conflict_id
                    ORDER BY COUNT(*) DESC, TRIM(e.disorder_type)
                    ) AS rn
                FROM event_conflict ec
                JOIN events e ON e.event_id_cnty = ec.event_id_cnty
                WHERE e.disorder_type IS NOT NULL
                    AND TRIM(e.disorder_type) <> ''
                    {scheme_filter}
                GROUP BY ec.conflict_id, TRIM(e.disorder_type)
        )
        UPDATE conflict_features
        SET disorder_type_mode = (
            SELECT r.disorder_type
            FROM ranked r
            WHERE r.conflict_id = conflict_features.conflict_id
                AND r.rn = 1
        )
        WHERE conflict_id IN (SELECT conflict_id FROM ranked WHERE rn = 1);
    """

    sql_event_type = f"""
        WITH RANKED AS (
            SELECT
                ec.conflict_id,
                TRIM(e.event_type) AS event_type,
                COUNT(*) AS n,
                ROW_NUMBER() OVER (
                    PARTITION BY ec.conflict_id
                    ORDER BY COUNT(*) DESC, TRIM(e.event_type)
                ) AS rn
            FROM event_conflict ec
            JOIN events e ON e.event_id_cnty = ec.event_id_cnty
            WHERE e.event_type IS NOT NULL
                AND TRIM(e.event_type) <> ''
                {scheme_filter}
            GROUP BY ec.conflict_id, TRIM(e.event_type)
        )
        UPDATE conflict_features
        SET event_type_mode = (
            SELECT r.event_type
            FROM ranked r
            WHERE r.conflict_id = conflict_features.conflict_id
                AND r.rn = 1
        )
        WHERE conflict_id IN (SELECT conflict_id FROM ranked WHERE rn = 1);
    """

    if scheme_name:
        cur.execute(sql_disorder_type, (scheme_name))
        cur.execute(sql_event_type, (scheme_name))
    else:
        cur.execute(sql_disorder_type)
        cur.execute(sql_event_type)

    conn.commit()
    logger.info("disorder_type + event_type filled")


def rebuild_conflict_type_counts(conn, logger, scheme_name=None):
    cur = conn.cursor()
    logger.info("Rebuilding type counts%s ...", f" for scheme={scheme_name}" if scheme_name else "")

    cur.execute("DELETE FROM conflict_disordertype_counts;")
    cur.execute("DELETE FROM conflict_eventtype_counts;")
    conn.commit()

    scheme_filter = "WHERE ec.conflict_scheme = ?" if scheme_name else ""

    sql_dis = f"""
        INSERT INTO conflict_disordertype_counts (conflict_id, disorder_type, n_events, fatalities)
        SELECT
            ec.conflict_id,
            TRIM(e.disorder_type) AS disorder_type,
            COUNT(DISTINCT ec.event_id_cnty) AS n_events,
            COALESCE(SUM(e.fatalities), 0) AS fatalities
        FROM event_conflict ec
        JOIN events e ON e.event_id_cnty = ec.event_id_cnty
        {scheme_filter}
        AND e.disorder_type IS NOT NULL AND TRIM(e.disorder_type) <> ''
        GROUP BY ec.conflict_id, TRIM(e.disorder_type);
    """

    sql_ev = f"""
        INSERT INTO conflict_eventtype_counts (conflict_id, event_type, n_events, fatalities)
        SELECT
            ec.conflict_id,
            TRIM(e.event_type) AS event_type,
            COUNT(DISTINCT ec.event_id_cnty) AS n_events,
            COALESCE(SUM(e.fatalities), 0) AS fatalities
        FROM event_conflict ec
        JOIN events e ON e.event_id_cnty = ec.event_id_cnty
        {scheme_filter}
        AND e.event_type IS NOT NULL AND TRIM(e.event_type) <> ''
        GROUP BY ec.conflict_id, TRIM(e.event_type);
    """

    if scheme_name:
        cur.execute(sql_ev, (scheme_name,))
        cur.execute(sql_dis, (scheme_name,))
    else:
        cur.execute(sql_ev.replace("WHERE ec.conflict_scheme = ?", "WHERE 1=1"))
        cur.execute(sql_dis.replace("WHERE ec.conflict_scheme = ?", "WHERE 1=1"))

    conn.commit()
    logger.info("Type counts rebuilt.")


# --------------------
# TIME FEATURES

def ensure_conflict_time_table(conn: sqlite3.Connection, logger):
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
    logger.info("conflict_time schema ensured.")


def rebuild_conflict_time(conn: sqlite3.Connection, logger):
    cur = conn.cursor()

    cur.execute("DELETE FROM conflict_time;")
    conn.commit()

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

    cur.execute("""
        UPDATE conflict_time
        SET
          duration_days = CAST((julianday(end_date) - julianday(start_date)) AS INTEGER),
          mid_date = date(julianday(start_date) + (julianday(end_date) - julianday(start_date)) / 2.0);
    """)
    conn.commit()

    logger.info("conflict_time rebuilt.")


def push_time_into_conflict_features(conn: sqlite3.Connection, logger):
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

    logger.info("Time columns pushed into conflict_features.")


def ensure_dashboard_indexes(conn: sqlite3.Connection, logger):
    cur = conn.cursor()
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{FEATURES_TABLE}_country ON {FEATURES_TABLE}(country);")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{FEATURES_TABLE}_actor1 ON {FEATURES_TABLE}(actor1);")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{FEATURES_TABLE}_start_date ON {FEATURES_TABLE}(start_date);")
    conn.commit()
    logger.info("Dashboard indexes ensured.")


# --------------------
# MAIN

def main():
    logger = init_logger("unique_conflicts")
    logger.info("Starting unique_conflicts script.")
    logger.info("Connecting to database at %s", DB_PATH)

    conn = get_db_connection(str(DB_PATH))

    try:
        create_unique_conflict_table(conn)
        ensure_indexes(conn, logger)
        populate_unique_conflict_table(conn, logger)

        ensure_conflict_type_count_tables(conn, logger)
        ensure_conflict_features_schema(conn, logger)

        rebuild_conflict_features_base(conn, logger)
        rebuild_conflict_type_counts(conn, logger)
        fill_event_type_modes(conn, logger)
        fill_assoc_actor_1_mode(conn, logger)

       

        ensure_conflict_time_table(conn, logger)
        rebuild_conflict_time(conn, logger)
        push_time_into_conflict_features(conn, logger)
        ensure_dashboard_indexes(conn, logger)

    except Exception:
        logger.exception("unique_conflicts script failed.")
        raise
    finally:
        conn.close()
        logger.info("Closed database connection.")



if __name__ == "__main__":
    main()
