#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
from datetime import datetime, timedelta
import re
from collections import defaultdict

# -------------------------
# CONFIG (adjust as needed)
# -------------------------
GNEWS_DB = "data/gnews_articles_from2023.db"
CONFLICT_DB = "data/conflict_data.db"
OUT_DB = "data/article_conflict_matches.db"

ART_TABLE = "articles_eng"
ART_ID = "id"
ART_DATE = "publishedAt"          # ISO "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS"
ART_COUNTRY = "article_country"
ART_TERMS = "tfidf_terms_en"       # if you use "..._en" instead, change here

CON_TABLE = "conflict_features"      # visible in your screenshot [file:5][file:6]
CON_ID = "conflict_id"
CON_COUNTRY = "country"
CON_START = "start_date"           # "YYYY-MM-DD"
CON_END = "end_date"               # "YYYY-MM-DD"
CON_TERMS = "tfidf_terms_en"       # visible in your screenshot [file:5][file:6]

# Matching rules
WINDOW_DAYS = 3
MIN_OVERLAP = 1         # keyword gate: 1–3 recommended; start with 1
TOPK_PER_ARTICLE = 5    # store top-k matches per article
SAMPLE_FOR_THRESHOLD = 200000  # number of candidate scores sampled for threshold estimation (speed)

# Israel/Palestine region mapping (allow both directions)
REGION_EQUIV = {
    "Israel": {"Israel", "Palestine"},
    "Palestine": {"Israel", "Palestine"},
    # optional if your country field uses these
    "Gaza Strip": {"Israel", "Palestine", "Gaza Strip"},
    "West Bank": {"Israel", "Palestine", "West Bank"},
}

# -------------------------
# Helpers
# -------------------------
_term_splitter = re.compile(r"[\s,;|]+")

def to_yyyy_mm_dd(s: str) -> str:
    """Return YYYY-MM-DD."""
    if s is None:
        return None
    s = s.strip()
    if len(s) >= 10:
        return s[:10]
    return s

def norm_country(c: str) -> str:
    return (c or "").strip()

def parse_terms(s: str):
    """
    Your tfidf_terms columns look like comma/whitespace separated tokens (screenshot). [file:5][file:6]
    If your format is JSON or 'token:weight', adjust this function.
    """
    if not s:
        return []
    parts = [p.strip().lower() for p in _term_splitter.split(s) if p.strip()]
    cleaned = []
    for t in parts:
        t = re.sub(r"[^a-z0-9äöüß\-]+", "", t)
        if t:
            cleaned.append(t)

    # unique, keep order
    seen = set()
    out = []
    for t in cleaned:
        if t not in seen:
            out.append(t)
            seen.add(t)
    return out

def allowed_countries(article_country: str):
    c = norm_country(article_country)
    return REGION_EQUIV.get(c, {c})

# -------------------------
# Build conflict indices (in RAM)
# -------------------------
def load_conflicts():
    con = sqlite3.connect(CONFLICT_DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    q = f"""
    SELECT {CON_ID} AS cid,
           {CON_COUNTRY} AS country,
           {CON_START} AS start_date,
           {CON_END} AS end_date,
           {CON_TERMS} AS terms
    FROM {CON_TABLE}
    WHERE {CON_COUNTRY} IS NOT NULL
      AND {CON_START} IS NOT NULL
      AND {CON_END} IS NOT NULL
    """
    rows = cur.execute(q).fetchall()
    con.close()

    # Index: country -> list of conflicts (cid, start, end, termset)
    by_country = defaultdict(list)
    for r in rows:
        c = norm_country(r["country"])
        sd = to_yyyy_mm_dd(r["start_date"])
        ed = to_yyyy_mm_dd(r["end_date"])
        termset = set(parse_terms(r["terms"]))
        by_country[c].append((r["cid"], sd, ed, termset))
    return by_country

# -------------------------
# Output DB
# -------------------------
def init_out_db():
    out = sqlite3.connect(OUT_DB)
    cur = out.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS matches (
        article_id TEXT,
        conflict_id INTEGER,
        score REAL,
        overlap INTEGER,
        article_date TEXT,
        article_country TEXT,
        PRIMARY KEY(article_id, conflict_id)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_article ON matches(article_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_conflict ON matches(conflict_id)")
    out.commit()
    return out

# -------------------------
# Main matching
# -------------------------
def main():
    conflicts_by_country = load_conflicts()

    g = sqlite3.connect(GNEWS_DB)
    g.row_factory = sqlite3.Row
    gcur = g.cursor()

    out = init_out_db()
    ocur = out.cursor()

    # Speed pragmas
    for db in (g, out):
        c = db.cursor()
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA synchronous=NORMAL;")
        c.execute("PRAGMA temp_store=MEMORY;")
        c.execute("PRAGMA cache_size=-200000;")  # ~200MB cache (optional)

    # Stream articles
    q = f"""
    SELECT {ART_ID} AS aid,
           {ART_DATE} AS published_at,
           {ART_COUNTRY} AS country,
           {ART_TERMS} AS terms
    FROM {ART_TABLE}
    WHERE {ART_DATE} IS NOT NULL
      AND {ART_COUNTRY} IS NOT NULL
    """
    gcur.execute(q)

    insert_buf = []
    n_articles = 0
    n_matched = 0

    # collect candidate scores for threshold estimation (stream sample)
    sampled_scores = []

    while True:
        batch = gcur.fetchmany(5000)
        if not batch:
            break

        for r in batch:
            n_articles += 1
            aid = str(r["aid"])
            adate = to_yyyy_mm_dd(r["published_at"])
            acountry = norm_country(r["country"])
            aterms_list = parse_terms(r["terms"])
            if not adate or not acountry or not aterms_list:
                continue

            aterms = set(aterms_list)

            # time window for candidate generation
            dt = datetime.strptime(adate, "%Y-%m-%d")
            lo = (dt - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
            hi = (dt + timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")

            # country hard filter (with Israel/Palestine mapping)
            cand_countries = allowed_countries(acountry)

            candidates = []
            for cc in cand_countries:
                for (cid, sd, ed, cterms) in conflicts_by_country.get(cc, []):
                    # interval overlap: conflict [sd,ed] intersects [lo,hi]
                    if not (ed < lo or sd > hi):
                        # keyword gate
                        overlap = len(aterms.intersection(cterms))
                        if overlap >= MIN_OVERLAP:
                            # score: normalized overlap by number of article terms (max ~20) -> 0..1
                            score = overlap / max(1, len(aterms))
                            candidates.append((cid, score, overlap))

            if not candidates:
                continue

            # keep top-k per article
            candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
            top = candidates[:TOPK_PER_ARTICLE]

            for cid, score, overlap in top:
                insert_buf.append((aid, int(cid), float(score), int(overlap), adate, acountry))
                if len(sampled_scores) < SAMPLE_FOR_THRESHOLD:
                    sampled_scores.append(score)

            n_matched += 1

        if insert_buf:
            ocur.executemany(
                "INSERT OR REPLACE INTO matches(article_id, conflict_id, score, overlap, article_date, article_country) VALUES (?,?,?,?,?,?)",
                insert_buf
            )
            out.commit()
            insert_buf.clear()

        if n_articles % 50000 == 0:
            print(f"Processed {n_articles:,} articles | matched {n_matched:,}")

    g.close()

    # Threshold: data-driven based on the observed candidate score distribution
    # simple: use e.g. 90th percentile as "matched", 95th as "high confidence"
    sampled_scores.sort()

    def qtile(p):
        if not sampled_scores:
            return None
        idx = int(p * (len(sampled_scores) - 1))
        return sampled_scores[idx]

    thr_90 = qtile(0.90)
    thr_95 = qtile(0.95)

    # store thresholds
    cur = out.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS thresholds (
        name TEXT PRIMARY KEY,
        value REAL
    )
    """)
    cur.execute("INSERT OR REPLACE INTO thresholds(name,value) VALUES (?,?)", ("score_p90", thr_90))
    cur.execute("INSERT OR REPLACE INTO thresholds(name,value) VALUES (?,?)", ("score_p95", thr_95))
    out.commit()
    out.close()

    print("Done.")
    print(f"Suggested thresholds: score>=p90={thr_90} | score>=p95={thr_95}")

if __name__ == "__main__":
    main()

