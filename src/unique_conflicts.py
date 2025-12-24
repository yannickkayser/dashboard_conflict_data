# unique_conflicts
import logging
import time
from pathlib import Path
import re
from collections import Counter, defaultdict

from utils import get_db_connection, init_logger

# compute DB path relative to this file
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "conflict_data.db"

# ------------
# FINE TUNE KEYWORDS
TOKEN_RE = re.compile(r"[A-Za-z]{3,}")  # words >= 3 letters

STOPWORDS = {
    "the","a","an","and","or","to","of","in","on","for","with","at","by","from",
    "is","are","was","were","be","been","being","as","that","this","it","its",
    "their","they","them","he","she","his","her","you","we","our","us",
    "after","before","during","over","under","into","out","up","down",
    "near","around","about","between","within","across",
    "said","report","reports","according","allegedly",
    # common conflict/news filler (tune later)
    "killed","injured","attack","attacked","clash","clashes","protest","protests",
    "police","army","soldiers","people","civilians","forces","security",
    # added later after first review
    "against", "there", "demonstration", "protest", "demand", "members", "gathered", "demonstrators",
    "protestors", 
}

# ------------

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


def populate_unique_conflict_table(conn, logger, batch_size = 100):
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

    # 1) Get all conflict_ids
    logger.info("Fetching list of conflict_ids...")
    conflict_ids = [row[0] for row in cur.execute(
        "SELECT DISTINCT conflict_id FROM event_conflict ORDER BY conflict_id;"
    ).fetchall()]

    total = len(conflict_ids)
    logger.info("Found %d unique conflict_ids.", total)

    # 2) Process in batches so we can log progress during the expensive  work
    insert_sql = """ 
    INSERT INTO unique_conflict (conflict_id, n_events, total_fatalities)
    VALUES (?, ?, ?);
    """

    processed = 0
    for i in range(0, total, batch_size):
        batch = conflict_ids[i:i + batch_size]

        # Build placeholder for  the IN clause
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

        # This is the expensive part (but now only for a batch)
        rows = cur.execute(agg_sql, batch).fetchall()

        # Insert results for this batch
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



def ensure_conflict_features_schema(conn, logger):
    """
    Derived/enriched per-conflict features table.
    Rebuilt by unique_conflicts.py (safe to delete + recreate rows).
    """
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

                -- to be filled later
                assoc_actor_1 TEXT,
                top_keyword_1 TEXT,
                top_keyword_2 TEXT,
                top_keyword_3 TEXT
                );
    """)
    conn.commit()

    # Helpful Indexes
    # Helpful indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_features_conflict_id ON conflict_features(conflict_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_features_country ON conflict_features(country);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_features_actor1 ON conflict_features(actor1);")
    conn.commit()

    logger.info("conflict_features schema ensured.")


def rebuild_conflict_features_base(conn, logger):
    """
    Rebuild base rows:
    - 1 row per conflict_id (same set as unique_conflict)
    - bring in conflict_key from conflict_lookup
    - parse conflict_key into country/actor1/primary_assoc_actor_1
    """
    cur = conn.cursor()

    logger.info("Rebuilding conflict features base (clearing table)...")
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
    """
    Fill conflict_features.assoc_actor_1 as the most common (mode) assoc_actor_1
    across all events belonging to each conflict_id.

    If scheme_name is provided, restrict to that conflict_scheme.
    """
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


def fill_top_keywords_from_notes(conn, logger, scheme_name=None, top_n=3):
    """
    Compute top N keywords per conflict_id from all notes tokens.
    Writes into conflict_features.top_keyword_1..3 (N capped at 3 by schema).
    """
    top_n = max(1, min(int(top_n), 3))
    cur = conn.cursor()
    logger.info("Computing top %d keywords from notes%s ...",
                top_n, f" for scheme={scheme_name}" if scheme_name else "")

    if scheme_name:
        sql = """
            SELECT ec.conflict_id, e.notes
            FROM event_conflict ec
            JOIN events e ON e.event_id_cnty = ec.event_id_cnty
            WHERE ec.conflict_scheme = ?
              AND e.notes IS NOT NULL
              AND TRIM(e.notes) <> '';
        """
        rows = cur.execute(sql, (scheme_name,))
    else:
        sql = """
            SELECT ec.conflict_id, e.notes
            FROM event_conflict ec
            JOIN events e ON e.event_id_cnty = ec.event_id_cnty
            WHERE e.notes IS NOT NULL
              AND TRIM(e.notes) <> '';
        """
        rows = cur.execute(sql)

    counts = defaultdict(Counter)

    for conflict_id, notes in rows:
        text = str(notes).lower()
        for tok in TOKEN_RE.findall(text):
            if tok in STOPWORDS:
                continue
            counts[conflict_id][tok] += 1

    updates = []
    for conflict_id, counter in counts.items():
        top = [w for w, _ in counter.most_common(top_n)]
        while len(top) < 3:
            top.append(None)
        updates.append((top[0], top[1], top[2], conflict_id))

    logger.info("Writing top keywords for %d conflicts.", len(updates))
    cur.executemany("""
        UPDATE conflict_features
        SET top_keyword_1 = ?,
            top_keyword_2 = ?,
            top_keyword_3 = ?
        WHERE conflict_id = ?;
    """, updates)
    conn.commit()
    logger.info("Top keywords filled from notes.")



def main():
    logger = init_logger("unique_conflicts")
    logger.info("Starting unique_conflicts script.")
    logger.info("Connecting to database at %s", DB_PATH)

    conn = get_db_connection(str(DB_PATH))

    try:
        logger.info("Creating unique_conflict table if it does not exist...")
        create_unique_conflict_table(conn)

        ensure_indexes(conn, logger)

        logger.info("Populating unique_conflict table...")
        populate_unique_conflict_table(conn, logger)

        logger.info("Finished updating unique_conflict table.")

        ensure_conflict_features_schema(conn, logger)
        rebuild_conflict_features_base(conn, logger)
        fill_assoc_actor_1_mode(conn, logger)
        fill_top_keywords_from_notes(conn, logger, top_n=3)


    except Exception:
        logger.exception("unique_conflicts script failed.")
        raise

    finally:
        conn.close()
        logger.info("Closed database connection.")


if __name__ == "__main__":
    main()
