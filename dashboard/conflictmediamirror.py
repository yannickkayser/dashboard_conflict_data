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

# -------------------------
# Paths
# -------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

CONFLICT_DB = DATA_DIR / "conflict_data.db"
MATCHING_DB = DATA_DIR / "matching_country.db"

COUNTRY_TABLE = "conflict_country"
MATCH_TABLE = "match_country_slim"
CONFLICT_FEATURES_TABLE = "conflict_features"

st.set_page_config(page_title="Conflict Media Mirror", layout="wide")

# -------------------------
# Styling
# -------------------------
st.markdown(
    """
<style>
.block-container {padding-top: 0rem; padding-bottom: 2.5rem; max-width: 1400px;}
.small-muted {color: rgba(250,250,250,0.65); font-size: 0.9rem;}
.main-header {
  padding: 2rem 2rem;
  background: linear-gradient(135deg, #87CEEB, #4A90E2);
  border-radius: 0px;
  margin: -1rem -1rem 2rem -1rem;
  text-align: center;
  box-shadow: 0 4px 6px rgba(0,0,0,0.1);
}
.main-header h1 {
  color: white;
  font-size: 2.5rem;
  font-weight: 700;
  margin: 0;
  text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
}
</style>
""",
    unsafe_allow_html=True,
)

# -------------------------
# Main Header
# -------------------------
st.markdown(
    """
<div class="main-header">
  <h1> Conflict Media Mirror</h1>
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


# -------------------------
# Check DBs exist
# -------------------------
if not CONFLICT_DB.exists():
    st.error(f"Missing DB: {CONFLICT_DB}")
    st.stop()
if not MATCHING_DB.exists():
    st.error(f"Missing DB: {MATCHING_DB}")
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

st.set_page_config(page_title="Underrepresentation Map", layout="wide")


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


# --------------------------
# End Helpers Underrepresentation Tab
# --------------------------


# --------------------------
# Helpers Sentiment
# --------------------------
DATA_PATH = PROJECT_ROOT / "data" / "processed_conflict_articles.csv"
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
# --------------------------
# End Helpers Sentiment
# --------------------------


# -------------------------
# Main Tabs
# -------------------------
tab1, tab2, tab3, tab5 = st.tabs(
    [
        "Conflict Underrepresentation",
        "Sentiment Analysis",
        "Conflict Media Explorer",
        "Impressum",
    ]
)


# -------------------------
# Tab 1: Conflict Underrepresentation
# -------------------------
with tab1:
    st.markdown("## Conflict Underrepresentation Analysis")

    
    st.markdown(
        """
        <p style="font-size:1.4rem; font-weight:700; margin-top:1.2rem; margin-bottom:0.35rem;">
            In which countries does media attention diverge most from conflict severity?
        </p>
        <p style="font-size:0.95rem; color:#444; margin:0 0 1.0rem 0;">
            This page compares how severe each country’s conflict situation is (events and fatalities)
            with how often it appears in conflict-related news. Countries where coverage lags behind
            severity can be interpreted as systematically underrepresented in media reporting.
        </p>
        """,
        unsafe_allow_html=True,
    )

    

    #df_idx = load_indices()
    df_plot = load_indices()
    world = load_world()

    # Global controls
    #st.sidebar.header("Global metric")
    w = st.slider(
        "Weight on fatalities (w) in severity share",
        0.0, 1.0, 0.5, 0.05,
        help="0 = only events share, 1 = only fatalities share; affects directly formula of severity_share"
    )

    # compute once, right after w exists
    df_plot["severity_share"] = (1 - w) * df_plot["share_events"] + w * df_plot["share_fatalities"]
    df_plot["underrep_share"] = df_plot["share_articles"] - df_plot["severity_share"]

    # STRUCTURE OF PAGE IN TABS
    tab2d, tab3d = st.tabs(["2D Map", "3D Map"])

    # -------------------------
    # 2D map
    # -------------------------
    with tab2d:

        
        #st.subheader("Which countries are visibly under- or overrepresented in media coverage?")
        st.caption("Color = share_articles − severity_share (negative = undercovered)")

        # Question + explanation for 2D map
        st.markdown(
            """
            
            </p>
            <p style="font-size:0.9rem; color:#555; margin:0 0 0.7rem 0;">
                The 2D map contrasts each country’s share of global conflict severity (events and fatalities)
                with its share of conflict-related articles. Countries shaded towards the undercovered end
                have fewer articles than their severity would suggest, while overcovered countries receive
                disproportionate attention relative to their conflict burden.
            </p>
            """,
            unsafe_allow_html=True,
        )

        # Controls in one line
        col_clip, col_gamma = st.columns(2)

        with col_clip:
            clip = st.slider(
                "2D color clip",
                min_value=0.001,
                max_value=0.1,
                value=0.01,
                step=0.01,
                help="Values beyond ±clip are saturated to the max color.",
            )

        with col_gamma:
            gamma = st.slider(
                "2D color gamma",
                min_value=0.2,
                max_value=2.0,
                value=1.0,
                step=0.05,
                help="Lower (<1) boosts contrast among low coverage values; higher (>1) compresses.",
            )
        geojson2d, merged2d = build_geojson_underrep(world, df_plot, clip=clip, gamma=gamma)

        layer2d = pdk.Layer(
            "GeoJsonLayer",
            data=geojson2d,
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
            underrep_share: {underrep_share}<br/>
            severity_share: {severity_share}<br/>
            share_articles: {share_articles}<br/>
            share_events: {share_events}<br/>
            share_fatalities: {share_fatalities}
            """,
            "style": {"backgroundColor": "white", "color": "black"},
        }

        st.pydeck_chart(
            pdk.Deck(layers=[layer2d], initial_view_state=view_state_2d, tooltip=tooltip2d, map_style=None),
            width="stretch",
        )

    # -------------------------
    # 3D map
    # -------------------------
    with tab3d:

        # Question + explanation for 3D map (playground)
        st.markdown(
            """
            
            </p>
            <p style="font-size:0.9rem; color:#555; margin:0 0 0.7rem 0;">
                The 3D map is an interactive playground: users can rotate, zoom, and adjust height and color
                settings to explore how conflict severity (height) and media coverage (color) vary across
                countries. Tall but relatively pale countries indicate intense conflict with limited coverage,
                while brightly colored pillars highlight locations that receive comparatively strong media attention.
            </p>
            """,
            unsafe_allow_html=True,
        )

        # Controls
        col1, col2 = st.columns([1, 2])

        with col1:
            height_scale = st.slider(
                "Height scale",
                min_value=100_000,
                max_value=5_000_000,
                value=3_000_000,
                step=100_000,
                help="DeckGL elevation is in 'meters' visually; this is a multiplier.",
            )
            height_gamma = st.slider(
                "Height exponent (gamma)",
                min_value=0.2,
                max_value=2.0,
                value=0.5,
                step=0.05,
                help="Lower (<1) boosts contrast among low severity values; higher (>1) compresses.",
            )
            color_gamma = st.slider(
                "Color exponent (gamma)",
                min_value=0.2,
                max_value=2.0,
                value=1.0,
                step=0.05,
                help="Lower (<1) boosts contrast among low coverage values; higher (>1) compresses.",
            )

        with col2:
            pitch = st.slider("3D pitch", 0, 70, 45, 1)
            opacity = st.slider("Opacity", 0.1, 1.0, 0.9, 0.05)

        geojson, merged = build_geojson(world, df_plot, color_gamma=color_gamma)

        # compute elevation in Python (more robust than JS expressions)
        merged["elevation"] = (merged["severity_share"] ** height_gamma) * height_scale

        layer = pdk.Layer(
            "GeoJsonLayer",
            data=json.loads(merged.to_json()),
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
            severity_share (height): {severity_share}<br/>
            share_articles (color): {share_articles}<br/>
            share_events: {share_events}<br/>
            share_fatalities: {share_fatalities}<br/>
            underrep_share: {underrep_share}
            """,
            "style": {"backgroundColor": "white", "color": "black"},
        }

        deck = pdk.Deck(
            layers=[layer],
            initial_view_state=view_state,
            tooltip=tooltip3d,
            map_style=None,
        )

        st.pydeck_chart(deck, width="stretch")

    # -------------------------
    # Top 10 countries table
    # -------------------------
    

    # Question + explanation for table

    
    st.markdown(
        """
        <p style="font-size:1.4rem; font-weight:700; margin-top:1.2rem; margin-bottom:0.35rem;">
            Which countries contribute most to the global burden of conflict severity?
        </p>
        <p style="font-size:0.95rem; color:#444; margin:0 0 1.0rem 0;">
            The table ranks countries by their severity share, combining shares of global events and fatalities
            into a single indicator of how strongly they shape worldwide conflict intensity. It allows quick
            identification of central conflict theatres and shows whether these high-severity cases also receive
            equvalent levels of media coverage and article volume.
        </p>
        """,
        unsafe_allow_html=True,
    )

    

    cols = [
        "country",
        "share_events",
        "share_fatalities",
        "share_articles",
        "n_events",
        "total_fatalities",
        "n_articles",  # (typo fix: not n_artciles)
    ]

    st.dataframe(
        df_plot.sort_values("severity_share", ascending=False).head(10)[cols],
        use_container_width=True,
    )

    # -------------------------
    # Coverage over time (global)
    # -------------------------
    

    # Question + explanation for coverage over time
    st.markdown(
        """
        <p style="font-size:1.4rem; font-weight:700; margin-top:1.2rem; margin-bottom:0.35rem;">
            How does global media attention to conflict evolve over time?
        </p>
        <p style="font-size:0.95rem; color:#444; margin:0 0 1.0rem 0;">
            The coverage-over-time graph aggregates all conflict-related articles by month to show how overall
            reporting intensity fluctuates, including bursts and quiet periods. Using the date filters, users can
            examine whether major conflict episodes coincide with sustained increases in coverage or only trigger
            short-lived spikes, informing interpretations of attention cycles and potential media fatigue.
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

        # --- Filters: country + time window ---
        f_col1, f_col2, f_col3 = st.columns([1.5, 1, 1])

        with f_col1:
            countries_cov = ["All countries"] + sorted(
                c for c in cov_df["country"].dropna().astype(str).unique()
            )
            cov_country = st.selectbox(
                "Country (coverage & events)",
                options=countries_cov,
                index=0,
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

            title_suffix = (
                f" – {cov_country}" if cov_country != "All countries" else " – all countries"
            )

                        # reshape to long format for two colored lines + legend
            month_long = month_all.melt(
                id_vars="month",
                value_vars=["n_articles", "n_events"],
                var_name="series",
                value_name="count",
            )

            color_scale = alt.Scale(
                domain=["n_articles", "n_events"],
                range=["#FF69B4", "#1f77b4"],  # pink & blue
            )

            line_chart = (
                alt.Chart(month_long)
                .mark_line(point=False)  # only lines, no dots
                .encode(
                    x=alt.X("month:N", title="Month"),
                    y=alt.Y("count:Q", title="Count"),
                    color=alt.Color(
                        "series:N",
                        title="",
                        scale=color_scale,
                        legend=alt.Legend(orient="bottom"),
                    ),
                    tooltip=["month", "series", "count"],
                )
                .properties(title=f"Coverage vs. conflict events over time{title_suffix}")
            )

            st.altair_chart(line_chart, use_container_width=True)



    

# -------------------------
# Tab 2: Sentiment Analysis
# -------------------------
with tab2:
    if df is None:
        st.error("Data files not found. Please run data_processor.py first.")
    else:
        # --- User Guide ---
        with st.expander("How to use this Dashboard"):
            st.markdown("""
            <div class="guide-box">
                1. <b>Observe Trends:</b> Check if reporting spikes on specific dates and how quickly the interest fades.<br>
                2. <b>Analyze Framing:</b> Observe the emotions media outlets use to "package" conflicts when reporting is not neutral.<br>
                3. <b>Compare Media:</b> Identify stance biases across different media organizations handling the same conflict.<br>
                4. <b>Deep Dive:</b> Explore specific narrative details within macro categories through algorithmic clustering.
            </div>
            """, unsafe_allow_html=True)

        # ============================================================
        # 2. FILTERS
        # ============================================================
        st.sidebar.title("Analytics Filters")
        date_range = st.sidebar.date_input("Date Range", [df['published_date'].min(), df['published_date'].max()])
        scope = st.sidebar.selectbox("Scope", ["All News", "International", "Domestic"])

        if len(date_range) == 2:
            mask = (df['published_date'].dt.date >= date_range[0]) & (df['published_date'].dt.date <= date_range[1])
            df_f = df.loc[mask]
        else:
            df_f = df.copy()

        if scope == "International": 
            df_f = df_f[df_f['is_domestic'] == False]
        elif scope == "Domestic": 
            df_f = df_f[df_f['is_domestic'] == True]

        # ============================================================
        # 3. ATTENTION DYNAMICS (English UI)
        # ============================================================
        st.markdown('<div class="section-header">Trends and Attention Dynamics</div>', unsafe_allow_html=True)

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

        burstiness, half_life = calc_attention_metrics(df_f)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Articles", len(df_f))
        m2.metric("Burstiness Index", f"{burstiness:.1f}%", help="Does coverage cluster in a few days? A high percentage indicates highly concentrated media attention.")
        m3.metric("Avg Emotional Tone", f"{df_f['sentiment_numeric'].mean():.2f}" if not df_f.empty else "N/A")
        m4.metric("Attention Half-life", half_life, help="The speed at which media interest fades. Smaller values indicate faster dissipation.")

        if not df_f.empty:
            st.plotly_chart(px.area(df_f.groupby('published_date').size().reset_index(name='Count'), x='published_date', y='Count', title="Daily Media Attention Spikes"), use_container_width=True)

        # ============================================================
        # 4. EMOTIONAL FRAMING (Scientific Interpretation)
        # ============================================================
        st.markdown('<div class="section-header">Emotional Framing Analysis</div>', unsafe_allow_html=True)

        st.markdown("""
        <div class="insight-box">
            <strong>Why study these emotions?</strong><br>
            The way media "packages" conflict shapes public perception. In this study:<br>
            • <b>Anger:</b> Adversarial framing. Typically points to faults of conflict parties or intense opposition.<br>
            • <b>Fear/Sadness:</b> Humanitarian/Victim framing. Focuses on suffering, threats, or losses resulting from the conflict.<br>
            • <b>Surprise/Joy:</b> Breakthrough or optimistic framing. May represent conflict de-escalation or unexpected positive developments.
        </div>
        """, unsafe_allow_html=True)

        col1, col2 = st.columns(2)

        exclude = ['neutral', 'others', 'other', 'label_1']
        df_active = df_f[~df_f['emotion_label'].str.lower().isin(exclude)]

        with col1:
            st.subheader("Tone Intensity by Event Type")
            if not df_f.empty:
                df_ev_sent = df_f.groupby('acled_event_type')['sentiment_numeric'].mean().sort_values().reset_index()
                fig_bar = px.bar(df_ev_sent, 
                                x='sentiment_numeric', y='acled_event_type', orientation='h', 
                                color='sentiment_numeric', color_continuous_scale='RdYlGn',
                                range_x=[-0.6, 0.4], # Optimized range to see variance near 0
                                labels={'sentiment_numeric': 'Aggregated Tone (Negative <---> Positive)'},
                                title="Tone Intensity per Conflict Category")
                st.plotly_chart(fig_bar, use_container_width=True)

        with col2:
            st.subheader("Top 5 Emotion Distribution")
            total_len = len(df_f)
            neutral_count = len(df_f[df_f['emotion_label'].str.lower().isin(exclude)])
            neutral_perc = (neutral_count / total_len * 100) if total_len > 0 else 0
            
            st.markdown(f"""
            <div style='font-size: 0.9em; color: gray;'>
                ℹ️ <b>Neutral/Other coverage share: {neutral_perc:.1f}%</b><br>
                A high neutral share indicates that most reporting consists of factual statements. The chart below only shows the distribution of active emotions to reveal media sentiment tendencies.
            </div>
            """, unsafe_allow_html=True)
            
            if not df_active.empty:
                top_5_emotions = df_active['emotion_label'].value_counts().nlargest(5).index.tolist()
                df_active_top5 = df_active[df_active['emotion_label'].isin(top_5_emotions)]
                
                fig_pie = px.pie(df_active_top5, names='emotion_label', hole=0.4, 
                                title="Active Framing Breakdown (Top 5)",
                                color_discrete_sequence=px.colors.qualitative.Pastel)
                st.plotly_chart(fig_pie, use_container_width=True)
            else:
                st.info("No active emotional labels found in the selected range.")

        # ============================================================
        # 5. MEDIA OUTLET COMPARISON
        # ============================================================
        st.markdown('<div class="section-header">Media Outlet Comparison</div>', unsafe_allow_html=True)
        st.write("Comparing institutional bias: Which of the Top 5 emotions do different media sources emphasize?")

        top_outlets = df_f['source_name'].value_counts().head(10).index
        df_top_outlets = df_f[df_f['source_name'].isin(top_outlets)]
        df_top_active = df_top_outlets[~df_top_outlets['emotion_label'].str.lower().isin(exclude)]

        tab_heatmap, tab_sentiment = st.tabs(["Institutional Emotion Profile", "Tone Variance Score"])

        with tab_heatmap:
            st.subheader("Institutional Emotion Heatmap (Top 5 Emotions)")
            if not df_top_active.empty:
                top_5_global = df_active['emotion_label'].value_counts().nlargest(5).index.tolist()
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
                                        range_x=[-0.2, 0.2], # Zoomed in to see bias variance near zero
                                        title="Outlet Positioning on the Emotional Spectrum",
                                        labels={'Avg Tone Score': 'Intense/Negative Framing <---> Calm/Positive Framing'})
                fig_outlet.add_vline(x=df_f['sentiment_numeric'].mean(), line_dash="dash", line_color="gray", annotation_text="Market Avg")
                st.plotly_chart(fig_outlet, use_container_width=True)
            else:
                st.info("No media outlet data available for the current selection.")

        # ============================================================
        # 6. NARRATIVE DEEP DIVE (With On-Demand Translation)
        # ============================================================
        st.markdown('<div class="section-header">Narrative Discovery</div>', unsafe_allow_html=True)

        st.markdown("""
        <div style='background-color: #f5f5f5; padding: 10px; border-radius: 5px; margin-bottom: 10px; font-size: 0.9em; color:#555;'>
            💡 <b>What is this?</b> This section uses algorithms to identify recurring storylines, answering: beyond macro categories like "Protests," what are the specific narrative focuses of the media?
        </div>
        """, unsafe_allow_html=True)

        top_clusters = df_f['article_cluster_id'].value_counts().head(5)
        if not top_clusters.empty:
            translate = st.checkbox("Enable Translation for Event Headlines")
            if translate:
                ts = get_translator()

            for cid, count in top_clusters.items():
                cluster_data = df_f[df_f['article_cluster_id'] == cid]
                if not cluster_data.empty:
                    sample = cluster_data.iloc[0]
                    label = sample.get('acled_event_type', 'Unknown')
                    
                    display_title = sample['title']
                    if translate:
                        try:
                            display_title = ts(display_title[:512])[0]['translation_text']
                        except:
                            pass
                        
                    with st.expander(f"Event Cluster {cid}: {label} ({count} articles)"):
                        st.write(f"**Headline:** {display_title}")
                        if translate: 
                            st.caption(f"Original German: {sample['title']}")
                        st.write(f"**Location:** {sample.get('detected_locations', 'Not specified')}")
                        st.write(f"**Tone Intensity Score:** {sample.get('sentiment_numeric', 0):.2f}")
                        st.progress(min(1.0, abs(sample.get('sentiment_numeric', 0))))

        st.divider()
        st.caption("Native German processing with English UI representation. The news were already preselected with conflict related news. Data source: processed_conflict_articles.csv")

            

# -------------------------
# Tab 3: Conflict × Media Explorer
# -------------------------
with tab3:
    st.markdown("## Conflict Media Explorer")

    # Leitfrage + Beschreibung für Länder-Übersicht
    st.markdown(
        """
        <p style="font-size:1.4rem; font-weight:700; margin-top:1.2rem; margin-bottom:0.35rem;">
            In which countries are conflict-related news articles currently concentrated?
        </p>
        <p style="font-size:0.95rem; color:#444; margin:0 0 1.0rem 0;">
            The country overview below summarizes how many conflict-related news articles are
            currently matched to each country in the dataset. This provides a high-level picture
            of where recent reporting on conflict is most frequent.
        </p>
        """,
        unsafe_allow_html=True,
    )

    # KPIs (graue Karten)
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(
            """
            <div style="
                background-color:#f5f5f5;
                padding:1rem 1.2rem;
                border-radius:12px;
            ">
              <div style="font-size:0.85rem; color:#555;">Countries shown</div>
              <div style="font-size:1.6rem; color:#555;font-weight:600;">
                {value}
              </div>
            </div>
            """.format(value=f"{len(conf):,}"),
            unsafe_allow_html=True,
        )

    with k2:
        st.markdown(
            """
            <div style="
                background-color:#f5f5f5;
                padding:1rem 1.2rem;
                border-radius:12px;
            ">
              <div style="font-size:0.85rem; color:#555;">Total events</div>
              <div style="font-size:1.6rem; color:#555;font-weight:600;">
                {value}
              </div>
            </div>
            """.format(
                value=f"{int(conf[C_EVENTS].sum()):,}" if not conf.empty else "0"
            ),
            unsafe_allow_html=True,
        )

    with k3:
        st.markdown(
            """
            <div style="
                background-color:#f5f5f5;
                padding:1rem 1.2rem;
                border-radius:12px;
            ">
              <div style="font-size:0.85rem; color:#555;">Total fatalities</div>
              <div style="font-size:1.6rem; color:#555;font-weight:600;">
                {value}
              </div>
            </div>
            """.format(
                value=f"{int(conf[C_FATAL].sum()):,}" if not conf.empty else "0"
            ),
            unsafe_allow_html=True,
        )

    with k4:
        st.markdown(
            """
            <div style="
                background-color:#f5f5f5;
                padding:1rem 1.2rem;
                border-radius:12px;
            ">
              <div style="font-size:0.85rem; color:#555;">Total matched articles</div>
              <div style="font-size:1.6rem; color:#555;font-weight:600;">
                {value}
              </div>
            </div>
            """.format(
                value=f"{int(conf['n_articles'].sum()):,}" if not conf.empty else "0"
            ),
            unsafe_allow_html=True,
        )

        # -------------------------
    # Country overview 
    # -------------------------
    st.caption(
        "Country-level overview of conflict events, fatalities, and matched news "
        "articles used for the detailed article view below."
    )

    # ---- Country-level Filter ----
    c1, c2, c3, c4 = st.columns([2, 1.2, 1.2, 1.2])
    with c1:
        filt_country = st.text_input("Country", value="")
    with c2:
        min_fatal_str = st.text_input("Min total fatalities", value="")
    with c3:
        min_events_str = st.text_input("Min events", value="")
    with c4:
        max_articles_str = st.text_input("Amount articles (detail)", value="300")


    def to_float(x, default=0.0):
        try:
            return float(x)
        except Exception:
            return default


    def to_int(x, default=300):
        try:
            return int(float(x))
        except Exception:
            return default


    min_fatal = to_float(min_fatal_str, 0.0)
    min_events = to_float(min_events_str, 0.0)
    max_articles = to_int(max_articles_str, 300)

    st.markdown(
        "<p style='font-size:0.85rem; color:#666; margin-top:0.2rem;'>"
        "Filter options for country, minimum total fatalities, minimum events, "
        "and number of articles used for the detailed article view below."
        "</p>",
        unsafe_allow_html=True,
    )

    # gefiltertes conf für Anzeige und Auswahl
    conf_filtered = conf.copy()
    if filt_country.strip():
        conf_filtered = contains_filter(conf_filtered, C_COUNTRY, filt_country.strip())

    conf_filtered = conf_filtered[
        (conf_filtered[C_FATAL] >= min_fatal)
        & (conf_filtered[C_EVENTS] >= min_events)
    ].copy()

    conf_filtered = (
        conf_filtered.sort_values(["n_articles", C_EVENTS, C_FATAL], ascending=False)
        .reset_index(drop=True)
    )

    show_master = conf_filtered[
        [
            C_COUNTRY,
            C_EVENTS,
            C_FATAL,
            "n_articles",
            "articles_per_event",
            "articles_per_100_fatal",
        ]
    ].copy()
    show_master = show_master.rename(
        columns={
            C_COUNTRY: "country",
            C_EVENTS: "n_events",
            C_FATAL: "total_fatalities",
        }
    )

    evt = st.dataframe(
        show_master,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )


    

    # Country-Auswahl aus Overview-Tabelle
    selected_country = ""
    if "evt" in locals() and evt and evt.selection.rows:
        i = evt.selection.rows[0]
        selected_country = str(show_master.iloc[i]["country"])

    #selected_country = st.text_input("Selected country", value=selected_country)

    st.divider()

    # -------------------------
    # Country conflict profile (VOR der Artikelliste)
    # -------------------------
    st.markdown(
        """
        <h4 style="margin-bottom:0.1rem;">Which types of conflict events and actors most strongly shape the current conflict situation in this country?</h4>
        <p style="font-size:0.9rem; color:#555; margin-top:0.15rem;">
            Explore dominant event types, disorder categories, and key actors from ACLED to see which kinds of conflict activity underpin the news coverage for the selected country.
        </p>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(f"**Selected country:** {selected_country}")

    if not selected_country.strip():
        st.info("Select a country from the overview table above to see details.")
        st.stop()

    if "conf_filtered" in locals():
        conf_for_detail = conf_filtered
    else:
        conf_for_detail = conf

    row = conf_for_detail[conf_for_detail[C_COUNTRY].astype(str) == selected_country.strip()]
    if row.empty:
        st.warning(
            "Selected country not found in current filtered table. Try adjusting filters."
        )
        st.stop()

    r = row.iloc[0]

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

        # 3 gleich breite Spalten über gesamte Dashboard-Breite
        col_event, col_disorder, col_actors = st.columns(3)

        # ---- Box 1: Event types share ----
        with col_event:
            st.markdown(
                """
                <div style="
                    background-color:#f5f5f5;
                    padding:1.0rem 1.1rem 1.2rem 1.1rem;
                    border-radius:12px;
                    margin-right:0.6rem;
                ">
                  <div style="font-size:0.95rem; color:#555; font-weight:600; margin-bottom:0.6rem;">
                    Event types share
                  </div>
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
            st.markdown("</div>", unsafe_allow_html=True)

        # ---- Box 2: Main disorder categories ----
        with col_disorder:
            st.markdown(
                """
                <div style="
                    background-color:#f5f5f5;
                    padding:1.0rem 1.1rem 1.2rem 1.1rem;
                    border-radius:12px;
                    margin:0 0.3rem;
                ">
                  <div style="font-size:0.95rem; color:#555; font-weight:600; margin-bottom:0.6rem;">
                    Main disorder categories
                  </div>
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
                        axis=alt.Axis(labelLimit=200)  # allow longer labels
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
                .properties(
                    width=380,          # more space for labels
                    height=220,
                )
                .configure_view(
                    continuousWidth=380,
                    strokeWidth=0
                )
            )

            st.altair_chart(disorder_bar, use_container_width=True)


            st.markdown("</div>", unsafe_allow_html=True)

        # ---- Box 3: Key primary actors + Zeitraum ----
        with col_actors:
            st.markdown(
                """
                <div style="
                    background-color:#f5f5f5;
                    padding:1.0rem 1.1rem 1.2rem 1.1rem;
                    border-radius:12px;
                    margin-left:0.6rem;
                ">
                  <div style="font-size:0.95rem; color:#555; font-weight:600; margin-bottom:0.6rem;">
                    Key primary actors
                  </div>
                """,
                unsafe_allow_html=True,
            )

            for _, ac in top_actors.iterrows():
                st.markdown(
                    f"- {ac['primary_assoc_actor_1']} ({int(ac['n_events'])} events)"
                )

            cf["start_date"] = pd.to_datetime(cf["start_date"], errors="coerce")
            cf["end_date"] = pd.to_datetime(cf["end_date"], errors="coerce")
            if cf["start_date"].notna().any() and cf["end_date"].notna().any():
                start_min = cf["start_date"].min().date()
                end_max = cf["end_date"].max().date()
                st.markdown(
                    f"<p style='font-size:0.85rem; color:#555; margin-top:0.9rem;'>"
                    f"NA = no key primary actor>",
                    unsafe_allow_html=True,
                )

            st.markdown("</div>", unsafe_allow_html=True)
    
    

    st.divider()

    # Leitfrage + Beschreibung für Artikelliste
    st.markdown(
        """
        <p style="font-size:1.4rem; font-weight:700; margin-top:0.5rem; margin-bottom:0.35rem;">
            What is currently reported about this country’s conflicts?
        </p>
        <p style="font-size:0.95rem; color:#444; margin:0 0 0.8rem 0;">
            The article list and metrics below show the most recent conflict-related news
            matched to the selected country. Titles, descriptions, outlets, and links
            allow you to inspect how ongoing conflicts are framed and which aspects
            receive attention in current reporting.
        </p>
        """,
        unsafe_allow_html=True,
    )

    # Country detail section
    
    st.markdown(f"**Selected country:** {selected_country}")

    # Pull article data for selected country
    show_cols = [A_ID, A_PUB, A_SOURCE, A_TITLE, A_DESC, M_COUNTRY]
    if A_URL:
        show_cols.append(A_URL)

    articles_sql = f"""
    SELECT {", ".join(show_cols)}
    FROM {MATCH_TABLE}
    WHERE {M_COUNTRY} = ?
    ORDER BY {A_PUB} DESC
    LIMIT ?
    """
    art = qdf_match(articles_sql, [selected_country.strip(), int(max_articles)])

    # Ensure datetime for recency metrics and time plots
    if not art.empty:
        art[A_PUB] = pd.to_datetime(art[A_PUB], errors="coerce")

    # Recency metrics
    now = datetime.utcnow()
    if not art.empty and art[A_PUB].notna().any():
        last_article_date = art[A_PUB].max()
        days_since_last = (now - last_article_date).days
        last_7 = (now - art[A_PUB] <= timedelta(days=7)).sum()
    else:
        days_since_last = None
        last_7 = 0

    # -------- Country KPIs: einzelne graue Karten + Top-3-Outlets --------

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
              <div style="font-size:1.6rem; color:#555;font-weight:600;">{int(art.shape[0]):,}</div>
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
              <div style="font-size:0.85rem; color:#555;">Days since last article</div>
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
              <div style="font-size:0.85rem; color:#555;">Articles last 7 days</div>
              <div style="font-size:1.6rem; color:#555;font-weight:600;">{last_7}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # -------------------------
    # Articles + Outlet detail (ohne Tabs)
    # -------------------------
    header_left, _ = st.columns([3, 1])
    with header_left:
        st.markdown(
            """
            <p style="font-size:1.0rem; font-weight:600; margin-bottom:0.4rem;">
                Search, news, topics and more...
            </p>
            """,
            unsafe_allow_html=True,
        )

    # Filter-Leiste
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

    if art_filtered.empty:
        st.info("No matched articles found for this selection and filters.")
    else:
        final_cols = [A_PUB, A_TITLE, A_DESC, A_SOURCE]
        if A_URL and A_URL in art_filtered.columns:
            final_cols.append(A_URL)

        out = art_filtered[final_cols].rename(
            columns={
                A_PUB: "published",
                A_TITLE: "title",
                A_DESC: "description",
                A_SOURCE: "source",
                A_URL: "url",
            }
        )

        if "url" in out.columns:
            st.dataframe(
                out,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "url": st.column_config.LinkColumn(
                        "url",
                        display_text="Link to article",
                    )
                },
            )
        else:
            st.dataframe(out, use_container_width=True, hide_index=True)

    # --- Outlet distribution below article table ---
    if not art_filtered.empty and A_SOURCE in art_filtered.columns:
        st.markdown("#### Outlet detail")
        by_outlet = (
            art_filtered.groupby(A_SOURCE, dropna=False)
            .size()
            .reset_index(name="n_articles")
        )
        by_outlet = by_outlet.sort_values("n_articles", ascending=False)

        

        
            # Farbskala: Abstufungen des Pink (#FF69B4)
        pink_scale = alt.Scale(
            domain=[by_outlet["n_articles"].min(), by_outlet["n_articles"].max()],
            range=["#FFE4F3", "#FF69B4"],  # helles Pink -> kräftiges Pink
        )

        bar = (
            alt.Chart(by_outlet)
            .mark_bar()
            .encode(
                y=alt.Y(f"{A_SOURCE}:N", sort="-x", title="Outlet"),
                x=alt.X("n_articles:Q", title="Articles"),
                tooltip=[A_SOURCE, "n_articles"],
                color=alt.Color(
                    "n_articles:Q",
                    title="Articles",
                    scale=pink_scale,
                ),
            )
            .properties(height=500)
        )

        st.altair_chart(bar, use_container_width=True)


    

   


# -------------------------
# Tab 5: Impressum
# -------------------------
with tab5:
    st.markdown("### ℹ️ Impressum")
    st.info(
        "🚧 This section is under development. It will display sentiment trends "
        "in conflict-related media coverage."
    )
    st.markdown(
        """
    #### About this Dashboard

    This dashboard provides insights into the relationship between conflict events and media coverage.

    **Data Sources:**
    - Conflict data from ACLED (Armed Conflict Location & Event Data Project)
    - Media coverage from news article databases

    **Purpose:**
    - Analyze conflict underrepresentation in media
    - Explore media outlet coverage patterns
    - Track temporal trends in conflict reporting

    **Contact:**
    For questions or feedback about this dashboard, please contact the research team.

    **Version:** 1.0  
    **Last Updated:** January 2026
    """
    )
