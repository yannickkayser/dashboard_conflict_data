#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
import altair as alt

# Underepresentation map imports
from pathlib import Path
import sqlite3
import json
import urllib.request

import numpy as np
import pydeck as pdk
import geopandas as gpd

# Sentiment imports
import plotly.express as px
import plotly.graph_objects as go
from transformers import pipeline

# ChatBot imports
import re

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

# UI
#import time
#from streamlit_autorefresh import st_autorefresh


# -------------------------
# Paths
# -------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

CONFLICT_DB = DATA_DIR / "conflict_data.db"
MATCHING_DB = DATA_DIR / "matching_country.db" #comment
#MATCHING_DB = DATA_DIR / "matched_conflict.db" #uncomment
GNEWS_DB = DATA_DIR / "deleted_dupgnews2023.db"

COUNTRY_TABLE = "conflict_country"
MATCH_TABLE = "match_country_slim"
CONFLICT_FEATURES_TABLE = "conflict_features"

st.set_page_config(page_title="Conflict Media Mirror", layout="wide")

# ------------------------- 
# Init Sweep
# -------------------------
if "page" not in st.session_state:
    st.session_state.page = "landing"
if "anim_nonce" not in st.session_state:
    st.session_state.anim_nonce = 0

# -------------------------
# Styling
# -------------------------

# -------------------------
# Background + cards + spacing
# ------------------------



# -------------------------
# Headline Sweep
# -------------------------
nonce = st.session_state.anim_nonce
anim_name = f"cmm_sweep_{nonce}"

st.markdown(
    f"""
<style>
/* Layout spacing */
.block-container {{
  padding-top: 2.0rem;
  padding-bottom: 2.5rem;
  max-width: 1400px;
}}

.cmm-hero {{
  margin: 0 0 1.2rem 0;
}}

.cmm-title-top {{
  font-size: 3.1rem;
  font-weight: 650;
  letter-spacing: -0.03em;
  line-height: 0.95;
  margin: 0;
}}

.cmm-title-bottom {{
  font-size: 3.1rem;
  font-weight: 650;
  letter-spacing: -0.02em;
  line-height: 0.95;
  margin: 0;
}}

/* Axis base (fade at ends) */
.cmm-axis-fade {{
  height: 2px;
  width: 520px;
  max-width: 80vw;
  margin: 0.55rem 0;

  /* your sweeping highlight background */
  background-image: linear-gradient(
    90deg,
    rgba(0,0,0,0) 0%,
    rgba(0,0,0,0.16) 22%,
    rgba(180,180,180,0.70) 50%,
    rgba(0,0,0,0.16) 78%,
    rgba(0,0,0,0) 100%
  );
  background-size: 220% 100%;
  background-position: 0% 50%;
  background-repeat: no-repeat;

  animation: cmm_sweep_X 1.2s ease-out forwards; /* keep your {anim_name} here */

  /* THIS is what creates the fade at both ends */
  -webkit-mask-image: linear-gradient(
    90deg,
    rgba(0,0,0,0) 0%,
    rgba(0,0,0,1) 14%,
    rgba(0,0,0,1) 86%,
    rgba(0,0,0,0) 100%
  );
  mask-image: linear-gradient(
    90deg,
    rgba(0,0,0,0) 0%,
    rgba(0,0,0,1) 14%,
    rgba(0,0,0,1) 86%,
    rgba(0,0,0,0) 100%
  );
}}

/* IMPORTANT: make sweep styles win over title colors */
.cmm-title-top.cmm-sweep-text,
.cmm-title-bottom.cmm-sweep-text {{
  background-image: linear-gradient(
    90deg,
    rgba(0,0,0,0.88) 0%,
    rgba(0,0,0,0.88) 42%,
    rgba(180,180,180,0.98) 50%,
    rgba(0,0,0,0.88) 58%,
    rgba(0,0,0,0.88) 100%
  );
  background-size: 220% 100%;
  background-position: 0% 50%;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent; /* helps Chrome/Safari */
  color: transparent;
  animation: {anim_name} 1.2s ease-out forwards;
}}

@keyframes {anim_name} {{
  0%   {{ background-position: 0% 50%; }}
  75%  {{ background-position: 100% 50%; }}
  100% {{ background-position: 50% 50%; }}
}}
</style>
""",
    unsafe_allow_html=True,
)

# ===== Button styles with mirror effect =====
st.markdown(
    """
    <style>
        button {
            background-color: white;
            border: 1px solid rgba(0,0,0,0.14);   /* slightly stronger border */
            border-radius: 14px;
            font-weight: 500;
            transition: all 0.18s ease;
        }

        /* Hover: clearer mirror reflection */
        button:hover {
            background:
                linear-gradient(90deg,
                    rgba(255,255,255,0.98) 0%,
                    rgba(180,180,180,0.32) 50%,
                    rgba(255,255,255,0.98) 100%);
        }

        /* Active & selected: mirror stays */
        button:active,
        button[aria-pressed="true"] {
            background:
                linear-gradient(90deg,
                    rgba(240,240,240,1.0) 0%,
                    rgba(170,170,170,0.45) 50%,
                    rgba(240,240,240,1.0) 100%);
            border-color: rgba(0,0,0,0.14);
            transform: scale(0.98);
        }

        /* Selected scenario button (you disable it) -> keep mirror */
        button:disabled {
            background:
                linear-gradient(90deg,
                    rgba(240,240,240,1.0) 0%,
                    rgba(170,170,170,0.45) 50%,
                    rgba(240,240,240,1.0) 100%);
            border-color: rgba(0,0,0,0.14);
            transform: none;          /* don't "pressed" shrink permanently */
            opacity: 1 !important;    /* Streamlit dims disabled buttons; override */
            cursor: default;
        }

    </style>
    """,
    unsafe_allow_html=True
)


# Sentiment analysis
st.markdown("""
<style>
[data-testid="stContainer"] {
    border-radius: 14px;
    padding: 12px;
}
</style>
""", unsafe_allow_html=True)


# -------------------------
# Main Header
# -------------------------
st.markdown(
    """
    <div class="cmm-hero">
      <div class="cmm-title-top cmm-sweep-text">Conflict Media</div>
      <div class="cmm-axis-fade"></div>
      <div class="cmm-title-bottom cmm-sweep-text">Mirror</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# -------------------------
# DB helpers
# -------------------------
@st.cache_resource
def get_conf_conn():
    return sqlite3.connect(str(CONFLICT_DB), check_same_thread=False)

@st.cache_resource
def get_gnews_conn():
    return sqlite3.connect(str(GNEWS_DB), check_same_thread=False)

@st.cache_resource
def get_match_conn():
    return sqlite3.connect(str(MATCHING_DB), check_same_thread=False)


@st.cache_data(ttl=30)
def qdf_conf(sql: str, params=None) -> pd.DataFrame:
    return pd.read_sql_query(sql, get_conf_conn(), params=params or [])


@st.cache_data(ttl=30)
def qdf_match(sql: str, params=None) -> pd.DataFrame:
    return pd.read_sql_query(sql, get_match_conn(), params=params or [])


@st.cache_data(ttl=60)
def qdf_conf_features(country: str) -> pd.DataFrame:
    sql = f"""
        SELECT country, event_type_mode, disorder_type_mode,
               primary_assoc_actor_1, n_events, total_fatalities,
               start_date, end_date, duration_days
        FROM {CONFLICT_FEATURES_TABLE}
        WHERE country = ?
    """
    return pd.read_sql_query(sql, get_conf_conn(), params=[country])


@st.cache_data(ttl=300)
def table_cols(which: str, table: str) -> set[str]:
    conn = get_conf_conn() if which == "conf" else get_match_conn()
    df = pd.read_sql_query(f"PRAGMA table_info({table})", conn)
    return set(df["name"].tolist())

@st.cache_data
def _get_country_date_bounds(country: str):
    con = get_conf_conn()
    row = con.execute(
        """
        SELECT MIN(event_date), MAX(event_date)
        FROM events
        WHERE country = ?
          AND event_date IS NOT NULL
          AND event_date <> ''
        """,
        (country,),
    ).fetchone()
    return row[0], row[1]  # strings like "YYYY-MM-DD"


def pick(colset: set[str], *names, required=False):
    for n in names:
        if n in colset:
            return n
    if required:
        raise RuntimeError(f"None of the columns exist: {names}")
    return None


def contains_filter(df: pd.DataFrame, col: str, needle: str) -> pd.DataFrame:
    if not needle or col not in df.columns:
        return df
    return df[df[col].astype(str).str.lower().str.contains(needle.lower(), na=False)]


def safe_div(a, b):
    a = float(a) if a is not None else 0.0
    b = float(b) if b is not None else 0.0
    return (a / b) if b > 0 else None


def to_month(dt: pd.Series) -> pd.Series:
    return dt.dt.to_period("M").astype(str)

@st.cache_data(ttl=60)
def build_indices_live() -> pd.DataFrame:
    # 1) ISO3 mapping from precomputed table (only for iso_a3 <-> country)
    idx = load_indices()[["country", "iso_a3"]].drop_duplicates()

    # 2) Live conflict totals (same as explorer uses via `conf`)
    cc = qdf_conf(f"""
        SELECT
            {C_COUNTRY} AS country,
            {C_EVENTS}  AS n_events,
            {C_FATAL}   AS total_fatalities
        FROM {COUNTRY_TABLE}
    """)

    # 3) Live article totals (same DB/table as explorer KPI)
    ac = qdf_match(f"""
        SELECT {M_COUNTRY} AS country, COUNT(*) AS n_articles
        FROM {MATCH_TABLE}
        GROUP BY {M_COUNTRY}
    """)

    df = cc.merge(ac, on="country", how="left").fillna({"n_articles": 0})
    df["n_articles"] = df["n_articles"].astype(int)

    # 4) Attach iso_a3 (for map join)
    df = df.merge(idx, on="country", how="left")

    # 5) Recompute shares from LIVE totals (so undercoverage score matches the explorer’s data)
    tot_articles = df["n_articles"].sum()
    tot_events   = df["n_events"].sum()
    tot_fat      = df["total_fatalities"].sum()

    df["share_articles"]    = df["n_articles"] / tot_articles if tot_articles else 0.0
    df["share_events"]      = df["n_events"] / tot_events     if tot_events else 0.0
    df["share_fatalities"]  = df["total_fatalities"] / tot_fat if tot_fat else 0.0

    return df



# -------------------------
# Check DBs exist
# -------------------------
if not CONFLICT_DB.exists():
    st.error(f"Missing DB: {CONFLICT_DB}")
    st.stop()
if not MATCHING_DB.exists():
    st.error(f"Missing DB: {MATCHING_DB}")
    st.stop()

if not GNEWS_DB.exists():
    st.error(f"Missing DB: {GNEWS_DB}")
    st.stop()

CC_COLS = table_cols("conf", COUNTRY_TABLE)
MC_COLS = table_cols("match", MATCH_TABLE)

# --- conflict_country required columns ---
C_COUNTRY = pick(CC_COLS, "country", required=True)
C_EVENTS = pick(CC_COLS, "n_events", required=True)
C_FATAL = pick(CC_COLS, "total_fatal", "total_fatalities", required=True)

# --- match table required columns ---
M_COUNTRY = pick(MC_COLS, "art_article_country", "article_country", required=True)
A_ID = pick(MC_COLS, "art_id", "article_id", required=True)
A_PUB = pick(MC_COLS, "art_publishedAt", "publishedAt", "article_date", required=True)
A_SOURCE = pick(MC_COLS, "art_source_name", "source_name", required=True)
A_TITLE = pick(MC_COLS, "art_title_en", "title_en", "title", required=True)
A_DESC = pick(MC_COLS, "art_description_en", "description_en", "description", required=True)
A_URL = pick(MC_COLS, "art_url", "url", required=False)

# -------------------------
# Load base data (country-level)
# -------------------------
conf = qdf_conf(f"SELECT * FROM {COUNTRY_TABLE}")
conf[C_FATAL] = pd.to_numeric(conf[C_FATAL], errors="coerce").fillna(0)
conf[C_EVENTS] = pd.to_numeric(conf[C_EVENTS], errors="coerce").fillna(0)

# Article counts per country
art_counts = qdf_match(
    f"""
    SELECT {M_COUNTRY} AS country, COUNT(*) AS n_articles
    FROM {MATCH_TABLE}
    GROUP BY {M_COUNTRY}
    """
)
art_counts["n_articles"] = pd.to_numeric(
    art_counts["n_articles"], errors="coerce"
).fillna(0).astype(int)

conf = conf.rename(
    columns={C_COUNTRY: "country", C_EVENTS: "n_events", C_FATAL: "total_fatalities"}
)
C_COUNTRY, C_EVENTS, C_FATAL = "country", "n_events", "total_fatalities"

conf = conf.merge(art_counts, how="left", on="country")
conf["n_articles"] = conf["n_articles"].fillna(0).astype(int)

# Simple media-coverage ratios
conf["articles_per_event"] = conf.apply(
    lambda r: safe_div(r["n_articles"], r[C_EVENTS]), axis=1
)
conf["articles_per_100_fatal"] = conf.apply(
    lambda r: safe_div(r["n_articles"] * 100.0, r[C_FATAL]), axis=1
)


# --------------------------
# Helpers Underrepresentation Tab
# --------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_CONFLICT = PROJECT_ROOT / "data" / "conflict_data.db"

st.set_page_config(page_title="Conflict Media Mirror", layout="wide")


@st.cache_data
def load_indices() -> pd.DataFrame:
    con = sqlite3.connect(DB_CONFLICT)
    df = pd.read_sql_query("SELECT * FROM country_indices;", con)
    con.close()
    return df


@st.cache_data
def load_world() -> gpd.GeoDataFrame:
    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(exist_ok=True)

    # Natural Earth Admin 0 countries (110m) zip (hosted by NACIS CDN)
    url = "https://naciscdn.org/naturalearth/110m/cultural/ne_110m_admin_0_countries.zip"
    local_zip = data_dir / "ne_110m_admin_0_countries.zip"

    if not local_zip.exists():
        urllib.request.urlretrieve(url, local_zip)

    world = gpd.read_file(local_zip)

    # Normalize likely column names
    cols = {c.lower(): c for c in world.columns}
    name_col = cols.get("name") or cols.get("admin")
    iso_col = cols.get("iso_a3")
    adm0_col = cols.get("adm0_a3")
    sov_col  = cols.get("sov_a3")

    if not name_col:
        raise KeyError("Natural Earth file does not contain a 'name' or 'admin' column for country names.")

    keep = [name_col, "geometry"]
    if iso_col: keep.insert(1, iso_col)
    if adm0_col: keep.insert(2, adm0_col)
    if sov_col:  keep.insert(3, sov_col)
        
    
    world = world[keep].copy()
    world = world.rename(columns={name_col: "name"})
    if iso_col:
        world = world.rename(columns={iso_col: "iso_a3"})
    else:
        world["iso_a3"] = None
    if adm0_col: world = world.rename(columns={adm0_col: "adm0_a3"})
    if sov_col:  world = world.rename(columns={sov_col: "sov_a3"})

    world["iso_a3"] = world["iso_a3"].astype(str)

    # Fix Natural Earth -99 placeholders
    if "adm0_a3" in world.columns:
        m = world["iso_a3"].eq("-99")
        world.loc[m, "iso_a3"] = world.loc[m, "adm0_a3"].astype(str)

    if "sov_a3" in world.columns:
        m = world["iso_a3"].eq("-99")
        world.loc[m, "iso_a3"] = world.loc[m, "sov_a3"].astype(str)

    # Optional normalization for Kosovo (depends what your DB uses)
    world["iso_a3"] = world["iso_a3"].replace({"KOS": "XKX"})

    world = world.set_crs(epsg=4326, allow_override=True).to_crs(epsg=4326)
    return world


def coverage_to_rgba(cov: float) -> list[int]:
    """
    Map coverage_index in [0,1] to an RGBA color.
    Low coverage -> greenish, high coverage -> reddish.
    Missing/invalid -> grey.
    """
    if cov is None or not np.isfinite(cov):
        return [200, 200, 200, 120]
    cov = float(np.clip(cov, 0.0, 1.0))
    r = int(255 * cov)
    g = int(255 * (1.0 - cov))
    b = 80
    a = 180
    return [r, g, b, a]


def build_geojson(world: gpd.GeoDataFrame, df_plot: pd.DataFrame, color_gamma: float = 0.6) -> tuple[dict, gpd.GeoDataFrame]:
    # Merge by ISO3 code
    merged = world.merge(df_plot, on="iso_a3", how="left")

    # Only fill what we actually use for rendering
    merged["severity_share"] = merged["severity_share"].fillna(0.0)

    cov_nonnull = merged["share_articles"].dropna()
    if cov_nonnull.empty:
        merged["fill_color"] = [[200, 200, 200, 120]] * len(merged)
        return json.loads(merged.to_json()), merged

    eps = 1e-6
    vals = np.log10(cov_nonnull + eps)
    lo, hi = vals.quantile([0.05, 0.95])
    hi = max(hi, lo + 1e-9)

    def cov_to_color(cov: float) -> list[int]:
        if cov is None or not np.isfinite(cov):
            return [200, 200, 200, 120]
        x = (np.log10(cov + eps) - lo) / (hi - lo)
        x = float(np.clip(x, 0.0, 1.0))
        x = x ** color_gamma

        # x=0 (low coverage) -> pink, x=1 (high coverage) -> gray
        pink = np.array([255, 105, 180], dtype=float)
        gray = np.array([180, 180, 180], dtype=float)
        rgb = (1.0 - x) * pink + x * gray

        return [int(rgb[0]), int(rgb[1]), int(rgb[2]), 200]

    merged["fill_color"] = merged["share_articles"].apply(cov_to_color)


    return json.loads(merged.to_json()), merged

def gap_to_color(g: float, clip: float = 0.01, gamma: float = 1.0) -> list[int]:
    if g is None or not np.isfinite(g):
        return [200, 200, 200, 120]
    g = float(np.clip(g, -clip, clip)) / clip  # -> [-1, 1]
    g = np.sign(g) * (abs(g) ** gamma)

    grey = np.array([180, 180, 180], dtype=float)
    pink = np.array([255, 105, 180], dtype=float)  # undercovered
    blue = np.array([80, 130, 255], dtype=float)   # overcovered

    if g < 0:
        t = -g
        rgb = (1 - t) * grey + t * pink
    else:
        t = g
        rgb = (1 - t) * grey + t * blue

    return [int(rgb[0]), int(rgb[1]), int(rgb[2]), 200]


def build_geojson_underrep(world: gpd.GeoDataFrame, df_plot: pd.DataFrame, clip: float, gamma: float) -> tuple[dict, gpd.GeoDataFrame]:
    merged = world.merge(df_plot, on="iso_a3", how="left")
    merged["underrep_share"] = pd.to_numeric(merged["underrep_share"], errors="coerce")
    merged["fill_color"] = merged["underrep_share"].apply(lambda x: gap_to_color(x, clip=clip, gamma=gamma))
    return json.loads(merged.to_json()), merged




# -------------------------
# Chatbot helpers (FTS5 + recent-events fallback)
# -------------------------

_CHAT_STOPWORDS = {
    "what", "whats", "is", "are", "was", "were", "be", "been", "being",
    "going", "on", "in", "this", "that", "the", "a", "an", "and", "or", "to",
    "of", "for", "with", "about", "please", "country", "happening", "happen",
    "tell", "me", "give", "overview", "summary", "currently", "now",
}

@st.cache_resource
def get_openai_client():
    """Create OpenAI client once. Returns None if package/key is missing."""
    if OpenAI is None:
        return None

    api_key = None
    # Allow either OPENAI_API_KEY at root or under [general]
    try:
        api_key = st.secrets.get("OPENAI_API_KEY")
    except Exception:
        api_key = None
    if not api_key:
        try:
            api_key = st.secrets.get("general", {}).get("OPENAI_API_KEY")
        except Exception:
            api_key = None

    if not api_key:
        return None
    return OpenAI(api_key=api_key)

def _sanitize_fts_query(user_text: str) -> str:
    """Turn user text into a forgiving FTS5 MATCH query."""
    user_text = user_text.replace('"', " ").replace("'", " ")
    tokens = re.findall(r"[A-Za-z0-9_]+", user_text.lower())

    # Remove generic words + very short tokens
    tokens = [t for t in tokens if t not in _CHAT_STOPWORDS and len(t) >= 3]
    if not tokens:
        return ""

    # Prefix-match longer tokens to increase recall (demonstrat*, offic*, etc.)
    tokens = tokens[:20]
    tokens = [f"{t}*" if len(t) >= 6 else t for t in tokens]
    return " OR ".join(tokens)


def _retrieve_notes(country: str, question: str, start_date: str, end_date: str, k: int):
    """
    Retrieve notes for a country within a chosen date range.
    1) Try FTS MATCH (if we can build a meaningful query)
    2) Fallback to most recent notes within the same date range

    Returns: (rows, used_fallback)
      rows: List[Tuple[event_id_cnty, event_date, notes]]
    """
    con = get_conf_conn()
    match_q = _sanitize_fts_query(question)

    if match_q:
        sql_fts = """
            SELECT e.event_id_cnty, e.event_date, e.notes
            FROM events_fts
            JOIN events e ON e.rowid = events_fts.rowid
            WHERE events_fts.country = ?
              AND e.event_date IS NOT NULL
              AND e.event_date BETWEEN ? AND ?
              AND events_fts MATCH ?
            ORDER BY bm25(events_fts)
            LIMIT ?;
        """
        try:
            rows = con.execute(
                sql_fts, (country, start_date, end_date, match_q, int(k))
            ).fetchall()
            if rows:
                return rows, False
        except sqlite3.Error:
            # If FTS/bm25 is unavailable, fall back to recent notes.
            pass

    # Fallback: most recent notes inside the selected date range
    sql_recent = """
        SELECT event_id_cnty, event_date, notes
        FROM events
        WHERE country = ?
          AND event_date IS NOT NULL
          AND event_date BETWEEN ? AND ?
          AND notes IS NOT NULL AND notes <> ''
        ORDER BY event_date DESC
        LIMIT ?;
    """
    rows = con.execute(sql_recent, (country, start_date, end_date, int(k))).fetchall()
    return rows, True

def _build_context(rows, max_chars: int = 12000) -> str:
    parts = []
    total = 0
    for event_id, event_date, notes in rows:
        if not notes:
            continue
        piece = f"[event_id_cnty={event_id} | date={event_date}] {str(notes).strip()}\n"
        if total + len(piece) > max_chars:
            break
        parts.append(piece)
        total += len(piece)
    return "".join(parts)

def _chat_answer(country: str, question: str, rows, model: str = "gpt-4o-mini") -> str:
    client = get_openai_client()
    if client is None:
        return (
            "Chatbot is not configured. Add an OpenAI API key in `.streamlit/secrets.toml` "
            "as `OPENAI_API_KEY = \"...\"` (or under `[general]`)."
        )

    context = _build_context(rows)
    system = (
        "You are a conflict dashboard assistant.\n"
        "Answer ONLY using the provided event notes as factual evidence.\n"
        "Do NOT use external knowledge, background facts, or assumptions.\n"
        "If the notes are insufficient, say so explicitly and suggest how to refine the question.\n"
        "When making factual claims, cite event_id_cnty values from the notes.\n"
        "Keep the answer clear and concise."
    )
    user = f"Country: {country}\nQuestion: {question}\n\nEvent notes:\n{context}"

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        max_tokens=450,
    )
    return resp.choices[0].message.content
    
# -------------------------
# HELPER IMPRESSUM
# -------------------------

@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_pipeline_status():
    """
    Query databases to get last update times and date ranges.
    Returns dict with status for each pipeline component.
    """
    status = {}
    
    # ACLED Data Status
    try:
        con_acled = get_conf_conn()
        
        # Last event date and date range
        acled_query = """
        SELECT 
            MIN(event_date) as first_event,
            MAX(event_date) as last_event,
            COUNT(*) as total_events
        FROM events
        WHERE event_date IS NOT NULL
        """
        acled_data = pd.read_sql_query(acled_query, con_acled)
        
        status['acled'] = {
            'last_update': acled_data['last_event'].iloc[0] if not acled_data.empty else 'N/A',
            'date_range_start': acled_data['first_event'].iloc[0] if not acled_data.empty else 'N/A',
            'date_range_end': acled_data['last_event'].iloc[0] if not acled_data.empty else 'N/A',
            'total_records': int(acled_data['total_events'].iloc[0]) if not acled_data.empty else 0,
            'status': 'operational'
        }
    except Exception as e:
        status['acled'] = {
            'last_update': 'Error',
            'date_range_start': 'N/A',
            'date_range_end': 'N/A',
            'total_records': 0,
            'status': 'error'
        }

    # GNews Articles Status
    try: 
        con_gnews = get_gnews_conn()

        news_query = """
        SELECT
            MIN(publishedAt) as first_article,
            MAX(publishedAt) as last_article,
            COUNT (*) as total_articles
        FROM articles_eng
        """
        articles_data = pd.read_sql_query(news_query, con_gnews)

        status['articles'] = {
            'last_update': articles_data['last_article'].iloc[0] if not articles_data.empty else 'N/A',
            'date_range_start': articles_data['first_article'].iloc[0] if not articles_data.empty else 'N/A',
            'date_range_end': articles_data['last_article'].iloc[0] if not articles_data.empty else 'N/A',
            'total_records': int(articles_data['total_articles'].iloc[0]) if not articles_data.empty else 0,
            'status': 'operational'
        }
    except Exception as e:
        status['articles'] = {
            'last_update': 'Error',
            'date_range_start': 'N/A',
            'date_range_end': 'N/A',
            'total_records': 0,
            'status': 'error'
        }

    
    # Matched Articles Status
    try:
        con_match = get_match_conn()
        
        articles_query = f"""
        SELECT 
            MIN({A_PUB}) as first_article,
            MAX({A_PUB}) as last_article,
            COUNT(*) as total_articles
        FROM {MATCH_TABLE}
        WHERE {A_PUB} IS NOT NULL
        """
        matched_data = pd.read_sql_query(articles_query, con_match)
        
        status['matching'] = {
            'last_update': matched_data['last_article'].iloc[0] if not matched_data.empty else 'N/A',
            'date_range_start': matched_data['first_article'].iloc[0] if not matched_data.empty else 'N/A',
            'date_range_end': matched_data['last_article'].iloc[0] if not matched_data.empty else 'N/A',
            'total_records': int(matched_data['total_articles'].iloc[0]) if not matched_data.empty else 0,
            'status': 'operational'
        }
    except Exception as e:
        status['matching'] = {
            'last_update': 'Error',
            'date_range_start': 'N/A',
            'date_range_end': 'N/A',
            'total_records': 0,
            'status': 'error'
        }
    
    # Sentiment Analysis Status (if available)
    try:
        if DATA_PATH.exists():
            df_sent = pd.read_csv(DATA_PATH, usecols=['published_date'], parse_dates=['published_date'])
            status['sentiment'] = {
                'last_update': df_sent['published_date'].max().strftime('%Y-%m-%d') if not df_sent.empty else 'N/A',
                'date_range_start': df_sent['published_date'].min().strftime('%Y-%m-%d') if not df_sent.empty else 'N/A',
                'date_range_end': df_sent['published_date'].max().strftime('%Y-%m-%d') if not df_sent.empty else 'N/A',
                'total_records': len(df_sent),
                'status': 'operational'
            }
        else:
            status['sentiment'] = {
                'last_update': 'N/A',
                'date_range_start': 'N/A',
                'date_range_end': 'N/A',
                'total_records': 0,
                'status': 'not available'
            }
    except Exception as e:
        status['sentiment'] = {
            'last_update': 'Error',
            'date_range_start': 'N/A',
            'date_range_end': 'N/A',
            'total_records': 0,
            'status': 'error'
        }
    
    return status


def format_date_short(date_str):
    """Format date string to readable format."""
    if date_str == 'N/A' or date_str == 'Error':
        return date_str
    try:
        dt = pd.to_datetime(date_str)
        return dt.strftime('%b %d, %Y')
    except:
        return date_str


def get_status_color(status):
    """Return color based on status."""
    colors = {
        'operational': '#4CAF50',  # Green
        'error': '#F44336',        # Red
        'not available': '#FFC107' # Amber
    }
    return colors.get(status, '#9E9E9E')  # Gray default

# --------------------------
# END HELPER IMPRESSUM
# --------------------------



# --------------------------
# Helpers Sentiment
# --------------------------
DATA_PATH = PROJECT_ROOT / "data" / "processed_conflict_articles_test.csv"
@st.cache_data
def load_data():
    if not DATA_PATH.exists():
        st.error(f"Cannot find the file：{DATA_PATH}")
        return None
    try:
        df = pd.read_csv(DATA_PATH)
        df['published_date'] = pd.to_datetime(df['published_date'])
        return df
    except Exception as e:
        return None

@st.cache_resource
def get_translator():
    return pipeline("translation_de_to_en", model="Helsinki-NLP/opus-mt-de-en")

def calc_attention_metrics(data):
    if data.empty: return 0.0, "N/A"
    daily_series = data.groupby('published_date').size().sort_index()
    if daily_series.empty: return 0.0, "N/A"
    sorted_series = daily_series.sort_values(ascending=False)
    top_10_percent_days = max(1, int(len(sorted_series) * 0.1))
    burstiness = (sorted_series.head(top_10_percent_days).sum() / daily_series.sum()) * 100
    peak_date = sorted_series.idxmax()
    peak_val = sorted_series.max()
    post_peak_data = daily_series[daily_series.index >= peak_date]
    half_life_threshold = peak_val / 2
    decay_dates = post_peak_data[post_peak_data <= half_life_threshold]
    if not decay_dates.empty:
        half_life_val = (decay_dates.index[0] - peak_date).days
        half_life_str = f"{half_life_val} Days"
    else:
        max_tracked = (daily_series.index[-1] - peak_date).days
        half_life_str = f">{max_tracked} Days"
    return burstiness, half_life_str

df = load_data()

# -------------------------
# Drilldown helpers
# -------------------------
def init_drilldown_state():
    if "selected_iso3" not in st.session_state:
        st.session_state.selected_iso3 = None
    if "selected_country_name" not in st.session_state:
        st.session_state.selected_country_name = None
    if "drilldown_pending" not in st.session_state:
        st.session_state.drilldown_pending = False


@st.cache_data(ttl=300)
def get_country_maps():
    """
    Build ISO3 <-> country name maps from country_indices.
    Assumes columns: iso_a3, country (adjust here if yours differ).
    """
    df = build_indices_live().copy()

    # Normalize column names (if needed)
    iso_col = "iso_a3"
    name_col = "country"

    if iso_col not in df.columns or name_col not in df.columns:
        raise KeyError(
            f"country_indices must include '{iso_col}' and '{name_col}'. "
            f"Found: {list(df.columns)}"
        )

    tmp = df[[iso_col, name_col]].dropna().drop_duplicates().copy()
    tmp[iso_col] = tmp[iso_col].astype(str).str.upper().str.strip()
    tmp[name_col] = tmp[name_col].astype(str).str.strip()

    iso3_to_country = dict(zip(tmp[iso_col], tmp[name_col]))
    country_to_iso3 = dict(zip(tmp[name_col].str.lower(), tmp[iso_col]))

    return iso3_to_country, country_to_iso3


def set_selected_country(*, iso3=None, country=None):
    """
    Store both ISO3 and country name in session_state.
    If only one is provided, derive the other via country_indices maps.
    """
    iso3_to_country, country_to_iso3 = get_country_maps()

    if iso3:
        iso3 = str(iso3).upper().strip()
        if not country:
            country = iso3_to_country.get(iso3)

    if country:
        country = str(country).strip()
        if not iso3:
            iso3 = country_to_iso3.get(country.lower())

    st.session_state.selected_iso3 = iso3
    st.session_state.selected_country_name = country


def extract_clicked_iso3(event, layer_id="countries"):
    """
    Read the clicked GeoJSON feature from st.pydeck_chart selection state
    and extract ISO3 from its properties.
    """
    if not event:
        return None

    sel = getattr(event, "selection", None)
    if not sel:
        return None

    objects = sel.get("objects") if isinstance(sel, dict) else getattr(sel, "objects", None)
    if not objects or layer_id not in objects:
        return None

    feats = objects.get(layer_id) if isinstance(objects, dict) else None
    if not feats:
        return None

    feat = feats[0]
    props = feat.get("properties", {})

    # In your app, iso_a3 should exist (you merged on it).
    # Keep fallbacks just in case.
    for k in ("iso_a3", "ISO_A3", "adm0_a3", "ADM0_A3", "sov_a3", "SOV_A3"):
        v = props.get(k)
        if v and str(v).strip():
            return str(v).strip()

    return None



# -------------------------
# Main Tabs
# -------------------------
# init once
init_drilldown_state()
if "page" not in st.session_state:
    st.session_state.page = "landing"

page_map = {
    "Our Mission": "landing",
    "Media Gap Map": "underrep",
    "Conflict Coverage Explorer": "explorer",
    "Framing the Conflict": "sentiment",
    "Impressum": "impressum",
}
labels = list(page_map.keys())

with st.sidebar:
    st.markdown("## Navigation")

    # apply programmatic nav change BEFORE the widget is created
    if "_nav_override" in st.session_state:
        st.session_state["nav_radio"] = st.session_state["_nav_override"]
        del st.session_state["_nav_override"]

    # guard against stale/renamed labels from previous runs
    if "nav_radio" in st.session_state and st.session_state["nav_radio"] not in labels:
        st.session_state["nav_radio"] = labels[1]   # or "Media Gap Map"

    chosen_label = st.radio("Go to", labels, key="nav_radio")
    new_page = page_map[chosen_label]

    # Sweep at every page change
    # bump animation nonce when page changes
    if st.session_state.page != new_page:
        st.session_state.anim_nonce += 1
        st.session_state.page = new_page
    ####


# -------------------------
# Landing Page
# -------------------------
if st.session_state.page == "landing":
    # Auto-refresh every 4 seconds
    #st_autorefresh(interval=4000, key="carousel_refresh")

    df_plot = build_indices_live()
    world = load_world()


    SCENARIO_W = {
        "Fatalities": 1.00,
        "Events": 0.00,
        "Balanced": 0.50,
    }

    def _set_scenario(name: str):
        st.session_state["scenario"] = name
        st.session_state["w_fat"] = SCENARIO_W[name]

    def _init_state():
        st.session_state.setdefault("scenario", "Balanced")
        st.session_state.setdefault("w_fat", SCENARIO_W["Balanced"])

    _init_state()


    # -----------------------------
    # Carousel content (your cards)
    # -----------------------------
    slides = [
        {
            "title": "Making Media Bias Visible",
            "body": """
            This project links **real-world conflict event data** with **German news coverage**
            to mirror which countries are currently undercovered in media reporting.
            The focus is not only on **what becomes visible in media reporting** but also how it is **framed** in reporting.
            """,
                },
        {
            "title": "When Coverage Fails Reality",
            "body": """
            Media coverage shapes how conflicts are perceived and prioritized.
            Yet attention is uneven: German media tend to focus on countries with cultural, geographic, or economic proximity to Germany — leaving other conflicts largely invisible.
            """,

        },
        {
            "title": "See the Gap Yourself",
            "body": """
            - **Media Gap Map**: Explore undercovered countries based on conflict severity and media coverage.
            - **Conflict Coverage Explorer**: Dive into event details and related articles of a country.
            - **Framing the Conflict**: Analyze framing patterns in news articles about conflicts in country.
            """,
        },
        {
            "title": "Data & People Behind It",
            "body": """
            - ACLED: Global event-level data on protests, violence, and armed conflict. 
            - German News Media: Conflict-related reporting matched by time and location.
            - People: Yannick Kayser, Johannes Reithmeier, Jana Speldrich, Hanshi Zhang
            """,
        },
    ]

    # -----------------------------
    # Carousel state
    # -----------------------------
    if "info_slide_idx" not in st.session_state:
        st.session_state.info_slide_idx = 0
    
    #if "last_slide_ts" not in st.session_state:  # autorefresh
    #    st.session_state.last_slide_ts = time.time() #autorefresh

    # autorefresh 
    #def prev_slide(): 
    #    st.session_state.info_slide_idx = (
    #        st.session_state.info_slide_idx - 1
    #    ) % len(slides)
    #    st.session_state.last_slide_ts = time.time()

    #def next_slide():
    #    st.session_state.info_slide_idx = (
    #        st.session_state.info_slide_idx + 1
    #    ) % len(slides)
    #    st.session_state.last_slide_ts = time.time()

    def prev_slide():
        st.session_state.info_slide_idx = (st.session_state.info_slide_idx - 1) % len(slides)

    def next_slide():
        st.session_state.info_slide_idx = (st.session_state.info_slide_idx + 1) % len(slides)

    # Autorefresh: Auto-advance slide every 4 seconds
    #now = time.time()
    #if now - st.session_state.last_slide_ts >= 4:
    #    st.session_state.info_slide_idx = (
    #        st.session_state.info_slide_idx + 1
    #    ) % len(slides)
    #    st.session_state.last_slide_ts = now

    # -----------------------------
    # Carousel UI
    # -----------------------------

    nav_l, nav_c, nav_r = st.columns([1, 6, 1], vertical_alignment="center")

    with nav_l:
        st.button("◀", use_container_width=True, on_click=prev_slide)

    with nav_r:
        st.button("▶", use_container_width=True, on_click=next_slide)

    # Slide card
    s = slides[st.session_state.info_slide_idx]
    with nav_c:
        st.markdown(
            f"""
            <div style="
                border: 1px solid rgba(0,0,0,0.08);
                border-radius: 18px;
                padding: 1.2rem 1.3rem;
                box-shadow: 0 6px 18px rgba(0,0,0,0.06);
            ">
                <div style="display:flex; justify-content:space-between; align-items:flex-end; gap:1rem;">
                    <div style="font-size:1.35rem; font-weight:750; margin:0;">{s['title']}</div>
                    <div style="opacity:0.6; font-size:0.9rem;">{st.session_state.info_slide_idx+1} / {len(slides)}</div>
                </div>
                <div style="margin-top:0.7rem; font-size:1.02rem; line-height:1.55;">
                    {s['body']}
            """,
            unsafe_allow_html=True,
        )

    
    # --------------------------
    #  Session State and Computations
    # --------------------------

    w = float(st.session_state["w_fat"])
    active = st.session_state["scenario"]
    
    # compute once, right after w exists (same as before)
    df_plot["severity_share"] = (1 - w) * df_plot["share_events"] + w * df_plot["share_fatalities"]
    df_plot["underrep_share"] = df_plot["share_articles"] - df_plot["severity_share"]

    df_plot["severity_pct"] = df_plot["severity_share"].map(lambda x: f"{x*100:.1f}%")
    df_plot["articles_pct"] = df_plot["share_articles"].map(lambda x: f"{x*100:.1f}%")
    df_plot["underrep_pct"] = df_plot["underrep_share"].map(lambda x: f"{x*100:.1f}%")
    df_plot["events_pct"] = df_plot["share_events"].map(lambda x: f"{x*100:.1f}%")
    df_plot["fatalities_pct"] = df_plot["share_fatalities"].map(lambda x: f"{x*100:.1f}%")


    # --------------------------
    #  Top 5 underrepresented countries
    # --------------------------

    st.markdown(
        """
        <p style="font-size:1.4rem; font-weight:700; margin-top:1.2rem; margin-bottom:0.35rem;">
            TOP 5 Undercovered Countries
        </p>
        """,
        unsafe_allow_html=True,
    )

    
    cols = [
        "country",
        "underrep_pct",
        "articles_pct",
        "severity_pct",
        "events_pct",
        "fatalities_pct",
        "n_events",
        "total_fatalities",
        "n_articles",  # (typo fix: not n_artciles)
    ]

    rename_map = {
        "country": "Country",
        "underrep_pct": "Undercoverage Score",
        "articles_pct": "Article Share",
        "severity_pct": "Conflict Severity",
        "events_pct": "Event Share",
        "fatalities_pct": "Fatality Share",
        "n_events": "Number of Events",
        "total_fatalities": "Total Fatalities",
        "n_articles": "Number of Articles",
    }

    df_display = (
        df_plot
        .sort_values("underrep_share", ascending=True)
        .head(5)[cols]
        .rename(columns=rename_map)
    )

    st.dataframe(df_display, use_container_width=True)
    st.caption(f"Active Scenario: **{active}** · events weight: {1-w:.0%} · fatalities weight: {w:.0%}")


    #  --------------------------
    #  Scenario buttons
    #  --------------------------

    st.markdown("### Scenarios")
    c1, c2, c3 = st.columns(3)

    with c1:
        st.button(
            "Fatalities",
            use_container_width=True,
            on_click=_set_scenario,
            args=("Fatalities",),
            disabled=(active == "Fatalities"),
        )
    with c2:
        st.button(
            "Events",
            use_container_width=True,
            on_click=_set_scenario,
            args=("Events",),
            disabled=(active == "Events"),
        )
    with c3:
        st.button(
            "Combined",
            use_container_width=True,
            on_click=_set_scenario,
            args=("Balanced",),
            disabled=(active == "Balanced"),
        )


            
    st.markdown("""
    <div style="margin-top:1rem; font-size:0.9em;">
        🔗 Project repository:
        <a href="https://github.com/yannickkayser/dashboard_conflict_data" target="_blank">
            github.com/yannickkayser/dashboard_conflict_data
        </a>
    </div>
    """, unsafe_allow_html=True)





# -------------------------
# Tab 1: Conflict Underrepresentation
# -------------------------
elif st.session_state.page == "underrep":
    #st.markdown("## Conflict Underrepresentation Analysis")

    
#    st.markdown(
#        """
#        <p style="font-size:1.4rem; font-weight:700; margin-top:1.2rem; margin-bottom:0.35rem;">
#            In which countries does media attention diverge most from conflict severity?
#        </p>
#        <p style="font-size:0.95rem; color:#444; margin:0 0 1.0rem 0;">
#            This page compares how severe each country’s conflict situation is (events and fatalities)
#            with how often it appears in conflict-related news. Countries where coverage lags behind
#            severity can be interpreted as systematically underrepresented in media reporting.
#       </p>
#        """,
#        unsafe_allow_html=True,
#    )

    

    #df_idx = load_indices()
    df_plot = build_indices_live()
    world = load_world()

    # -------------------------
    # Global controls
    # -------------------------
    # -----------------------------
    SCENARIO_W = {
        "Fatalities": 1.00,
        "Events": 0.00,
        "Balanced": 0.50,
    }

    def _init_state():
        st.session_state.setdefault("scenario", "Balanced")
        st.session_state.setdefault("w_fat", SCENARIO_W["Balanced"])

        # 2D advanced defaults
        st.session_state.setdefault("clip_2d", 0.01)
        st.session_state.setdefault("gamma_2d", 1.0)

        # 3D advanced defaults
        st.session_state.setdefault("height_scale", 3_000_000)
        st.session_state.setdefault("height_gamma", 0.5)
        st.session_state.setdefault("color_gamma", 1.0)
        st.session_state.setdefault("pitch", 45)
        st.session_state.setdefault("opacity", 0.9)

    def _set_scenario(name: str):
        st.session_state["scenario"] = name
        st.session_state["w_fat"] = SCENARIO_W[name]

    _init_state()

    st.markdown("### Scenarios")

    c1, c2, c3 = st.columns(3)
    active = st.session_state["scenario"]

    with c1:
        st.button(
            "Fatalities",
            use_container_width=True,
            on_click=_set_scenario,
            args=("Fatalities",),
            disabled=(active == "Fatalities"),
        )
    with c2:
        st.button(
            "Events",
            use_container_width=True,
            on_click=_set_scenario,
            args=("Events",),
            disabled=(active == "Events"),
        )
    with c3:
        st.button(
            "Combined",
            use_container_width=True,
            on_click=_set_scenario,
            args=("Balanced",),
            disabled=(active == "Balanced"),
        )

    w = float(st.session_state["w_fat"])
    st.caption(f"Active: **{active}** · events weight: {1-w:.0%} · fatalities weight: {w:.0%}")

    # compute once, right after w exists (same as before)
    df_plot["severity_share"] = (1 - w) * df_plot["share_events"] + w * df_plot["share_fatalities"]
    df_plot["underrep_share"] = df_plot["share_articles"] - df_plot["severity_share"]

    df_plot["severity_pct"] = df_plot["severity_share"].map(lambda x: f"{x*100:.1f}%")
    df_plot["articles_pct"] = df_plot["share_articles"].map(lambda x: f"{x*100:.1f}%")
    df_plot["underrep_pct"] = df_plot["underrep_share"].map(lambda x: f"{x*100:.1f}%")

    # STRUCTURE OF PAGE IN TABS
    tab2d, tab3d = st.tabs(["2D Map", "3D Map"])

    # -------------------------
    # 2D map
    # -------------------------
    with tab2d:

        
        #st.subheader("Which countries are visibly under- or overrepresented in media coverage?")
        st.markdown(
            """
            <div style="font-size:0.85rem; color:#555;">
                &nbsp;
                <span style="display:inline-block; width:12px; height:12px; background:#FF69B4; border-radius:2px;"></span>
                undercovered
                &nbsp;
                <span style="display:inline-block; width:12px; height:12px; background:#5082FF; border-radius:2px;"></span>
                overcovered
                &nbsp;
                <span style="display:inline-block; width:12px; height:12px; background:#B4B4B4; border-radius:2px;"></span>
                balanced
            </div>
            """,
            unsafe_allow_html=True,
        )



        clip = float(st.session_state["clip_2d"])
        gamma = float(st.session_state["gamma_2d"])
        geojson2d, merged2d = build_geojson_underrep(world, df_plot, clip=clip, gamma=gamma)


        layer2d = pdk.Layer(
            "GeoJsonLayer",
            data=geojson2d,
            id="countries",
            stroked=True,
            filled=True,
            extruded=False,
            get_fill_color="properties.fill_color",
            get_line_color=[80, 80, 80, 80],
            pickable=True,
        )

        view_state_2d = pdk.ViewState(latitude=20, longitude=0, zoom=1.1, pitch=0)

        tooltip2d = {
            "html": """
            <b>{name}</b><br/>
            Undercoverage Score: {underrep_pct}<br/>
            Articles: {n_articles}<br/>
            Events: {n_events}<br/>
            Fatalities: {total_fatalities}
            """,
            "style": {"backgroundColor": "white", "color": "black"},
        }

        
        deck2d = pdk.Deck(
            layers=[layer2d],
            initial_view_state=view_state_2d,
            tooltip=tooltip2d,
            map_style=None,
        )

        event = st.pydeck_chart(
            deck2d,
            width="stretch",
            on_select="rerun",              # enable click selections :contentReference[oaicite:1]{index=1}
            selection_mode="single-object", # one country at a time :contentReference[oaicite:2]{index=2}
            key="underrep_map2d",
        )

        clicked_iso3 = extract_clicked_iso3(event, layer_id="countries")

        if clicked_iso3:
            # store ISO3 + derive country name from country_indices
            set_selected_country(iso3=clicked_iso3)

            if st.session_state.selected_country_name:
                st.session_state.drilldown_pending = True
                st.session_state.page = "explorer"
                st.session_state._nav_override = "Conflict Coverage Explorer"
                st.rerun()
            else:
                st.warning(f"Clicked ISO3='{clicked_iso3}', but couldn't map it to a country name in country_indices.")

        with st.expander("Advanced 2D Visuals", expanded=False):
            col1, col2 = st.columns(2)
            with col1:
                st.slider(
                    "Saturation threshold",
                    min_value=0.001,
                    max_value=0.1,
                    step=0.01,
                    key="clip_2d",
                    help="“Limits how extreme values affect the color scale. Lower = more countries reach full pink/blue; higher = only the most extreme do.”",
                )
        
            with col2:
                st.slider(
                    "Emphasize mid-range",
                    min_value=0.2,
                    max_value=2.0,
                    step=0.05,
                    key="gamma_2d",
                    help="Changes how strongly mid-range differences show up. < 1 highlights small differences; > 1 makes colors more gradual.",
                )

        

        # Question + explanation for 2D map
        with st.expander("How to read the 2D map", expanded=False):
            st.markdown(
                """
                <p style="font-size:0.9rem; color:#555; margin:0 0 0.7rem 0;">
                    The 2D map contrasts each country’s share of global conflict severity (events and fatalities)
                    with its share of conflict-related articles. Countries shaded towards the undercovered end
                    have fewer articles than their severity would suggest, while overcovered countries receive
                    disproportionate attention relative to their conflict burden.
                </p>
                <p style="font-size:0.9rem; color:#555; margin:0 0 0.7rem 0;">
                    The undercoverage score is calculated as the difference between a country’s article share
                    and its conflict severity. A negative score indicates undercoverage, while a positive
                    score indicates overcoverage.
                </p>
                <p style="font-size:0.9rem; color:#555; margin:0 0 0.7rem 0;">
                    Conflict severity itself is computed as a weighted combination of event share and fatality share,
                    with user-adjustable weights via the scenario buttons above. The user can choose to emphasize
                    fatalities, events, or balance both equally when assessing undercoverage.
                </p>
                """,
                unsafe_allow_html=True,
            )




    # -------------------------
    # 3D map
    # -------------------------
    with tab3d:

        st.markdown(
            """
            <div style="font-size:0.85rem; color:#555;">
                &nbsp;
                <span style="display:inline-block; width:12px; height:12px; background:#FF69B4; border-radius:2px;"></span>
                low coverage
                &nbsp;
                <span style="display:inline-block; width:12px; height:12px; background:#B4B4B4; border-radius:2px;"></span>
                high coverage
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Read advanced values (no sliders here)
        height_scale = int(st.session_state["height_scale"])
        height_gamma = float(st.session_state["height_gamma"])
        color_gamma = float(st.session_state["color_gamma"])
        pitch = int(st.session_state["pitch"])
        opacity = float(st.session_state["opacity"])


        geojson, merged = build_geojson(world, df_plot, color_gamma=color_gamma)

        # --- ensure we have an iso3 field in properties for click drilldown ---
        if "iso3" not in merged.columns:
            for cand in ["ISO3", "ISO_A3", "ADM0_A3", "id"]:
                if cand in merged.columns:
                    merged["iso3"] = merged[cand]
                    break

        # compute elevation in Python (more robust than JS expressions)
        merged["elevation"] = (merged["severity_share"] ** height_gamma) * height_scale

        layer = pdk.Layer(
            "GeoJsonLayer",
            data=json.loads(merged.to_json()),
            id = "countries3d",
            opacity=opacity,
            stroked=True,
            filled=True,
            extruded=True,
            wireframe=True,
            get_fill_color="properties.fill_color",
            # stronger borders for 3D
            get_line_color=[20, 20, 20, 220],
            line_width_min_pixels=1,
            line_width_max_pixels=3,
            get_elevation="properties.elevation",
            pickable=True,
        )

        view_state = pdk.ViewState(
            latitude=20,
            longitude=0,
            zoom=1.1,
            pitch=pitch,
        )

        tooltip3d = {
            "html": """
            <b>{name}</b><br/>
            Conflict severity (height): {severity_pct}<br/>
            Articles share (color): {articles_pct}<br/>
            """,
            "style": {"backgroundColor": "white", "color": "black"},
        }

        deck = pdk.Deck(
            layers=[layer],
            initial_view_state=view_state,
            tooltip=tooltip3d,
            map_style=None,
        )

        event3d = st.pydeck_chart(
            deck,
            width="stretch",
            on_select="rerun",              # enable click selections
            selection_mode="single-object", # one country at a time
            key="underrep_map3d",
        )

        clicked_iso3 = extract_clicked_iso3(event3d, layer_id="countries3d")

        if clicked_iso3:
            set_selected_country(iso3=clicked_iso3)

            if st.session_state.selected_country_name:
                st.session_state.drilldown_pending = True
                st.session_state.page = "explorer"
                st.session_state._nav_override = "Conflict Coverage Explorer"
                st.rerun()
            else:
                st.warning(
                    f"Clicked ISO3='{clicked_iso3}', but couldn't map it to a country name in country_indices."
                )

        #st.pydeck_chart(deck, width="stretch")


        with st.expander("Advanced 3D Visuals", expanded=False):
            colA, colB = st.columns([1, 2])

            with colA:
                st.slider(
                    "Overall height",
                    min_value=100_000,
                    max_value=5_000_000,
                    step=100_000,
                    key="height_scale",
                    help="Scales the overall height of all pillars",
                )
                st.slider(
                    "Height contrast",
                    min_value=0.2,
                    max_value=2.0,
                    step=0.05,
                    key="height_gamma",
                    help=(
                        "Adjusts how height differences are distributed. "
                        "< 1 highlights smaller conflicts; > 1 emphasizes only the largest ones."
                    ),
                )
                st.slider(
                    "Color contrast",
                    min_value=0.2,
                    max_value=2.0,
                    step=0.05,
                    key="color_gamma",
                    help=(
                        "Adjusts how strongly coverage differences appear in color. "
                        "< 1 highlights subtle differences; > 1 makes colors more gradual."
                    ),
                )

            with colB:
                st.slider("Viewing angle", 0, 70, 45, 1, key="pitch", help="Tilt the camera to better compare pillar heights.",)
                st.slider("Pillar opacity", 0.1, 1.0, 0.9, 0.05, key="opacity", help="Reduce opacity to see overlapping countries more clearly.",)

        # Question + explanation for 3D map (playground)
        with st.expander("How to read the 3D map", expanded=False):
            st.markdown(
                """
            
                </p>
                <p style="font-size:0.9rem; color:#555; margin:0 0 0.7rem 0;">
                    The 3D map is an interactive playground: users can rotate, zoom, and adjust height and color
                    settings to explore how conflict severity (height) and media coverage (color) vary across
                    countries. Tall but relatively pale countries indicate intense conflict with sufficient coverage,
                    while brightly colored pillars highlight locations that receive comparatively weak media attention.
                </p>
                <p style="font-size:0.9rem; color:#555; margin:0 0 0.7rem 0;">
                    Within our 3D map it is especially easy to spot countries of similar height
                    (conflict severity) but differing color intensity (media coverage).
                </p>
                """,
                unsafe_allow_html=True,
            )

    
# -------------------------
# Tab 2: Sentiment Analysis (Country Comparison Version)
# -------------------------
elif st.session_state.page == "sentiment":
    if df is None:
        st.error("Data files not found. Please run data_processor.py first.")
    else:
        st.markdown("### Media Attention Across Countries")

        with st.expander("What does this comparison show?", expanded=False):
                st.markdown("""
                This section compares how international media covers conflicts between different countries.
                It highlights differences in **emotional framing**, **attention intensity**, and **volatility**
                over the selected time period.
                """)
        # ============================================================
        # 2. FILTERS (Applied to both countries)
        # ============================================================
        with st.container():
            st.markdown(
            "<div class='filter-box'><b>Country Comparison Setup</b></div>",
            unsafe_allow_html=True
            )

            f1 = st.columns(1)[0]

            with f1:
                date_range = st.date_input(
                    "Date Range",
                    [df['published_date'].min(), df['published_date'].max()]
                )

        # Apply date filter
        if len(date_range) == 2:
            mask = (df['published_date'].dt.date >= date_range[0]) & (df['published_date'].dt.date <= date_range[1])
            df_filtered = df.loc[mask]
        else:
            df_filtered = df.copy()

        # ============================================================
        # 1. COUNTRY SELECTION (NEW)
        # ============================================================
        
        # Get unique countries from the dataset
        available_countries = sorted(df['article_country_x'].dropna().unique().tolist())

        # --- pick default for Country A from map selection ---
        map_country = st.session_state.get("selected_country_name", None)

        # if map_country is present & valid, use it; else fall back to 0
        if map_country in available_countries:
            default_a_idx = available_countries.index(map_country)
        else:
            default_a_idx = 0

        # --- force update when coming from map drilldown ---
        if st.session_state.get("drilldown_pending") and map_country in available_countries:
            st.session_state["country_a"] = map_country
            st.session_state.drilldown_pending = False

        # end default country code
        
        if len(available_countries) < 2:
            st.warning("Not enough countries in the dataset for comparison. Showing single country view.")
            country_comparison_mode = False
            selected_countries = available_countries[:1] if available_countries else []
        else:
            country_comparison_mode = True
            col_select1, col_select2 = st.columns(2)
            
            with col_select1:
                country_1 = st.selectbox(
                    "Country A",
                    available_countries,
                    index=default_a_idx,
                    key="country_a"
                )
            
            with col_select2:
                # Default to second country, or first if only one available
                default_idx = min(1, len(available_countries) - 1)
                country_2 = st.selectbox(
                    "Country B",
                    available_countries,
                    index=default_idx,
                    key="country_b"
                )
            
            selected_countries = [country_1, country_2]
        
        st.markdown("---")

        with st.expander("How to read the indicators", expanded=False):
            st.markdown("""
            Indicators
            - **Total Articles** – Number of matched news articles in the selected period.  
            - **Burstiness Index** – Share of total coverage occurring on peak days. 
            - **Attention Half-life** – Days until attention drops by half after a peak.  
            - **Avg Emotional Tone** – Average sentiment score across all articles.
                        
            Charts
            - **Top 5 Emotions** - Emotion charts show the distribution of the five most frequent **non-neutral**
            emotions in media coverage. Neutral articles are excluded from the pie charts.
            - **Daily Media Attention** – Area chart of daily article counts over time.
            - **Emotion Framing Heatmap** - Each row represents a media outlet. Values show the percentage distribution of emotions **within that outlet**. Rows are normalized to sum to 100%.
            """)

        
       
        # ============================================================
        # HELPER FUNCTIONS
        # ============================================================
        def calc_attention_metrics(data):
            if data.empty:
                return 0.0, "N/A"

            daily_series = data.groupby('published_date').size().sort_index()
            if daily_series.empty:
                return 0.0, "N/A"

            sorted_series = daily_series.sort_values(ascending=False)
            top_10_percent_days = max(1, int(len(sorted_series) * 0.1))
            burstiness = (sorted_series.head(top_10_percent_days).sum() / daily_series.sum()) * 100

            peak_date = sorted_series.idxmax()
            peak_val = sorted_series.max()

            post_peak = daily_series[daily_series.index >= peak_date]
            half_threshold = peak_val / 2
            decay = post_peak[post_peak <= half_threshold]

            if not decay.empty:
                half_life = f"{(decay.index[0] - peak_date).days} Days"
            else:
                half_life = f">{(daily_series.index[-1] - peak_date).days} Days"

            return burstiness, half_life

        def kpi_card(label, value, description=None):
            st.markdown(f"""
            <div style="
                background-color:#ffffff;
                border-radius:10px;
                padding:14px;
                box-shadow:0 2px 6px rgba(0,0,0,0.08);
                text-align:center;
            ">
                <div style="font-size:0.85em; color:#666;">{label}</div>
                <div style="font-size:1.6em; color:#666; font-weight:700;">{value}</div>
                {f"<div style='font-size:0.75em; color:#999;'>{description}</div>" if description else ""}
            </div>
            """, unsafe_allow_html=True)

        def render_country_analysis(df_country, country_name):
            """Render all analysis components for a single country"""
            
            st.markdown(f"### {country_name}")
            
            # Calculate metrics
            burstiness, half_life = calc_attention_metrics(df_country)
            
            # KPI Cards
            k1, k2 = st.columns(2)
            with k1:
                kpi_card("Total Articles", len(df_country))
            with k2:
                kpi_card("Burstiness Index", f"{burstiness:.1f}%",
                         "Peak day concentration")
            
            k3, k4 = st.columns(2)
            with k3:
                avg_tone = f"{df_country['sentiment_numeric'].mean():.2f}" if not df_country.empty else "N/A"
                kpi_card("Avg Emotional Tone", avg_tone)
            with k4:
                kpi_card("Attention Half-life", half_life,
                         "Interest fade speed")
            
            st.markdown("---")

            # Emotion distribution
            exclude = ['neutral', 'others', 'other', 'label_1']
            df_active = df_country[~df_country['emotion_label'].str.lower().isin(exclude)]
            
            total_len = len(df_country)
            neutral_count = len(df_country[df_country['emotion_label'].str.lower().isin(exclude)])
            neutral_perc = (neutral_count / total_len * 100) if total_len > 0 else 0

            

            if not df_active.empty:
                top5 = df_active['emotion_label'].value_counts().nlargest(5).index.tolist()
                df_top = df_active[df_active['emotion_label'].isin(top5)]

                fig_pie = px.pie(
                    df_top,
                    names='emotion_label',
                    hole=0.4,
                    title=f"Top 5 Emotions - {country_name}"
                )
                st.plotly_chart(fig_pie, use_container_width=True)

            st.markdown(
                f"<div style='font-size:0.85em; color:#777;'>"
                f"Neutral coverage: <b>{neutral_perc:.1f}%</b>"
                "</div>",
                unsafe_allow_html=True
            )

            st.markdown("---")

            
            # Attention time series
            if not df_country.empty:
                ts = df_country.groupby('published_date').size().reset_index(name='Count')
                fig_area = px.area(
                    ts,
                    x='published_date',
                    y='Count',
                    title=f"Daily Media Attention - {country_name}"
                )
                st.plotly_chart(fig_area, use_container_width=True)
            
            st.markdown("---")
            # Tone by event type
            #st.markdown("#### Tone by Event Type")
            #if not df_country.empty:
            #    df_ev = (
            #        df_country.groupby('acled_event_type')['sentiment_numeric']
            #        .mean()
            #        .sort_values()
            #        .reset_index()
            #    )
            #
            #    fig_bar = px.bar(
            #        df_ev,
            #        x='sentiment_numeric',
            #        y='acled_event_type',
            #        orientation='h',
            #        color='sentiment_numeric',
            #        color_continuous_scale='RdYlGn',
            #        range_x=[-0.6, 0.4],
            #        labels={'sentiment_numeric': 'Negative ←→ Positive'},
            #        title=f"Emotional Tone - {country_name}"
            #    )
            #    st.plotly_chart(fig_bar, use_container_width=True)

            #st.markdown("#### Institutional Emotion Profile")
            if not df_country.empty:
                # Filter out neutral emotions
                exclude = ['neutral', 'others', 'other', 'label_1']
                df_active = df_country[~df_country['emotion_label'].str.lower().isin(exclude)]
        
                if not df_active.empty:
                    # Get top 10 outlets for this country
                    top_outlets = df_country['source_name'].value_counts().head(10).index
                    df_top_outlets = df_country[df_country['source_name'].isin(top_outlets)]
                    df_top_active = df_top_outlets[~df_top_outlets['emotion_label'].str.lower().isin(exclude)]
            
                    # Get top 5 emotions globally from this country's data
                    top_5_emotions = df_active['emotion_label'].value_counts().nlargest(5).index.tolist()
                    df_top_active_top5 = df_top_active[df_top_active['emotion_label'].isin(top_5_emotions)]
            
                    if not df_top_active_top5.empty:
                        # Create crosstab with percentage normalization
                        ctab = pd.crosstab(
                            df_top_active_top5['source_name'], 
                            df_top_active_top5['emotion_label'], 
                            normalize='index'
                        ) * 100
                
                        fig_heat = px.imshow(
                            ctab, 
                            text_auto=".1f", 
                            aspect="auto",
                            labels=dict(x="Top 5 Emotions", y="Media Outlet", color="Percentage (%)"),
                            color_continuous_scale="Purples",
                            title=f"Emotion Framing by Outlet - {country_name}"
                        )
                        st.plotly_chart(fig_heat, use_container_width=True)
                else:
                    st.info(f"Insufficient emotion data for {country_name}")
                
            else:
                st.info(f"No active emotional framing data for {country_name}")
    
            
            
        # ============================================================
        # 3. RENDER COUNTRY COMPARISONS
        # ============================================================

        if country_comparison_mode and len(selected_countries) == 2:
            col1, col2 = st.columns(2)
            
            with col1: 
                df_country_1 = df_filtered[df_filtered['article_country_x'] == selected_countries[0]]
                if not df_country_1.empty:
                    with st.container(border=True):
                        render_country_analysis(df_country_1, selected_countries[0])
                else:
                    st.warning(f"No data available for {selected_countries[0]} with current filters.")

            
            with col2:
                df_country_2 = df_filtered[df_filtered['article_country_x'] == selected_countries[1]]
                if not df_country_2.empty:
                    with st.container(border=True):
                        render_country_analysis(df_country_2, selected_countries[1])
                else:
                    st.warning(f"No data available for {selected_countries[1]} with current filters.")
                
        else:
            # Fallback to single country view
            if selected_countries:
                df_single = df_filtered[df_filtered['article_country_x'] == selected_countries[0]]
                render_country_analysis(df_single, selected_countries[0])

        # ============================================================
        # 4. CROSS-COUNTRY COMPARISON CHARTS
        # ============================================================
        #if country_comparison_mode and len(selected_countries) == 2:
        #    st.markdown("""
        #    <div style="margin-top:2.2rem;">
        #        <div style="font-size:1.6em; font-weight:700;">
        #            Cross-Country Comparison
        #        </div>
        #        <hr>
        #    </div>
        #    """, unsafe_allow_html=True)
        #    
        #    # Side-by-side sentiment comparison
        #    comp_col1, comp_col2 = st.columns(2)
        #    
        #    with comp_col1:
        #        st.markdown("#### Average Sentiment Comparison")
        #        df_country_1 = df_filtered[df_filtered['article_country_x'] == selected_countries[0]]
        #        df_country_2 = df_filtered[df_filtered['article_country_x'] == selected_countries[1]]
        #        
        #       comparison_data = pd.DataFrame({
        #            'Country': selected_countries,
        #            'Avg Sentiment': [
        #                df_country_1['sentiment_numeric'].mean() if not df_country_1.empty else 0,
        #                df_country_2['sentiment_numeric'].mean() if not df_country_2.empty else 0
        #            ]
        #        })
                
         #       fig_comp = px.bar(
         #           comparison_data,
         #           x='Country',
         #           y='Avg Sentiment',
         #           color='Avg Sentiment',
         #           color_continuous_scale='RdYlGn',
         #           title="Sentiment Score Comparison"
         #       )
         #       st.plotly_chart(fig_comp, use_container_width=True)
            
         #   with comp_col2:
         #       st.markdown("#### Coverage Volume Comparison")
         #       coverage_data = pd.DataFrame({
         #           'Country': selected_countries,
         #           'Article Count': [
         #               len(df_country_1),
         #               len(df_country_2)
         #           ]
         #       })
                
         #       fig_coverage = px.bar(
         #           coverage_data,
         #           x='Country',
         #           y='Article Count',
         #           color='Country',
         #           title="Media Attention Volume"
         #       )
         #       st.plotly_chart(fig_coverage, use_container_width=True)



# -------------------------
# Tab 3: Conflict × Media Explorer
# -------------------------
elif st.session_state.page == "explorer":
    selected_country = (st.session_state.get("selected_country_name") or "").strip()

    # If user opened Explorer directly (no map click), show guidance + exit early
    if not selected_country:
        st.info("No country selected yet. Please go back and click a country on the map to explore its coverage.")
        if st.button("← Back to map", key="back_to_map_no_country"):
            st.session_state.page = "underrep"
            st.session_state._nav_override = "Conflict Underrepresentation"
            st.rerun()
        st.stop()


    st.markdown(f"### {st.session_state.selected_country_name}")

    if st.button("← Back to map", key="back_to_map"):
        st.session_state.page = "underrep"
        st.session_state._nav_override = "Conflict Underrepresentation"
        st.rerun()

    # ---- Country-level Filter ----
    # --- prefill once when arriving from drilldown ---
    if "filt_country" not in st.session_state:
        st.session_state.filt_country = ""


    if st.session_state.get("drilldown_pending") and st.session_state.get("selected_country_name"):
        st.session_state.filt_country = st.session_state.selected_country_name
        st.session_state["cov_country_prefill"] = st.session_state.selected_country_name #Evolution Plot
        st.session_state.drilldown_pending = False


    # -------------------------
    # Country overview 
    # -------------------------

    selected_country = st.session_state.selected_country_name

    # Keep session_state synced if user picked a different row
    MAX_TABLE_ROWS = 1000

    if selected_country:
        set_selected_country(country=selected_country)

        # Pull article data for selected country
        show_cols = [A_ID, A_PUB, A_SOURCE, A_TITLE, A_DESC, M_COUNTRY]
        if A_URL:
            show_cols.append(A_URL)

        articles_sql = f"""
        SELECT {", ".join(show_cols)}
        FROM {MATCH_TABLE}
        WHERE {M_COUNTRY} = ?
        ORDER BY {A_PUB} DESC
        """
        art = qdf_match(articles_sql, [selected_country.strip()])
        if not art.empty:
            art[A_PUB] = pd.to_datetime(art[A_PUB], errors="coerce")



        # Ensure datetime for recency metrics and time plots
        if not art.empty:
            art[A_PUB] = pd.to_datetime(art[A_PUB], errors="coerce")

        # Recency metrics
        DATA_LAG_DAYS = 365
        effective_now = datetime.utcnow() - timedelta(days=DATA_LAG_DAYS)
        window_start = effective_now - timedelta(days=7)

        # last article date (no LIMIT)
        last_sql = f"""
        SELECT MAX({A_PUB}) AS last_pub
        FROM {MATCH_TABLE}
        WHERE {M_COUNTRY} = ?
        """
        last_pub = qdf_match(last_sql, [selected_country.strip()]).iloc[0]["last_pub"]

        if last_pub is not None and str(last_pub) != "nan":
            last_article_date = pd.to_datetime(last_pub, errors="coerce")
            days_since_last = (effective_now - last_article_date.to_pydatetime()).days
        else:
            days_since_last = None

        # articles in last 7 days (no LIMIT)
        last7_sql = f"""
        SELECT COUNT(*) AS n_last7
        FROM {MATCH_TABLE}
        WHERE {M_COUNTRY} = ?
        AND {A_PUB} >= ?
        AND {A_PUB} <= ?
        """
        last_7 = int(qdf_match(last7_sql, [selected_country.strip(), window_start, effective_now]).iloc[0]["n_last7"])


        # --- Total matched articles KPI (accurate, no LIMIT) ---
        total_sql = f"""
        SELECT COUNT(*) AS n_total
        FROM {MATCH_TABLE}
        WHERE {M_COUNTRY} = ?
        """
        total_matched = int(qdf_match(total_sql, [selected_country.strip()]).iloc[0]["n_total"])


        # -------- Country KPIs: einzelne graue Karten + Top-3-Outlets --------

        row = conf[conf[C_COUNTRY].astype(str) == selected_country.strip()]
        if row.empty:
            st.warning("Selected country not found in conflict_country. Try another country.")
            st.stop()
        r = row.iloc[0]


        # Zeile 1: 3 Kennzahlen + Top-3-Outlets (alles im jeweiligen grauen Block)
        row1_col1, row1_col2, row1_col3 = st.columns([1, 1, 1])

        with row1_col1:
            st.markdown(
                f"""
                <div style="
                    background-color:#f5f5f5;
                    padding:0.9rem 1.1rem;
                    border-radius:12px;
                    margin-bottom:0.8rem;
                ">
                <div style="font-size:0.85rem; color:#555;">Matched articles</div>
                <div style="font-size:1.6rem; color:#555;font-weight:600;">{int(total_matched):,}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with row1_col2:
            st.markdown(
                f"""
                <div style="
                    background-color:#f5f5f5;
                    padding:0.9rem 1.1rem;
                    border-radius:12px;
                    margin-bottom:0.8rem;
                ">
                <div style="font-size:0.85rem; color:#555;">Events</div>
                <div style="font-size:1.6rem; color:#555;font-weight:600;">{int(r[C_EVENTS]):,}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with row1_col3:
            st.markdown(
                f"""
                <div style="
                    background-color:#f5f5f5;
                    padding:0.9rem 1.1rem;
                    border-radius:12px;
                    margin-bottom:0.8rem;
                ">
                <div style="font-size:0.85rem; color:#555;">Fatalities</div>
                <div style="font-size:1.6rem; color:#555;font-weight:600;">{int(r[C_FATAL]):,}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        

        # row 2
        row2_col1, row2_col2, row2_col3 = st.columns(3)

        with row2_col1:
            val = (
                f"{r['articles_per_event']:.3f}"
                if r["articles_per_event"] is not None
                else "NA"
            )
            st.markdown(
                f"""
                <div style="
                    background-color:#f5f5f5;
                    padding:0.9rem 1.1rem;
                    border-radius:12px;
                    margin-top:0.2rem;
                ">
                <div style="font-size:0.85rem; color:#555;">Articles per event</div>
                <div style="font-size:1.6rem; color:#555;font-weight:600;">{val}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with row2_col2:
            ds = f"{days_since_last}" if days_since_last is not None else "NA"
            st.markdown(
                f"""
                <div style="
                    background-color:#f5f5f5;
                    padding:0.9rem 1.1rem;
                    border-radius:12px;
                    margin-top:0.2rem;
                ">
                <div style="font-size:0.85rem; color:#555;">Days since last article (data timeline)</div>
                <div style="font-size:1.6rem; color:#555;font-weight:600;">{ds}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with row2_col3:
            st.markdown(
                f"""
                <div style="
                    background-color:#f5f5f5;
                    padding:0.9rem 1.1rem;
                    border-radius:12px;
                    margin-top:0.2rem;
                ">
                <div style="font-size:0.85rem; color:#555;">Articles last 7 days (data timeline)</div>
                <div style="font-size:1.6rem; color:#555;font-weight:600;">{last_7}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


    #selected_country = st.text_input("Selected country", value=selected_country)

    st.divider()

    # -------------------------
    # Country conflict profile (VOR der Artikelliste)
    # -------------------------

    #tab_evolution, tab_events, tab_articles = st.tabs(["Evolution", "Events", "Articles"])

    #-------------------------
    # Stateful Tab bar
    # -------------------------

    TAB_LABELS = ["Evolution", "Events", "Articles"]

    # pick a default *once*
    if "explorer_tab" not in st.session_state:
        st.session_state["explorer_tab"] = "Evolution"   

    active = st.radio(
        "",
        TAB_LABELS,
        key="explorer_tab",
        horizontal=True,
        label_visibility="collapsed",
    )   

    if active == "Evolution":

        with st.expander("What this chart shows", expanded=False):
            st.markdown(
                """
                <p style="font-size:0.95rem; color:#444; margin:0 0 1.0rem 0;">
                    The coverage-over-time graph aggregates all conflict-related articles by month to show how overall
                    reporting intensity fluctuates, including bursts and quiet periods. Using the date filters, users can
                    examine whether major conflict episodes coincide with sustained increases in coverage or only trigger
                    short-lived spikes, informing interpretations of attention cycles and potential media fatigue.
                    To enable direct trend comparison, article coverage and conflict events are normalized, allowing users to assess 
                    whether both rise and fall together over time regardless of differences in absolute scale.
                </p>
                """,
                unsafe_allow_html=True,
            )


        # --- load article dates & countries from MATCH_TABLE ---
        cov_sql = f"""
        SELECT {A_PUB} AS published, {M_COUNTRY} AS country
        FROM {MATCH_TABLE}
        WHERE {A_PUB} IS NOT NULL
        """
        cov_df = qdf_match(cov_sql)

        # --- load conflict events (event_date, country) from events table ---
        ev_sql = """
        SELECT event_date AS event_date, country
        FROM events
        WHERE event_date IS NOT NULL
        """
        ev_df = qdf_conf(ev_sql)

        if cov_df.empty or ev_df.empty:
            st.info("No parsable publication dates or event dates available for this view.")
        else:
            cov_df["published"] = pd.to_datetime(cov_df["published"], errors="coerce")
            cov_df = cov_df.dropna(subset=["published"])

            ev_df["event_date"] = pd.to_datetime(ev_df["event_date"], errors="coerce")
            ev_df = ev_df.dropna(subset=["event_date"])

            # --- Filters: country + date range ---
            f_col1, f_col2, f_col3 = st.columns([1.5, 1, 1])

            with f_col1:
                countries_cov = ["All countries"] + sorted(
                    c for c in cov_df["country"].dropna().astype(str).unique()
                )

                # Ensure the selectbox key exists with a valid default
                if "cov_country" not in st.session_state or st.session_state["cov_country"] not in countries_cov:
                    st.session_state["cov_country"] = "All countries"

                # Apply drilldown prefill ONCE (then remove it so user can change manually later)
                pre = st.session_state.pop("cov_country_prefill", None)
                if pre:
                    pre_str = str(pre)
                    if pre_str in countries_cov:
                        st.session_state["cov_country"] = pre_str
                    else:
                        # optional: case-insensitive match fallback
                        match = next((c for c in countries_cov if c.lower() == pre_str.lower()), None)
                        if match:
                            st.session_state["cov_country"] = match

                cov_country = st.selectbox(
                    "Country (coverage & events)",
                    options=countries_cov,
                    key="cov_country",
                    help="Select a specific country or keep 'All countries' for global trends.",
                )


            with f_col2:
                cov_from = st.date_input("From date (coverage)", value=None)

            with f_col3:
                cov_to = st.date_input("To date (coverage)", value=None)

            # apply country filter
            art_tmp = cov_df.copy()
            ev_tmp = ev_df.copy()

            if cov_country != "All countries":
                art_tmp = art_tmp[art_tmp["country"].astype(str) == cov_country]
                ev_tmp = ev_tmp[ev_tmp["country"].astype(str) == cov_country]

            # apply date filter (same window for both series)
            if cov_from and cov_to:
                art_tmp = art_tmp[
                    (art_tmp["published"].dt.date >= cov_from)
                    & (art_tmp["published"].dt.date <= cov_to)
                ]
                ev_tmp = ev_tmp[
                    (ev_tmp["event_date"].dt.date >= cov_from)
                    & (ev_tmp["event_date"].dt.date <= cov_to)
                ]

            if art_tmp.empty and ev_tmp.empty:
                st.info("No articles or events in the selected period and country filter.")
            else:
                # aggregate per month
                if not art_tmp.empty:
                    art_tmp["month"] = to_month(art_tmp["published"])
                    by_month_art = (
                        art_tmp.groupby("month")
                        .size()
                        .reset_index(name="n_articles")
                    )
                else:
                    by_month_art = pd.DataFrame(columns=["month", "n_articles"])

                if not ev_tmp.empty:
                    ev_tmp["month"] = to_month(ev_tmp["event_date"])
                    by_month_ev = (
                        ev_tmp.groupby("month")
                        .size()
                        .reset_index(name="n_events")
                    )
                else:
                    by_month_ev = pd.DataFrame(columns=["month", "n_events"])

                # merge to ensure same x-axis
                month_all = pd.DataFrame(
                    {"month": sorted(set(by_month_art["month"]) | set(by_month_ev["month"]))}
                )
                month_all = month_all.merge(by_month_art, on="month", how="left")
                month_all = month_all.merge(by_month_ev, on="month", how="left")
                month_all["n_articles"] = month_all["n_articles"].fillna(0)
                month_all["n_events"] = month_all["n_events"].fillna(0)

                # --- normalize both series (0..1) for trend comparison ---
                month_all["articles_norm"] = (
                    (month_all["n_articles"] - month_all["n_articles"].min())
                    / ((month_all["n_articles"].max() - month_all["n_articles"].min()) or 1)
                )
                month_all["events_norm"] = (
                    (month_all["n_events"] - month_all["n_events"].min())
                    / ((month_all["n_events"].max() - month_all["n_events"].min()) or 1)
                )

                title_suffix = f" – {cov_country}" if cov_country != "All countries" else " – all countries"

                month_long = month_all.melt(
                    id_vars="month",
                    value_vars=["articles_norm", "events_norm"],
                    var_name="series",
                    value_name="value",
                )

                # prettier legend labels
                month_long["series"] = month_long["series"].map({
                    "articles_norm": "Articles (normalized)",
                    "events_norm": "Events (normalized)",
                })

                color_scale = alt.Scale(
                    domain=["Articles (normalized)", "Events (normalized)"],
                    range=["#FF69B4", "#1f77b4"],
                )

                line_chart = (
                    alt.Chart(month_long)
                    .mark_line(point=False)
                    .encode(
                        x=alt.X("month:N", title="Month"),
                        y=alt.Y("value:Q", title="Normalized (0–1)"),
                        color=alt.Color(
                            "series:N",
                            title="",
                            scale=color_scale,
                            legend=alt.Legend(orient="bottom"),
                        ),
                        tooltip=[
                            "month",
                            "series",
                            alt.Tooltip("value:Q", title="Normalized", format=".2f"),
                        ],
                    )
                    .properties(title=f"Coverage vs. conflict events over time{title_suffix}")
                )

                st.altair_chart(line_chart, use_container_width=True)



    elif active == "Events":

        # Konfliktprofil-Daten
        cf = qdf_conf_features(selected_country.strip())
        if cf.empty:
            st.info("No conflict feature data available for this country.")
        else:
            # Event-type-Verteilung für Pie-Chart
            top_events = (
                cf.groupby("event_type_mode", dropna=True)["n_events"]
                .sum()
                .reset_index()
                .sort_values("n_events", ascending=False)
            )
            if top_events.shape[0] > 5:
                top5 = top_events.head(5).copy()
                others = pd.DataFrame(
                    {
                        "event_type_mode": ["Other"],
                        "n_events": [top_events["n_events"].iloc[5:].sum()],
                    }
                )
                event_pie_df = pd.concat([top5, others], ignore_index=True)
            else:
                event_pie_df = top_events.copy()

            # Disorder-Type-Bar-Chart
            top_disorders = (
                cf.groupby("disorder_type_mode", dropna=True)["n_events"]
                .sum()
                .reset_index()
                .sort_values("n_events", ascending=False)
                .head(5)
            )

            # Key actors
            top_actors = (
                cf.groupby("primary_assoc_actor_1", dropna=True)["n_events"]
                .sum()
                .reset_index()
                .sort_values("n_events", ascending=False)
                .head(4)
            )

            col_event, col_disorder, col_actors = st.columns(3)

            # ---- Event types share ----
            with col_event:
                st.markdown(
                    """
                    <p style="font-size:1.4rem; font-weight:700; margin-top:1.2rem; margin-bottom:0.35rem;">
                        Event types share
                    </p>
                    """,
                    unsafe_allow_html=True,
                )


                pie = (
                    alt.Chart(event_pie_df)
                    .mark_arc(outerRadius=115, innerRadius=40)
                    .encode(
                        theta=alt.Theta("n_events:Q", stack=True),
                        color=alt.Color(
                            "event_type_mode:N",
                            title="Event type",
                            legend=alt.Legend(orient="bottom"),
                            scale=alt.Scale(
                                range=[
                                    "#FF69B4",  # main pink
                                    "#1f77b4",  # blue
                                    "#FFE4F3",  # light pink
                                    "#A6D4FF",  # light blue
                                    "#FFC1E6",  # soft pink
                                ]
                            ),
                        ),
                        tooltip=["event_type_mode", "n_events"],
                    )
                    .properties(width=330, height=330)
                )
                st.altair_chart(pie, use_container_width=True)

            # ---- Main disorder categories ----
            with col_disorder:
                st.markdown(
                    """
                    <p style="font-size:1.4rem; font-weight:700; margin-top:1.2rem; margin-bottom:0.35rem;">
                        Main disorder categories
                    </p>
                    """,
                    unsafe_allow_html=True,
                )

                disorder_bar = (
                    alt.Chart(top_disorders)
                    .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                    .encode(
                        y=alt.Y(
                            "disorder_type_mode:N",
                            sort="-x",
                            title=None,
                            axis=alt.Axis(labelLimit=200),
                        ),
                        x=alt.X(
                            "n_events:Q",
                            title="Events",
                            axis=alt.Axis(tickMinStep=10),
                        ),
                        color=alt.Color(
                            "disorder_type_mode:N",
                            legend=None,
                            scale=alt.Scale(
                                range=["#FF69B4", "#1f77b4", "#FFE4F3", "#A6D4FF", "#FFC1E6"],
                            ),
                        ),
                        tooltip=["disorder_type_mode", "n_events"],
                    )
                    .properties(width=380, height=220)
                    .configure_view(continuousWidth=380, strokeWidth=0)
                )
                st.altair_chart(disorder_bar, use_container_width=True)

            # ---- Key primary actors + Zeitraum ----
            with col_actors:
                st.markdown(
                    """
                    <p style="font-size:1.4rem; font-weight:700; margin-top:1.2rem; margin-bottom:0.35rem;">
                        Key primary actors
                    </p>
                    """,
                    unsafe_allow_html=True,
                )

                for _, ac in top_actors.iterrows():
                    st.markdown(f"- {ac['primary_assoc_actor_1']} ({int(ac['n_events'])} events)")

                cf["start_date"] = pd.to_datetime(cf["start_date"], errors="coerce")
                cf["end_date"] = pd.to_datetime(cf["end_date"], errors="coerce")
                if cf["start_date"].notna().any() and cf["end_date"].notna().any():
                    start_min = cf["start_date"].min().date()
                    end_max = cf["end_date"].max().date()
                    st.markdown(
                        "<p style='font-size:0.85rem; color:#555; margin-top:0.9rem;'>"
                        "NA = no key primary actor</p>",
                        unsafe_allow_html=True,
                    )
            
        # Chatbot
        # -------------------------
        # Chatbot (prominent, tied to selected country)
        # -------------------------
        st.markdown(
            f"""
            <h4 style="margin-bottom:0.1rem;">Chat with Mirror AI about conflicts in {selected_country}</h4>
            
            """,
            unsafe_allow_html=True,
        )

        with st.expander("How our AI Assistant works", expanded=False):
            st.markdown(
            f"""
            <p style="font-size:0.9rem; color:#555; margin-top:0.15rem;">
                Ask about conflict events and developments, independent of media coverage.
                Answers are generated <b>only</b> from ACLED event notes stored in the database (no external sources).
                Use the date range to control which events are summarized.
            </p>
            """,
            unsafe_allow_html=True,
        )

        # Reset chat when country changes
        if st.session_state.get("_chat_country") != selected_country:
            st.session_state["_chat_country"] = selected_country
            st.session_state["_chat_messages"] = []
            st.session_state.pop("_chat_date_range", None)

        chat_left, chat_right = st.columns([2.2, 1])

        with chat_right:
            # --- Date bounds for this country ---
            min_d_str, max_d_str = _get_country_date_bounds(selected_country.strip())

            if not min_d_str or not max_d_str:
                st.warning("No dated events available for this country.")
                chat_start_iso, chat_end_iso = None, None
            else:
                min_d = datetime.fromisoformat(min_d_str).date()
                max_d = datetime.fromisoformat(max_d_str).date()

                st.caption(f"Available event dates: {min_d_str} → {max_d_str}")

                # Default range = last ~180 days within the dataset for this country
                default_start = max(min_d, max_d - timedelta(days=180))
                default_range = st.session_state.get("_chat_date_range", (default_start, max_d))

                picked = st.date_input(
                    "Event date range",
                    value=default_range,
                    min_value=min_d,
                    max_value=max_d,
                    key="_chat_date_range",
                )

                # Streamlit returns either a single date or a tuple/list of two dates
                if isinstance(picked, (tuple, list)) and len(picked) == 2:
                    start_d, end_d = picked
                else:
                    start_d, end_d = picked, picked

                # Ensure ordering
                if start_d > end_d:
                    start_d, end_d = end_d, start_d

                chat_start_iso = start_d.isoformat()
                chat_end_iso = end_d.isoformat()

            chat_k = st.slider(
                "Notes to retrieve",
                min_value=30,
                max_value=200,
                value=80,
                step=10,
                key="_chat_k",
                help="More notes can improve grounding but makes prompts larger.",
            )
            #chat_model = st.selectbox(
            #    "Model",
            #    options=["gpt-4o-mini", "gpt-4o"],
            #    index=0,
            #    key="_chat_model",
            #)
            if st.button("Clear chat", use_container_width=True, key="_chat_clear"):
                st.session_state["_chat_messages"] = []

            st.caption(
                "Tip: Specific questions work best (actors, cities, event type). "
                "For broad questions, the app will summarize the most recent events in the date range."
            )

        with chat_left:
            # Render history
            for m in st.session_state.get("_chat_messages", []):
                with st.chat_message(m["role"]):
                    st.markdown(m["content"])

            with st.form(key="_chat_form", clear_on_submit=True):
                user_q = st.text_input(
                    "Ask a question",
                    value="",
                    placeholder="E.g., What are the main conflict dynamics in the last 6 months?",
                )
                submitted = st.form_submit_button("Ask")

            if submitted and user_q.strip():
                st.session_state.setdefault("_chat_messages", []).append(
                    {"role": "user", "content": user_q.strip()}
                )
                with st.chat_message("user"):
                    st.markdown(user_q.strip())

                with st.chat_message("assistant"):
                    with st.spinner("Searching notes and generating answer..."):
                        if chat_start_iso is None or chat_end_iso is None:
                            rows, used_fallback = [], True
                        else:
                            rows, used_fallback = _retrieve_notes(
                                selected_country.strip(),
                                user_q.strip(),
                                start_date=chat_start_iso,
                                end_date=chat_end_iso,
                                k=int(chat_k),
                            )

                        if not rows:
                            out = (
                                "I couldn't find usable event notes for this country in the selected date range. "
                                "Try expanding the date range or asking about a different aspect."
                            )
                            st.markdown(out)
                        else:
                            if used_fallback:
                                st.caption(
                                    "No strong keyword match detected — summarizing the most recent events in the date range."
                                )
                            out = _chat_answer(
                                selected_country.strip(),
                                user_q.strip(),
                                rows,
                                model="gpt-4o-mini",
                            )
                            st.markdown(out)

                            with st.expander("Sources used (top rows)"):
                                for event_id, event_date, _ in rows[:15]:
                                    st.write(f"- event_id_cnty={event_id}, date={event_date}")

                st.session_state["_chat_messages"].append({"role": "assistant", "content": out})

        # End ChatBot


    else:  # Articles tab

        if not selected_country.strip():
            st.info("Select a country from the overview table above to see details.")
            st.stop()


        # Leitfrage + Beschreibung für Artikelliste
        st.markdown(
            f"""
            <p style="font-size:1.4rem; font-weight:700; margin-top:0.5rem; margin-bottom:0.35rem;">
                What is currently reported about conflicts in {selected_country}?
            </p>
            """,
            unsafe_allow_html=True,
        )

        # -------------------------
        # Articles + Outlet detail 
        # -------------------------
        
        # Filter
        search_col, date_from_col, date_to_col = st.columns([2, 1, 1])

        with search_col:
            search_query = st.text_input(
                "Search title and description",
                value="",
                placeholder="e.g. ceasefire, protest, election ...",
            )

        with date_from_col:
            date_from = st.date_input("From date", value=None)

        with date_to_col:
            date_to = st.date_input("To date", value=None)

        art_filtered = art.copy()

        if date_from and date_to:
            art_filtered = art_filtered[
                (art_filtered[A_PUB].dt.date >= date_from)
                & (art_filtered[A_PUB].dt.date <= date_to)
            ]

        if search_query.strip():
            q = search_query.strip().lower()
            mask = (
                art_filtered[A_TITLE].astype(str).str.lower().str.contains(q, na=False)
                | art_filtered[A_DESC].astype(str).str.lower().str.contains(q, na=False)
            )
            art_filtered = art_filtered[mask]

        st.caption(
            "Filter articles by keywords in titles/descriptions and restrict results "
            "to a custom publication date range."
        )

        # --- Outlet distribution below article table ---
        if not art_filtered.empty and A_SOURCE in art_filtered.columns:
            
            
            st.markdown(
                    """
                    <p style="font-size:1.4rem; font-weight:700; margin-top:1.2rem; margin-bottom:0.35rem;">
                        Which media outlets contribute most to the conflict coverage?
                    </p>
                    """,
                    unsafe_allow_html=True,
                )
            
            with st.expander("What this chart shows", expanded=False):
                st.markdown(
                    """
                    <p style="font-size:0.95rem; color:#444; margin:0 0 1.0rem 0;">
                        The outlet detail chart ranks news organizations by the number of related articles they publish about the selected country, showing how strongly each outlet shapes the countries news agenda. Longer bars and higher article counts indicate a larger influence on the narrative, while shorter bars highlight outlets that report on the same events far less frequently.
                    </p>
                    """,
                    unsafe_allow_html=True,
                )

            by_outlet = (
                art_filtered.groupby(A_SOURCE, dropna=False)
                .size()
                .reset_index(name="n_articles")
            ).sort_values("n_articles", ascending=False)

            # optional: nicer label for missing sources
            by_outlet[A_SOURCE] = by_outlet[A_SOURCE].fillna("(Unknown)")

            max_n = int(by_outlet["n_articles"].max())
            min_n = max(1, int((0.10 * max_n) + 0.9999))  # ceil(10% of max), at least 1

            by_outlet_plot = by_outlet[by_outlet["n_articles"] >= min_n].copy()

                
            pink_scale = alt.Scale(
                domain=[by_outlet["n_articles"].min(), by_outlet["n_articles"].max()],
                range=["#FFE4F3", "#FF69B4"],  # helles Pink -> kräftiges Pink
            )

            max_articles_outlet = int(by_outlet_plot["n_articles"].max())

            bar = (
                alt.Chart(by_outlet_plot)
                .mark_bar()
                .encode(
                    y=alt.Y(
                        f"{A_SOURCE}:N",
                        sort="-x",
                        title="Outlet",
                        axis=alt.Axis(labelLimit=250),  # allow longer names
                    ),
                    x=alt.X(
                        "n_articles:Q",
                        title="Articles",
                        axis=alt.Axis(
                            tickMinStep=5,        # steps of 5
                            tickCount=max_articles_outlet // 5 + 1,
                        ),
                    ),
                    tooltip=[A_SOURCE, "n_articles"],
                    color=alt.Color(
                        "n_articles:Q",
                        title="Articles",
                        scale=pink_scale,
                    ),
                )
                .properties(
                    height=500,
                    width=700,  # more horizontal space; adjust as needed
                )
                .configure_view(
                    continuousWidth=700,
                    strokeWidth=0,
                )
            )

            st.altair_chart(bar, use_container_width=True)



        if art_filtered.empty:
            st.info("No matched articles found for this selection and filters.")
        else:
            final_cols = [A_PUB, A_TITLE, A_SOURCE]
            if A_URL and A_URL in art_filtered.columns:
                final_cols.append(A_URL)

            art_table = art_filtered.head(MAX_TABLE_ROWS)

            out = art_table[final_cols].rename(
                columns={
                    A_PUB: "Published",
                    A_TITLE: "Title",
                    A_SOURCE: "Source",
                    A_URL: "URL",
                }
            )

            if "URL" in out.columns:
                st.dataframe(
                    out,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "URL": st.column_config.LinkColumn(
                            "URL",
                            display_text="Link to article",
                        )
                    },
                )
            else:
                st.dataframe(out, use_container_width=True, hide_index=True)
            
    st.caption(f"Maximum articles displayed: {MAX_TABLE_ROWS}")


# -------------------------
# Tab 5: Impressum
# -------------------------
elif st.session_state.page == "impressum":
    st.markdown("## About This Project")
    
    # ============================================================
    # 1. VIDEO SHOWCASE
    # ============================================================
    st.markdown("""
    <div style="margin-top:1.5rem;">
        <div style="font-size:1.6em; font-weight:700;">
            Project Overview
        </div>
        <div style="color:#666; font-size:0.9em;">
            Watch how we analyze conflict media coverage
        </div>
        <hr>
    </div>
    """, unsafe_allow_html=True)
    
    # Video container
    video_col1, video_col2 = st.columns([2, 1])
    
    with video_col1:
        # Check if video file exists
        video_path = PROJECT_ROOT / "data" / "project_video_small.mp4"
        
        if video_path.exists():
            video_file = open(video_path, 'rb')
            video_bytes = video_file.read()
            st.video(video_bytes)
        else:
            st.info(
                "📹 **Video placeholder**: Place your `project_video.mp4` file in the `data/` directory.\n\n"
                "Alternative: You can also use a YouTube link by replacing this section with:\n"
                "```python\nst.video('https://www.youtube.com/watch?v=YOUR_VIDEO_ID')\n```"
            )
    
    with video_col2:
        st.markdown("""
        **What you'll see:**
        
        - How we connect conflict events with media coverage
        - Pipeline architecture and data flow
        - Key insights from our analysis
        - Interactive dashboard features
        
        **Project Goals:**
        
        Analyze how German media frames global conflicts and identify 
        systematic patterns in coverage, attention, and emotional framing.
        """)
    
    st.divider()
    
    # ============================================================
    # 2. PIPELINE STATUS & DATA FRESHNESS
    # ============================================================
    st.markdown("""
    <div style="margin-top:2rem;">
        <div style="font-size:1.6em; font-weight:700;">
            Pipeline Status & Data Freshness
        </div>
        <div style="color:#666; font-size:0.9em;">
            Real-time status of automated data collection and processing
        </div>
        <hr>
    </div>
    """, unsafe_allow_html=True)
    
    # Get pipeline status
    with st.spinner("Loading pipeline status..."):
        pipeline_status = get_pipeline_status()
    
    # Status cards in grid
    col1, col2, col3, col4 = st.columns(4)
    
    components = [
        ('ACLED Events', 'acled', col1),
        ('News Articles', 'articles', col2),
        ('Country Matching', 'matching', col3),
        ('Sentiment Analysis', 'sentiment', col4)
    ]
    
    for name, key, col in components:
        with col:
            data = pipeline_status.get(key, {})
            status_color = get_status_color(data.get('status', 'unknown'))
            last_update = format_date_short(data.get('last_update', 'N/A'))
            total_records = f"{data.get('total_records', 0):,}"
            
            st.markdown(f"""
            <div style="
                background-color:#f5f5f5;
                padding:1rem 1.1rem;
                border-radius:12px;
                border-left: 4px solid {status_color};
                margin-bottom:1rem;
            ">
                <div style="font-size:1.2rem; color:#666; margin-bottom:0.3rem;">{name}</div>
                <div style="font-size:0.75rem; color:#666; margin-bottom:0.5rem;">
                    Status: <span style="color:{status_color}; font-weight:600;">●</span> 
                    {data.get('status', 'unknown').title()}
                </div>
                <div style="font-size:0.85rem; color:#555;">
                    <b>Newest Date:</b><br/>{last_update}
                </div>
                <div style="font-size:0.85rem; color:#555; margin-top:0.5rem;">
                    <b>Total Records:</b><br/>{total_records}
                </div>
            </div>
            """, unsafe_allow_html=True)
    
    # Date ranges section
    st.markdown("""
    <div style="margin-top:1.5rem;">
        <div style="font-size:1.3em; font-weight:600;">
            Available Data Ranges
        </div>
        <hr>
    </div>
    """, unsafe_allow_html=True)
    
    range_col1, range_col2 = st.columns(2)
    
    with range_col1:
        st.markdown("#### ACLED Conflict Events")
        acled_data = pipeline_status.get('acled', {})
        st.markdown(f"""
        - **First Event:** {format_date_short(acled_data.get('date_range_start', 'N/A'))}
        - **Latest Event:** {format_date_short(acled_data.get('date_range_end', 'N/A'))}
        - **Total Events:** {acled_data.get('total_records', 0):,}
        """)
    
    with range_col2:
        st.markdown("#### German News Coverage")
        articles_data = pipeline_status.get('articles', {})
        st.markdown(f"""
        - **First Article:** {format_date_short(articles_data.get('date_range_start', 'N/A'))}
        - **Latest Article:** {format_date_short(articles_data.get('date_range_end', 'N/A'))}
        - **Total Articles:** {articles_data.get('total_records', 0):,}
        """)
    
    st.divider()
    
    # ============================================================
    # 3. AUTOMATION SCHEDULE
    # ============================================================
    st.markdown("""
    <div style="margin-top:2rem;">
        <div style="font-size:1.6em; font-weight:700;">
            Automation Schedule
        </div>
        <div style="color:#666; font-size:0.9em;">
            Automated pipeline execution using Supercronic
        </div>
        <hr>
    </div>
    """, unsafe_allow_html=True)
    
    # Schedule visualization
    schedule_data = pd.DataFrame([
        {
            'Pipeline': 'ACLED Data Collection',
            'Frequency': 'Weekly (Sunday)',
            'Time': '02:00 AM UTC',
            'Purpose': 'Fetch latest conflict events from ACLED API',
            'Icon': '📊'
        },
        {
            'Pipeline': 'GNews Article Fetching',
            'Frequency': 'Daily',
            'Time': '06:00 AM UTC',
            'Purpose': 'Collect German-language conflict news',
            'Icon': '📰'
        },
        {
            'Pipeline': 'Country Matching',
            'Frequency': 'Weekly (Sunday)',
            'Time': '04:00 AM UTC',
            'Purpose': 'Match articles to countries and conflicts',
            'Icon': '🔗'
        },
        {
            'Pipeline': 'Sentiment Analysis',
            'Frequency': 'Daily',
            'Time': '05:00 AM UTC',
            'Purpose': 'Analyze emotional framing and topics',
            'Icon': '💭'
        }
    ])
    
    # Create visual schedule
    for _, row in schedule_data.iterrows():
        st.markdown(f"""
        <div style="
            background-color:#ffffff;
            padding:1rem 1.5rem;
            border-radius:10px;
            border-left: 4px solid #4A90E2;
            margin-bottom:1rem;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        ">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div style="flex: 1;">
                    <div style="font-size:1.1rem; color:#666; font-weight:600; margin-bottom:0.3rem;">
                        {row['Icon']} {row['Pipeline']}
                    </div>
                    <div style="font-size:0.85rem; color:#666;">
                        {row['Purpose']}
                    </div>
                </div>
                <div style="text-align: right; min-width: 200px;">
                    <div style="font-size:0.9rem; color:#4A90E2; font-weight:600;">
                        {row['Frequency']}
                    </div>
                    <div style="font-size:0.8rem; color:#999;">
                        {row['Time']}
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    # Execution flow diagram
    st.markdown("#### ⏱️ Sunday Execution Flow")
    st.markdown("""
    ```
    02:00 AM  ──→  ACLED starts
    02:15 AM  ──→  ACLED completes
    04:00 AM  ──→  Matching starts (uses fresh ACLED data)
    04:10 AM  ──→  Matching completes
    05:00 AM  ──→  Sentiment starts (processes week's articles)
    05:45 AM  ──→  Sentiment completes
    06:00 AM  ──→  GNews starts (collects new day's articles)
    06:20 AM  ──→  GNews completes
    ```
    """)
    
    st.divider()
    
    # ============================================================
    # 4. PROJECT INFORMATION (Original Impressum)
    # ============================================================
    st.markdown("""
    <div style="margin-top:2rem;">
        <div style="font-size:1.6em; font-weight:700;">
            Project Information
        </div>
        <hr>
    </div>
    """, unsafe_allow_html=True)
    
    info_col1, info_col2 = st.columns(2)
    
    with info_col1:
        st.markdown("""
        **Project Title:** Conflict Media Mirror  
        
        **Project Type:** Academic / Non-commercial data analysis project  
        
        **Description:**  
        This dashboard was developed as part of an academic research project
        analyzing the relationship between real-world conflict events and
        German-language media coverage.
        
        **Data Sources:**  
        - Armed Conflict Location & Event Data Project (ACLED)  
        - German-language news media articles  
        """)
    
    with info_col2:
        st.markdown("""
        **Disclaimer:**  
        This project is for research and educational purposes only.
        The visualizations and analyses do not claim completeness or factual correctness
        of individual events or media reports.
        
        **Responsibility for content:**  
        The project authors are responsible for the content presented in this dashboard.
        
        **Version:** 1.0  
        **Last Updated:** January 2026
        """)
    
    st.markdown("""
    <div style="margin-top:1.5rem; margin-bottom:1.5rem; text-align:center;">
        <a href="https://github.com/yannickkayser/dashboard_conflict_data" target="_blank" 
           style="
               display: inline-block;
               background: linear-gradient(135deg, #87CEEB, #4A90E2);
               color: white;
               padding: 0.8rem 2rem;
               border-radius: 8px;
               text-decoration: none;
               font-weight: 600;
               box-shadow: 0 4px 6px rgba(0,0,0,0.1);
           ">
            📂 View GitHub Repository
        </a>
    </div>
    """, unsafe_allow_html=True)
    
    # Technical details expander
    with st.expander("🔧 Technical Details"):
        st.markdown("""
        **Technologies Used:**
        - **Frontend:** Streamlit
        - **Data Processing:** Python (pandas, transformers, scikit-learn)
        - **NLP Models:** Helsinki-NLP translation, sentiment analysis
        - **Visualization:** Altair, Plotly, PyDeck
        - **Databases:** SQLite
        - **Automation:** Supercronic (cron scheduler)
        
        **Pipeline Components:**
        1. **Data Acquisition:** GNews API + ACLED API
        2. **Deduplication:** SimHash algorithm
        3. **Translation:** German → English (M2M-100)
        4. **Classification:** TF-IDF country matching
        5. **NLP Enrichment:** Sentiment, emotion, topic modeling
        6. **Matching:** Article-to-conflict linking
        
        **Databases:**
        - `conflict_data.db` - ACLED events and aggregations
        - `matchde_conflict.db` - Article-country matches
        - `processed_conflict_articles.csv` - NLP-enriched articles
        """)
    
    # Contact section
    st.markdown("""
    <div style="margin-top:2rem; padding:1.5rem; background-color:#f9f9f9; border-radius:10px;">
        <div style="font-size:1.2em; color:#666; font-weight:600; margin-bottom:0.8rem;">
            📧 Contact & Feedback
        </div>
        <div style="font-size:0.9rem; color:#666;">
            For questions, feedback, or collaboration inquiries, please open an issue 
            on our GitHub repository or contact the project maintainers through the 
            repository's discussion section.
        </div>
    </div>
    """, unsafe_allow_html=True)


    # BONUS
    if df is None:
        st.error("Data files not found. Please run data_processor.py first.")
    else:
        # ============================================================
        # 5. MEDIA OUTLET COMPARISON (Aggregate Analysis)
        # ============================================================
        st.markdown("""
        <div style="margin-top:3rem; margin-bottom:1.5rem;">
            <div style="
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                border-radius: 12px;
                color: white;
                box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            ">
                <div style="font-size:1.6em; font-weight:700; margin-bottom:8px;">
                    BONUS Media Outlet Comparison
                </div>
                <div style="font-size:0.95em; opacity:0.95;">
                    This section analyzes all selected data 
                    to identify institutional patterns and editorial biases 
                    across German media outlets.
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        with st.container():
            st.markdown(
                "<div class='filter-box'><b>Analytics Filters</b></div>",
                unsafe_allow_html=True
            )

            f1, f2 = st.columns([2, 1])

            with f1:
                date_range = st.date_input(
                    "Date Range",
                    [df['published_date'].min(), df['published_date'].max()]
                )

            with f2:
                scope = st.selectbox(
                    "Scope",
                    ["All News", "International", "Domestic"]
                )

        # Apply date filter
        if len(date_range) == 2:
            mask = (df['published_date'].dt.date >= date_range[0]) & (df['published_date'].dt.date <= date_range[1])
            df_filtered = df.loc[mask]
        else:
            df_filtered = df.copy()

        # Apply scope filter
        if scope == "International": 
            df_filtered = df_filtered[df_filtered['is_domestic'] == False]
        elif scope == "Domestic": 
            df_filtered = df_filtered[df_filtered['is_domestic'] == True]    

        top_outlets = df_filtered['source_name'].value_counts().head(10).index
        df_top_outlets = df_filtered[df_filtered['source_name'].isin(top_outlets)]
        exclude = ['neutral', 'others', 'other', 'label_1']
        df_top_active = df_top_outlets[~df_top_outlets['emotion_label'].str.lower().isin(exclude)]

        tab_heatmap, tab_sentiment = st.tabs(["Institutional Emotion Profile", "Tone Variance Score"])

        with tab_heatmap:
            st.subheader("Institutional Emotion Heatmap (Top 5 Emotions)")
            if not df_top_active.empty:
                df_active_all = df_filtered[~df_filtered['emotion_label'].str.lower().isin(exclude)]
                top_5_global = df_active_all['emotion_label'].value_counts().nlargest(5).index.tolist()
                df_top_active_top5 = df_top_active[df_top_active['emotion_label'].isin(top_5_global)]
                
                if not df_top_active_top5.empty:
                    ctab = pd.crosstab(df_top_active_top5['source_name'], df_top_active_top5['emotion_label'], normalize='index') * 100
                    fig_heat = px.imshow(ctab, text_auto=".1f", aspect="auto",
                                        labels=dict(x="Top 5 Emotions", y="Media Outlet", color="Percentage (%)"),
                                        color_continuous_scale="Purples",
                                        title="Framing Choice by Outlet (Top 5 Active Emotions %)")
                    st.plotly_chart(fig_heat, use_container_width=True)
                else:
                    st.warning("No articles matching the Top 5 global emotions found for these outlets.")
            else:
                st.warning("Insufficient specific emotional data.")

        with tab_sentiment:
            st.subheader("Average Tone Variance (Bias Check)")
            if not df_top_outlets.empty:
                df_outlet_avg = df_top_outlets.groupby('source_name')['sentiment_numeric'].agg(['mean', 'count']).reset_index()
                df_outlet_avg.columns = ['Media Outlet', 'Avg Tone Score', 'Article Volume']
                
                fig_outlet = px.scatter(df_outlet_avg, x='Avg Tone Score', y='Media Outlet', 
                                        size='Article Volume', color='Avg Tone Score',
                                        color_continuous_scale='RdBu', 
                                        color_continuous_midpoint=0,
                                        range_x=[-0.2, 0.2],
                                        title="Outlet Positioning on the Emotional Spectrum",
                                        labels={'Avg Tone Score': 'Intense/Negative Framing <---> Calm/Positive Framing'})
                fig_outlet.add_vline(x=df_filtered['sentiment_numeric'].mean(), line_dash="dash", line_color="gray", annotation_text="Market Avg")
                st.plotly_chart(fig_outlet, use_container_width=True)
            else:
                st.info("No media outlet data available for the current selection.")

        # ============================================================
        # 6. NARRATIVE DEEP DIVE (Applied to all filtered data)
        # ============================================================
        st.markdown("""
        <div style="margin-top:2.2rem;">
            <div style="font-size:1.6em; font-weight:700;">
                Narrative Discovery
            </div>
            <div style="color:#666; font-size:0.9em;">
                Recurring storylines across selected countries
            </div>
            <hr>
        </div>
        """, unsafe_allow_html=True)

        top_clusters = df_filtered['article_cluster_id'].value_counts().head(5)

        if not top_clusters.empty:
            translate = st.checkbox("Enable Translation for Event Headlines")
            if translate:
                ts = get_translator()

            for rank, (cid, count) in enumerate(top_clusters.items(), start=1):
                cluster_data = df_filtered[df_filtered['article_cluster_id'] == cid]
                if cluster_data.empty:
                    continue

                sample = cluster_data.iloc[0]

                display_title = sample.get('title', 'No headline available')
                if translate:
                    try:
                        display_title = ts(display_title[:512])[0]['translation_text']
                    except:
                        pass

                event_type = sample.get('acled_event_type', 'Unknown event type')

                expander_title = (
                    f"Top {rank} · {event_type} · "
                    f"{display_title[:80]}{'…' if len(display_title) > 80 else ''} "
                    f"({count} articles)"
                )

                with st.expander(expander_title):
                    st.write(f"**Headline:** {display_title}")
                    if translate:
                        st.caption(f"Original German: {sample.get('title', '')}")

                    st.write(f"**Event Type:** {sample.get('acled_event_type', 'Unknown')}")
                    st.write(f"**Location:** {sample.get('detected_locations', 'Not specified')}")
                    st.write(f"**Tone Intensity Score:** {sample.get('sentiment_numeric', 0):.2f}")
                    st.progress(min(1.0, abs(sample.get('sentiment_numeric', 0))))

        st.divider()
        st.caption("Native German processing with English UI representation. Data source: processed_conflict_articles.csv")
