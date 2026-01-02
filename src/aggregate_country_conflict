# conflict_country.py
import sqlite3
from pathlib import Path
from collections import Counter, defaultdict

from utils import get_db_connection, init_logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "conflict_data.db"

FEATURES_TABLE = "conflict_features"
COUNTRY_TABLE = "conflict_country"


def norm_text(x: object) -> str | None:
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    if s.upper() == "NA":
        return None
    return s


def join_unique(values, sep=" | ") -> str | None:
    # stabil + dedupliziert (Einfügereihenfolge)
    seen = set()
    out = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return sep.join(out) if out else None


def format_topk(counter: Counter, k: int) -> str | None:
    top = counter.most_common(k)  # (value, count) [web:28]
    if not top:
        return None
    return " | ".join([f"{v} ({c})" for v, c in top])


def ensure_conflict_country_table(conn: sqlite3.Connection, logger, topk_actor1=5, topk_eventmode=3) -> None:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    logger.info("Dropping %s if exists...", COUNTRY_TABLE)
    cur.execute(f"DROP TABLE IF EXISTS {COUNTRY_TABLE};")
    conn.commit()

    logger.info("Reading source rows from %s...", FEATURES_TABLE)
    rows = cur.execute(f"""
        SELECT
            country,
            COALESCE(n_events, 0) AS n_events,
            COALESCE(total_fatalities, 0) AS total_fatalities,
            actor1,
            primary_assoc_actor_1,
            event_type_mode
        FROM {FEATURES_TABLE}
        WHERE country IS NOT NULL AND TRIM(country) <> '';
    """).fetchall()

    logger.info("Aggregating in Python (countries=%d source rows=%d)...",
                len({r["country"] for r in rows}), len(rows))

    # Aggregations
    sum_events = defaultdict(int)
    sum_fatals = defaultdict(int)
    n_rows = defaultdict(int)

    actor1_vals = defaultdict(list)
    primary_vals = defaultdict(list)

    actor1_counter = defaultdict(Counter)
    eventmode_counter = defaultdict(Counter)

    for r in rows:
        ctry = r["country"].strip()
        sum_events[ctry] += int(r["n_events"] or 0)
        sum_fatals[ctry] += int(r["total_fatalities"] or 0)
        n_rows[ctry] += 1

        a1 = norm_text(r["actor1"])
        if a1:
            actor1_vals[ctry].append(a1)
            actor1_counter[ctry][a1] += 1

        p1 = norm_text(r["primary_assoc_actor_1"])
        if p1:
            primary_vals[ctry].append(p1)

        etm = norm_text(r["event_type_mode"])
        if etm:
            eventmode_counter[ctry][etm] += 1

    logger.info("Creating %s table schema...", COUNTRY_TABLE)
    cur.execute(f"""
        CREATE TABLE {COUNTRY_TABLE}(
            country TEXT PRIMARY KEY,
            n_events INTEGER,
            total_fatalities INTEGER,
            n_conflict_rows INTEGER,

            actor1_unique TEXT,
            primary_assoc_actor_1_unique TEXT,

            top5_actor1 TEXT,
            top3_event_type_mode TEXT,

            top1_actor1 TEXT,
            top1_actor1_count INTEGER,
            top1_event_type_mode TEXT,
            top1_event_type_mode_count INTEGER,

            n_unique_actor1 INTEGER,
            n_unique_primary_assoc_actor_1 INTEGER,
            n_unique_event_type_mode INTEGER,

            fatalities_per_event REAL
        );
    """)
    conn.commit()

    logger.info("Inserting aggregated rows into %s...", COUNTRY_TABLE)
    insert_sql = f"""
        INSERT INTO {COUNTRY_TABLE} (
            country, n_events, total_fatalities, n_conflict_rows,
            actor1_unique, primary_assoc_actor_1_unique,
            top5_actor1, top3_event_type_mode,
            top1_actor1, top1_actor1_count,
            top1_event_type_mode, top1_event_type_mode_count,
            n_unique_actor1, n_unique_primary_assoc_actor_1, n_unique_event_type_mode,
            fatalities_per_event
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    countries = sorted(sum_events.keys())
    payload = []
    for ctry in countries:
        n_ev = sum_events[ctry]
        n_fa = sum_fatals[ctry]

        a1_unique = join_unique(actor1_vals[ctry])
        p1_unique = join_unique(primary_vals[ctry])

        top_actor1 = format_topk(actor1_counter[ctry], topk_actor1)
        top_eventm = format_topk(eventmode_counter[ctry], topk_eventmode)

        top1_a1 = None
        top1_a1_cnt = None
        mc_a1 = actor1_counter[ctry].most_common(1)  # [web:28]
        if mc_a1:
            top1_a1, top1_a1_cnt = mc_a1[0]

        top1_em = None
        top1_em_cnt = None
        mc_em = eventmode_counter[ctry].most_common(1)  # [web:28]
        if mc_em:
            top1_em, top1_em_cnt = mc_em[0]

        n_unique_a1 = len(actor1_counter[ctry])
        n_unique_p1 = len(set(primary_vals[ctry]))
        n_unique_em = len(eventmode_counter[ctry])

        fpe = (float(n_fa) / float(n_ev)) if n_ev > 0 else None

        payload.append((
            ctry, n_ev, n_fa, n_rows[ctry],
            a1_unique, p1_unique,
            top_actor1, top_eventm,
            top1_a1, top1_a1_cnt,
            top1_em, top1_em_cnt,
            n_unique_a1, n_unique_p1, n_unique_em,
            fpe
        ))

    cur.executemany(insert_sql, payload)
    conn.commit()

    logger.info("Creating indexes...")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{COUNTRY_TABLE}_n_events ON {COUNTRY_TABLE}(n_events);")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{COUNTRY_TABLE}_fatalities ON {COUNTRY_TABLE}(total_fatalities);")
    conn.commit()

    logger.info("Done. Inserted %d countries.", len(payload))


def main():
    logger = init_logger("conflict_country")
    logger.info("Starting conflict_country build.")
    logger.info("DB: %s", DB_PATH)

    conn = get_db_connection(str(DB_PATH))
    try:
        ensure_conflict_country_table(conn, logger, topk_actor1=5, topk_eventmode=3)
    except Exception:
        logger.exception("conflict_country failed.")
        raise
    finally:
        conn.close()
        logger.info("Closed database connection.")


if __name__ == "__main__":
    main()
