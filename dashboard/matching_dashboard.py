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
OUT_DB = DATA_DIR / "article_conflict_matches.db"

DETAILS_TABLE = "match_details"  # created by your matching script (Option B)

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


def detect_cols():
    df = qdf(f"PRAGMA table_info({DETAILS_TABLE})")
    return set(df["name"].tolist())


COLS = detect_cols()


def pick(*names, required=False):
    for n in names:
        if n in COLS:
            return n
    if required:
        raise RuntimeError(f"None of the columns exist: {names}")
    return None


def col_list(cols):
    return [c for c in cols if c and c in COLS]


# -------------------------
# Column mapping
# -------------------------
# conflict columns
C_CONFLICT_ID = pick("conflict_id", required=True)
C_CONFLICT_KEY = pick("conflict_key")  # name of conflict
C_COUNTRY = pick("country", "conflict_country", "conf_country")
C_START = pick("start_date", "conflict_start", "conf_start")
C_END = pick("end_date", "conflict_end", "conf_end")

# match columns
C_SCORE = pick("score", required=True)
C_OVERLAP = pick("overlap", required=True)
C_ARTICLE_DATE = pick("article_date", "publishedAt", "published_at")
C_ARTICLE_COUNTRY = pick("article_country", "country_article", "art_country")

# article columns
A_TITLE = pick("title_en", "title", "art_title_en", "art_title")
A_DESC = pick("description_en", "description", "art_description_en", "art_description")
A_URL = pick("url", "art_url")
A_SOURCE = pick("source_name", "source", "publisher", "art_source_name")


# -------------------------
# UI
# -------------------------
st.title("Article–Conflict Matching QA (match_details)")

if not OUT_DB.exists():
    st.error(f"OUT_DB not found: {OUT_DB}")
    st.stop()

# Sidebar controls
with st.sidebar:
    st.header("Filters")

    country = st.text_input("Conflict country contains", value="").strip()
    min_n = st.number_input("Min matched articles per conflict", min_value=0, value=1, step=1)

    score_min = st.slider("Score min", 0.0, 1.0, 0.0, 0.01)
    overlap_min = st.slider("Overlap min", 0, 20, 1, 1)

    max_conflicts = st.slider("Max conflicts shown", 50, 5000, 500, 50)
    max_articles = st.slider("Max articles shown per conflict", 50, 2000, 300, 50)

    st.divider()
    st.header("QA shortcuts")
    show_low_score = st.checkbox("Show lowest-score examples (spot false positives)", value=False)
    show_top_sources = st.checkbox("Show top sources for conflict", value=True)


# -------------------------
# Conflict summary table
# -------------------------
where = []
params = []

if C_COUNTRY and country:
    where.append(f"LOWER({C_COUNTRY}) LIKE ?")
    params.append(f"%{country.lower()}%")

where.append(f"{C_SCORE} >= ?")
params.append(float(score_min))
where.append(f"{C_OVERLAP} >= ?")
params.append(int(overlap_min))

where_sql = "WHERE " + " AND ".join(where) if where else ""

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

conf_summary_sql = f"""
SELECT
    {", ".join(select_cols)},
    COUNT(DISTINCT article_id) AS n_articles,
    AVG({C_SCORE}) AS avg_score,
    MAX({C_SCORE}) AS max_score,
    AVG({C_OVERLAP}) AS avg_overlap,
    MAX({C_OVERLAP}) AS max_overlap
FROM {DETAILS_TABLE}
{where_sql}
GROUP BY {", ".join(group_cols)}
ORDER BY n_articles DESC, max_score DESC
LIMIT ?
"""
conf_df = qdf(conf_summary_sql, params + [int(max_conflicts)])

# Put conflict_key next to conflict_id (conflict_id first)
front = ["conflict_id"] + (["conflict_key"] if "conflict_key" in conf_df.columns else [])
rest = [c for c in conf_df.columns if c not in front]
conf_df = conf_df[front + rest]

conf_df = conf_df[conf_df["n_articles"] >= int(min_n)].reset_index(drop=True)

# Top-line metrics
c1, c2, c3, c4 = st.columns(4)
c1.metric("Conflicts (filtered)", f"{len(conf_df):,}")
c2.metric("Sum matched articles", f"{int(conf_df['n_articles'].sum()):,}" if not conf_df.empty else "0")
c3.metric("Mean avg_score", f"{conf_df['avg_score'].mean():.3f}" if not conf_df.empty else "n/a")
c4.metric("Mean avg_overlap", f"{conf_df['avg_overlap'].mean():.2f}" if not conf_df.empty else "n/a")

st.subheader("Conflicts")
st.caption("Click a row to inspect all matched articles for that conflict (under current score/overlap filters).")

event = st.dataframe(
    conf_df,
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
)

# --- IMPORTANT: define selected_conflict_id BEFORE using it ---
selected_conflict_id = 0
if event and event.selection.rows:
    row_idx = event.selection.rows[0]
    selected_conflict_id = int(conf_df.iloc[row_idx]["conflict_id"])

# Manual fallback
selected_conflict_id = st.number_input(
    "Selected conflict_id (manual override)",
    min_value=0,
    value=int(selected_conflict_id),
    step=1,
)

# Load conflict_key (after selected_conflict_id exists)
conf_key = None
if C_CONFLICT_KEY and selected_conflict_id > 0:
    tmp = qdf(
        f"SELECT {C_CONFLICT_KEY} AS conflict_key FROM {DETAILS_TABLE} WHERE conflict_id=? LIMIT 1",
        [int(selected_conflict_id)],
    )
    if not tmp.empty:
        conf_key = tmp.loc[0, "conflict_key"]

st.divider()

# -------------------------
# Articles for selected conflict
# -------------------------
if selected_conflict_id and selected_conflict_id > 0:
    if conf_key:
        st.subheader(f"Matched articles for conflict_id={selected_conflict_id} — {conf_key}")
    else:
        st.subheader(f"Matched articles for conflict_id={selected_conflict_id}")

    awhere = [
        "conflict_id = ?",
        f"{C_SCORE} >= ?",
        f"{C_OVERLAP} >= ?",
    ]
    aparams = [int(selected_conflict_id), float(score_min), int(overlap_min)]

    order = "score ASC" if show_low_score else "score DESC"

    show_cols = col_list([
        "article_id",
        C_ARTICLE_DATE,
        C_ARTICLE_COUNTRY,
        C_SCORE,
        C_OVERLAP,
        A_SOURCE,
        A_TITLE,
        A_DESC,
        A_URL,
    ])

    sql_articles = f"""
    SELECT {", ".join(show_cols) if show_cols else "*"}
    FROM {DETAILS_TABLE}
    WHERE {" AND ".join(awhere)}
    ORDER BY {order}, {C_OVERLAP} DESC
    LIMIT ?
    """
    art_df = qdf(sql_articles, aparams + [int(max_articles)])

    if art_df.empty:
        st.info("No articles for this conflict under current filters.")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Articles shown", f"{len(art_df):,}")
        m2.metric("Avg score", f"{art_df[C_SCORE].mean():.3f}")
        m3.metric("Median score", f"{art_df[C_SCORE].median():.3f}")
        m4.metric("Avg overlap", f"{art_df[C_OVERLAP].mean():.2f}")

        col_config = {}
        if A_URL and A_URL in art_df.columns:
            col_config[A_URL] = st.column_config.LinkColumn("url", display_text="open")

        st.dataframe(
            art_df,
            use_container_width=True,
            hide_index=True,
            column_config=col_config,
        )

        qa1, qa2 = st.columns(2)

        with qa1:
            st.markdown("### Score distribution (quick)")
            bins = pd.cut(
                art_df[C_SCORE],
                bins=[0, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0],
                include_lowest=True,
            )
            dist = bins.value_counts().sort_index().rename_axis("score_bin").reset_index(name="n")
            st.dataframe(dist, hide_index=True, use_container_width=True)

        with qa2:
            st.markdown("### Overlap distribution (quick)")
            od = art_df[C_OVERLAP].value_counts().sort_index().reset_index()
            od.columns = ["overlap", "n"]
            st.dataframe(od, hide_index=True, use_container_width=True)

        if show_top_sources and A_SOURCE and A_SOURCE in art_df.columns:
            st.markdown("### Top sources (outlets)")
            src = (
                art_df[A_SOURCE]
                .fillna("(missing)")
                .value_counts()
                .head(20)
                .reset_index()
            )
            src.columns = ["source_name", "n_articles"]
            st.dataframe(src, hide_index=True, use_container_width=True)

        st.markdown("### Conflict metadata (from match_details)")
        meta_cols = col_list([C_CONFLICT_ID, C_CONFLICT_KEY, C_COUNTRY, C_START, C_END])
        if meta_cols:
            meta = qdf(
                f"SELECT {', '.join(meta_cols)} FROM {DETAILS_TABLE} WHERE conflict_id=? LIMIT 1",
                [int(selected_conflict_id)],
            )
            st.dataframe(meta, hide_index=True, use_container_width=True)
        else:
            st.info("No conflict metadata columns detected in match_details.")
else:
    st.info("Select a conflict above to inspect its matched articles.")
