 #!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
from pathlib import Path
from datetime import date

import pandas as pd
import streamlit as st

# -------------------------
# Paths
# -------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # .../dashboard_conflict_data
DATA_DIR = PROJECT_ROOT / "data"
OUT_DB = DATA_DIR / "article_conflict_matches.db"

DETAILS_TABLE = "match_details"

st.set_page_config(page_title="Matching QA (match_details)", layout="wide")


# -------------------------
# DB helpers
# -------------------------
@st.cache_resource
def get_conn():
    return sqlite3.connect(str(OUT_DB), check_same_thread=False)


@st.cache_data(ttl=30)
def qdf(sql: str, params=None) -> pd.DataFrame:
    conn = get_conn()
    return pd.read_sql_query(sql, conn, params=params or [])


@st.cache_data(ttl=60)
def table_cols(table: str) -> set[str]:
    df = qdf(f"PRAGMA table_info({table})")
    return set(df["name"].tolist())


COLS = table_cols(DETAILS_TABLE)


def pick(*names, required=False):
    for n in names:
        if n in COLS:
            return n
    if required:
        raise RuntimeError(f"None of the columns exist: {names}")
    return None


def col_list(cols):
    return [c for c in cols if c and c in COLS]


def to_dt_series(s: pd.Series) -> pd.Series:
    # robust parse; values are often "YYYY-MM-DD"
    return pd.to_datetime(s, errors="coerce").dt.date


# -------------------------
# Column mapping (best-effort)
# -------------------------
# conflict columns
C_CONFLICT_ID = pick("conflict_id", required=True)
C_CONFLICT_KEY = pick("conflict_key")  # conflict name
C_COUNTRY = pick("country", "conflict_country", "conf_country")
C_START = pick("start_date", "conflict_start", "conf_start")
C_END = pick("end_date", "conflict_end", "conf_end")
C_CONFLICT_TERMS = pick("conf_tfidf_terms_en", "conf_terms", "conflict_terms", "tfidf_terms_en")  # may collide

# match columns
C_SCORE = pick("score", required=True)
C_OVERLAP = pick("overlap", required=True)

# article columns
A_PUB = pick("publishedAt", "published_at", "article_date")
A_ARTICLE_COUNTRY = pick("article_country", "art_country")
A_SOURCE = pick("source_name", "source", "publisher", "art_source_name")
A_TITLE = pick("title_en", "title", "art_title_en", "art_title")
A_DESC = pick("description_en", "description", "art_description_en", "art_description")
A_URL = pick("url", "art_url")
A_ARTICLE_TERMS = pick("art_tfidf_terms_en", "article_terms", "article_tfidf_terms", "tfidf_terms_en")  # may collide


# -------------------------
# UI
# -------------------------
st.title("Article–Conflict Matching QA (match_details)")

if not OUT_DB.exists():
    st.error(f"OUT_DB not found: {OUT_DB}")
    st.stop()

# -------------------------
# Load a conflict-level summary from SQLite
# (keeps app responsive even if match_details is large)
# -------------------------
def load_conflict_summary_sql(limit: int = 5000) -> pd.DataFrame:
    group_cols = [C_CONFLICT_ID]
    if C_CONFLICT_KEY:
        group_cols.append(C_CONFLICT_KEY)
    if C_COUNTRY:
        group_cols.append(C_COUNTRY)
    if C_START:
        group_cols.append(C_START)
    if C_END:
        group_cols.append(C_END)

    select_cols = []
    for c in group_cols:
        if c == C_CONFLICT_ID:
            select_cols.append(f"{c} AS conflict_id")
        elif c == C_CONFLICT_KEY:
            select_cols.append(f"{c} AS conflict_key")
        else:
            select_cols.append(c)

    sql = f"""
    SELECT
        {", ".join(select_cols)},
        COUNT(DISTINCT article_id) AS n_articles,
        AVG({C_SCORE}) AS avg_score,
        MAX({C_SCORE}) AS max_score,
        AVG({C_OVERLAP}) AS avg_overlap,
        MAX({C_OVERLAP}) AS max_overlap
    FROM {DETAILS_TABLE}
    GROUP BY {", ".join(group_cols)}
    ORDER BY n_articles DESC, max_score DESC
    LIMIT ?
    """
    df = qdf(sql, [int(limit)])

    # place conflict_key right after conflict_id
    front = ["conflict_id"] + (["conflict_key"] if "conflict_key" in df.columns else [])
    rest = [c for c in df.columns if c not in front]
    return df[front + rest]


# Sidebar: global filters (match quality)
with st.sidebar:
    st.header("Match quality filters")
    score_min = st.slider("Score min", 0.0, 1.0, 0.0, 0.01)
    overlap_min = st.slider("Overlap min", 0, 30, 1, 1)
    max_conflicts = st.slider("Max conflicts loaded", 200, 20000, 5000, 200)
    max_articles = st.slider("Max articles shown per conflict", 50, 3000, 300, 50)

# Load conflicts summary
conf_df = load_conflict_summary_sql(limit=int(max_conflicts))

# -------------------------
# Per-column filtering UI for top table
# -------------------------
st.subheader("Conflicts (filterable)")
st.caption("Filter by conflict_key text and by conflict start/end date range, then click a row to inspect matched articles. [web:733]")

# Filters row
f1, f2, f3, f4 = st.columns([2, 1, 1, 1])

with f1:
    conflict_key_query = st.text_input("Filter: conflict_key contains", value="").strip().lower()
with f2:
    country_query = st.text_input("Filter: country contains", value="").strip().lower()
with f3:
    min_n = st.number_input("Min #articles", min_value=0, value=1, step=1)
with f4:
    sort_by = st.selectbox("Sort by", ["n_articles", "max_score", "avg_score", "avg_overlap"], index=0)

# Date filters
df_dates = conf_df.copy()
if C_START and C_START in df_dates.columns:
    df_dates["_start_dt"] = to_dt_series(df_dates[C_START])
else:
    df_dates["_start_dt"] = pd.NaT

if C_END and C_END in df_dates.columns:
    df_dates["_end_dt"] = to_dt_series(df_dates[C_END])
else:
    df_dates["_end_dt"] = pd.NaT

min_start = df_dates["_start_dt"].dropna().min()
max_end = df_dates["_end_dt"].dropna().max()

d1, d2 = st.columns(2)
with d1:
    start_range = st.date_input(
        "Filter: start_date range",
        value=(min_start, max_end) if pd.notna(min_start) and pd.notna(max_end) else (),
    )
with d2:
    end_range = st.date_input(
        "Filter: end_date range",
        value=(min_start, max_end) if pd.notna(min_start) and pd.notna(max_end) else (),
    )

# Apply filters
f = conf_df.copy()

# text filters
if "conflict_key" in f.columns and conflict_key_query:
    f = f[f["conflict_key"].astype(str).str.lower().str.contains(conflict_key_query, na=False)]
if C_COUNTRY and C_COUNTRY in f.columns and country_query:
    f = f[f[C_COUNTRY].astype(str).str.lower().str.contains(country_query, na=False)]

# numeric filter
f = f[f["n_articles"] >= int(min_n)]

# date filters
if len(start_range) == 2 and pd.notna(df_dates["_start_dt"]).any():
    s0, s1 = start_range
    tmp = df_dates.loc[f.index, "_start_dt"]
    f = f[(tmp >= s0) & (tmp <= s1)]

if len(end_range) == 2 and pd.notna(df_dates["_end_dt"]).any():
    e0, e1 = end_range
    tmp = df_dates.loc[f.index, "_end_dt"]
    f = f[(tmp >= e0) & (tmp <= e1)]

# quality filters applied at *article-level* later; show them here as guidance
# (still show conflicts even if only a subset of their matches survive the thresholds)
f = f.sort_values(by=sort_by, ascending=False).reset_index(drop=True)

# top metrics
m1, m2, m3, m4 = st.columns(4)
m1.metric("Conflicts shown", f"{len(f):,}")
m2.metric("Sum matched articles", f"{int(f['n_articles'].sum()):,}" if not f.empty else "0")
m3.metric("Score min", f"{score_min:.2f}")
m4.metric("Overlap min", f"{overlap_min:d}")

event = st.dataframe(
    f,
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
)

selected_conflict_id = 0
selected_conflict_key = None
if event and event.selection.rows:
    row_idx = event.selection.rows[0]
    selected_conflict_id = int(f.iloc[row_idx]["conflict_id"])
    if "conflict_key" in f.columns:
        selected_conflict_key = f.iloc[row_idx].get("conflict_key")

selected_conflict_id = st.number_input(
    "Selected conflict_id (manual override)",
    min_value=0,
    value=int(selected_conflict_id),
    step=1,
)

# load conflict_key from DB if not from selection
if selected_conflict_id > 0 and C_CONFLICT_KEY and not selected_conflict_key:
    tmp = qdf(
        f"SELECT {C_CONFLICT_KEY} AS conflict_key FROM {DETAILS_TABLE} WHERE conflict_id=? LIMIT 1",
        [int(selected_conflict_id)],
    )
    if not tmp.empty:
        selected_conflict_key = tmp.loc[0, "conflict_key"]

st.divider()

# -------------------------
# Articles for selected conflict
# -------------------------
if selected_conflict_id and selected_conflict_id > 0:
    title = f"Matched articles for conflict_id={selected_conflict_id}"
    if selected_conflict_key:
        title += f" — {selected_conflict_key}"
    st.subheader(title)

    awhere = [
        "conflict_id = ?",
        f"{C_SCORE} >= ?",
        f"{C_OVERLAP} >= ?",
    ]
    aparams = [int(selected_conflict_id), float(score_min), int(overlap_min)]

    show_cols = col_list([
        "article_id",
        A_PUB,
        A_ARTICLE_COUNTRY,
        C_SCORE,
        C_OVERLAP,
        A_SOURCE,
        A_TITLE,
        A_DESC,
        A_URL,
        A_ARTICLE_TERMS,   # show article tfidf
        C_CONFLICT_TERMS,  # show conflict tfidf
    ])

    order = "score DESC, overlap DESC"
    sql_articles = f"""
    SELECT {", ".join(show_cols) if show_cols else "*"}
    FROM {DETAILS_TABLE}
    WHERE {" AND ".join(awhere)}
    ORDER BY {order}
    LIMIT ?
    """
    art_df = qdf(sql_articles, aparams + [int(max_articles)])

    if art_df.empty:
        st.info("No articles for this conflict under current score/overlap thresholds.")
    else:
        # quick QA metrics
        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Articles shown", f"{len(art_df):,}")
        q2.metric("Avg score", f"{art_df[C_SCORE].mean():.3f}")
        q3.metric("Median score", f"{art_df[C_SCORE].median():.3f}")
        q4.metric("Avg overlap", f"{art_df[C_OVERLAP].mean():.2f}")

        # clickable URL column
        col_config = {}
        if A_URL and A_URL in art_df.columns:
            col_config[A_URL] = st.column_config.LinkColumn("url", display_text="open")

        st.dataframe(
            art_df,
            use_container_width=True,
            hide_index=True,
            column_config=col_config,
        )

        # quick distributions
        left, right = st.columns(2)
        with left:
            st.markdown("### Score distribution (quick)")
            bins = pd.cut(
                art_df[C_SCORE],
                bins=[0, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0],
                include_lowest=True,
            )
            dist = bins.value_counts().sort_index().rename_axis("score_bin").reset_index(name="n")
            st.dataframe(dist, hide_index=True, use_container_width=True)

        with right:
            st.markdown("### Overlap distribution (quick)")
            od = art_df[C_OVERLAP].value_counts().sort_index().reset_index()
            od.columns = ["overlap", "n"]
            st.dataframe(od, hide_index=True, use_container_width=True)

        # show conflict metadata row
        st.markdown("### Conflict metadata (from match_details)")
        meta_cols = col_list([C_CONFLICT_ID, C_CONFLICT_KEY, C_COUNTRY, C_START, C_END])
        if meta_cols:
            meta = qdf(
                f"SELECT {', '.join(meta_cols)} FROM {DETAILS_TABLE} WHERE conflict_id=? LIMIT 1",
                [int(selected_conflict_id)],
            )
            st.dataframe(meta, hide_index=True, use_container_width=True)

else:
    st.info("Select a conflict above to inspect its matched articles.")
