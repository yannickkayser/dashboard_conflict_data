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
  <h1>🌍 Conflict Media Mirror</h1>
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



# -------------------------
# Main Tabs
# -------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    [
        "📊 Conflict Underrepresentation",
        "💭 Sentiment Analysis",
        "🔗 Conflict Media Explorer",
        "📅 Timeline",
        "ℹ️ Impressum",
    ]
)

# -------------------------
# Tab 1: Conflict Underrepresentation
# -------------------------
with tab1:
    st.markdown("### 📊 Conflict Underrepresentation Analysis")

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

    with tab2d:
        st.subheader("2D map: Under/Overrepresentation")
        st.caption("Color = share_articles − severity_share (negative = undercovered)")
        # Controls
        clip = st.slider("2D color clip", 
                        min_value=0.001, 
                        max_value=0.1, 
                        value=0.01, 
                        step=0.01,
                        help="Values beyond ±clip are saturated to the max color.")
        
        gamma = st.slider("2D color gamma", 
                        min_value=0.2, 
                        max_value=2.0, 
                        value=1.0, 
                        step=0.05,
                        help="Lower (<1) boosts contrast among low coverage values; higher (>1) compresses.")

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


    with tab3d:

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
                help="Lower (<1) boosts contrast among low severity values; higher (>1) compresses."
            )
            color_gamma = st.slider(
                "Color exponent (gamma)",
                min_value=0.2, 
                max_value=2.0, 
                value=1.0, 
                step=0.05,
                help="Lower (<1) boosts contrast among low coverage values; higher (>1) compresses."
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


    st.subheader("Top 10 countries by severity_share")
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
# Tab 2: Sentiment Analysis
# -------------------------
with tab2:
    st.markdown("### 💭 Sentiment Analysis")
    st.info(
        "🚧 This section is under development. It will display sentiment trends "
        "in conflict-related media coverage."
    )

    

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
              <div style="font-size:1.6rem; font-weight:600;">
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
              <div style="font-size:1.6rem; font-weight:600;">
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
              <div style="font-size:1.6rem; font-weight:600;">
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
              <div style="font-size:1.6rem; font-weight:600;">
                {value}
              </div>
            </div>
            """.format(
                value=f"{int(conf['n_articles'].sum()):,}" if not conf.empty else "0"
            ),
            unsafe_allow_html=True,
        )

    # Tabs: Country overview / scatter
    tab_overview, tab_scatter = st.tabs(
        ["Country overview (matched articles)", "Coverage vs conflict scatter"]
    )

    with tab_overview:
        st.caption(
            "Country-level overview of conflict events, fatalities, and matched news "
            "articles used for the detailed article view below."
        )

        # ---- Country-level Filterleiste ----
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

    with tab_scatter:
        conf_scatter = conf.copy()
        if "filt_country" in locals() and filt_country.strip():
            conf_scatter = contains_filter(conf_scatter, C_COUNTRY, filt_country.strip())
        if "min_fatal" in locals() and "min_events" in locals():
            conf_scatter = conf_scatter[
                (conf_scatter[C_FATAL] >= min_fatal)
                & (conf_scatter[C_EVENTS] >= min_events)
            ].copy()

        if conf_scatter.empty:
            st.info("No countries match the current filters.")
        else:
            scatter = (
                alt.Chart(conf_scatter)
                .mark_circle(size=90, opacity=0.85)
                .encode(
                    x=alt.X(f"{C_EVENTS}:Q", title="Conflict intensity: events"),
                    y=alt.Y(
                        "n_articles:Q", title="Media coverage: matched articles"
                    ),
                    tooltip=[
                        alt.Tooltip("country:N"),
                        alt.Tooltip("n_events:Q"),
                        alt.Tooltip("total_fatalities:Q"),
                        alt.Tooltip("n_articles:Q"),
                    ],
                    color=alt.Color(
                        "articles_per_event:Q", title="Articles per event"
                    ),
                )
            )
            st.altair_chart(scatter, use_container_width=True)
            st.caption(
                "Countries with many events but few articles tend to be undercovered."
            )

    # Country-Auswahl aus Overview-Tabelle
    selected_country = ""
    if "evt" in locals() and evt and evt.selection.rows:
        i = evt.selection.rows[0]
        selected_country = str(show_master.iloc[i]["country"])

    selected_country = st.text_input("Selected country", value=selected_country)

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
                  <div style="font-size:0.95rem; font-weight:600; margin-bottom:0.6rem;">
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
                        legend=alt.Legend(orient="right"),
                        scale=alt.Scale(scheme="tableau20"),
                    ),
                    tooltip=["event_type_mode", "n_events"],
                )
                .properties(width=260, height=260)
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
                  <div style="font-size:0.95rem; font-weight:600; margin-bottom:0.6rem;">
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
                    ),
                    x=alt.X(
                        "n_events:Q",
                        title="Events",
                        axis=alt.Axis(tickMinStep=10),
                    ),
                    color=alt.Color(
                        "disorder_type_mode:N",
                        legend=None,
                        scale=alt.Scale(scheme="set2"),
                    ),
                    tooltip=["disorder_type_mode", "n_events"],
                )
                .properties(width=320, height=220)
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
                  <div style="font-size:0.95rem; font-weight:600; margin-bottom:0.6rem;">
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
                    f"ACLED conflict events covered from <strong>{start_min}</strong> "
                    f"to <strong>{end_max}</strong>.</p>",
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
    row1_col1, row1_col2, row1_col3, row1_col4 = st.columns([1, 1, 1, 1.6])

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
              <div style="font-size:1.6rem; font-weight:600;">{int(art.shape[0]):,}</div>
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
              <div style="font-size:1.6rem; font-weight:600;">{int(r[C_EVENTS]):,}</div>
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
              <div style="font-size:1.6rem; font-weight:600;">{int(r[C_FATAL]):,}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with row1_col4:
        st.markdown(
            """
            <div style="
                background-color:#f5f5f5;
                padding:0.9rem 1.1rem;
                border-radius:12px;
                margin-bottom:0.8rem;
            ">
              <div style="font-size:0.85rem; color:#555;">Top 3 outlets (snapshot)</div>
            """,
            unsafe_allow_html=True,
        )
        if art.empty or A_SOURCE not in art.columns:
            st.info("No outlet data available for this selection.")
        else:
            by_outlet_all = (
                art.groupby(A_SOURCE, dropna=False)
                .size()
                .reset_index(name="n_articles")
            )
            by_outlet_top3 = by_outlet_all.sort_values(
                "n_articles", ascending=False
            ).head(3)
            max_val = by_outlet_top3["n_articles"].max()

            snap_bar = (
                alt.Chart(by_outlet_top3)
                .mark_bar()
                .encode(
                    y=alt.Y(f"{A_SOURCE}:N", sort="-x", title=None),
                    x=alt.X(
                        "n_articles:Q",
                        title="Articles",
                        scale=alt.Scale(domain=[0, max_val * 1.1]),
                        axis=alt.Axis(tickMinStep=10),
                    ),
                    tooltip=[A_SOURCE, "n_articles"],
                    color=alt.Color("n_articles:Q", legend=None),
                )
                .properties(height=120, width=260)
            )
            st.altair_chart(snap_bar, use_container_width=False)
        st.markdown("</div>", unsafe_allow_html=True)

    # Zeile 2: weitere Kennzahlen, je eigener grauer Block
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
              <div style="font-size:1.6rem; font-weight:600;">{val}</div>
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
              <div style="font-size:1.6rem; font-weight:600;">{ds}</div>
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
              <div style="font-size:1.6rem; font-weight:600;">{last_7}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # -------------------------
    # Detail tabs
    # -------------------------
    t_articles, t_outlets, t_time = st.tabs(
        ["📰 Articles", "🏢 Outlets detail", "📈 Coverage over time"]
    )

    # ---------- Articles tab ----------
    with t_articles:
        header_left, _ = st.columns([3, 1])
        with header_left:
            st.markdown(
                """
                <p style="font-size:1.0rem; font-weight:600; margin-bottom:0.4rem;">
                    Search, news, topics and more.
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
                | art_filtered[A_DESC]
                .astype(str)
                .str.lower()
                .str.contains(q, na=False)
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

    # ---------- Outlets detail ----------
    with t_outlets:
        if art.empty or A_SOURCE not in art.columns:
            st.info("No outlet data available for this selection.")
        else:
            by_outlet = (
                art.groupby(A_SOURCE, dropna=False)
                .size()
                .reset_index(name="n_articles")
            )
            by_outlet = by_outlet.sort_values("n_articles", ascending=False)

            bar = (
                alt.Chart(by_outlet)
                .mark_bar()
                .encode(
                    y=alt.Y(f"{A_SOURCE}:N", sort="-x", title="Outlet"),
                    x=alt.X("n_articles:Q", title="Articles"),
                    tooltip=[A_SOURCE, "n_articles"],
                    color=alt.Color("n_articles:Q", title="Articles"),
                )
                .properties(height=500)
            )
            st.altair_chart(bar, use_container_width=True)

            total = int(art.shape[0])
            top1 = int(by_outlet["n_articles"].iloc[0]) if not by_outlet.empty else 0
            top3 = (
                int(by_outlet["n_articles"].iloc[:3].sum())
                if by_outlet.shape[0] >= 3
                else int(by_outlet["n_articles"].sum())
            )
            c1, c2, c3 = st.columns(3)
            c1.metric("Unique outlets", f"{art[A_SOURCE].nunique(dropna=True):,}")
            c2.metric("Top-1 outlet share", f"{(top1/total):.0%}" if total > 0 else "NA")
            c3.metric(
                "Top-3 outlets share", f"{(top3/total):.0%}" if total > 0 else "NA"
            )

    # ---------- Coverage over time ----------
    with t_time:
        if art.empty or art[A_PUB].isna().all():
            st.info("No parsable publication dates available.")
        else:
            tmp = art.dropna(subset=[A_PUB]).copy()

            # Filter für Zeitraum des Line-Plots
            date_from_line, date_to_line = st.columns(2)
            with date_from_line:
                cov_from = st.date_input("From date (coverage)", value=None)
            with date_to_line:
                cov_to = st.date_input("To date (coverage)", value=None)

            if cov_from and cov_to:
                tmp = tmp[
                    (tmp[A_PUB].dt.date >= cov_from)
                    & (tmp[A_PUB].dt.date <= cov_to)
                ]

            tmp["month"] = to_month(tmp[A_PUB])
            by_month = (
                tmp.groupby("month")
                .size()
                .reset_index(name="n_articles")
                .sort_values("month")
            )

            line = (
                alt.Chart(by_month)
                .mark_line(point=True)
                .encode(
                    x=alt.X("month:N", title="Month"),
                    y=alt.Y("n_articles:Q", title="Articles"),
                    tooltip=["month", "n_articles"],
                )
            )
            st.altair_chart(line, use_container_width=True)
            st.caption(
                "This is coverage volume over time (articles/month) for the selected country."
            )

# -------------------------
# Tab 4: Timeline
# -------------------------
with tab4:
    st.markdown("### 📅 Timeline View")
    st.info(
        "🚧 This section is under development. It will show a temporal visualization "
        "of conflicts and media coverage."
    )

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
