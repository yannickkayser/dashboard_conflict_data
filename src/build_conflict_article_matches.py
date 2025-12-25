import sqlite3
from pathlib import Path

DB_PATH = Path("/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/data/conflict_data.db")

T_ART = "articles_eng"
T_CON = "conflict_features"

# Output
T_MATCH = "conflict_article_match_kw"
T_WIDE  = "conflict_article_matches_kw"

def norm_kw(x: str) -> str:
    if x is None:
        return ""
    return str(x).strip().lower()

def main():
    with sqlite3.connect(str(DB_PATH)) as con:
        con.row_factory = sqlite3.Row

        # Kandidaten: Country + Date window (publishedAT in [start_date, end_date]) [web:499]
        candidates_sql = f"""
        SELECT
            c.rowid AS conflict_rowid,
            a.rowid AS article_rowid,

            c.country,
            c.start_date,
            c.end_date,
            c.top_keyword1, c.top_keyword2, c.top_keyword3,

            a.publishedAT,
            a.article_country,
            a.kw_1, a.kw_2, a.kw_3
        FROM {T_CON} c
        JOIN {T_ART} a
          ON a.article_country = c.country
         AND date(a.publishedAT) BETWEEN date(c.start_date) AND date(c.end_date)
        """

        rows = con.execute(candidates_sql).fetchall()

        # Output tables neu
        con.executescript(f"""
        DROP TABLE IF EXISTS {T_MATCH};
        CREATE TABLE {T_MATCH} (
            conflict_rowid INTEGER,
            article_rowid  INTEGER,
            match_score    INTEGER,
            matched_keywords TEXT
        );

        DROP TABLE IF EXISTS {T_WIDE};
        """)

        to_insert = []
        for r in rows:
            c_kws = {norm_kw(r["top_keyword1"]), norm_kw(r["top_keyword2"]), norm_kw(r["top_keyword3"])}
            a_kws = {norm_kw(r["kw_1"]), norm_kw(r["kw_2"]), norm_kw(r["kw_3"])}

            c_kws.discard("")
            a_kws.discard("")

            matched = sorted(c_kws.intersection(a_kws))
            score = len(matched)

            if score > 0:
                to_insert.append((r["conflict_rowid"], r["article_rowid"], score, ",".join(matched)))

        con.executemany(
            f"INSERT INTO {T_MATCH}(conflict_rowid, article_rowid, match_score, matched_keywords) VALUES (?,?,?,?)",
            to_insert
        )

        # Wide Tabelle fürs Dashboard
        con.executescript(f"""
        CREATE TABLE {T_WIDE} AS
        SELECT
            m.match_score,
            m.matched_keywords,
            c.*,
            a.*
        FROM {T_MATCH} m
        JOIN {T_CON} c ON c.rowid = m.conflict_rowid
        JOIN {T_ART} a ON a.rowid = m.article_rowid
        ;

        CREATE INDEX IF NOT EXISTS idx_{T_MATCH}_conflict ON {T_MATCH}(conflict_rowid);
        CREATE INDEX IF NOT EXISTS idx_{T_MATCH}_article  ON {T_MATCH}(article_rowid);
        """)

        n = con.execute(f"SELECT COUNT(*) FROM {T_MATCH}").fetchone()[0]
        print(f"OK: wrote {n} matches into {T_MATCH} and materialized {T_WIDE}")

if __name__ == "__main__":
    main()
