#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # .../dashboard_conflict_data
DB_PATH = PROJECT_ROOT / "data" / "conflict_data.db"

SRC_TABLE = "conflict_features"
DST_TABLE = "conflict_features_coarse"

# group key (point 1)
GROUP_COLS = ["country", "actor1"]  # collapse many Protesters(...) variants etc.

# tfidf union control
MAX_TERMS_OUT = 80  # keep at most N unique tokens in tfidf_terms_conflict output
_term_splitter = re.compile(r"[\s,;|]+")


def table_cols(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.cursor()
    rows = cur.execute(f"PRAGMA table_info({table});").fetchall()
    return [r[1] for r in rows]


def parse_terms(s: str) -> list[str]:
    if not s:
        return []
    parts = [p.strip().lower() for p in _term_splitter.split(str(s)) if p.strip()]
    out = []
    seen = set()
    for t in parts:
        t = re.sub(r"[^a-z0-9äöüß\-]+", "", t)
        if t and t not in seen:
            out.append(t)
            seen.add(t)
    return out


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cols = table_cols(conn, SRC_TABLE)
    cols_set = set(cols)

    # ensure group cols exist
    for c in GROUP_COLS:
        if c not in cols_set:
            raise RuntimeError(f"Missing grouping column '{c}' in {SRC_TABLE}")

    # columns we try to aggregate if present
    has_n_events = "n_events" in cols_set
    has_fat = "total_fatalities" in cols_set
    has_start = "start_date" in cols_set
    has_end = "end_date" in cols_set
    has_dur = "duration_days" in cols_set
    has_mid = "mid_date" in cols_set
    has_tfidf = "tfidf_terms_conflict" in cols_set

    # 1) create destination table with same schema (column names)
    cur.execute(f"DROP TABLE IF EXISTS {DST_TABLE};")

    # copies schema (columns/types/defaults) from SRC by creating empty select
    cur.execute(f"CREATE TABLE {DST_TABLE} AS SELECT * FROM {SRC_TABLE} WHERE 0;")
    conn.commit()

    # 2) Build representative row per group using window function
    # We pick row with highest n_events, then total_fatalities, then (end-start), then smallest conflict_id
    # If columns are missing, we fall back gracefully.
    order_parts = []
    if has_n_events:
        order_parts.append("COALESCE(n_events, 0) DESC")
    if has_fat:
        order_parts.append("COALESCE(total_fatalities, 0) DESC")
    if has_start and has_end:
        order_parts.append("(julianday(end_date) - julianday(start_date)) DESC")
    order_parts.append("conflict_id ASC")
    order_by = ", ".join(order_parts)

    # We'll compute aggregates in a separate query and join by group key.
    group_key_sql = ", ".join(GROUP_COLS)

    # aggregate pieces
    agg_select = [group_key_sql]

    if has_n_events:
        agg_select.append("SUM(COALESCE(n_events,0)) AS n_events")
    if has_fat:
        agg_select.append("SUM(COALESCE(total_fatalities,0)) AS total_fatalities")
    if has_start:
        agg_select.append("MIN(start_date) AS start_date")
    if has_end:
        agg_select.append("MAX(end_date) AS end_date")

    agg_sql = f"""
    WITH agg AS (
        SELECT {", ".join(agg_select)}
        FROM {SRC_TABLE}
        GROUP BY {group_key_sql}
    ),
    rep AS (
        SELECT
            *,
            ROW_NUMBER() OVER (
                PARTITION BY {group_key_sql}
                ORDER BY {order_by}
            ) AS rn
        FROM {SRC_TABLE}
    )
    SELECT rep.*, agg.*
    FROM rep
    JOIN agg
      USING ({group_key_sql})
    WHERE rep.rn = 1;
    """

    rows = cur.execute(agg_sql).fetchall()

    # 3) insert into destination, but ensure conflict_id unique:
    # we will create new conflict_id values to avoid collisions, while keeping same column name.
    # (This is important because table has PRIMARY KEY conflict_id in original schema.)
    # We'll just number them from 1..K in insertion order.
    out_cols = cols[:]  # exactly same names

    # Prepare inserts
    inserts = []
    new_id = 0
    for r in rows:
        new_id += 1
        rec = dict(r)

        # overwrite conflict_id with new coarse id
        rec["conflict_id"] = new_id

        # conflict_key: make it explicit this is coarse
        # keep same column name, but change value
        if "conflict_key" in cols_set:
            # country|actor1|COARSE
            ctry = (rec.get("country") or "").strip()
            a1 = (rec.get("actor1") or "").strip()
            rec["conflict_key"] = f"{ctry}|{a1}|COARSE"

        # duration/mid_date recompute if columns exist
        if has_start and has_end and has_dur:
            try:
                # leave SQLite to compute later is also fine; here keep it simple:
                # duration_days = julianday(end)-julianday(start)
                pass
            except Exception:
                pass

        inserts.append(tuple(rec.get(c) for c in out_cols))

    placeholders = ",".join(["?"] * len(out_cols))
    cur.executemany(
        f"INSERT INTO {DST_TABLE} ({', '.join(out_cols)}) VALUES ({placeholders})",
        inserts
    )
    conn.commit()

    # 4) tfidf_terms_conflict union per group (optional but recommended)
    if has_tfidf:
        # build union in python from original rows grouped by country+actor1
        # then update dst table by its coarse key (country,actor1)
        src = cur.execute(
            f"SELECT {group_key_sql}, tfidf_terms_conflict FROM {SRC_TABLE} "
            f"WHERE tfidf_terms_conflict IS NOT NULL AND TRIM(tfidf_terms_conflict) <> ''"
        ).fetchall()

        union = {}
        for rr in src:
            key = tuple((rr[c] or "").strip() for c in GROUP_COLS)
            toks = parse_terms(rr["tfidf_terms_conflict"])
            if key not in union:
                union[key] = []
            # append preserving order
            seen = set(union[key])
            for t in toks:
                if t not in seen:
                    union[key].append(t)
                    seen.add(t)

        # update dst rows
        for key, toks in union.items():
            toks = toks[:MAX_TERMS_OUT]
            tf = ",".join(toks) if toks else None
            where = " AND ".join([f"{c} = ?" for c in GROUP_COLS])
            cur.execute(
                f"UPDATE {DST_TABLE} SET tfidf_terms_conflict=? WHERE {where}",
                (tf, *key)
            )
        conn.commit()

    # Helpful indexes (optional)
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{DST_TABLE}_country ON {DST_TABLE}(country);")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{DST_TABLE}_actor1 ON {DST_TABLE}(actor1);")
    conn.commit()

    print(f"OK: created {DST_TABLE} from {SRC_TABLE}")
    print(f"Rows: {cur.execute(f'SELECT COUNT(*) FROM {DST_TABLE}').fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    main()
