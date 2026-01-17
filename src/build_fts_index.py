from pathlib import Path
import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "conflict_data.db"

def build_fts():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Sanity: ensure FTS5 is available
    cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_check USING fts5(x);")
    cur.execute("DROP TABLE IF EXISTS _fts5_check;")

    # Sanity: ensure rowid exists on events
    cur.execute("SELECT rowid FROM events LIMIT 1;")

    print("Creating FTS5 table (events_fts) linked via events.rowid ...")
    cur.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS events_fts
        USING fts5(
            notes,
            country,
            content='events',
            content_rowid='rowid'
        );
    """)

    print("Backfilling FTS index from events...")
    cur.execute("""
        INSERT INTO events_fts(rowid, notes, country)
        SELECT e.rowid, e.notes, e.country
        FROM events e
        WHERE e.notes IS NOT NULL AND e.notes <> ''
          AND e.rowid NOT IN (SELECT rowid FROM events_fts);
    """)

    print("Creating triggers to keep events_fts in sync...")
    cur.execute("DROP TRIGGER IF EXISTS events_ai;")
    cur.execute("DROP TRIGGER IF EXISTS events_ad;")
    cur.execute("DROP TRIGGER IF EXISTS events_au;")

    cur.execute("""
        CREATE TRIGGER events_ai AFTER INSERT ON events BEGIN
          INSERT INTO events_fts(rowid, notes, country)
          VALUES (new.rowid, new.notes, new.country);
        END;
    """)

    cur.execute("""
        CREATE TRIGGER events_ad AFTER DELETE ON events BEGIN
          INSERT INTO events_fts(events_fts, rowid, notes, country)
          VALUES('delete', old.rowid, old.notes, old.country);
        END;
    """)

    cur.execute("""
        CREATE TRIGGER events_au AFTER UPDATE ON events BEGIN
          INSERT INTO events_fts(events_fts, rowid, notes, country)
          VALUES('delete', old.rowid, old.notes, old.country);
          INSERT INTO events_fts(rowid, notes, country)
          VALUES (new.rowid, new.notes, new.country);
        END;
    """)

# Maintenance step so FTS index is prevented from being staled.
    cur.execute("INSERT INTO events_fts(events_fts) VALUES('rebuild');")
    cur.execute("INSERT INTO events_fts(events_fts) VALUES('optimize');")

    con.commit()
    con.close()
    print("Done. FTS index ready.")

if __name__ == "__main__":
    build_fts()
