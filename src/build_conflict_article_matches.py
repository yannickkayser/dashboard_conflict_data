import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Optional, Dict, List, Tuple

# ----------------
# Paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFLICT_DB = PROJECT_ROOT / "data" / "conflict_data.db"
GNEWS_DB = PROJECT_ROOT / "data" / "gnews_articles_from2023.db"

# Tables
CONFLICT_TABLE = "conflict_features"
ART_TABLE = "articles_eng"

# Output tables (in conflict_db)
T_BEST = "conflict_article_bestmatch"          # optional: 1-best per conflict
T_WIDE = "conflict_article_bestmatch_wide"     # required: MANY-to-MANY wide output

# Hard match window
EXTRA_DAYS = 2

# Scoring knobs
ACTOR_WEIGHT = 2
KW_WEIGHT = 1
MIN_ACTOR_LEN = 4

# Threshold: keep every (conflict, article) with total_score >= threshold
MATCH_THRESHOLD = 2


# ----------------
# Text helpers
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: Optional[str]) -> str:
    return strip_accents((s or "").lower()).strip()


def collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def boundary_regex(term_norm: str) -> re.Pattern:
    # boundary-ish match to reduce substring false positives (e.g., "iran" in "tirana")
    return re.compile(r"(?<![a-z])" + re.escape(term_norm) + r"(?![a-z])")


def actor_terms(c: sqlite3.Row) -> List[str]:
    terms = [norm(c["actor1"]), norm(c["primary_assoc_actor_1"]), norm(c["assoc_actor_1"])]
    return [t for t in terms if t and len(t) >= MIN_ACTOR_LEN]


def kw_terms_conflict(c: sqlite3.Row) -> List[str]:
    terms = [norm(c["top_keyword_1"]), norm(c["top_keyword_2"]), norm(c["top_keyword_3"])]
    return [t for t in terms if t]


def kw_terms_article(a: sqlite3.Row) -> List[str]:
    terms = [norm(a["kw_1"]), norm(a["kw_2"]), norm(a["kw_3"])]
    return [t for t in terms if t]


def compute_actor_score(c: sqlite3.Row, article_text_norm: str) -> Tuple[int, str]:
    hits = []
    for t in actor_terms(c):
        if boundary_regex(t).search(article_text_norm):
            hits.append(t)
    hits = sorted(set(hits))
    return len(hits), ",".join(hits)


def compute_kw_score(c: sqlite3.Row, a: sqlite3.Row) -> Tuple[int, str]:
    cset = set(kw_terms_conflict(c))
    aset = set(kw_terms_article(a))
    matched = sorted(cset.intersection(aset))
    return len(matched), ",".join(matched)


# ----------------
# SQLite schema helpers
def get_table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return [r[1] for r in rows]


def add_column_if_missing(conn: sqlite3.Connection, table: str, col: str, coltype: str) -> None:
    cols = set(get_table_columns(conn, table))
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype};")


def ensure_conflict_has_bestmatch_columns(conn: sqlite3.Connection) -> None:
    """
    These columns are ONLY for the optional in-place enrichment of conflict_features
    with ONE bestmatched article (many-to-many can't be stored in a single row).
    """
    needed: Dict[str, str] = {
        "matched_article_rowid": "INTEGER",
        "match_total_score": "INTEGER",
        "match_actor_score": "INTEGER",
        "match_kw_score": "INTEGER",
        "matched_actors": "TEXT",
        "matched_keywords": "TEXT",

        "article_publishedAt": "TEXT",
        "article_url": "TEXT",
        "article_source_name": "TEXT",
        "article_source_url": "TEXT",
        "article_title_en": "TEXT",
        "article_description_en": "TEXT",
        "article_content": "TEXT",
        "article_content_en": "TEXT",
        "article_country": "TEXT",
        "article_country_score": "INTEGER",
        "article_kw_1": "TEXT",
        "article_kw_2": "TEXT",
        "article_kw_3": "TEXT",
    }
    for col, coltype in needed.items():
        add_column_if_missing(conn, CONFLICT_TABLE, col, coltype)
    conn.commit()


def main():
    if not CONFLICT_DB.exists():
        raise FileNotFoundError(CONFLICT_DB)
    if not GNEWS_DB.exists():
        raise FileNotFoundError(GNEWS_DB)

    with sqlite3.connect(str(CONFLICT_DB)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")

        # Attach GNews DB for cross-db joins. [web:861]
        con.execute("ATTACH DATABASE ? AS gnews", (str(GNEWS_DB),))

        # Optional: allow in-place bestmatch enrichment of conflict_features
        ensure_conflict_has_bestmatch_columns(con)

        # Drop and recreate output tables
        con.executescript(f"""
        DROP TABLE IF EXISTS {T_BEST};
        DROP TABLE IF EXISTS {T_WIDE};

        CREATE TABLE {T_BEST} (
            conflict_rowid INTEGER,
            article_rowid  INTEGER,
            total_score    INTEGER,
            actor_score    INTEGER,
            kw_score       INTEGER,
            matched_actors TEXT,
            matched_keywords TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_{T_BEST}_conflict ON {T_BEST}(conflict_rowid);

        CREATE TABLE {T_WIDE} (
            -- conflict identifier
            conflict_rowid INTEGER,
            matched_article_rowid INTEGER,

            -- match scores
            match_total_score INTEGER,
            match_actor_score INTEGER,
            match_kw_score INTEGER,
            matched_actors TEXT,
            matched_keywords TEXT,

            -- conflict fields (copied from conflict_features)
            -- NOTE: filled by INSERT..SELECT below as c.*
            -- (SQLite doesn't have "c.*" inside CREATE TABLE definition)
            dummy INTEGER
        );
        DROP TABLE {T_WIDE};
        """)

        # 1) Hard-filter candidates (country + publishedAt window)
        candidates = con.execute(f"""
        SELECT
            c.rowid AS conflict_rowid,
            a.rowid AS article_rowid,
            c.*,
            a.publishedAt, a.url, a.source_name, a.source_url,
            a.title_en, a.description_en, a.content, a.content_en,
            a.article_country, a.article_country_score,
            a.kw_1, a.kw_2, a.kw_3
        FROM {CONFLICT_TABLE} c
        JOIN gnews.{ART_TABLE} a
          ON a.article_country = c.country
         AND date(a.publishedAt) BETWEEN date(c.start_date)
                                   AND date(c.end_date, '+{EXTRA_DAYS} days')
        ORDER BY c.rowid, a.publishedAt, a.rowid
        """).fetchall()

        # 2) Score candidates; keep ALL matches above threshold; also track best
        best_by_conflict: Dict[int, Tuple] = {}
        wide_rows = []

        for r in candidates:
            conflict_rowid = r["conflict_rowid"]

            article_text = collapse_ws(f"{r['title_en'] or ''} {r['description_en'] or ''} {r['content_en'] or ''}")
            article_text_norm = norm(article_text)

            actor_score, matched_actors = compute_actor_score(r, article_text_norm)
            kw_score, matched_keywords = compute_kw_score(r, r)
            total_score = ACTOR_WEIGHT * actor_score + KW_WEIGHT * kw_score

            if total_score < MATCH_THRESHOLD:
                continue

            # store for MANY-to-MANY wide output (we'll materialize via CREATE TABLE AS SELECT)
            # easiest: insert into a temp list first
            wide_rows.append((
                conflict_rowid,
                r["article_rowid"],
                total_score,
                actor_score,
                kw_score,
                matched_actors,
                matched_keywords,
                r["article_rowid"],  # repeated for later join if needed
            ))

            # track best match per conflict too (optional)
            if conflict_rowid not in best_by_conflict:
                best_by_conflict[conflict_rowid] = (r, total_score, actor_score, kw_score, matched_actors, matched_keywords)
            else:
                old_r, old_total, *_ = best_by_conflict[conflict_rowid]
                old_pub = old_r["publishedAt"] or ""
                old_rowid = old_r["article_rowid"]
                pub = r["publishedAt"] or ""

                better = False
                if total_score > old_total:
                    better = True
                elif total_score == old_total:
                    if pub < old_pub:
                        better = True
                    elif pub == old_pub and r["article_rowid"] < old_rowid:
                        better = True

                if better:
                    best_by_conflict[conflict_rowid] = (r, total_score, actor_score, kw_score, matched_actors, matched_keywords)

        # 3) Write bestmatch table (optional but handy)
        rows_best = []
        for conflict_rowid, (r, total_score, actor_score, kw_score, matched_actors, matched_keywords) in best_by_conflict.items():
            rows_best.append((
                conflict_rowid,
                r["article_rowid"],
                total_score,
                actor_score,
                kw_score,
                matched_actors,
                matched_keywords
            ))

        con.executemany(
            f"""
            INSERT INTO {T_BEST}(
                conflict_rowid, article_rowid,
                total_score, actor_score, kw_score,
                matched_actors, matched_keywords
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows_best
        )
        con.commit()

        # 4) Materialize MANY-to-MANY wide table:
        # Create a temporary match table from the Python-scored results, then join to c.* and a.*
        con.executescript("""
        DROP TABLE IF EXISTS _tmp_matches;
        CREATE TABLE _tmp_matches (
            conflict_rowid INTEGER,
            article_rowid  INTEGER,
            total_score    INTEGER,
            actor_score    INTEGER,
            kw_score       INTEGER,
            matched_actors TEXT,
            matched_keywords TEXT
        );
        CREATE INDEX IF NOT EXISTS idx__tmp_matches_conflict ON _tmp_matches(conflict_rowid);
        CREATE INDEX IF NOT EXISTS idx__tmp_matches_article  ON _tmp_matches(article_rowid);
        """)
        con.executemany(
            """
            INSERT INTO _tmp_matches(
                conflict_rowid, article_rowid,
                total_score, actor_score, kw_score,
                matched_actors, matched_keywords
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [x[:7] for x in wide_rows]
        )
        con.commit()

        # Now build the real wide table from tmp matches + full conflict + full article columns
        con.executescript(f"""
        DROP TABLE IF EXISTS {T_WIDE};

        CREATE TABLE {T_WIDE} AS
        SELECT
            c.rowid AS conflict_rowid,

            m.article_rowid AS matched_article_rowid,
            m.total_score   AS match_total_score,
            m.actor_score   AS match_actor_score,
            m.kw_score      AS match_kw_score,
            m.matched_actors,
            m.matched_keywords,

            c.*,

            a.publishedAt AS article_publishedAt,
            a.url         AS article_url,
            a.source_name AS article_source_name,
            a.source_url  AS article_source_url,
            a.title_en    AS article_title_en,
            a.description_en AS article_description_en,
            a.content     AS article_content,
            a.content_en  AS article_content_en,
            a.article_country,
            a.article_country_score,
            a.kw_1 AS article_kw_1,
            a.kw_2 AS article_kw_2,
            a.kw_3 AS article_kw_3

        FROM _tmp_matches m
        JOIN {CONFLICT_TABLE} c
          ON c.rowid = m.conflict_rowid
        JOIN gnews.{ART_TABLE} a
          ON a.rowid = m.article_rowid
        ;
        CREATE INDEX IF NOT EXISTS idx_{T_WIDE}_conflict ON {T_WIDE}(conflict_rowid);
        CREATE INDEX IF NOT EXISTS idx_{T_WIDE}_country ON {T_WIDE}(country);
        """)
        con.commit()

        # optional: drop temp
        con.execute("DROP TABLE IF EXISTS _tmp_matches;")
        con.commit()

        # 5) In-place enrichment of conflict_features with ONE best match (optional)
        # (If you don't want this, you can delete this whole block.)
        con.execute(f"""
            UPDATE {CONFLICT_TABLE}
            SET
                matched_article_rowid = NULL,
                match_total_score = NULL,
                match_actor_score = NULL,
                match_kw_score = NULL,
                matched_actors = NULL,
                matched_keywords = NULL,

                article_publishedAt = NULL,
                article_url = NULL,
                article_source_name = NULL,
                article_source_url = NULL,
                article_title_en = NULL,
                article_description_en = NULL,
                article_content = NULL,
                article_content_en = NULL,
                article_country = NULL,
                article_country_score = NULL,
                article_kw_1 = NULL,
                article_kw_2 = NULL,
                article_kw_3 = NULL
        """)
        con.commit()

        # Fill back bestmatches from T_BEST
        fill_sql = f"""
        UPDATE {CONFLICT_TABLE}
        SET
            matched_article_rowid = (
                SELECT article_rowid FROM {T_BEST} m WHERE m.conflict_rowid = {CONFLICT_TABLE}.rowid
            ),
            match_total_score = (
                SELECT total_score FROM {T_BEST} m WHERE m.conflict_rowid = {CONFLICT_TABLE}.rowid
            ),
            match_actor_score = (
                SELECT actor_score FROM {T_BEST} m WHERE m.conflict_rowid = {CONFLICT_TABLE}.rowid
            ),
            match_kw_score = (
                SELECT kw_score FROM {T_BEST} m WHERE m.conflict_rowid = {CONFLICT_TABLE}.rowid
            ),
            matched_actors = (
                SELECT matched_actors FROM {T_BEST} m WHERE m.conflict_rowid = {CONFLICT_TABLE}.rowid
            ),
            matched_keywords = (
                SELECT matched_keywords FROM {T_BEST} m WHERE m.conflict_rowid = {CONFLICT_TABLE}.rowid
            )
        ;
        """
        con.execute(fill_sql)
        con.commit()

        n_best = con.execute(f"SELECT COUNT(*) FROM {T_BEST};").fetchone()[0]
        n_wide = con.execute(f"SELECT COUNT(*) FROM {T_WIDE};").fetchone()[0]
        print(f"Done. bestmatch rows: {n_best} | wide (many-to-many) rows: {n_wide}")
        print("conflict_article_bestmatch_wide now contains ALL matches with score >= threshold.")
        print("conflict_features enriched with ONE best match (optional).")


if __name__ == "__main__":
    main()
