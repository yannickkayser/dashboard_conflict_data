# backfill_country_keywords_from_conflicts.py

import sqlite3
from pathlib import Path
from collections import Counter
import re
import unicodedata
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GNEWS_DB = PROJECT_ROOT / "data" / "gnews_articles_from2023.db"
CONFLICT_DB = PROJECT_ROOT / "data" / "conflict_data.db"

ART_TABLE = "articles_eng"
CONFLICT_COUNTRY_TABLE = "events"
CONFLICT_COUNTRY_COL = "country"

BATCH_SIZE = 2000
TEST_LIMIT: Optional[int] = None

# Update control:
ONLY_UPDATE_MISSING = True  # if True: only fill rows where country/kw missing


# ----------------
# Keyword extraction aligned with unique_conflicts.py
TOKEN_RE = re.compile(r"[A-Za-z]{3,}")  # words >= 3 letters

STOPWORDS = {
    "the","a","an","and","or","to","of","in","on","for","with","at","by","from",
    "is","are","was","were","be","been","being","as","that","this","it","its",
    "their","they","them","he","she","his","her","you","we","our","us",
    "after","before","during","over","under","into","out","up","down",
    "near","around","about","between","within","across",
    "said","report","reports","according","allegedly",
    "killed","injured","attack","attacked","clash","clashes","protest","protests",
    "police","army","soldiers","people","civilians","forces","security",
    "against","there","demonstration","demand","members","gathered","demonstrators",
    "protestors",
}

# ----------------
# Germany boost (copy-paste same sets you used)
GERMANY_CITY_ALIASES = {
    "Berlin","Hamburg",
    "Munich","Muenchen","München",
    "Cologne","Koeln","Köln",
    "Frankfurt","Frankfurt am Main",
    "Dusseldorf","Düsseldorf",
    "Stuttgart","Leipzig","Dortmund","Bremen","Essen","Dresden",
    "Nuremberg","Nuernberg","Nürnberg",
    "Hanover","Hannover",
    "Duisburg","Wuppertal","Bochum","Bielefeld","Bonn","Mannheim",
    "Karlsruhe","Augsburg","Wiesbaden","Gelsenkirchen",
    "Monchengladbach","Mönchengladbach",
    "Braunschweig","Chemnitz","Kiel","Aachen",
    "Halle","Halle (Saale)","Magdeburg",
    "Freiburg","Freiburg im Breisgau",
    "Krefeld","Luebeck","Lübeck","Oberhausen","Erfurt","Mainz",
    "Rostock","Kassel","Hagen","Saarbruecken","Saarbrücken",
    "Hamm","Potsdam","Ludwigshafen","Ludwigshafen am Rhein",
    "Oldenburg","Leverkusen","Osnabrueck","Osnabrück",
    "Solingen","Heidelberg","Herne","Neuss","Darmstadt","Paderborn",
    "Regensburg","Ingolstadt","Wuerzburg","Würzburg","Ulm",
    "Offenbach","Offenbach am Main","Heilbronn","Pforzheim","Wolfsburg",
    "Goettingen","Göttingen","Grimmen", "Rügen"
}

GERMANY_STATE_ALIASES = {
    "Baden-Württemberg","Baden Württemberg",
    "Bavaria","Bayern",
    "Berlin","Brandenburg","Bremen","Hamburg",
    "Hesse","Hessen",
    "Lower Saxony","Niedersachsen",
    "Mecklenburg-Western Pomerania","Mecklenburg-Vorpommern",
    "North Rhine-Westphalia","Nordrhein-Westfalen",
    "Rhineland-Palatinate","Rheinland-Pfalz",
    "Saarland",
    "Saxony","Sachsen",
    "Saxony-Anhalt","Sachsen-Anhalt",
    "Schleswig-Holstein",
    "Thuringia","Thüringen","Thueringen",
}

MANUAL_COUNTRY_ALIAS_PATCHES = {
    "United States": {"us", "u.s.", "u.s", "usa", "united states of america", "american"},
    "United Kingdom": {"uk", "u.k.", "u.k", "britain", "great britain"},
    "Côte d’Ivoire": {"ivory coast", "cote d'ivoire", "cote d ivoire", "cote divoire"},
    "Democratic Republic of the Congo": {"drc", "dr congo", "congo-kinshasa"},
    "Republic of the Congo": {"congo-brazzaville"},
    "Türkiye": {"turkey"},
    "Czechia": {"czech republic"},
    "Eswatini": {"swaziland"},
    "Myanmar": {"burma"},
    "Viet Nam": {"vietnam"},
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_text(s: str) -> str:
    return strip_accents((s or "").lower())


def regex_count(text_norm: str, alias_norm: str) -> int:
    # boundary-ish matching
    pattern = r"(?<![a-z])" + re.escape(alias_norm) + r"(?![a-z])"
    return len(re.findall(pattern, text_norm))


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.cursor()
    rows = cur.execute(f"PRAGMA table_info({table});").fetchall()
    return {r[1] for r in rows}


def ensure_columns(conn: sqlite3.Connection) -> None:
    existing = table_columns(conn, ART_TABLE)
    cur = conn.cursor()

    wanted = [
        ("article_country", "TEXT"),
        ("article_country_score", "INTEGER"),
        ("kw_1", "TEXT"),
        ("kw_2", "TEXT"),
        ("kw_3", "TEXT"),
    ]

    for col, coltype in wanted:
        if col not in existing:
            cur.execute(f"ALTER TABLE {ART_TABLE} ADD COLUMN {col} {coltype};")

    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{ART_TABLE}_article_country ON {ART_TABLE}(article_country);")
    conn.commit()


def load_conflict_countries(conflict_db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(conflict_db_path))
    try:
        cur = conn.cursor()
        rows = cur.execute(
            f"""
            SELECT DISTINCT {CONFLICT_COUNTRY_COL}
            FROM {CONFLICT_COUNTRY_TABLE}
            WHERE {CONFLICT_COUNTRY_COL} IS NOT NULL AND TRIM({CONFLICT_COUNTRY_COL}) <> ''
            ORDER BY {CONFLICT_COUNTRY_COL};
            """
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def build_country_aliases_from_conflicts(conflict_countries: list[str]) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    for c in conflict_countries:
        base = {normalize_text(c)}
        patch = MANUAL_COUNTRY_ALIAS_PATCHES.get(c, set())
        aliases[c] = set(base) | {normalize_text(p) for p in patch}

    if "Germany" in aliases:
        aliases["Germany"] |= (
            {"germany", "german", "deutschland"} |
            {normalize_text(x) for x in GERMANY_CITY_ALIASES} |
            {normalize_text(x) for x in GERMANY_STATE_ALIASES}
        )
    return aliases


def guess_country(text: str, country_aliases: dict[str, set[str]]) -> tuple[Optional[str], int]:
    t = normalize_text(text)
    if not t.strip():
        return None, 0

    best_country, best_score = None, 0
    for country, aliases in country_aliases.items():
        score = 0
        for a in aliases:
            score += regex_count(t, a)
        if score > best_score:
            best_country, best_score = country, score
    return best_country, best_score


def top_keywords(text: str, k: int = 3) -> list[Optional[str]]:
    if not text or not str(text).strip():
        return [None, None, None]

    text_l = str(text).lower()
    toks = []
    for tok in TOKEN_RE.findall(text_l):
        if tok in STOPWORDS:
            continue
        toks.append(tok)

    c = Counter(toks)
    top = [w for w, _ in c.most_common(k)]
    while len(top) < 3:
        top.append(None)
    return top[:3]


def main():
    if not GNEWS_DB.exists():
        raise FileNotFoundError(f"Missing GNews DB: {GNEWS_DB}")
    if not CONFLICT_DB.exists():
        raise FileNotFoundError(f"Missing conflict DB: {CONFLICT_DB}")

    conflict_countries = load_conflict_countries(CONFLICT_DB)
    country_aliases = build_country_aliases_from_conflicts(conflict_countries)
    print(f"Countries loaded: {len(conflict_countries)}; alias dict built: {len(country_aliases)}")

    conn = sqlite3.connect(str(GNEWS_DB))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        ensure_columns(conn)

        cur = conn.cursor()

        where = ""
        if ONLY_UPDATE_MISSING:
            where = """
            WHERE
                article_country IS NULL OR TRIM(article_country) = ''
                OR kw_1 IS NULL OR TRIM(kw_1) = ''
            """

        sql = f"""
            SELECT id, title_en, description_en
            FROM {ART_TABLE}
            {where}
            ORDER BY id
        """
        if TEST_LIMIT is not None:
            sql += f" LIMIT {int(TEST_LIMIT)}"

        cur.execute(sql)

        total = 0
        while True:
            rows = cur.fetchmany(BATCH_SIZE)
            if not rows:
                break

            updates = []
            for _id, title_en, desc_en in rows:
                analysis_text = (title_en or "") + " " + (desc_en or "")
                country, score = guess_country(analysis_text, country_aliases)
                kw1, kw2, kw3 = top_keywords(analysis_text, k=3)
                updates.append((country, score, kw1, kw2, kw3, _id))

            conn.executemany(
                f"""
                UPDATE {ART_TABLE}
                SET article_country = ?,
                    article_country_score = ?,
                    kw_1 = ?,
                    kw_2 = ?,
                    kw_3 = ?
                WHERE id = ?;
                """,
                updates
            )  # UPDATE modifies only rows targeted by WHERE [web:251]
            conn.commit()

            total += len(rows)
            print(f"Backfilled: {total} rows")

        print("Done backfilling.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
