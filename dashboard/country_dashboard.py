#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

# -------------------------
# Paths
# -------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # .../dashboard_conflict_data
DATA_DIR = PROJECT_ROOT / "data"

CONFLICT_DB = DATA_DIR / "conflict_data.db"
MATCHING_DB = DATA_DIR / "matching_country.db"

COUNTRY_TABLE = "conflict_country"
# CHANGED: use the slim output (articles + country only)
MATCH_TABLE = "match_country_slim"

st.set_page_config(page_title="Conflict–Article Dashboard", layout="wide")

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


# -------------------------
# App
# -------------------------
st.title("Conflict–Article Dashboard")

if not CONFLICT_DB.exists():
    st.error(f"Missing DB: {CONFLICT_DB}")
    st.stop()
if not MATCHING_DB.exists():
    st.error(f"Missing DB: {MATCHING_DB}")
    st.stop()

CC_COLS = table_cols("conf", COUNTRY_TABLE)
MC_COLS = table_cols("match", MATCH_TABLE)

# --- conflict_country required columns (with fallbacks) ---
C_COUNTRY = pick(CC_COLS, "country", required=True)
C_EVENTS = pick(CC_COLS, "n_events", required=True)
C_FATAL = pick(CC_COLS, "total_fatal", "total_fatalities", required=True)

# --- match table required columns (with fallbacks) ---
# In slim table, the joined country column is named "country"
M_COUNTRY = pick(MC_COLS, "art_article_country", "article_country", required=True)
JOIN_COUNTRY = pick(MC_COLS, "country", "conf_country", required=False)

A_ID = pick(MC_COLS, "art_id", "article_id", required=True)
A_PUB = pick(MC_COLS, "art_publishedAt", "publishedAt", "article_date", required=True)
A_SOURCE = pick(MC_COLS, "art_source_name", "source_name", required=True)
A_TITLE = pick(MC_COLS, "art_title_en", "title_en", "title", required=True)
A_DESC = pick(MC_COLS, "art_description_en", "description_en", "description", required=True)

st.header("Conflicts by country")

# -------------------------
# Filters (top)
# -------------------------
f1, f2, f3, f4 = st.columns([2.2, 1.2, 1.2, 1.2])
with f1:
    filt_country = st.text_input("Country contains", value="")
with f2:
    min_fatal = st.number_input("Min total fatalities", min_value=0, value=0, step=10)
with f3:
    min_events = st.number_input("Min n_events", min_value=0, value=0, step=10)
with f4:
    max_rows = st.number_input("Max rows", min_value=10, value=300, step=10)

conf_sql = f"SELECT * FROM {COUNTRY_TABLE}"
conf = qdf_conf(conf_sql)

conf[C_FATAL] = pd.to_numeric(conf[C_FATAL], errors="coerce").fillna(0)
conf[C_EVENTS] = pd.to_numeric(conf[C_EVENTS], errors="coerce").fillna(0)

if filt_country.strip():
    conf = contains_filter(conf, C_COUNTRY, filt_country.strip())

conf = conf[(conf[C_FATAL] >= float(min_fatal)) & (conf[C_EVENTS] >= float(min_events))].copy()
conf = conf.sort_values([C_EVENTS, C_FATAL], ascending=False).head(int(max_rows)).reset_index(drop=True)

m1, m2, m3 = st.columns(3)
m1.metric("Countries shown", f"{len(conf):,}")
m2.metric("Sum n_events", f"{int(conf[C_EVENTS].sum()):,}" if not conf.empty else "0")
m3.metric("Sum fatalities", f"{int(conf[C_FATAL].sum()):,}" if not conf.empty else "0")

st.caption("Select a country row to show its articles below.")
evt = st.dataframe(
    conf,
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
)

selected_country = ""
if evt and evt.selection.rows:
    i = evt.selection.rows[0]
    selected_country = str(conf.iloc[i][C_COUNTRY])

selected_country = st.text_input("Selected country (manual override)", value=selected_country)

st.divider()
st.subheader("Articles for selected country")

if not selected_country.strip():
    st.info("Select a country above to inspect its matched articles.")
    st.stop()

a1, a2 = st.columns([2, 1])
with a1:
    art_text = st.text_input("Article title/description contains", value="")
with a2:
    max_articles = st.number_input("Max articles shown", min_value=10, value=500, step=10)

# Query articles (only needed columns; no event_type)
show_cols = [A_ID, A_PUB, A_SOURCE, A_TITLE, A_DESC, M_COUNTRY]
articles_sql = f"""
SELECT {", ".join(show_cols)}
FROM {MATCH_TABLE}
WHERE {M_COUNTRY} = ?
ORDER BY {A_PUB} DESC
LIMIT ?
"""
art = qdf_match(articles_sql, [selected_country.strip(), int(max_articles)])

if art_text.strip() and not art.empty:
    mask = (
        art[A_TITLE].astype(str).str.lower().str.contains(art_text.strip().lower(), na=False)
        | art[A_DESC].astype(str).str.lower().str.contains(art_text.strip().lower(), na=False)
    )
    art = art[mask].reset_index(drop=True)

final_cols = [A_ID, A_PUB, A_SOURCE, A_TITLE, A_DESC]
rename_map = {
    A_ID: "art_id",
    A_PUB: "art_publishedAt",
    A_SOURCE: "art_source_name",
    A_TITLE: "art_title_en",
    A_DESC: "art_description_en",
}
art = art[final_cols].rename(columns=rename_map)

st.metric("Articles shown", f"{len(art):,}")
st.dataframe(art, use_container_width=True, hide_index=True)
