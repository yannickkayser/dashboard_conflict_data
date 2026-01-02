#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import re
from collections import defaultdict
from pathlib import Path
import time

from simhash import Simhash

# -------------------------
# Paths / Names
# -------------------------
SRC_DB_PATH = "/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/data/gnews_articles_from2023.db"

# new output DB file:
OUT_DB_PATH = "/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/data/deleted_dupgnews2023.db"

SRC_TABLE = "articles"
DEDUP_TABLE = "article_without_duplicates"  # per your request

URL_COL = "url"
TITLE_COL = "title"
DESC_COL = "description"
DATE_COL = "publishedAt"  # earliest wins
ID_COL = "id"  # optional

LIMIT_N = None  # e.g. 400 for testing, or None for all rows

SIMHASH_DISTANCE = 2

# Bucketing: 64-bit SimHash -> split into 4 chunks of 16 bits
NUM_BANDS = 4
BAND_BITS = 16
MASK_16 = (1 << BAND_BITS) - 1

# Progress prints (terminal only)
PROGRESS_EVERY = 10_000

_ws = re.compile(r"\s+")


def fmt_secs(s: float) -> str:
    s = max(0.0, float(s))
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}h {m:02d}m {sec:02d}s"
    if m:
        return f"{m:d}m {sec:02d}s"
    return f"{sec:d}s"


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
    t0 = time.perf_counter()
    print("Starting dedup (terminal progress only)...", flush=True)
    print("SRC_DB_PATH:", SRC_DB_PATH, flush=True)
    print("OUT_DB_PATH:", OUT_DB_PATH, flush=True)
    print("SRC_TABLE:", SRC_TABLE, "| OUT_TABLE:", DEDUP_TABLE, flush=True)
    print("SIMHASH_DISTANCE:", SIMHASH_DISTANCE, "| PROGRESS_EVERY:", PROGRESS_EVERY, flush=True)
    if LIMIT_N:
        print("LIMIT_N:", LIMIT_N, flush=True)

    src = sqlite3.connect(SRC_DB_PATH)
    src.row_factory = sqlite3.Row
    cur = src.cursor()

    # Ensure output dir exists
    Path(OUT_DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    # (Re)create output DB file cleanly
    out_path = Path(OUT_DB_PATH)
    if out_path.exists():
        out_path.unlink()

    # Create the file immediately so you can see it even while processing
    sqlite3.connect(str(out_path)).close()
    print("Created output DB file:", str(out_path), flush=True)

    # Attach new DB file
    cur.execute("ATTACH DATABASE ? AS outdb;", (str(out_path),))
    print("Attached outdb", flush=True)

    # schema columns
    colinfo = cur.execute(f"PRAGMA table_info({SRC_TABLE});").fetchall()
    cols = [c[1] for c in colinfo]
    print(f"Detected {len(cols)} columns in {SRC_TABLE}", flush=True)

    # -------------------------
    # Load rows
    # -------------------------
    t_load0 = time.perf_counter()
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
    print(f"Loaded rows: {len(rows):,} | load_time={fmt_secs(time.perf_counter()-t_load0)}", flush=True)

    # -------------------------
    # Pass 1: URL dedup
    # -------------------------
    t_p10 = time.perf_counter()
    by_url = {}
    no_url = []

    for r in rows:
        d = dict(r)
        u = norm_url(d.get(URL_COL))
        if u:
            by_url[u] = better(by_url[u], d) if u in by_url else d
        else:
            no_url.append(d)

    candidates = list(by_url.values()) + no_url
    print(
        f"Pass1 done | unique_urls={len(by_url):,} | no_url={len(no_url):,} | candidates={len(candidates):,} "
        f"| time={fmt_secs(time.perf_counter()-t_p10)}",
        flush=True,
    )

    # -------------------------
    # Pass 2: Near-dup by SimHash + bucketing
    # -------------------------
    t_p20 = time.perf_counter()
    total = len(candidates)
    print(f"Pass2 start | candidates={total:,}", flush=True)

    buckets = defaultdict(list)  # (band_index, band_value) -> indices in kept
    kept = []
    kept_hash = []
    n_near_dups = 0
    processed = 0

    for d in candidates:
        processed += 1

        text = fp_text(d.get(TITLE_COL), d.get(DESC_COL))
        if not text:
            kept.append(d)
            kept_hash.append(None)
        else:
            h = Simhash(text).value

            cand_idx = set()
            for k in band_keys(h):
                for idx in buckets.get(k, []):
                    cand_idx.add(idx)

            dup_of = None
            for idx in cand_idx:
                h2 = kept_hash[idx]
                if h2 is None:
                    continue
                if Simhash(h).distance(Simhash(h2)) <= SIMHASH_DISTANCE:
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
                kept[dup_of] = better(kept[dup_of], d)

        if processed % PROGRESS_EVERY == 0:
            elapsed = time.perf_counter() - t_p20
            rate = processed / max(elapsed, 1e-9)
            eta = (total - processed) / max(rate, 1e-9)
            print(
                f"Pass2 {processed:,}/{total:,} ({processed/total:.1%}) | "
                f"kept={len(kept):,} | near_dups={n_near_dups:,} | "
                f"rate={rate:,.0f}/s | ETA={fmt_secs(eta)}",
                flush=True,
            )

    print(
        f"Pass2 done | kept={len(kept):,} | near_dups={n_near_dups:,} | time={fmt_secs(time.perf_counter()-t_p20)}",
        flush=True,
    )

    # -------------------------
    # Write output into NEW DB (outdb)
    # -------------------------
    t_w0 = time.perf_counter()
    cur.execute(f"DROP TABLE IF EXISTS outdb.{DEDUP_TABLE};")
    cur.execute(f"CREATE TABLE outdb.{DEDUP_TABLE} AS SELECT * FROM main.{SRC_TABLE} WHERE 0;")
    src.commit()

    placeholders = ",".join(["?"] * len(cols))
    cur.executemany(
        f"INSERT INTO outdb.{DEDUP_TABLE} ({', '.join(cols)}) VALUES ({placeholders});",
        [tuple(r.get(c) for c in cols) for r in kept],
    )
    src.commit()

    print(f"Write done | time={fmt_secs(time.perf_counter()-t_w0)}", flush=True)

    print(f"Loaded rows: {len(rows):,}", flush=True)
    print(f"After URL dedup + near-dup: kept={len(kept):,}", flush=True)
    print(f"Near-duplicates merged (pass2): {n_near_dups:,}", flush=True)
    print(f"Output DB: {OUT_DB_PATH}", flush=True)
    print(f"Output table: {DEDUP_TABLE}", flush=True)
    print(f"Total elapsed: {fmt_secs(time.perf_counter()-t0)}", flush=True)

    cur.execute("DETACH DATABASE outdb;")
    src.close()


if __name__ == "__main__":
    main()
