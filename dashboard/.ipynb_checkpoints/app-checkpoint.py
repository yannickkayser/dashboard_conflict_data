import streamlit as st
import pandas as pd
import plotly.express as px
from pathlib import Path

# --- Setup ---
st.set_page_config(layout="wide")
st.title("German Media Agenda Dashboard")

# --- Configuration ---
DATA_FILE = Path("processed_articles_monthly.csv")
TOPIC_INFO_GENERAL = Path("topic_info_general.csv") 

# --- Data Loading Utilities ---

@st.cache_data
def load_data(file_path):
    """Loads and prepares the main processed data."""
    try:
        df = pd.read_csv(file_path)
        df['published_date'] = pd.to_datetime(df['published_date'])
        
        # Ensure month column is string for filtering
        if 'published_month' not in df.columns:
            df['published_month'] = df['published_date'].dt.to_period('M').astype(str)
            
        if 'topic_category' not in df.columns:
            df['topic_category'] = 'Uncategorized'
            
        return df
    except FileNotFoundError:
        return None

@st.cache_data
def load_topic_info(file_path):
    """Loads the general BERTopic information."""
    try:
        return pd.read_csv(file_path)
    except FileNotFoundError:
        return None

# --- Main Data Load & Validation ---

df = load_data(DATA_FILE)
if df is None:
    st.error("Error: Data file not found ('processed_articles_monthly.csv').")
    st.write("Please run `data_processor.py` first to generate the data file.")
    st.stop()

# --- Global Filters & Sidebar ---
st.sidebar.header("Global Filters")

# Prepare list of unique months (most recent first)
all_months = df['published_month'].unique().tolist()
all_months.sort(reverse=True)
month_options = ["All Time"] + all_months

# Filter by month
selected_month = st.sidebar.selectbox("Select Month:", month_options)

# Filter by Topic Category
all_categories = df['topic_category'].unique().tolist()
selected_category = st.sidebar.selectbox("Select Topic Category:", ["All Categories"] + all_categories)

# Apply filters
df_filtered = df.copy()
if selected_month != "All Time":
    df_filtered = df_filtered[df_filtered['published_month'] == selected_month]
if selected_category != "All Categories":
    df_filtered = df_filtered[df_filtered['topic_category'] == selected_category]

# --- VIZ CONFIG ---
EMOTION_COLOR_MAP = {
    'joy': '#2ca02c',       # Green (Joy)
    'optimism': '#17becf',  # Cyan (Optimism)
    'sadness': '#1f77b4',   # Blue (Sadness)
    'anger': '#d62728',     # Red (Anger)
    'neutral': '#7f7f7f'    # Gray (Neutral/Backup)
}

# --- 1. Overview Tab (Time Series Focus) ---
st.header("1. Media Agenda Timeline Analysis")

# Aggregate data by month for time series charts
df_monthly_agg = df.groupby('published_month').agg(
    article_count=('source_name', 'size'),
    avg_emotion=('sentiment_numeric', 'mean')
).reset_index()

# Plot 1: Article Count Over Time
col_count, col_emotion = st.columns(2)
with col_count:
    st.subheader("Monthly Attention (Article Count)")
    fig_count = px.bar(df_monthly_agg, x='published_month', y='article_count', 
                       title="Total Articles Published per Month")
    st.plotly_chart(fig_count, use_container_width=True)

# Plot 2: Average Emotion Over Time
with col_emotion:
    st.subheader("Monthly Average Sentiment")
    fig_emotion = px.line(df_monthly_agg, x='published_month', y='avg_emotion', 
                          title="Sentiment Trend Over Time (Positive > 0, Negative < 0)")
    fig_emotion.add_hline(y=0, line_dash="dash", line_color="red")
    st.plotly_chart(fig_emotion, use_container_width=True)

st.divider()

# --- 2. Monthly Detailed View ---
st.header(f"2. Detailed Analysis: {selected_month} / {selected_category}")

if df_filtered.empty:
    st.warning("No data matches the current filters.")
else:
    # --- Top Metrics (Key Indicators) ---
    metrics_col1, metrics_col2, metrics_col3 = st.columns([1,1,1])
    
    with metrics_col1:
        st.metric("Total Articles (Filtered)", f"{len(df_filtered)}")
    with metrics_col2:
        st.metric("Average Sentiment Score", f"{df_filtered['sentiment_numeric'].mean():.2f}")
    with metrics_col3:
        most_frequent_emotion = df_filtered['sentiment_label'].mode().iloc[0]
        st.metric("Dominant Emotion", most_frequent_emotion.capitalize())

    st.markdown("---")
    
    # --- Detailed Visualization (Emotion, Topics, Time) ---
    viz_col1, viz_col2 = st.columns([1, 2])
    
    with viz_col1:
        st.subheader("Emotion Distribution")
        emotion_counts = df_filtered['sentiment_label'].value_counts().reset_index()
        emotion_counts.columns = ['Emotion', 'Count']
        
        fig_pie = px.pie(emotion_counts, 
                         values='Count', 
                         names='Emotion', 
                         color='Emotion',
                         color_discrete_map=EMOTION_COLOR_MAP,
                         title="Emotion Breakdown")
        st.plotly_chart(fig_pie, use_container_width=True)

    with viz_col2:
        st.subheader("Top 3 General Topics")
        topic_info_df = load_topic_info(TOPIC_INFO_GENERAL)
        
        if topic_info_df is not None:
            topic_counts = df_filtered['topic_id_general'].value_counts().reset_index()
            topic_counts.columns = ['topic_id_general', 'Count']
            
            topic_counts = topic_counts.merge(
                topic_info_df[['Topic', 'Name']].rename(columns={'Topic': 'topic_id_general'}),
                on='topic_id_general', how='left'
            )
            
            fig_bar = px.bar(topic_counts.head(3), x='Count', y='Name', orientation='h', 
                             title="Top 3 General Topics (Filtered)")
            fig_bar.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("General topic info not available. Please run data_processor.py.")

    # --- Daily Trend ---
    if selected_month != "All Time":
        st.divider()
        st.subheader(f"Daily Trend ({selected_month})")
        
        df_daily_agg = df_filtered.groupby('published_date').agg(
            daily_count=('source_name', 'size'),
            daily_emotion=('sentiment_numeric', 'mean')
        ).reset_index()

        fig_daily = px.line(df_daily_agg, x='published_date', y='daily_emotion', 
                            title="Daily Average Sentiment Score")
        fig_daily.add_hline(y=df_filtered['sentiment_numeric'].mean(), line_dash="dot", line_color="gray")
        
        st.plotly_chart(fig_daily, use_container_width=True)

    st.divider()

    # --- 3. Topic Keyword Information ---
    st.header("3. Topic Keyword Information")
    if topic_info_df is not None:
        st.subheader("Keywords of Filtered Topics")
        present_topics = df_filtered['topic_id_general'].unique()
        display_topic_info = topic_info_df[topic_info_df['Topic'].isin(present_topics) & (topic_info_df['Topic'] != -1)]
        
        st.dataframe(display_topic_info[['Topic', 'Count', 'Name', 'Representation']], use_container_width=True)
    else:
        st.info("Topic keyword information not available.")