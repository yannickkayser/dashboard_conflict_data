import streamlit as st
import sqlite3
import pandas as pd
import altair as alt
from datetime import datetime

st.set_page_config(page_title="Conflict Data Dashboard", layout="wide")
st.title("Conflict Events Dashboard")

# --- Database connection ---
DB_PATH = "/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/data/conflict_data.db"

# --- Database helpers ---

def get_connection():
    """Return a fresh SQLite connection."""
    return sqlite3.connect(DB_PATH)

def load_event_types():
    with get_connection() as conn:
        return pd.read_sql("SELECT DISTINCT event_type FROM events;", conn)["event_type"].tolist()

def load_all_events():
    with get_connection() as conn:
        return pd.read_sql("SELECT country, event_date, latitude, longitude, event_type FROM events", conn, parse_dates=["event_date"])


def load_countries():
    with get_connection() as conn:
        return pd.read_sql("SELECT DISTINCT country FROM events;", conn)["country"].tolist()

def load_data(event_type, countries, start_date, end_date):
    with get_connection() as conn:
        query = """
            SELECT country, event_date, location, actor1, latitude, longitude, fatalities
            FROM events
            WHERE fatalities = 0
              AND event_type = ?
              AND country IN ({})
              AND event_date BETWEEN ? AND ?
        """.format(",".join(["?"] * len(countries)))
        params = [event_type] + countries + [start_date, end_date]
        df = pd.read_sql(query, conn, params=params, parse_dates=["event_date"])
    return df

def get_date_range():
    with get_connection() as conn:
        date_range = pd.read_sql("SELECT MIN(event_date) as min_date, MAX(event_date) as max_date FROM events;", conn)
    min_date = pd.to_datetime(date_range["min_date"][0])
    max_date = pd.to_datetime(date_range["max_date"][0])
    return min_date, max_date

# --- Sidebar filters ---
conn = get_connection()
event_types = load_event_types()
countries = load_countries()

st.sidebar.header(" Filters")

selected_type = st.sidebar.selectbox("Event type", event_types)
selected_countries = st.sidebar.multiselect("Countries", countries, default=countries[:3])

# Query min and max dates from DB for a dynamic range
date_range = pd.read_sql("SELECT MIN(event_date) as min_date, MAX(event_date) as max_date FROM events;", conn)
min_date = pd.to_datetime(date_range["min_date"][0])
max_date = pd.to_datetime(date_range["max_date"][0])

selected_dates = st.sidebar.date_input(
    "Event date range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date
)

# --- Load data ---
if selected_countries and len(selected_dates) == 2:
    df = load_data(selected_type, selected_countries, selected_dates[0], selected_dates[1])
else:
    st.warning("Please select at least one country and a valid date range.")
    st.stop()


# --- Main dashboard ---
st.subheader("So much going on in the World")

st.subheader(f" {selected_type} events ({selected_dates[0]} → {selected_dates[1]})")

map_df = df.dropna(subset=["latitude", "longitude"])
st.map(map_df, latitude="latitude", longitude="longitude")

# --- Charts ---
col1, col2 = st.columns([2, 1])

with col1:
    if not df.empty:
        line_chart = (
            alt.Chart(df)
            .mark_line(point=True)
            .encode(
                x=alt.X("event_date:T", title="Date"),
                y=alt.Y("count():Q", title="Number of Events"),
                color="country:N",
                tooltip=["country:N", "event_date:T", "count()"]
            )
            .properties(title="Event frequency over time")
        )
        st.altair_chart(line_chart, use_container_width=True)
    else:
        st.info("No events found for the selected filters.")

with col2:
    if not df.empty:
        # Aggregate by actor1 and count occurrences
        actor_counts = df["actor1"].value_counts().reset_index()
        actor_counts.columns = ["Actor", "Number of Events"]
        st.dataframe(actor_counts)
    else:
        st.info("No events found for the selected filters.")
