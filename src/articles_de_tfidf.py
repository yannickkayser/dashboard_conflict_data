# gnews_update_tfidf_and_articles_eng.py

import os
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Set

from sklearn.feature_extraction.text import TfidfVectorizer  # [web:339]
from joblib import dump, load  # [web:346]
from transformers import pipeline  # [web:418]


# ----------------
# Paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
GNEWS_DB = DATA_DIR / "gnews_articles_from2023.db"
CONFLICT_DB = DATA_DIR / "conflict_data.db"
VECTORIZER_PATH = DATA_DIR / "tfidf_de_vectorizer.joblib"

SOURCE_TABLE = "articles"
TFIDF_DE_TABLE = "articles_de_tfidf"
TARGET_TABLE = "articles_eng"

CONFLICT_COUNTRY_TABLE = "events"
CONFLICT_COUNTRY_COL = "country"

# ----------------
# Runtime knobs
FIT_TFIDF = False  # True: fit + save vectorizer; False: load + transform only

TEST_LIMIT_TFIDF: Optional[int] = None   # only used when fitting or (optional) when transforming missing
TEST_LIMIT_UPDATE: Optional[int] = 100  # how many missing IDs to backfill in this run (tfidf+eng)

BATCH_SIZE = 200
TRANSLATE_BATCH_SIZE = 32

TRANSLATION_MODEL = "Helsinki-NLP/opus-mt-de-en"
ZSC_MODEL = "facebook/bart-large-mnli"

ACLED_EVENT_TYPES = [
    "Protests",
    "Battles",
    "Strategic developments",
    "Violence against civilians",
    "Riots",
    "Explosions/Remote violence",
]

TOP_N = 20
MIN_DF = 5
MAX_DF = 0.85
NGRAM_MAX = 2

GERMAN_STOPWORDS = [
    "die","der","das","den","dem","des",
    "ein","eine","einer","einem","einen",
    "und","oder","aber","dass","weil","wenn",
    "in","im","am","an","auf","aus","bei","mit","nach","von","vor","über","unter","zu",
    "ist","sind","war","waren","sein",
    "nicht","auch","nur","noch","sehr","mehr",
    "uhr","januar","februar","märz","maerz","april","mai","juni","juli","august",
    "september","oktober","november","dezember",
    "dpa","reuters",
]

GERMANY_CITY_ALIASES = {
    # --- Top cities / already present ---
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
    "Goettingen","Göttingen",

    # --- Very common additional big/medium cities (media frequent) ---
    "Muenster","Münster","Bremen",
    "Bergisch Gladbach",
    "Recklinghausen","Bottrop",
    "Remscheid","Moers","Siegen",
    "Gummersbach",
    "Iserlohn",
    "Hildesheim",
    "Salzgitter",
    "Goslar",
    "Cottbus",
    "Brandenburg an der Havel",
    "Frankfurt (Oder)","Frankfurt Oder",
    "Jena","Weimar",
    "Gera",
    "Zwickau",
    "Plauen",
    "Dessau","Dessau-Rosslau",
    "Wittenberg","Lutherstadt Wittenberg",
    "Potsdam",
    "Neubrandenburg",
    "Greifswald",
    "Stralsund",
    "Schwerin",
    "Wismar",
    "Lueneburg","Lüneburg",
    "Wilhelmshaven",
    "Emden",
    "Delmenhorst",
    "Lingen",
    "Aalen",
    "Esslingen",
    "Waiblingen",
    "Sindelfingen",
    "Ludwigsburg",
    "Reutlingen",
    "Tuebingen","Tübingen",
    "Konstanz","Constance",
    "Friedrichshafen",
    "Ravensburg",
    "Offenburg",
    "Baden-Baden",
    "Ulm",
    "Heilbronn",
    "Pforzheim",
    "Kempten",
    "Rosenheim",
    "Landshut",
    "Passau",
    "Bayreuth",
    "Bamberg",
    "Aschaffenburg",
    "Coburg",
    "Schweinfurt",
    "Erlangen",
    "Fürth","Fuerth",
    "Kaiserslautern",
    "Trier",
    "Koblenz",
    "Worms",
    "Speyer",
    "Landau","Landau in der Pfalz",
    "Neustadt","Neustadt an der Weinstraße","Neustadt an der Weinstrasse",
    "Hildesheim",
    "Celle",
    "Lueneburg","Lüneburg",
    "Rendsburg",
    "Flensburg",
    "Neumuenster","Neumünster",
    "Pinneberg",
    "Itzehoe",
    "Husum",
    "Sankt Peter-Ording","St. Peter-Ording",
    "Bremerhaven",

    # --- Capital/seat cities often in news ---
    "Potsdam","Wiesbaden","Mainz","Saarbrücken","Erfurt","Kiel","Schwerin",
    "Magdeburg","Dresden","Hanover","Hannover","Munich","München","Stuttgart",

    # --- Regions / metro areas / common German geography in media ---
    "Ruhrgebiet","Ruhr area","Ruhrpott","Rhein-Ruhr","Rhein Ruhr",
    "Rhein-Main","Rhine-Main","RheinMain","Rhein-Main-Gebiet",
    "Rhein-Neckar","Rhine-Neckar","Rhein Neckar","Metropolregion Rhein-Neckar",
    "Rheinland","Rhineland","Nordrhein-Westfalen","North Rhine-Westphalia","NRW",
    "Baden-Württemberg","Baden Wuerttemberg","Baden Wurttemberg",
    "Bayern","Bavaria",
    "Sachsen","Saxony",
    "Thüringen","Thueringen","Thuringia",
    "Brandenburg",
    "Mecklenburg-Vorpommern","Mecklenburg Western Pomerania",
    "Schleswig-Holstein","Schleswig Holstein",
    "Niedersachsen","Lower Saxony",
    "Hessen","Hesse",
    "Saarland",
    "Sachsen-Anhalt","Saxony-Anhalt",
    "Berlin",
    "Hamburg",
    "Bremen",

    # --- Areas often cited in security / disaster / politics coverage ---
    "Harz",
    "Schwarzwald","Black Forest",
    "Bodensee","Lake Constance",
    "Eifel",
    "Spreewald",
    "Erzgebirge","Ore Mountains",
    "Allgäu","Allgaeu",
    "Oberbayern","Upper Bavaria",
    "Franken","Franconia",
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
    "Thuringia","Thüringen","Thueringen", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8",
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


def build_doc(parts: List[Optional[str]]) -> str:
    return " ".join([str(p) for p in parts if p and str(p).strip()])


def ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table});").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype};")
        conn.commit()


def ensure_tfidf_schema(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TFIDF_DE_TABLE} (
            id TEXT PRIMARY KEY,
            tfidf_terms_de TEXT,
            tfidf_version TEXT
        );
    """)
    conn.commit()


def ensure_articles_eng_schema(conn: sqlite3.Connection) -> None:
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
            article_country_score INTEGER,
            event_type TEXT,
            tfidf_terms_de TEXT,
            tfidf_terms_en TEXT
        );
    """)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET_TABLE}_publishedAt ON {TARGET_TABLE}(publishedAt);")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET_TABLE}_article_country ON {TARGET_TABLE}(article_country);")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET_TABLE}_url ON {TARGET_TABLE}(url);")
    conn.commit()

    ensure_column(conn, TARGET_TABLE, "tfidf_terms_de", "TEXT")
    ensure_column(conn, TARGET_TABLE, "tfidf_terms_en", "TEXT")


# ----------------
# Country helpers

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


def guess_country(text: str, country_aliases: Dict[str, Set[str]]) -> Tuple[Optional[str], int]:
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


# ----------------
# NLP helpers

def translate_texts(translator, texts: List[str], batch_size: int) -> List[str]:
    texts = [t if isinstance(t, str) else "" for t in texts]
    if all((not t.strip()) for t in texts):
        return ["" for _ in texts]
    outs = translator(texts, batch_size=batch_size, truncation=True)  # [web:418]
    return [o["translation_text"] for o in outs]


def classify_one(acled_classifier, text_de: str) -> str:
    if not text_de or not str(text_de).strip():
        return "Other"
    try:
        res = acled_classifier(str(text_de), candidate_labels=ACLED_EVENT_TYPES, truncation=True)
        return res["labels"][0] if res.get("labels") else "Other"
    except Exception:
        return "Other"


def to_date_yyyy_mm_dd(published_at: Optional[str]) -> Optional[str]:
    if not published_at:
        return None
    s = str(published_at)
    return s[:10] if len(s) >= 10 else None


# ----------------
# TF-IDF core

def fit_and_save_vectorizer(conn: sqlite3.Connection, limit: Optional[int]) -> TfidfVectorizer:
    sql = f"SELECT title, description, content FROM {SOURCE_TABLE} ORDER BY rowid"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    docs = [build_doc([r[0], r[1], r[2]]) for r in rows]

    vectorizer = TfidfVectorizer(
        stop_words=GERMAN_STOPWORDS,
        ngram_range=(1, NGRAM_MAX),
        min_df=MIN_DF,
        max_df=MAX_DF,
    )
    vectorizer.fit(docs)  # fit once, then reuse [web:339]
    dump(vectorizer, str(VECTORIZER_PATH))  # persist for later transforms [web:346]
    print(f"Saved vectorizer to: {VECTORIZER_PATH}")
    return vectorizer


def load_vectorizer() -> TfidfVectorizer:
    if not VECTORIZER_PATH.exists():
        raise FileNotFoundError(f"Missing vectorizer file: {VECTORIZER_PATH}. Run with FIT_TFIDF=True once.")
    return load(str(VECTORIZER_PATH))  # [web:346]


def compute_top_terms_for_rows(vectorizer: TfidfVectorizer, docs: List[str], top_n: int) -> List[str]:
    X = vectorizer.transform(docs)  # use fixed weights/vocab [web:339]
    vocab = vectorizer.get_feature_names_out()

    out_terms = []
    for i in range(X.shape[0]):
        row = X.getrow(i)
        if row.nnz == 0:
            out_terms.append("")
            continue
        idx = row.indices
        data = row.data
        top_local = idx[data.argsort()[::-1][:top_n]]
        out_terms.append(",".join(vocab[top_local]))
    return out_terms


def fetch_missing_ids_for_tfidf(conn: sqlite3.Connection, limit: Optional[int]) -> List[Tuple]:
    sql = f"""
        SELECT a.id, a.title, a.description, a.content
        FROM {SOURCE_TABLE} a
        LEFT JOIN {TFIDF_DE_TABLE} t ON a.id = t.id
        WHERE t.id IS NULL
        ORDER BY a.rowid
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def upsert_tfidf_rows(conn: sqlite3.Connection, ids: List[str], terms: List[str], version: str) -> None:
    payload = [(ids[i], terms[i], version) for i in range(len(ids))]
    conn.executemany(
        f"""
        INSERT INTO {TFIDF_DE_TABLE}(id, tfidf_terms_de, tfidf_version)
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            tfidf_terms_de=excluded.tfidf_terms_de,
            tfidf_version=excluded.tfidf_version;
        """,
        payload
    )
    conn.commit()


# ----------------
# articles_eng backfill

def fetch_missing_ids_for_eng(conn: sqlite3.Connection, limit: Optional[int]) -> List[Tuple]:
    sql = f"""
        SELECT a.id, a.publishedAt, a.title, a.description, a.content, a.url, a.source_name, a.source_url,
               COALESCE(t.tfidf_terms_de, '')
        FROM {SOURCE_TABLE} a
        LEFT JOIN {TARGET_TABLE} e ON a.id = e.id
        LEFT JOIN {TFIDF_DE_TABLE} t ON a.id = t.id
        WHERE e.id IS NULL
        ORDER BY a.rowid
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def upsert_articles_eng(conn: sqlite3.Connection, rows: List[Tuple], country_aliases: Dict[str, Set[str]]) -> None:
    translator = pipeline("translation_de_to_en", model=TRANSLATION_MODEL)  # [web:418]
    acled_classifier = pipeline("zero-shot-classification", model=ZSC_MODEL)  # [web:418]

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]

        ids = [r[0] for r in batch]
        published = [to_date_yyyy_mm_dd(r[1]) for r in batch]
        title_de = [r[2] for r in batch]
        desc_de = [r[3] for r in batch]
        content_de = [r[4] for r in batch]
        url = [r[5] for r in batch]
        source_name = [r[6] for r in batch]
        source_url = [r[7] for r in batch]
        tfidf_terms_de = [r[8] for r in batch]

        title_en = translate_texts(translator, title_de, TRANSLATE_BATCH_SIZE)
        desc_en = translate_texts(translator, desc_de, TRANSLATE_BATCH_SIZE)
        tfidf_terms_en = translate_texts(translator, tfidf_terms_de, TRANSLATE_BATCH_SIZE)

        # --- NEW: zero-shot batch call ---
        analysis_text_de_list = [
            f"{title_de[i] or ''} {desc_de[i] or ''} {content_de[i] or ''}".strip()
            for i in range(len(batch))
        ]
        zsc_out = acled_classifier(
            analysis_text_de_list,
            candidate_labels=ACLED_EVENT_TYPES,
            truncation=True,
        )  # list in -> list out [web:418]
        event_types = [(o["labels"][0] if o and o.get("labels") else "Other") for o in zsc_out]
        # --- END NEW ---

        payload = []
        for i in range(len(batch)):
            analysis_text_en = f"{title_en[i] or ''} {desc_en[i] or ''}".strip()

            country, score = guess_country(analysis_text_en, country_aliases)
            event_type = event_types[i]

            payload.append((
                ids[i], published[i], url[i], source_name[i], source_url[i],
                title_en[i], desc_en[i], country, score, event_type,
                tfidf_terms_de[i], tfidf_terms_en[i],
            ))

        conn.executemany(
            f"""
            INSERT INTO {TARGET_TABLE} (
                id, publishedAt, url, source_name, source_url,
                title_en, description_en,
                article_country, article_country_score,
                event_type,
                tfidf_terms_de, tfidf_terms_en
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                publishedAt=excluded.publishedAt,
                url=excluded.url,
                source_name=excluded.source_name,
                source_url=excluded.source_url,
                title_en=excluded.title_en,
                description_en=excluded.description_en,
                article_country=excluded.article_country,
                article_country_score=excluded.article_country_score,
                event_type=excluded.event_type,
                tfidf_terms_de=excluded.tfidf_terms_de,
                tfidf_terms_en=excluded.tfidf_terms_en;
            """,
            payload
        )
        conn.commit()
        print(f"articles_eng upserted: {min(start + BATCH_SIZE, len(rows))}/{len(rows)}")



# ----------------
# Main

def main():
    print("cwd:", os.getcwd())
    print("GNEWS_DB:", GNEWS_DB)
    print("VECTOR:", VECTORIZER_PATH)
    print("FIT_TFIDF:", FIT_TFIDF)
    print("TEST_LIMIT_TFIDF:", TEST_LIMIT_TFIDF)
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
        ensure_tfidf_schema(conn)
        ensure_articles_eng_schema(conn)

        # 1) TF-IDF stage
        if FIT_TFIDF:
            vectorizer = fit_and_save_vectorizer(conn, TEST_LIMIT_TFIDF)
        else:
            vectorizer = load_vectorizer()

        # Fill only missing tfidf rows
        missing_tfidf = fetch_missing_ids_for_tfidf(conn, TEST_LIMIT_UPDATE)
        print(f"Missing TF-IDF rows: {len(missing_tfidf)}")
        if missing_tfidf:
            ids = [r[0] for r in missing_tfidf]
            docs = [build_doc([r[1], r[2], r[3]]) for r in missing_tfidf]
            terms = compute_top_terms_for_rows(vectorizer, docs, TOP_N)
            version = f"frozenVec_top{TOP_N}_minDf{MIN_DF}_maxDf{MAX_DF}_ng{NGRAM_MAX}"
            upsert_tfidf_rows(conn, ids, terms, version)
            print("TF-IDF backfill done.")

        # 2) articles_eng stage (translate + country + event_type), only missing ids
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
