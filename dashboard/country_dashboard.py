#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
from pathlib import Path
from datetime import date

import pandas as pd
import streamlit as st
import altair as alt


# -------------------------
# Paths
# -------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

CONFLICT_DB = DATA_DIR / "conflict_data.db"
MATCHING_DB = DATA_DIR / "matching_country.db"

COUNTRY_TABLE = "conflict_country"
MATCH_TABLE = "match_country_slim"

st.set_page_config(page_title="Conflict × Media Coverage", layout="wide")


# -------------------------
# Minimal styling (public-facing)
# -------------------------
st.markdown(
    """
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 2.5rem; max-width: 1250px;}
.small-muted {color: rgba(250,250,250,0.65); font-size: 0.9rem;}
.hero {
  padding: 1.2rem 1.4rem;
  border-radius: 16px;
  background: linear-gradient(135deg, rgba(70,90,255,0.25), rgba(20,20,20,0.05));
  border: 1px solid rgba(130,130,130,0.25);
}
.kpi {
  padding: 0.9rem 1rem;
  border-radius: 14px;
  border: 1px solid rgba(130,130,130,0.25);
  background: rgba(30,30,30,0.02);
}
</style>
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
    # Normalize to YYYY-MM (string)
    return dt.dt.to_period("M").astype(str)


# -------------------------
# App header
# -------------------------
st.markdown(
    """
<div class="hero">
  <div style="font-size:1.55rem; font-weight:700;">Conflict × Media Coverage Explorer</div>
  <div class="small-muted">
    Explore country-level conflict intensity (events, fatalities) alongside matched news coverage.
    Select a country to drill down into outlets, time patterns, and article examples.
  </div>
</div>
""",
    unsafe_allow_html=True,
)

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
A_URL = pick(MC_COLS, "art_url", "url", required=False)  # optional


# -------------------------
# Load base country table
# -------------------------
conf = qdf_conf(f"SELECT * FROM {COUNTRY_TABLE}")
conf[C_FATAL] = pd.to_numeric(conf[C_FATAL], errors="coerce").fillna(0)
conf[C_EVENTS] = pd.to_numeric(conf[C_EVENTS], errors="coerce").fillna(0)

# Precompute article counts per country (fast aggregate)
art_counts = qdf_match(
    f"""
    SELECT {M_COUNTRY} AS country, COUNT(*) AS n_articles
    FROM {MATCH_TABLE}
    GROUP BY {M_COUNTRY}
    """
)
art_counts["n_articles"] = pd.to_numeric(art_counts["n_articles"], errors="coerce").fillna(0).astype(int)

# Ensure stable column names for plotting/merging
conf = conf.rename(columns={C_COUNTRY: "country", C_EVENTS: "n_events", C_FATAL: "total_fatalities"})
C_COUNTRY, C_EVENTS, C_FATAL = "country", "n_events", "total_fatalities"

# Now merge article counts safely
conf = conf.merge(art_counts, how="left", on="country")
conf["n_articles"] = conf["n_articles"].fillna(0).astype(int)



# -------------------------
# Sidebar filters
# -------------------------
with st.sidebar:
    st.markdown("### Filters")
    filt_country = st.text_input("Country contains", value="")
    min_fatal = st.number_input("Min fatalities", min_value=0, value=0, step=50)
    min_events = st.number_input("Min events", min_value=0, value=0, step=50)
    max_rows = st.number_input("Max countries", min_value=10, value=200, step=10)

    st.markdown("---")
    st.markdown("### Article filters")
    max_articles = st.number_input("Max articles (detail)", min_value=10, value=300, step=10)
    art_text = st.text_input("Title/description contains", value="")

# Apply filters to master table
if filt_country.strip():
    conf = contains_filter(conf, C_COUNTRY, filt_country.strip())

conf = conf[(conf[C_FATAL] >= float(min_fatal)) & (conf[C_EVENTS] >= float(min_events))].copy()

# Under/over coverage diagnostics (simple, explainable)
conf["articles_per_event"] = conf.apply(lambda r: safe_div(r["n_articles"], r[C_EVENTS]), axis=1)
conf["articles_per_100_fatal"] = conf.apply(lambda r: safe_div(r["n_articles"] * 100.0, r[C_FATAL]), axis=1)

conf = conf.sort_values(["n_articles", C_EVENTS, C_FATAL], ascending=False).head(int(max_rows)).reset_index(drop=True)

# -------------------------
# Top KPIs + charts
# -------------------------
k1, k2, k3, k4 = st.columns(4)
k1.metric("Countries shown", f"{len(conf):,}")
k2.metric("Total events", f"{int(conf[C_EVENTS].sum()):,}" if not conf.empty else "0")
k3.metric("Total fatalities", f"{int(conf[C_FATAL].sum()):,}" if not conf.empty else "0")
k4.metric("Total matched articles", f"{int(conf['n_articles'].sum()):,}" if not conf.empty else "0")

tab_overview, tab_scatter = st.tabs(["Country table", "Coverage vs conflict scatter"])

with tab_scatter:
    if conf.empty:
        st.info("No countries match the current filters.")
    else:
        scatter = (
            alt.Chart(conf)
            .mark_circle(size=90, opacity=0.85)
            .encode(
                x=alt.X(f"{C_EVENTS}:Q", title="Conflict intensity: events"),
                y=alt.Y("n_articles:Q", title="Media coverage: matched articles"),
                tooltip=[alt.Tooltip("country:N"), alt.Tooltip("n_events:Q"), alt.Tooltip("total_fatalities:Q"), 
                alt.Tooltip("n_articles:Q")],

                color=alt.Color("articles_per_event:Q", title="Articles per event"),
            )
        )
        st.altair_chart(scatter, use_container_width=True)  # Streamlit supports Altair charts. [web:352]
        st.caption("Tip: Countries with many events but few articles tend to be undercovered.")

with tab_overview:
    st.caption("Select a country row to drill down into matched articles, outlet concentration, and coverage over time.")
    show_master = conf[[C_COUNTRY, C_EVENTS, C_FATAL, "n_articles", "articles_per_event", "articles_per_100_fatal"]].copy()
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
    )  # Row selections are available via st.dataframe. [web:269]

selected_country = ""
if evt and evt.selection.rows:
    i = evt.selection.rows[0]
    selected_country = str(show_master.iloc[i]["country"])

# manual override (useful for copying/pasting country names)
selected_country = st.text_input("Selected country", value=selected_country)

st.divider()

# -------------------------
# Detail section
# -------------------------
st.subheader("Country detail")
if not selected_country.strip():
    st.info("Select a country from the table above to see details.")
    st.stop()

# Get the selected country row
row = conf[conf[C_COUNTRY].astype(str) == selected_country.strip()]
if row.empty:
    st.warning("Selected country not found in current filtered table. Try adjusting filters.")
    st.stop()

r = row.iloc[0]
d1, d2, d3, d4 = st.columns(4)
d1.metric("Matched articles", f"{int(r['n_articles']):,}")
d2.metric("Events", f"{int(r[C_EVENTS]):,}")
d3.metric("Fatalities", f"{int(r[C_FATAL]):,}")
d4.metric("Articles per event", f"{r['articles_per_event']:.3f}" if r["articles_per_event"] is not None else "NA")

# Pull articles
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

# Parse dates for time charts
if not art.empty:
    art[A_PUB] = pd.to_datetime(art[A_PUB], errors="coerce")

# Apply text filter
if art_text.strip() and not art.empty:
    mask = (
        art[A_TITLE].astype(str).str.lower().str.contains(art_text.strip().lower(), na=False)
        | art[A_DESC].astype(str).str.lower().str.contains(art_text.strip().lower(), na=False)
    )
    art = art[mask].reset_index(drop=True)

# Detail tabs
t_articles, t_outlets, t_time = st.tabs(["Articles", "Outlets", "Coverage over time"])

with t_articles:
    st.metric("Articles shown", f"{len(art):,}")
    if art.empty:
        st.info("No matched articles found for this country (or filtered out).")
    else:
        # Make a clean public-facing table
        final_cols = [A_ID, A_PUB, A_SOURCE, A_TITLE, A_DESC]
        rename_map = {
            A_ID: "id",
            A_PUB: "published",
            A_SOURCE: "source",
            A_TITLE: "title",
            A_DESC: "description",
        }
        if A_URL and A_URL in art.columns:
            final_cols.append(A_URL)
            rename_map[A_URL] = "url"

        out = art[final_cols].rename(columns=rename_map)

        st.dataframe(out, use_container_width=True, hide_index=True)

with t_outlets:
    if art.empty or A_SOURCE not in art.columns:
        st.info("No outlet data available for this selection.")
    else:
        by_outlet = art.groupby(A_SOURCE, dropna=False).size().reset_index(name="n_articles")
        by_outlet = by_outlet.sort_values("n_articles", ascending=False).head(20)

        bar = (
            alt.Chart(by_outlet)
            .mark_bar()
            .encode(
                y=alt.Y(f"{A_SOURCE}:N", sort="-x", title="Outlet"),
                x=alt.X("n_articles:Q", title="Articles"),
                tooltip=[A_SOURCE, "n_articles"],
            )
        )
        st.altair_chart(bar, use_container_width=True)  # [web:352]

        # Simple concentration metrics
        total = int(art.shape[0])
        top1 = int(by_outlet["n_articles"].iloc[0]) if not by_outlet.empty else 0
        top3 = int(by_outlet["n_articles"].iloc[:3].sum()) if by_outlet.shape[0] >= 3 else int(by_outlet["n_articles"].sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("Unique outlets", f"{art[A_SOURCE].nunique(dropna=True):,}")
        c2.metric("Top-1 outlet share", f"{(top1/total):.0%}" if total > 0 else "NA")
        c3.metric("Top-3 outlets share", f"{(top3/total):.0%}" if total > 0 else "NA")

with t_time:
    if art.empty or art[A_PUB].isna().all():
        st.info("No parsable publication dates available.")
    else:
        tmp = art.dropna(subset=[A_PUB]).copy()
        tmp["month"] = to_month(tmp[A_PUB])
        by_month = tmp.groupby("month").size().reset_index(name="n_articles").sort_values("month")

        line = (
            alt.Chart(by_month)
            .mark_line(point=True)
            .encode(
                x=alt.X("month:N", title="Month"),
                y=alt.Y("n_articles:Q", title="Articles"),
                tooltip=["month", "n_articles"],
            )
        )
        st.altair_chart(line, use_container_width=True)  # [web:352]
        st.caption("This is coverage volume over time (articles/month) for the selected country.")
