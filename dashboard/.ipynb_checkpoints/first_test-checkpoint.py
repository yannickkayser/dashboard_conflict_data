import streamlit as st
import sqlite3
import pandas as pd
import altair as alt

st.title("First Visualisation")

# Connect to the local SQLite database
conn = sqlite3.connect("/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/data/conflict_data.db")

event_type = pd.read_sql("SELECT DISTINCT event_type FROM events;", conn)["event_type"].tolist()
selected_type = st.sidebar.selectbox("Choose a event type", event_type)

query = """
SELECT country, event_date, location, actor1, latitude, longitude
FROM events
WHERE fatalities = 0 AND event_type = ?
"""
df = pd.read_sql(query, conn, params=(selected_type,))
conn.close()

st.write(f"### Prices for {selected_type}")
st.dataframe(df)

chart_data = (
    df.groupby("country")
    .size()
    .reset_index(name="event_count")
)

chart = (
    alt.Chart(df)
    .mark_line()
    .encode(
        x=alt.X("event_date:T", title="Date"),
        y=alt.Y("count():Q", title="Number of Events"),
        tooltip=["event_date", "count()"]
    )
)

st.map(df, latitude="latitude", longitude="longitude")
st.altair_chart(chart, use_container_width=True)
