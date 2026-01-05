import os
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Set
import time

from transformers import pipeline
import torch


def tnow():
    return time.perf_counter()


DEVICE = 0 if torch.cuda.is_available() else -1  # GPU 0


# ----------------
# Paths
HERE = Path(__file__).resolve()

cand1 = HERE.parents[1] / "data"
cand2 = HERE.parents[2] / "data"

if cand1.exists():
    DATA_DIR = cand1
elif cand2.exists():
    DATA_DIR = cand2
else:
    DATA_DIR = Path(os.getcwd()).resolve().parents[0] / "data"

GNEWS_DB = DATA_DIR / "deleted_dupgnews2023.db"
CONFLICT_DB = DATA_DIR / "conflict_data.db"

SOURCE_TABLE = "article_without_duplicates"
TARGET_TABLE = "articles_eng"

CONFLICT_COUNTRY_TABLE = "events"
CONFLICT_COUNTRY_COL = "country"


# ----------------
# Runtime knobs
TEST_LIMIT_UPDATE: Optional[int] = None
BATCH_SIZE = 200
TRANSLATE_BATCH_SIZE = 128

TRANSLATION_MODEL = "Helsinki-NLP/opus-mt-de-en"

COUNTRY_CANDIDATE_TOPK = 3


GERMANY_CITY_ALIASES = {
    "Berlin", "Hamburg",
    "Munich", "Muenchen", "München",
    "Cologne", "Koeln", "Köln",
    "Frankfurt", "Frankfurt am Main",
    "Dusseldorf", "Düsseldorf",
    "Stuttgart", "Leipzig", "Dortmund", "Bremen", "Essen", "Dresden",
    "Nuremberg", "Nuernberg", "Nürnberg",
    "Hanover", "Hannover",
    "Duisburg", "Wuppertal", "Bochum", "Bielefeld", "Bonn", "Mannheim",
    "Karlsruhe", "Augsburg", "Wiesbaden", "Gelsenkirchen",
    "Monchengladbach", "Mönchengladbach",
    "Braunschweig", "Chemnitz", "Kiel", "Aachen",
    "Halle", "Halle (Saale)", "Magdeburg",
    "Freiburg", "Freiburg im Breisgau",
    "Krefeld", "Luebeck", "Lübeck", "Oberhausen", "Erfurt", "Mainz",
    "Rostock", "Kassel", "Hagen", "Saarbruecken", "Saarbrücken",
    "Hamm", "Potsdam", "Ludwigshafen", "Ludwigshafen am Rhein",
    "Oldenburg", "Leverkusen", "Osnabrueck", "Osnabrück",
    "Solingen", "Heidelberg", "Herne", "Neuss", "Darmstadt", "Paderborn",
    "Regensburg", "Ingolstadt", "Wuerzburg", "Würzburg", "Ulm",
    "Offenbach", "Offenbach am Main", "Heilbronn", "Pforzheim", "Wolfsburg",
    "Goettingen", "Göttingen",
}

GERMANY_STATE_ALIASES = {
    "Baden-Württemberg", "Baden Württemberg",
    "Bavaria", "Bayern",
    "Berlin", "Brandenburg", "Bremen", "Hamburg",
    "Hesse", "Hessen",
    "Lower Saxony", "Niedersachsen",
    "Mecklenburg-Western Pomerania", "Mecklenburg-Vorpommern",
    "North Rhine-Westphalia", "Nordrhein-Westfalen",
    "Rhineland-Palatinate", "Rheinland-Pfalz",
    "Saarland",
    "Saxony", "Sachsen",
    "Saxony-Anhalt", "Sachsen-Anhalt",
    "Schleswig-Holstein",
    "Thuringia", "Thüringen", "Thueringen",
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
    "Ukraine": {"kiev"},
}


# ----------------
# Helpers: text + schema
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_text(s: str) -> str:
    return strip_accents((s or "").lower())


def regex_count(text_norm: str, alias_norm: str) -> int:
    pattern = r"(?<![a-z])" + re.escape(alias_norm) + r"(?![a-z])"
    return len(re.findall(pattern, text_norm))


def ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table});").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype};")
        conn.commit()


def ensure_articles_eng_schema(conn: sqlite3.Connection) -> None:
    # Create minimal table if it doesn't exist.
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
            id TEXT PRIMARY KEY,
            publishedAt TEXT,
            url TEXT,
            source_name TEXT,
            source_url TEXT,
            title_en TEXT,
            description_en TEXT,
            article_country TEXT,
            article_country_score INTEGER
        );
    """)  # IF NOT EXISTS makes it safe to run repeatedly. [web:76]
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET_TABLE}_publishedAt ON {TARGET_TABLE}(publishedAt);")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET_TABLE}_article_country ON {TARGET_TABLE}(article_country);")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET_TABLE}_url ON {TARGET_TABLE}(url);")
    conn.commit()

    # Ensure expected columns exist even if table was created earlier with fewer cols.
    ensure_column(conn, TARGET_TABLE, "title_en", "TEXT")
    ensure_column(conn, TARGET_TABLE, "description_en", "TEXT")
    ensure_column(conn, TARGET_TABLE, "article_country", "TEXT")
    ensure_column(conn, TARGET_TABLE, "article_country_score", "INTEGER")


# ----------------
# Country helpers (STRICT)
def load_conflict_countries(conflict_db_path: Path) -> List[str]:
    conn = sqlite3.connect(str(conflict_db_path))
    try:
        rows = conn.execute(
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


def build_country_aliases_from_conflicts(conflict_countries: List[str]) -> Dict[str, Set[str]]:
    aliases: Dict[str, Set[str]] = {}
    for c in conflict_countries:
        base = {normalize_text(c)}
        patch = MANUAL_COUNTRY_ALIAS_PATCHES.get(c, set())
        aliases[c] = set(base) | {normalize_text(p) for p in patch}

    if "Germany" in aliases:
        extra = (
            {"germany", "german", "deutschland"} |
            {normalize_text(x) for x in GERMANY_CITY_ALIASES} |
            {normalize_text(x) for x in GERMANY_STATE_ALIASES}
        )
        aliases["Germany"] |= extra
    return aliases


def guess_country_candidates(text: str, country_aliases: Dict[str, Set[str]], k: int) -> List[Tuple[str, int]]:
    t = normalize_text(text)
    if not t.strip():
        return []
    scored: List[Tuple[str, int]] = []
    for country, aliases in country_aliases.items():
        score = 0
        for a in aliases:
            score += regex_count(t, a)
        if score > 0:
            scored.append((country, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def choose_country_strict(text_en: str, country_aliases: Dict[str, Set[str]]) -> Tuple[str, int]:
    cand_scored = guess_country_candidates(text_en, country_aliases, k=COUNTRY_CANDIDATE_TOPK)
    candidates = [c for (c, _s) in cand_scored]
    if len(candidates) == 1:
        return candidates[0], 100
    return "NA", 0


# ----------------
# Translation helpers
def translate_texts(translator, texts: List[str], batch_size: int, num_beams: int = 4) -> List[str]:
    texts = [t if isinstance(t, str) else "" for t in texts]
    if all((not t.strip()) for t in texts):
        return ["" for _ in texts]
    outs = translator(
        texts,
        batch_size=batch_size,
        truncation=True,
        num_beams=int(num_beams),
        do_sample=False,
    )
    return [o["translation_text"] for o in outs]


def to_date_yyyy_mm_dd(published_at: Optional[str]) -> Optional[str]:
    if not published_at:
        return None
    s = str(published_at)
    return s[:10] if len(s) >= 10 else None


def fetch_missing_ids_for_eng(conn: sqlite3.Connection, limit: Optional[int]) -> List[Tuple]:
    sql = f"""
        SELECT a.id, a.publishedAt, a.title, a.description, a.url, a.source_name, a.source_url
        FROM {SOURCE_TABLE} a
        LEFT JOIN {TARGET_TABLE} e ON a.id = e.id
        WHERE e.id IS NULL
        ORDER BY a.rowid
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def upsert_articles_eng(conn: sqlite3.Connection, rows: List[Tuple], country_aliases: Dict[str, Set[str]]) -> None:
    t_model0 = tnow()
    translator = pipeline("translation_de_to_en", model=TRANSLATION_MODEL, device=DEVICE)
    print(f"[timing] model_load_s={tnow()-t_model0:.2f}")

    for start in range(0, len(rows), BATCH_SIZE):
        t0 = tnow()
        batch = rows[start:start + BATCH_SIZE]

        ids = [r[0] for r in batch]
        published = [to_date_yyyy_mm_dd(r[1]) for r in batch]
        title_de = [r[2] for r in batch]
        desc_de = [r[3] for r in batch]
        url = [r[4] for r in batch]
        source_name = [r[5] for r in batch]
        source_url = [r[6] for r in batch]

        NUM_BEAMS = 2
        title_en = translate_texts(translator, title_de, TRANSLATE_BATCH_SIZE, num_beams=NUM_BEAMS)
        desc_en = translate_texts(translator, desc_de, TRANSLATE_BATCH_SIZE, num_beams=NUM_BEAMS)

        payload = []
        for i in range(len(batch)):
            analysis_text_en = f"{title_en[i] or ''} {desc_en[i] or ''}".strip()
            country, score = choose_country_strict(analysis_text_en, country_aliases)

            payload.append((
                ids[i], published[i], url[i], source_name[i], source_url[i],
                title_en[i], desc_en[i],
                country, score,
            ))

        conn.executemany(
            f"""
            INSERT INTO {TARGET_TABLE} (
                id, publishedAt, url, source_name, source_url,
                title_en, description_en,
                article_country, article_country_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                publishedAt=excluded.publishedAt,
                url=excluded.url,
                source_name=excluded.source_name,
                source_url=excluded.source_url,
                title_en=excluded.title_en,
                description_en=excluded.description_en,
                article_country=excluded.article_country,
                article_country_score=excluded.article_country_score;
            """,
            payload
        )  # uses SQLite upsert syntax. [web:58]
        conn.commit()
        print(f"[batch] n={len(batch)} total_s={tnow()-t0:.2f}")


def main():
    print("torch.cuda.is_available():", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("GPU name:", torch.cuda.get_device_name(0))
    print("cwd:", os.getcwd())
    print("GNEWS_DB:", GNEWS_DB)
    print("TEST_LIMIT_UPDATE:", TEST_LIMIT_UPDATE)

    if not GNEWS_DB.exists():
        raise FileNotFoundError(GNEWS_DB)
    if not CONFLICT_DB.exists():
        raise FileNotFoundError(CONFLICT_DB)

    conflict_countries = load_conflict_countries(CONFLICT_DB)
    country_aliases = build_country_aliases_from_conflicts(conflict_countries)

    conn = sqlite3.connect(str(GNEWS_DB))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        ensure_articles_eng_schema(conn)

        missing_eng = fetch_missing_ids_for_eng(conn, TEST_LIMIT_UPDATE)
        print(f"Missing articles_eng rows: {len(missing_eng)}")
        if missing_eng:
            upsert_articles_eng(conn, missing_eng, country_aliases)
            print("articles_eng backfill done.")
    finally:
        conn.close()
        print("Closed DB connection.")


if __name__ == "__main__":
    main()
