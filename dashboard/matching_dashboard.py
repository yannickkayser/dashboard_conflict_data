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

OUT_DB = DATA_DIR / "article_conflict_matches.db"   # contains match_details
CONFLICT_DB = DATA_DIR / "conflict_data.db"         # contains conflict_features

DETAILS_TABLE = "match_details"
FEATURES_TABLE = "conflict_features"

st.set_page_config(page_title="Matching QA + conflict_features", layout="wide")


# -------------------------
# DB helpers
# -------------------------
@st.cache_resource
def get_out_conn():
    return sqlite3.connect(str(OUT_DB), check_same_thread=False)


@st.cache_resource
def get_conf_conn():
    return sqlite3.connect(str(CONFLICT_DB), check_same_thread=False)


@st.cache_data(ttl=30)
def qdf_out(sql: str, params=None) -> pd.DataFrame:
    return pd.read_sql_query(sql, get_out_conn(), params=params or [])


@st.cache_data(ttl=30)
def qdf_conf(sql: str, params=None) -> pd.DataFrame:
    return pd.read_sql_query(sql, get_conf_conn(), params=params or [])


@st.cache_data(ttl=120)
def table_cols(which: str, table: str) -> set[str]:
    if which == "out":
        df = qdf_out(f"PRAGMA table_info({table})")
    else:
        df = qdf_conf(f"PRAGMA table_info({table})")
    return set(df["name"].tolist())


def pick(colset: set[str], *names, required=False):
    for n in names:
        if n in colset:
            return n
    if required:
        raise RuntimeError(f"None of the columns exist: {names}")
    return None


def col_list(colset: set[str], cols):
    return [c for c in cols if c and c in colset]


def contains_filter(df: pd.DataFrame, col: str, needle: str) -> pd.DataFrame:
    if not needle or col not in df.columns:
        return df
    return df[df[col].astype(str).str.lower().str.contains(needle.lower(), na=False)]


# -------------------------
# App
# -------------------------
st.title("Conflict–Article Dashboard")

tab_match, tab_features = st.tabs(["Matching (match_details)", "conflict_features"])  # [web:1040]

# =========================================================
# TAB 1: match_details
# =========================================================
with tab_match:
    if not OUT_DB.exists():
        st.error(f"Missing DB: {OUT_DB}")
        st.stop()

    MD_COLS = table_cols("out", DETAILS_TABLE)

    # required
    C_CONFLICT_ID = pick(MD_COLS, "conflict_id", required=True)
    C_SCORE = pick(MD_COLS, "score", required=True)
    C_OVERLAP = pick(MD_COLS, "overlap", required=True)

    # optional
    C_CONFLICT_KEY = pick(MD_COLS, "conflict_key")
    C_COUNTRY = pick(MD_COLS, "country")
    C_START = pick(MD_COLS, "start_date")
    C_END = pick(MD_COLS, "end_date")

    A_PUB = pick(MD_COLS, "publishedAt", "article_date")
    A_ARTICLE_COUNTRY = pick(MD_COLS, "article_country")
    A_SOURCE = pick(MD_COLS, "source_name")
    A_TITLE = pick(MD_COLS, "title_en", "title")
    A_DESC = pick(MD_COLS, "description_en", "description")
    A_URL = pick(MD_COLS, "url")

    A_TFIDF = pick(MD_COLS, "tfidf_terms_en")
    C_TFIDF = pick(MD_COLS, "tfidf_terms_conflict")

    # extra columns requested for bottom table (if they exist in match_details)
    EXTRA = [
        "event_type",
        "disorder_type",
        "event_type_mode",
        "event_type_conflict",  # if you renamed event_type_mode -> event_type_conflict in another pipeline
    ]

    st.header("Conflicts")

    # --- Filters above conflict table ---
    f1, f2, f3, f4, f5, f6 = st.columns([1.2, 2.2, 1.2, 1.2, 1.2, 1.2])
    with f1:
        filt_country = st.text_input("Conflict country contains", value="")
    with f2:
        filt_key = st.text_input("Conflict key contains", value="")
    with f3:
        min_n = st.number_input("Min matched articles", min_value=0, value=1, step=1)
    with f4:
        score_min = st.slider("Score min", 0.0, 1.0, 0.0, 0.01)
    with f5:
        overlap_min = st.slider("Overlap min", 0, 30, 1, 1)
    with f6:
        max_conflicts = st.number_input("Max conflicts", min_value=50, value=500, step=50)

    max_articles = st.slider("Max articles per selected conflict", 50, 2000, 300, 50)

    # --- conflict summary query ---
    group_cols = [C_CONFLICT_ID]
    select_cols = [f"{C_CONFLICT_ID} AS conflict_id"]

    if C_CONFLICT_KEY:
        group_cols.append(C_CONFLICT_KEY)
        select_cols.append(f"{C_CONFLICT_KEY} AS conflict_key")
    if C_COUNTRY:
        group_cols.append(C_COUNTRY)
        select_cols.append(C_COUNTRY)
    if C_START:
        group_cols.append(C_START)
        select_cols.append(C_START)
    if C_END:
        group_cols.append(C_END)
        select_cols.append(C_END)

    conf_summary_sql = f"""
    SELECT
        {", ".join(select_cols)},
        COUNT(DISTINCT article_id) AS n_articles,
        AVG({C_SCORE}) AS avg_score,
        MAX({C_SCORE}) AS max_score,
        AVG({C_OVERLAP}) AS avg_overlap,
        MAX({C_OVERLAP}) AS max_overlap
    FROM {DETAILS_TABLE}
    WHERE {C_SCORE} >= ?
      AND {C_OVERLAP} >= ?
    GROUP BY {", ".join(group_cols)}
    ORDER BY n_articles DESC, max_score DESC
    LIMIT ?
    """
    conf_df = qdf_out(conf_summary_sql, [float(score_min), int(overlap_min), int(max_conflicts)])

    # apply text filters
    if "country" in conf_df.columns and filt_country.strip():
        conf_df = contains_filter(conf_df, "country", filt_country.strip())
    if "conflict_key" in conf_df.columns and filt_key.strip():
        conf_df = contains_filter(conf_df, "conflict_key", filt_key.strip())

    # min_n filter
    if "n_articles" in conf_df.columns:
        conf_df = conf_df[conf_df["n_articles"] >= int(min_n)].reset_index(drop=True)

    # metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Conflicts shown", f"{len(conf_df):,}")
    m2.metric("Sum matched articles", f"{int(conf_df['n_articles'].sum()):,}" if not conf_df.empty else "0")
    m3.metric("Score min", f"{score_min:.2f}")
    m4.metric("Overlap min", f"{overlap_min:d}")

    st.caption("Select a conflict row to show its matched articles below.")
    event = st.dataframe(
        conf_df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )  # [web:733]

    selected_conflict_id = 0
    selected_conflict_key = None

    if event and event.selection.rows:
        i = event.selection.rows[0]
        selected_conflict_id = int(conf_df.iloc[i]["conflict_id"])
        if "conflict_key" in conf_df.columns:
            selected_conflict_key = conf_df.iloc[i].get("conflict_key")

    selected_conflict_id = st.number_input(
        "Selected conflict_id (manual override)",
        min_value=0,
        value=int(selected_conflict_id),
        step=1,
    )

    if selected_conflict_id <= 0:
        st.info("Select a conflict above to inspect its matched articles.")
    else:
        header = f"Matched articles for conflict_id={selected_conflict_id}"
        if selected_conflict_key:
            header += f" — {selected_conflict_key}"
        st.subheader(header)

        show_cols = col_list(MD_COLS, [
            "article_id",
            A_PUB,
            C_COUNTRY,
            A_ARTICLE_COUNTRY,
            *EXTRA,
            C_SCORE,
            C_OVERLAP,
            A_SOURCE,
            A_TITLE,
            A_DESC,
            A_URL,
            A_TFIDF,
            C_TFIDF,
        ])

        sql_articles = f"""
        SELECT {", ".join(show_cols) if show_cols else "*"}
        FROM {DETAILS_TABLE}
        WHERE conflict_id = ?
          AND {C_SCORE} >= ?
          AND {C_OVERLAP} >= ?
        ORDER BY {C_SCORE} DESC, {C_OVERLAP} DESC
        LIMIT ?
        """
        art_df = qdf_out(sql_articles, [int(selected_conflict_id), float(score_min), int(overlap_min), int(max_articles)])

        if art_df.empty:
            st.info("No articles for this conflict under current thresholds.")
        else:
            a1, a2, a3, a4 = st.columns(4)
            a1.metric("Articles shown", f"{len(art_df):,}")
            a2.metric("Avg score", f"{art_df[C_SCORE].mean():.3f}")
            a3.metric("Median score", f"{art_df[C_SCORE].median():.3f}")
            a4.metric("Avg overlap", f"{art_df[C_OVERLAP].mean():.2f}")

            col_config = {}
            if A_URL and A_URL in art_df.columns:
                col_config[A_URL] = st.column_config.LinkColumn("url", display_text="open")

            st.dataframe(
                art_df,
                use_container_width=True,
                hide_index=True,
                column_config=col_config,
            )

# =========================================================
# TAB 2: conflict_features
# =========================================================
with tab_features:
    if not CONFLICT_DB.exists():
        st.error(f"Missing DB: {CONFLICT_DB}")
        st.stop()

    CF_COLS = table_cols("conf", FEATURES_TABLE)

    st.header("conflict_features")

    # Load full table once (simple + robust)
    cf_df = qdf_conf(f"SELECT * FROM {FEATURES_TABLE}")

    # ---- Filters ABOVE conflict_features table ----
    # To avoid 30+ filters always visible, choose which columns to filter.
    default_filter_cols = [c for c in [
        "conflict_key",
        "country",
        "actor1",
        "primary_assoc_actor_1",
        "assoc_actor_1",
        "disorder_type_mode",
        "event_type_mode",
        "event_type_conflict",
        "n_events",
        "total_fatalities",
        "start_date",
        "end_date",
        "tfidf_terms_conflict",
    ] if c in CF_COLS]

    st.subheader("Filters")
    filter_cols = st.multiselect(
        "Columns to filter (contains)",
        options=sorted(list(CF_COLS)),
        default=default_filter_cols,
    )

    filters = {}
    if filter_cols:
        cols_per_row = 3
        for i in range(0, len(filter_cols), cols_per_row):
            row = filter_cols[i:i + cols_per_row]
            ui = st.columns(len(row))
            for j, colname in enumerate(row):
                with ui[j]:
                    filters[colname] = st.text_input(f"{colname} contains", value="", key=f"cf_{colname}")

    # apply filters (contains)
    cf_f = cf_df.copy()
    for colname, needle in filters.items():
        if needle.strip():
            cf_f = contains_filter(cf_f, colname, needle.strip())

    limit_rows = st.slider("Max rows shown", 50, 20000, 2000, 50)
    st.caption("Showing all columns from conflict_features (filtered).")
    st.dataframe(cf_f.head(int(limit_rows)), use_container_width=True, hide_index=True)

    st.divider()

    st.subheader("Conflicts by n_events (desc)")
    if "conflict_key" in cf_f.columns and "n_events" in cf_f.columns:
        top = cf_f[["conflict_id", "conflict_key", "n_events"]].copy()
        top["n_events"] = pd.to_numeric(top["n_events"], errors="coerce")
        top = top.sort_values("n_events", ascending=False)
        st.dataframe(top, use_container_width=True, hide_index=True)
    else:
        st.warning("Required columns not found: conflict_key and/or n_events.")

