import sqlite3
from pathlib import Path

# Main DB (conflicts + where we will store the output tables)
CONFLICT_DB = Path(
    "/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/data/conflict_data.db"
)

# Secondary DB (articles)
ARTICLES_DB = Path(
    "/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/data/gnews_articles_from2023.db"
)

# Input tables
T_CON = "conflict_features"   # in CONFLICT_DB
T_ART = "articles_eng"        # in ARTICLES_DB

# Output tables (created in CONFLICT_DB)
T_MATCH = "conflict_article_bestmatch"
T_WIDE  = "conflict_article_bestmatch_wide"

# Settings
DATE_WINDOW_EXTRA_DAYS = 2    # extend end_date by +2 days [web:499]
KEEP_ZERO_SCORE = False       # if False: only keep best match if score > 0

def norm_kw(x) -> str:
    """Normalize keywords for comparison (None -> '', strip, lowercase)."""
    if x is None:
        return ""
    return str(x).strip().lower()

def compute_overlap_score(conflict_row, article_row):
    """Return (score, matched_keywords_csv)."""
    c_kws = {
        norm_kw(conflict_row["top_keyword_1"]),
        norm_kw(conflict_row["top_keyword_2"]),
        norm_kw(conflict_row["top_keyword_3"]),
    }
    a_kws = {
        norm_kw(article_row["kw_1"]),
        norm_kw(article_row["kw_2"]),
        norm_kw(article_row["kw_3"]),
    }

    c_kws.discard("")
    a_kws.discard("")

    matched = sorted(c_kws.intersection(a_kws))
    return len(matched), ",".join(matched)

def main():
    if not CONFLICT_DB.exists():
        raise FileNotFoundError(CONFLICT_DB)
    if not ARTICLES_DB.exists():
        raise FileNotFoundError(ARTICLES_DB)

    with sqlite3.connect(str(CONFLICT_DB)) as con:
        con.row_factory = sqlite3.Row

        # Attach the articles DB so we can join across files in one connection. [web:825]
        con.execute("ATTACH DATABASE ? AS gnews", (str(ARTICLES_DB),))

        # 1) Candidate generation: HARD filters only (country + date window). [web:499]
        candidates_sql = f"""
        SELECT
            c.rowid AS conflict_rowid,
            a.rowid AS article_rowid,
            c.top_keyword_1, c.top_keyword_2, c.top_keyword_3,
            a.kw_1, a.kw_2, a.kw_3,
            a.publishedAT AS article_publishedAT
        FROM {T_CON} c
        JOIN gnews.{T_ART} a
          ON a.article_country = c.country
         AND date(a.publishedAT) BETWEEN date(c.start_date)
                                    AND date(c.end_date, '+{DATE_WINDOW_EXTRA_DAYS} days')
        """
        candidates = con.execute(candidates_sql).fetchall()

        # 2) Build a scored temp table (keep score=0 for now; we will pick best later).
        con.executescript("""
        DROP TABLE IF EXISTS _tmp_scored;
        CREATE TABLE _tmp_scored (
            conflict_rowid INTEGER,
            article_rowid  INTEGER,
            match_score    INTEGER,
            matched_keywords TEXT,
            article_publishedAT TEXT
        );
        """)

        rows_to_insert = []
        for r in candidates:
            # We already have the columns needed in r; wrap as dict-like for functions.
            score, matched = compute_overlap_score(
                {"top_keyword_1": r["top_keyword_1"], "top_keyword_2": r["top_keyword_2"], "top_keyword_3": r["top_keyword_3"]},
                {"kw_1": r["kw_1"], "kw_2": r["kw_2"], "kw_3": r["kw_3"]},
            )
            rows_to_insert.append(
                (r["conflict_rowid"], r["article_rowid"], score, matched, r["article_publishedAT"])
            )

        con.executemany(
            """
            INSERT INTO _tmp_scored(conflict_rowid, article_rowid, match_score, matched_keywords, article_publishedAT)
            VALUES (?,?,?,?,?)
            """,
            rows_to_insert,
        )

        # 3) Pick BEST article per conflict using ROW_NUMBER() ranking. [web:845][web:840]
        con.executescript(f"""
        DROP TABLE IF EXISTS {T_MATCH};

        CREATE TABLE {T_MATCH} AS
        SELECT conflict_rowid, article_rowid, match_score, matched_keywords
        FROM (
            SELECT
                s.*,
                ROW_NUMBER() OVER (
                    PARTITION BY s.conflict_rowid
                    ORDER BY
                        s.match_score DESC,
                        date(s.article_publishedAT) ASC,
                        s.article_rowid ASC
                ) AS rn
            FROM _tmp_scored s
        )
        WHERE rn = 1
        {"AND match_score > 0" if not KEEP_ZERO_SCORE else ""}
        ;

        CREATE INDEX IF NOT EXISTS idx_{T_MATCH}_conflict ON {T_MATCH}(conflict_rowid);
        CREATE INDEX IF NOT EXISTS idx_{T_MATCH}_article  ON {T_MATCH}(article_rowid);

        DROP TABLE IF EXISTS {T_WIDE};

        -- 4) Materialize a wide table with ALL columns from conflicts + ALL columns from articles
        CREATE TABLE {T_WIDE} AS
        SELECT
            m.match_score,
            m.matched_keywords,
            c.*,
            a.*
        FROM {T_MATCH} m
        JOIN {T_CON} c ON c.rowid = m.conflict_rowid
        JOIN gnews.{T_ART} a ON a.rowid = m.article_rowid
        ;

        DROP TABLE IF EXISTS _tmp_scored;
        """)

        n = con.execute(f"SELECT COUNT(*) FROM {T_MATCH}").fetchone()[0]
        print(f"OK: created {T_MATCH} (n={n}) and {T_WIDE}")

if __name__ == "__main__":
    main()
