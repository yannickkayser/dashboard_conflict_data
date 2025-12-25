import sqlite3
from pathlib import Path
import re
import zipfile
import urllib.request
import unicodedata
from typing import Optional, Iterable

# ----------------
# Paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]
GNEWS_DB = PROJECT_ROOT / "data" / "gnews_articles_from2023.db"
CONFLICT_DB = PROJECT_ROOT / "data" / "conflict_data.db"

GEONAMES_DIR = PROJECT_ROOT / "data" / "geonames"
GEONAMES_DIR.mkdir(parents=True, exist_ok=True)

GEONAMES_ZIP = GEONAMES_DIR / "cities15000.zip"
GEONAMES_TXT = GEONAMES_DIR / "cities15000.txt"  # extracted
GEONAMES_CACHE_DB = PROJECT_ROOT / "data" / "geonames_cache.db"

GEONAMES_URL = "https://download.geonames.org/export/dump/cities15000.zip"  # official dump host [web:441]

# Tables
ART_TABLE = "articles_eng"
CONFLICT_COUNTRY_TABLE = "events"
CONFLICT_COUNTRY_COL = "country"

# Performance
ARTICLE_BATCH = 2000
GEONAMES_INSERT_BATCH = 5000
TEST_LIMIT: Optional[int] = None

ONLY_UPDATE_MISSING = True

# Minimal tokenizer for matching
WORD_RE = re.compile(r"[a-z][a-z\-\.\']{2,}")  # keeps hyphen/apostrophe; min len 3


# ----------------
# Manual patches for country-name variants
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


def norm(s: str) -> str:
    return strip_accents((s or "").lower()).strip()


def download_geonames_zip():
    if GEONAMES_ZIP.exists():
        return
    print(f"Downloading GeoNames: {GEONAMES_URL} -> {GEONAMES_ZIP}")
    urllib.request.urlretrieve(GEONAMES_URL, GEONAMES_ZIP)


def extract_geonames_zip():
    if GEONAMES_TXT.exists():
        return
    print(f"Extracting: {GEONAMES_ZIP} -> {GEONAMES_TXT}")
    with zipfile.ZipFile(GEONAMES_ZIP, "r") as zf:
        # cities15000.zip contains cities15000.txt
        name = "cities15000.txt"
        zf.extract(name, GEONAMES_DIR)
        (GEONAMES_DIR / name).replace(GEONAMES_TXT)


def ensure_geonames_schema(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS geonames_city (
            geonameid INTEGER PRIMARY KEY,
            name TEXT,
            asciiname TEXT,
            alternatenames TEXT,
            country_code TEXT,
            population INTEGER
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS geonames_country (
            country_code TEXT PRIMARY KEY,
            country_name TEXT
        );
    """)
    # alias -> country_name, and keep a population for disambiguation
    cur.execute("""
        CREATE TABLE IF NOT EXISTS geonames_city_alias (
            alias_norm TEXT,
            country_name TEXT,
            population INTEGER
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_city_alias_norm ON geonames_city_alias(alias_norm);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_city_alias_country ON geonames_city_alias(country_name);")
    conn.commit()


def load_conflict_country_codes_from_geonames(conn: sqlite3.Connection):
    """
    Download and import countryInfo.txt would be ideal, but to keep this script minimal,
    this function creates country_code->country_name only for codes that appear in cities15000,
    by using a small GeoNames web-free fallback:
    - We will map country_code to country_name via the ACLED country list later.
    So geonames_country can remain empty and we use country_code only.
    """
    # Intentionally left minimal; we’ll map via ACLED country names in a later step.
    pass


def rebuild_geonames_cache():
    """
    Build geonames_city + geonames_city_alias from cities15000.txt.
    """
    print(f"Building GeoNames cache DB: {GEONAMES_CACHE_DB}")
    conn = sqlite3.connect(str(GEONAMES_CACHE_DB))
    try:
        ensure_geonames_schema(conn)
        cur = conn.cursor()

        # Clear previous load
        cur.execute("DELETE FROM geonames_city;")
        cur.execute("DELETE FROM geonames_city_alias;")
        conn.commit()

        with open(GEONAMES_TXT, "r", encoding="utf-8") as f:
            batch = []
            alias_batch = []

            for line in f:
                parts = line.rstrip("\n").split("\t")
                # GeoNames dump columns (subset we need)
                # 0 geonameid, 1 name, 2 asciiname, 3 alternatenames, 8 country_code, 14 population
                geonameid = int(parts[0])
                name = parts[1]
                asciiname = parts[2]
                alternatenames = parts[3]
                country_code = parts[8]
                population = int(parts[14]) if parts[14].isdigit() else 0

                batch.append((geonameid, name, asciiname, alternatenames, country_code, population))

                # Build aliases from name/asciiname/alternatenames
                # Keep only reasonable-length aliases to reduce noise
                def push_alias(a: str):
                    a = norm(a)
                    if len(a) < 3:
                        return
                    if len(a) > 60:
                        return
                    alias_batch.append((a, country_code, population))

                push_alias(name)
                push_alias(asciiname)
                if alternatenames:
                    for a in alternatenames.split(","):
                        push_alias(a)

                if len(batch) >= GEONAMES_INSERT_BATCH:
                    cur.executemany(
                        "INSERT INTO geonames_city (geonameid, name, asciiname, alternatenames, country_code, population) VALUES (?, ?, ?, ?, ?, ?);",
                        batch
                    )
                    # We store country_code in alias table first; later we map to ACLED country names
                    cur.executemany(
                        "INSERT INTO geonames_city_alias (alias_norm, country_name, population) VALUES (?, ?, ?);",
                        [(a, cc, pop) for (a, cc, pop) in alias_batch]
                    )
                    conn.commit()
                    batch.clear()
                    alias_batch.clear()

            if batch:
                cur.executemany(
                    "INSERT INTO geonames_city (geonameid, name, asciiname, alternatenames, country_code, population) VALUES (?, ?, ?, ?, ?, ?);",
                    batch
                )
                cur.executemany(
                    "INSERT INTO geonames_city_alias (alias_norm, country_name, population) VALUES (?, ?, ?);",
                    [(a, cc, pop) for (a, cc, pop) in alias_batch]
                )
                conn.commit()

        # Deduplicate aliases by keeping max population per (alias, country_code)
        # (city names can appear multiple times; this reduces duplicates)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS geonames_city_alias_dedup (
                alias_norm TEXT,
                country_code TEXT,
                population INTEGER,
                PRIMARY KEY (alias_norm, country_code)
            );
        """)
        cur.execute("DELETE FROM geonames_city_alias_dedup;")
        cur.execute("""
            INSERT OR REPLACE INTO geonames_city_alias_dedup(alias_norm, country_code, population)
            SELECT alias_norm, country_name AS country_code, MAX(population)
            FROM geonames_city_alias
            GROUP BY alias_norm, country_name;
        """)
        cur.execute("DROP TABLE geonames_city_alias;")
        cur.execute("ALTER TABLE geonames_city_alias_dedup RENAME TO geonames_city_alias;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_city_alias_norm ON geonames_city_alias(alias_norm);")
        conn.commit()

        n_alias = cur.execute("SELECT COUNT(*) FROM geonames_city_alias;").fetchone()[0]
        print(f"GeoNames cache built. Alias rows: {n_alias}")

    finally:
        conn.close()


def load_acled_countries(conflict_db_path: Path) -> list[str]:
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


def build_country_name_aliases(acled_countries: list[str]) -> dict[str, set[str]]:
    d: dict[str, set[str]] = {}
    for c in acled_countries:
        base = {norm(c)}
        patch = MANUAL_COUNTRY_ALIAS_PATCHES.get(c, set())
        d[c] = base | {norm(p) for p in patch}
    return d


def ensure_article_columns(conn: sqlite3.Connection):
    cur = conn.cursor()
    cols = {r[1] for r in cur.execute(f"PRAGMA table_info({ART_TABLE});").fetchall()}  # [web:246]

    wanted = [
        ("article_country", "TEXT"),
        ("article_country_score", "INTEGER"),
    ]
    for col, coltype in wanted:
        if col not in cols:
            cur.execute(f"ALTER TABLE {ART_TABLE} ADD COLUMN {col} {coltype};")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{ART_TABLE}_article_country ON {ART_TABLE}(article_country);")
    conn.commit()


def tokenize_norm(text: str) -> list[str]:
    t = norm(text)
    return WORD_RE.findall(t)


def best_country_from_text(
    text: str,
    country_aliases: dict[str, set[str]],
    geonames_conn: sqlite3.Connection,
    min_city_pop: int = 15000
) -> tuple[Optional[str], int]:
    """
    Returns (country_name, score).
    Score is heuristic: +10 for a direct country-name hit, +3 for a city hit.
    City hits are filtered by min population to reduce ambiguity.
    """
    t = norm(text)
    if not t:
        return None, 0

    score_by_country: dict[str, int] = {}

    # 1) Country-name aliases (high precision)
    for country, aliases in country_aliases.items():
        for a in aliases:
            if not a:
                continue
            # boundary-ish
            pattern = r"(?<![a-z])" + re.escape(a) + r"(?![a-z])"
            hits = len(re.findall(pattern, t))
            if hits:
                score_by_country[country] = score_by_country.get(country, 0) + 10 * hits

    # 2) City hits via GeoNames (medium precision)
    toks = set(tokenize_norm(t))
    # also try 2-grams for "new york", "gaza city", etc.
    words = tokenize_norm(t)
    bigrams = {" ".join(words[i:i+2]) for i in range(len(words)-1)}
    candidates = toks | bigrams

    cur = geonames_conn.cursor()
    for alias in candidates:
        if len(alias) < 3:
            continue
        rows = cur.execute(
            "SELECT country_code, population FROM geonames_city_alias WHERE alias_norm = ?;",
            (alias,)
        ).fetchall()
        if not rows:
            continue

        # Add scores for each matched country_code
        for cc, pop in rows:
            if pop is None:
                pop = 0
            if pop < min_city_pop:
                continue
            # temporarily store under pseudo-key cc; later map to ACLED country names
            score_by_country[cc] = score_by_country.get(cc, 0) + 3

    if not score_by_country:
        return None, 0

    # winner
    best = max(score_by_country.items(), key=lambda kv: kv[1])
    return best[0], int(best[1])


def main():
    if not GNEWS_DB.exists():
        raise FileNotFoundError(f"Missing GNews DB: {GNEWS_DB}")
    if not CONFLICT_DB.exists():
        raise FileNotFoundError(f"Missing conflict DB: {CONFLICT_DB}")

    # 1) Prepare GeoNames local cache
    download_geonames_zip()
    extract_geonames_zip()
    rebuild_geonames_cache()

    # 2) Load ACLED countries and name-aliases
    acled_countries = load_acled_countries(CONFLICT_DB)
    country_name_aliases = build_country_name_aliases(acled_countries)
    print(f"ACLED countries loaded: {len(acled_countries)}")

    # 3) Open DBs
    gconn = sqlite3.connect(str(GNEWS_DB))
    gconn.execute("PRAGMA journal_mode=WAL;")
    gconn.execute("PRAGMA synchronous=NORMAL;")

    geo_conn = sqlite3.connect(str(GEONAMES_CACHE_DB))

    try:
        ensure_article_columns(gconn)

        cur = gconn.cursor()

        where = ""
        if ONLY_UPDATE_MISSING:
            where = "WHERE article_country IS NULL OR TRIM(article_country) = ''"

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
            rows = cur.fetchmany(ARTICLE_BATCH)
            if not rows:
                break

            updates = []
            for article_id, title_en, desc_en in rows:
                text = (title_en or "") + " " + (desc_en or "")

                winner, score = best_country_from_text(
                    text=text,
                    country_aliases=country_name_aliases,
                    geonames_conn=geo_conn,
                    min_city_pop=15000
                )

                # winner can be a real ACLED country name OR a GeoNames country_code (cc)
                # For v1: if winner is a 2-letter code, leave it as-is OR map later.
                # Better: map cc->country name using countryInfo.txt (next iteration).
                article_country = winner
                updates.append((article_country, score, article_id))

            gconn.executemany(
                f"""
                UPDATE {ART_TABLE}
                SET article_country = ?,
                    article_country_score = ?
                WHERE id = ?;
                """,
                updates
            )  # UPDATE usage [web:251]
            gconn.commit()

            total += len(rows)
            print(f"Updated {total} articles")

        print("Done.")

    finally:
        geo_conn.close()
        gconn.close()


if __name__ == "__main__":
    main()
