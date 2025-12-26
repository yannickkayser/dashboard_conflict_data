# gnews_translate_to_articles_eng_no_content.py
#
# Builds articles_eng from articles, translates title/description/content,
# copies original content into articles_eng.content, and stores English translation in content_en.
# Also computes article_country + kw_1..kw_3 using conflict-country aliases.

import os
import re
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Optional, Iterable, Tuple, List

from transformers import pipeline


# ----------------
# Paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]
GNEWS_DB = PROJECT_ROOT / "data" / "gnews_articles_from2023.db"
CONFLICT_DB = PROJECT_ROOT / "data" / "conflict_data.db"

SOURCE_TABLE = "articles"
TARGET_TABLE = "articles_eng"

CONFLICT_COUNTRY_TABLE = "events"
CONFLICT_COUNTRY_COL = "country"


# ----------------
# Runtime knobs
BATCH_SIZE = 50                      # smaller, because content translation is heavy
TEST_LIMIT: Optional[int] = None      # set None for full run (start small!)
TRANSLATION_MODEL = "Helsinki-NLP/opus-mt-de-en"

# Chunking to avoid truncation
CONTENT_CHUNK_CHARS = 2000           # rough, conservative; adjust if too slow
MAX_EMPTY_CONTENT_FRACTION_WARN = 0.2


# ----------------
# Keyword extraction
TOKEN_RE = re.compile(r"[A-Za-z]{3,}")

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

GERMANY_CITY_ALIASES = {
    "Berlin","Hamburg","Munich","Muenchen","München","Cologne","Koeln","Köln",
    "Frankfurt","Frankfurt am Main","Dusseldorf","Düsseldorf","Stuttgart","Leipzig",
    "Dortmund","Bremen","Essen","Dresden","Nuremberg","Nuernberg","Nürnberg",
    "Hanover","Hannover","Duisburg","Wuppertal","Bochum","Bielefeld","Bonn",
    "Mannheim","Karlsruhe","Augsburg","Wiesbaden","Gelsenkirchen",
    "Monchengladbach","Mönchengladbach","Braunschweig","Chemnitz","Kiel","Aachen",
    "Halle","Halle (Saale)","Magdeburg","Freiburg","Freiburg im Breisgau",
    "Krefeld","Luebeck","Lübeck","Oberhausen","Erfurt","Mainz","Rostock","Kassel",
    "Hagen","Saarbruecken","Saarbrücken","Hamm","Potsdam","Ludwigshafen",
    "Ludwigshafen am Rhein","Oldenburg","Leverkusen","Osnabrueck","Osnabrück",
    "Solingen","Heidelberg","Herne","Neuss","Darmstadt","Paderborn","Regensburg",
    "Ingolstadt","Wuerzburg","Würzburg","Ulm","Offenbach","Offenbach am Main",
    "Heilbronn","Pforzheim","Wolfsburg","Goettingen","Göttingen",
}

GERMANY_STATE_ALIASES = {
    "Baden-Württemberg","Baden Württemberg","Bavaria","Bayern","Berlin","Brandenburg",
    "Bremen","Hamburg","Hesse","Hessen","Lower Saxony","Niedersachsen",
    "Mecklenburg-Western Pomerania","Mecklenburg-Vorpommern","North Rhine-Westphalia",
    "Nordrhein-Westfalen","Rhineland-Palatinate","Rheinland-Pfalz","Saarland","Saxony",
    "Sachsen","Saxony-Anhalt","Sachsen-Anhalt","Schleswig-Holstein","Thuringia",
    "Thüringen","Thueringen",
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


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def normalize_text(s: str) -> str:
    return strip_accents((s or "").lower())

def regex_count(text_norm: str, alias_norm: str) -> int:
    pattern = r"(?<![a-z])" + re.escape(alias_norm) + r"(?![a-z])"
    return len(re.findall(pattern, text_norm))

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
        extra = (
            {"germany", "german", "deutschland"} |
            {normalize_text(x) for x in GERMANY_CITY_ALIASES} |
            {normalize_text(x) for x in GERMANY_STATE_ALIASES}
        )
        aliases["Germany"] |= extra
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
    if not text or not text.strip():
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

def ensure_target_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            publishedAt TEXT,
            url TEXT,
            source_name TEXT,
            source_url TEXT,

            title_en TEXT,
            description_en TEXT,

            content TEXT,        -- original content (copy from source)
            content_en TEXT,     -- translated content

            article_country TEXT,
            article_country_score INTEGER,
            kw_1 TEXT,
            kw_2 TEXT,
            kw_3 TEXT
        );
    """)
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET_TABLE}_publishedAt ON {TARGET_TABLE}(publishedAt);")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET_TABLE}_article_country ON {TARGET_TABLE}(article_country);")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET_TABLE}_url ON {TARGET_TABLE}(url);")
    conn.commit()

def clear_target_table(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(f"DELETE FROM {TARGET_TABLE};")
    cur.execute("DELETE FROM sqlite_sequence WHERE name = ?;", (TARGET_TABLE,))
    conn.commit()

def fetch_source_batches(conn: sqlite3.Connection, batch_size: int, limit: Optional[int] = None) -> Iterable[list[Tuple]]:
    cur = conn.cursor()
    base_sql = f"""
        SELECT
            publishedAt,
            title,
            description,
            content,
            url,
            source_name,
            source_url
        FROM {SOURCE_TABLE}
        ORDER BY rowid
    """
    if limit is not None:
        base_sql += f" LIMIT {int(limit)}"
    cur.execute(base_sql)

    while True:
        rows = cur.fetchmany(batch_size)
        if not rows:
            break
        yield rows

def to_date_yyyy_mm_dd(published_at: Optional[str]) -> Optional[str]:
    if not published_at:
        return None
    s = str(published_at)
    return s[:10] if len(s) >= 10 else None

def chunk_text(s: str, chunk_chars: int) -> List[str]:
    s = s or ""
    s = s.strip()
    if not s:
        return [""]

    # split on paragraph boundaries when possible
    parts = re.split(r"\n\s*\n+", s)
    chunks: List[str] = []
    cur = ""

    def flush():
        nonlocal cur
        if cur.strip():
            chunks.append(cur.strip())
        cur = ""

    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) > chunk_chars:
            # hard split long paragraphs
            for i in range(0, len(p), chunk_chars):
                seg = p[i:i+chunk_chars]
                if len(cur) + 1 + len(seg) > chunk_chars:
                    flush()
                cur = (cur + "\n" + seg).strip() if cur else seg
        else:
            if len(cur) + 2 + len(p) > chunk_chars:
                flush()
            cur = (cur + "\n\n" + p).strip() if cur else p

    flush()
    return chunks or [""]

def translate_texts(translator, texts: list[str]) -> list[str]:
    # Pipeline truncation: if truncation=True and no max_length is set, the tokenizer/model limits apply,
    # so we avoid losing content by chunking before calling the pipeline. [web:989]
    texts = [t if isinstance(t, str) else "" for t in texts]
    if all((not t.strip()) for t in texts):
        return ["" for _ in texts]
    outputs = translator(texts, batch_size=8, truncation=True)
    return [o["translation_text"] for o in outputs]

def translate_long_text(translator, text: str) -> str:
    chunks = chunk_text(text, CONTENT_CHUNK_CHARS)
    out_chunks = translate_texts(translator, chunks)
    return "\n\n".join([c for c in out_chunks if c is not None])

def insert_translated_batch(
    conn: sqlite3.Connection,
    batch_rows: list[Tuple],
    translator,
    country_aliases: dict[str, set[str]],
) -> None:
    publishedAt = [r[0] for r in batch_rows]
    title = [r[1] for r in batch_rows]
    description = [r[2] for r in batch_rows]
    content = [r[3] for r in batch_rows]
    url = [r[4] for r in batch_rows]
    source_name = [r[5] for r in batch_rows]
    source_url = [r[6] for r in batch_rows]

    published_date = [to_date_yyyy_mm_dd(x) for x in publishedAt]

    # translate short fields in batch
    title_en = translate_texts(translator, title)
    description_en = translate_texts(translator, description)

    # translate content row-by-row (because it's long and chunked)
    content_en = [translate_long_text(translator, c or "") for c in content]

    to_insert = []
    for i in range(len(batch_rows)):
        analysis_text = (title_en[i] or "") + " " + (description_en[i] or "") + " " + (content_en[i] or "")
        country, score = guess_country(analysis_text, country_aliases)
        kw1, kw2, kw3 = top_keywords(analysis_text, k=3)

        to_insert.append((
            published_date[i], url[i], source_name[i], source_url[i],
            title_en[i], description_en[i],
            content[i], content_en[i],
            country, score, kw1, kw2, kw3
        ))

    cur = conn.cursor()
    cur.executemany(f"""
        INSERT INTO {TARGET_TABLE} (
            publishedAt, url, source_name, source_url,
            title_en, description_en,
            content, content_en,
            article_country, article_country_score,
            kw_1, kw_2, kw_3
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, to_insert)
    conn.commit()

def main():
    print("cwd:", os.getcwd())
    print("GNEWS_DB:", GNEWS_DB)
    print("CONFLICT_DB:", CONFLICT_DB)

    if not GNEWS_DB.exists():
        raise FileNotFoundError(f"Missing GNews DB: {GNEWS_DB}")
    if not CONFLICT_DB.exists():
        raise FileNotFoundError(f"Missing conflict DB: {CONFLICT_DB}")

    conflict_countries = load_conflict_countries(CONFLICT_DB)
    print(f"Loaded {len(conflict_countries)} countries from conflict_data.db")
    country_aliases = build_country_aliases_from_conflicts(conflict_countries)

    conn = sqlite3.connect(str(GNEWS_DB))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        ensure_target_schema(conn)

        print(f"Clearing target table: {TARGET_TABLE}")
        clear_target_table(conn)

        print(f"Loading translation model: {TRANSLATION_MODEL}")
        translator = pipeline("translation_de_to_en", model=TRANSLATION_MODEL)

        total_in = 0
        empty_content = 0

        for batch in fetch_source_batches(conn, BATCH_SIZE, TEST_LIMIT):
            # quick monitoring
            empty_content += sum(1 for r in batch if not (r[3] or "").strip())
            insert_translated_batch(conn, batch, translator, country_aliases)
            total_in += len(batch)
            print(f"Processed: {total_in} rows")

        if total_in > 0:
            frac = empty_content / total_in
            if frac > MAX_EMPTY_CONTENT_FRACTION_WARN:
                print(f"Warning: {frac:.1%} rows had empty content")

        n = conn.execute(f"SELECT COUNT(*) FROM {TARGET_TABLE};").fetchone()[0]
        print(f"Done. {TARGET_TABLE} rows: {n}")
    finally:
        conn.close()
        print("Closed DB connection.")

if __name__ == "__main__":
    main()