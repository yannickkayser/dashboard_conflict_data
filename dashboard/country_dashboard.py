#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

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
CONFLICT_FEATURES_TABLE = "conflict_features"

st.set_page_config(page_title="Conflict Dashboard", layout="wide")

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
  <h1>🌍 Conflict Dashboard</h1>
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

# -------------------------
# Main Tabs
# -------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    [
        "📊 Conflict Underrepresentation",
        "💭 Sentiment Analysis",
        "🔗 Conflict × Media Explorer",
        "📅 Timeline",
        "ℹ️ Impressum",
    ]
)

# -------------------------
# Tab 1: Conflict Underrepresentation
# -------------------------
with tab1:
    st.markdown("### 📊 Conflict Underrepresentation Analysis")
    st.info(
        "🚧 This section is under development. It will analyze which conflicts receive "
        "disproportionately low media coverage relative to their intensity."
    )

    if not conf.empty:
        st.markdown("#### Top underrepresented conflicts (few articles per event)")
        under = (
            conf[conf["articles_per_event"].notna()]
            .sort_values("articles_per_event")
            .head(10)
        )
        st.dataframe(
            under[
                [
                    "country",
                    "n_events",
                    "total_fatalities",
                    "n_articles",
                    "articles_per_event",
                ]
            ],
            use_container_width=True,
            hide_index=True,
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
    st.markdown("## 🔗 Conflict Media Explorer")

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

    # Sidebar filters – nur in diesem Tab
    with st.sidebar:
        st.markdown("### 🔍 Filters")
        filt_country = st.text_input("Country contains", value="")
        min_fatal = st.number_input("Min fatalities", min_value=0, value=0, step=50)
        min_events = st.number_input("Min events", min_value=0, value=0, step=50)
        max_rows = st.number_input("Max countries", min_value=10, value=200, step=10)

        st.markdown("---")
        st.markdown("### 📰 Article filters")
        max_articles = st.number_input(
            "Max articles (detail)", min_value=10, value=300, step=10
        )
        art_text = st.text_input("Title/description contains", value="")

        st.markdown("---")
        st.markdown("### ⏱️ Recency thresholds")
        recency_days = st.number_input(
            "No-coverage threshold (days)", min_value=1, value=30, step=1
        )

    # Filter country table
    if filt_country.strip():
        conf = contains_filter(conf, C_COUNTRY, filt_country.strip())

    conf = conf[
        (conf[C_FATAL] >= float(min_fatal))
        & (conf[C_EVENTS] >= float(min_events))
    ].copy()

    conf = (
        conf.sort_values(["n_articles", C_EVENTS, C_FATAL], ascending=False)
        .head(int(max_rows))
        .reset_index(drop=True)
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

    with tab_scatter:
        if conf.empty:
            st.info("No countries match the current filters.")
        else:
            scatter = (
                alt.Chart(conf)
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

    with tab_overview:
        st.caption(
            "Country-level overview of conflict events, fatalities, and matched news "
            "articles used for the detailed article view below."
        )
        show_master = conf[
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

    selected_country = ""
    if evt and evt.selection.rows:
        i = evt.selection.rows[0]
        selected_country = str(show_master.iloc[i]["country"])

    selected_country = st.text_input("Selected country", value=selected_country)

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
    st.subheader("🌍 Country detail")
    st.markdown(f"**Selected country:** {selected_country}")

    if not selected_country.strip():
        st.info("Select a country from the overview table above to see details.")
        st.stop()

    row = conf[conf[C_COUNTRY].astype(str) == selected_country.strip()]
    if row.empty:
        st.warning(
            "Selected country not found in current filtered table. Try adjusting filters."
        )
        st.stop()

    r = row.iloc[0]

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

    # Apply text filter (global)
    if art_text.strip() and not art.empty:
        mask = (
            art[A_TITLE]
            .astype(str)
            .str.lower()
            .str.contains(art_text.strip().lower(), na=False)
            | art[A_DESC]
            .astype(str)
            .str.lower()
            .str.contains(art_text.strip().lower(), na=False)
        )
        art = art[mask].reset_index(drop=True)

    # Recency metrics
    now = datetime.utcnow()
    if not art.empty and art[A_PUB].notna().any():
        last_article_date = art[A_PUB].max()
        days_since_last = (now - last_article_date).days
        last_7 = (now - art[A_PUB] <= timedelta(days=7)).sum()
    else:
        days_since_last = None
        last_7 = 0

    # Zeile 1: 3 Kennzahlen + Top-3-Outlets-Plot
    row1_col1, row1_col2, row1_col3, row1_col4 = st.columns([1, 1, 1, 1.6])

    with row1_col1:
        st.metric("Matched articles", f"{int(art.shape[0]):,}")
    with row1_col2:
        st.metric("Events", f"{int(r[C_EVENTS]):,}")
    with row1_col3:
        st.metric("Fatalities", f"{int(r[C_FATAL]):,}")

    with row1_col4:
        st.markdown("**Top 3 outlets (snapshot)**")
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
                    color=alt.Color(
                        "n_articles:Q",
                        title=None,
                        scale=alt.Scale(scheme="blues"),
                    ),
                )
                .properties(height=120, width=260)
            )
            st.altair_chart(snap_bar, use_container_width=False)

    # Zeile 2: restliche 3 Kennzahlen
    row2_col1, row2_col2, row2_col3 = st.columns(3)
    with row2_col1:
        st.metric(
            "Articles per event",
            f"{r['articles_per_event']:.3f}"
            if r["articles_per_event"] is not None
            else "NA",
        )
    with row2_col2:
        st.metric(
            "Days since last article",
            f"{days_since_last}" if days_since_last is not None else "NA",
        )
    with row2_col3:
        st.metric("Articles last 7 days", f"{last_7}")

    # -------------------------
    # Detail tabs
    # -------------------------
    t_articles, t_outlets, t_time = st.tabs(
        ["📰 Articles", "🏢 Outlets detail", "📈 Coverage over time"]
    )

    # ---------- Articles tab: left articles, right conflict profile ----------
    # -------------------------
    # Detail tabs
    # -------------------------
    t_articles, t_outlets, t_time = st.tabs(
        ["📰 Articles", "🏢 Outlets detail", "📈 Coverage over time"]
    )

    # ---------- Articles tab: search + table, conflict profile BELOW ----------
    with t_articles:
        header_left, header_right = st.columns([3, 1])
        with header_left:
            st.markdown(
                """
                <p style="font-size:1.0rem; font-weight:600; margin-bottom:0.4rem;">
                    Search, news, topics and more.
                </p>
                """,
                unsafe_allow_html=True,
            )
        with header_right:
            st.metric("Articles shown", f"{len(art):,}")

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

        # Artikel filtern
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

        # Artikeltabelle
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

        st.markdown("---")

        # -------- Country conflict profile (below table) --------
        st.markdown(
            """
            <h4 style="margin-bottom:0.1rem;">Country conflict profile</h4>
            <p style="font-size:0.9rem; color:#555; margin-top:0.15rem;">
                Conflict characteristics from ACLED help to contextualize the news articles
                shown above.
            </p>
            """,
            unsafe_allow_html=True,
        )

        cf = qdf_conf_features(selected_country.strip())

        if cf.empty:
            st.info("No conflict feature data available for this country.")
        else:
            # Event-type-Verteilung für Pie-Chart vorbereiten
            top_events = (
                cf.groupby("event_type_mode", dropna=True)["n_events"]
                .sum()
                .reset_index()
                .sort_values("n_events", ascending=False)
            )
            # nur Top 5 explizit, Rest bündeln
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

            # Layout: links Pie, rechts Bar + Text
            pie_col, bar_col = st.columns([1.1, 1.3])

            with pie_col:
                st.markdown("**Event types share**")
                pie = (
                    alt.Chart(event_pie_df)
                    .mark_arc(outerRadius=90, innerRadius=35)  # Donut
                    .encode(
                        theta=alt.Theta("n_events:Q", stack=True),
                        color=alt.Color(
                            "event_type_mode:N",
                            title="Event type",
                            scale=alt.Scale(scheme="tableau20"),
                        ),
                        tooltip=["event_type_mode", "n_events"],
                    )
                    .properties(width=220, height=200)
                )
                st.altair_chart(pie, use_container_width=False)

            with bar_col:
                st.markdown("**Main disorder categories**")
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
                    .properties(width=260, height=180)
                )
                st.altair_chart(disorder_bar, use_container_width=False)

                # Key actors + Zeitraum
                top_actors = (
                    cf.groupby("primary_assoc_actor_1", dropna=True)["n_events"]
                    .sum()
                    .reset_index()
                    .sort_values("n_events", ascending=False)
                    .head(4)
                )
                st.markdown("**Key primary actors:**")
                for _, ac in top_actors.iterrows():
                    st.markdown(f"- {ac['primary_assoc_actor_1']} ({int(ac['n_events'])} events)")

                cf["start_date"] = pd.to_datetime(cf["start_date"], errors="coerce")
                cf["end_date"] = pd.to_datetime(cf["end_date"], errors="coerce")
                if cf["start_date"].notna().any() and cf["end_date"].notna().any():
                    start_min = cf["start_date"].min().date()
                    end_max = cf["end_date"].max().date()
                    st.markdown(
                        f"<p style='font-size:0.85rem; color:#555; margin-top:0.4rem;'>"
                        f"ACLED conflict events covered from <strong>{start_min}</strong> "
                        f"to <strong>{end_max}</strong>.</p>",
                        unsafe_allow_html=True,
                    )


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
