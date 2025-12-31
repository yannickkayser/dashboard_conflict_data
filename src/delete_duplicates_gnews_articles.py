#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import re
from collections import defaultdict

# pip install simhash
from simhash import Simhash

DB_PATH = "/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/data/gnews_articles_from2023.db"

SRC_TABLE = "articles"
DEDUP_TABLE = "articles_without_duplicates"

URL_COL = "url"
TITLE_COL = "title"
DESC_COL = "description"
DATE_COL = "publishedAt"   # used only for tie-break, keep newer
ID_COL = "id"              # optional

# For testing:
LIMIT_N = None  # e.g. 400, or None for all rows

# Similarity threshold:
SIMHASH_DISTANCE = 3  # 2-4 typical; smaller => stricter [web:1128]

# Bucketing:
# 64-bit SimHash -> split into 4 chunks of 16 bits
NUM_BANDS = 4
BAND_BITS = 16
MASK_16 = (1 << BAND_BITS) - 1


_ws = re.compile(r"\s+")

def norm_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("#", 1)[0]
    u = u.rstrip("/")
    return u

def norm_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower().strip()
    s = _ws.sub(" ", s)
    return s

def fp_text(title: str, desc: str) -> str:
    return norm_text((title or "") + " " + (desc or ""))

def better(a: dict, b: dict) -> dict:
    # keep the article that appeared first (earliest publishedAt)
    da = (a.get(DATE_COL) or "").strip()
    db = (b.get(DATE_COL) or "").strip()

    # normalize typical formats by using the leading ISO part
    # e.g. "2024-01-02T13:04:05Z" -> "2024-01-02T13:04:05"
    da_key = da[:19] if len(da) >= 19 else da
    db_key = db[:19] if len(db) >= 19 else db

    # If one is missing, keep the one that has a date
    if da_key and not db_key:
        return a
    if db_key and not da_key:
        return b

    # If both missing, fall back to "more informative text"
    if not da_key and not db_key:
        ta = fp_text(a.get(TITLE_COL), a.get(DESC_COL))
        tb = fp_text(b.get(TITLE_COL), b.get(DESC_COL))
        return a if len(ta) >= len(tb) else b

    # earliest wins
    return a if da_key <= db_key else b


def band_keys(h: int):
    # produce (band_index, band_value) keys
    # band0=lowest 16 bits, band1=next, ...
    keys = []
    x = h
    for b in range(NUM_BANDS):
        keys.append((b, x & MASK_16))
        x >>= BAND_BITS
    return keys

def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # schema columns (to create identical output schema)
    colinfo = cur.execute(f"PRAGMA table_info({SRC_TABLE});").fetchall()
    cols = [c[1] for c in colinfo]

    limit_sql = f"LIMIT {int(LIMIT_N)}" if LIMIT_N else ""
    rows = cur.execute(
        f"""
        SELECT *
        FROM {SRC_TABLE}
        WHERE ({URL_COL} IS NOT NULL AND TRIM({URL_COL}) <> '')
           OR ({TITLE_COL} IS NOT NULL AND TRIM({TITLE_COL}) <> '')
           OR ({DESC_COL} IS NOT NULL AND TRIM({DESC_COL}) <> '')
        {limit_sql}
        """
    ).fetchall()

    # -------------------------
    # Pass 1: URL dedup
    # -------------------------
    by_url = {}
    no_url = []

    for r in rows:
        d = dict(r)
        u = norm_url(d.get(URL_COL))
        if u:
            if u in by_url:
                by_url[u] = better(by_url[u], d)
            else:
                by_url[u] = d
        else:
            no_url.append(d)

    candidates = list(by_url.values()) + no_url

    # -------------------------
    # Pass 2: Near-dup by SimHash + bucketing
    # -------------------------
    buckets = defaultdict(list)  # (band_index, band_value) -> list of indices in kept[]
    kept = []                    # list of dict rows
    kept_hash = []               # list of simhash ints (parallel)

    n_near_dups = 0

    for d in candidates:
        text = fp_text(d.get(TITLE_COL), d.get(DESC_COL))
        if not text:
            kept.append(d)
            kept_hash.append(None)
            continue

        h = Simhash(text).value

        # gather candidate indices from buckets
        cand_idx = set()
        for k in band_keys(h):
            for idx in buckets.get(k, []):
                cand_idx.add(idx)

        # verify near-dup via hamming distance
        dup_of = None
        for idx in cand_idx:
            h2 = kept_hash[idx]
            if h2 is None:
                continue
            if Simhash(h).distance(Simhash(h2)) <= SIMHASH_DISTANCE:  # [web:1128]
                dup_of = idx
                break

        if dup_of is None:
            new_idx = len(kept)
            kept.append(d)
            kept_hash.append(h)
            for k in band_keys(h):
                buckets[k].append(new_idx)
        else:
            n_near_dups += 1
            # keep better representative
            kept[dup_of] = better(kept[dup_of], d)

    # -------------------------
    # Write output
    # -------------------------
    cur.execute(f"DROP TABLE IF EXISTS {DEDUP_TABLE};")
    cur.execute(f"CREATE TABLE {DEDUP_TABLE} AS SELECT * FROM {SRC_TABLE} WHERE 0;")
    con.commit()

    placeholders = ",".join(["?"] * len(cols))
    cur.executemany(
        f"INSERT INTO {DEDUP_TABLE} ({', '.join(cols)}) VALUES ({placeholders});",
        [tuple(r.get(c) for c in cols) for r in kept],
    )
    con.commit()

    print(f"Loaded rows: {len(rows):,}")
    print(f"After URL dedup + near-dup: kept={len(kept):,}")
    print(f"Near-duplicates merged (pass2): {n_near_dups:,}")
    print(f"Output table: {DEDUP_TABLE}")

    con.close()

if __name__ == "__main__":
    main()
