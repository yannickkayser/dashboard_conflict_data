import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import numpy as np
from transformers import pipeline

# ============================================================
# 0. PAGE CONFIGURATION
# ============================================================
st.set_page_config(layout="wide", page_title="Conflict Media Analytics")

st.markdown("""
    <style>
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .section-header { color: #1f77b4; font-weight: bold; margin-top: 2rem; border-bottom: 2px solid #1f77b4; }
    .insight-box { background-color: #e1f5fe; padding: 15px; border-radius: 5px; border-left: 5px solid #01579b; margin: 10px 0; }
    .guide-box { background-color: #f1f8e9; padding: 15px; border-radius: 5px; border-left: 5px solid #558b2f; margin-bottom: 20px; }
    </style>
    """, unsafe_allow_html=True)

# ============================================================
# 1. DATA LOADING & ON-DEMAND TRANSLATOR
# ============================================================
@st.cache_data
def load_data():
    try:
        df = pd.read_csv("processed_conflict_articles.csv")
        df['published_date'] = pd.to_datetime(df['published_date'])
        return df
    except Exception as e:
        st.error(f"Error loading data: {e}")
        return None

@st.cache_resource
def get_translator():
    return pipeline("translation_de_to_en", model="Helsinki-NLP/opus-mt-de-en")

df = load_data()
if df is None:
    st.error("Data files not found. Please run data_processor.py first.")
    st.stop()

# --- User Guide ---
with st.expander("How to use this Dashboard"):
    st.markdown("""
    <div class="guide-box">
        1. <b>Observe Trends:</b> Check if reporting spikes on specific dates and how quickly the interest fades.<br>
        2. <b>Analyze Framing:</b> Observe the emotions media outlets use to "package" conflicts when reporting is not neutral.<br>
        3. <b>Compare Media:</b> Identify stance biases across different media organizations handling the same conflict.<br>
        4. <b>Deep Dive:</b> Explore specific narrative details within macro categories through algorithmic clustering.
    </div>
    """, unsafe_allow_html=True)

# ============================================================
# 2. FILTERS
# ============================================================
st.sidebar.title("Analytics Filters")
date_range = st.sidebar.date_input("Date Range", [df['published_date'].min(), df['published_date'].max()])
scope = st.sidebar.selectbox("Scope", ["All News", "International", "Domestic"])

if len(date_range) == 2:
    mask = (df['published_date'].dt.date >= date_range[0]) & (df['published_date'].dt.date <= date_range[1])
    df_f = df.loc[mask]
else:
    df_f = df.copy()

if scope == "International": 
    df_f = df_f[df_f['is_domestic'] == False]
elif scope == "Domestic": 
    df_f = df_f[df_f['is_domestic'] == True]

# ============================================================
# 3. ATTENTION DYNAMICS (English UI)
# ============================================================
st.markdown('<div class="section-header">Trends and Attention Dynamics</div>', unsafe_allow_html=True)

def calc_attention_metrics(data):
    if data.empty: 
        return 0.0, "N/A"
    
    daily_series = data.groupby('published_date').size().sort_index()
    if daily_series.empty: 
        return 0.0, "N/A"
    
    sorted_series = daily_series.sort_values(ascending=False)
    top_10_percent_days = max(1, int(len(sorted_series) * 0.1))
    burstiness = (sorted_series.head(top_10_percent_days).sum() / daily_series.sum()) * 100
    
    peak_date = sorted_series.idxmax()
    peak_val = sorted_series.max()
    
    post_peak_data = daily_series[daily_series.index >= peak_date]
    half_life_threshold = peak_val / 2
    decay_dates = post_peak_data[post_peak_data <= half_life_threshold]
    
    if not decay_dates.empty:
        half_life_val = (decay_dates.index[0] - peak_date).days
        half_life_str = f"{half_life_val} Days"
    else:
        max_tracked = (daily_series.index[-1] - peak_date).days
        half_life_str = f">{max_tracked} Days"
        
    return burstiness, half_life_str

burstiness, half_life = calc_attention_metrics(df_f)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Articles", len(df_f))
m2.metric("Burstiness Index", f"{burstiness:.1f}%", help="Does coverage cluster in a few days? A high percentage indicates highly concentrated media attention.")
m3.metric("Avg Emotional Tone", f"{df_f['sentiment_numeric'].mean():.2f}" if not df_f.empty else "N/A")
m4.metric("Attention Half-life", half_life, help="The speed at which media interest fades. Smaller values indicate faster dissipation.")

if not df_f.empty:
    st.plotly_chart(px.area(df_f.groupby('published_date').size().reset_index(name='Count'), x='published_date', y='Count', title="Daily Media Attention Spikes"), use_container_width=True)

# ============================================================
# 4. EMOTIONAL FRAMING (Scientific Interpretation)
# ============================================================
st.markdown('<div class="section-header">Emotional Framing Analysis</div>', unsafe_allow_html=True)

st.markdown("""
<div class="insight-box">
    <strong>Why study these emotions?</strong><br>
    The way media "packages" conflict shapes public perception. In this study:<br>
    • <b>Anger:</b> Adversarial framing. Typically points to faults of conflict parties or intense opposition.<br>
    • <b>Fear/Sadness:</b> Humanitarian/Victim framing. Focuses on suffering, threats, or losses resulting from the conflict.<br>
    • <b>Surprise/Joy:</b> Breakthrough or optimistic framing. May represent conflict de-escalation or unexpected positive developments.
</div>
""", unsafe_allow_html=True)

col1, col2 = st.columns(2)

exclude = ['neutral', 'others', 'other', 'label_1']
df_active = df_f[~df_f['emotion_label'].str.lower().isin(exclude)]

with col1:
    st.subheader("Tone Intensity by Event Type")
    if not df_f.empty:
        df_ev_sent = df_f.groupby('acled_event_type')['sentiment_numeric'].mean().sort_values().reset_index()
        fig_bar = px.bar(df_ev_sent, 
                         x='sentiment_numeric', y='acled_event_type', orientation='h', 
                         color='sentiment_numeric', color_continuous_scale='RdYlGn',
                         range_x=[-0.6, 0.4], # Optimized range to see variance near 0
                         labels={'sentiment_numeric': 'Aggregated Tone (Negative <---> Positive)'},
                         title="Tone Intensity per Conflict Category")
        st.plotly_chart(fig_bar, use_container_width=True)

with col2:
    st.subheader("Top 5 Emotion Distribution")
    total_len = len(df_f)
    neutral_count = len(df_f[df_f['emotion_label'].str.lower().isin(exclude)])
    neutral_perc = (neutral_count / total_len * 100) if total_len > 0 else 0
    
    st.markdown(f"""
    <div style='font-size: 0.9em; color: gray;'>
        ℹ️ <b>Neutral/Other coverage share: {neutral_perc:.1f}%</b><br>
        A high neutral share indicates that most reporting consists of factual statements. The chart below only shows the distribution of active emotions to reveal media sentiment tendencies.
    </div>
    """, unsafe_allow_html=True)
    
    if not df_active.empty:
        top_5_emotions = df_active['emotion_label'].value_counts().nlargest(5).index.tolist()
        df_active_top5 = df_active[df_active['emotion_label'].isin(top_5_emotions)]
        
        fig_pie = px.pie(df_active_top5, names='emotion_label', hole=0.4, 
                         title="Active Framing Breakdown (Top 5)",
                         color_discrete_sequence=px.colors.qualitative.Pastel)
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("No active emotional labels found in the selected range.")

# ============================================================
# 5. MEDIA OUTLET COMPARISON
# ============================================================
st.markdown('<div class="section-header">Media Outlet Comparison</div>', unsafe_allow_html=True)
st.write("Comparing institutional bias: Which of the Top 5 emotions do different media sources emphasize?")

top_outlets = df_f['source_name'].value_counts().head(10).index
df_top_outlets = df_f[df_f['source_name'].isin(top_outlets)]
df_top_active = df_top_outlets[~df_top_outlets['emotion_label'].str.lower().isin(exclude)]

tab_heatmap, tab_sentiment = st.tabs(["Institutional Emotion Profile", "Tone Variance Score"])

with tab_heatmap:
    st.subheader("Institutional Emotion Heatmap (Top 5 Emotions)")
    if not df_top_active.empty:
        top_5_global = df_active['emotion_label'].value_counts().nlargest(5).index.tolist()
        df_top_active_top5 = df_top_active[df_top_active['emotion_label'].isin(top_5_global)]
        
        if not df_top_active_top5.empty:
            ctab = pd.crosstab(df_top_active_top5['source_name'], df_top_active_top5['emotion_label'], normalize='index') * 100
            fig_heat = px.imshow(ctab, text_auto=".1f", aspect="auto",
                                 labels=dict(x="Top 5 Emotions", y="Media Outlet", color="Percentage (%)"),
                                 color_continuous_scale="Purples",
                                 title="Framing Choice by Outlet (Top 5 Active Emotions %)")
            st.plotly_chart(fig_heat, use_container_width=True)
        else:
            st.warning("No articles matching the Top 5 global emotions found for these outlets.")
    else:
        st.warning("Insufficient specific emotional data.")

with tab_sentiment:
    st.subheader("Average Tone Variance (Bias Check)")
    if not df_top_outlets.empty:
        df_outlet_avg = df_top_outlets.groupby('source_name')['sentiment_numeric'].agg(['mean', 'count']).reset_index()
        df_outlet_avg.columns = ['Media Outlet', 'Avg Tone Score', 'Article Volume']
        
        fig_outlet = px.scatter(df_outlet_avg, x='Avg Tone Score', y='Media Outlet', 
                                size='Article Volume', color='Avg Tone Score',
                                color_continuous_scale='RdBu', 
                                color_continuous_midpoint=0,
                                range_x=[-0.2, 0.2], # Zoomed in to see bias variance near zero
                                title="Outlet Positioning on the Emotional Spectrum",
                                labels={'Avg Tone Score': 'Intense/Negative Framing <---> Calm/Positive Framing'})
        fig_outlet.add_vline(x=df_f['sentiment_numeric'].mean(), line_dash="dash", line_color="gray", annotation_text="Market Avg")
        st.plotly_chart(fig_outlet, use_container_width=True)
    else:
        st.info("No media outlet data available for the current selection.")

# ============================================================
# 6. NARRATIVE DEEP DIVE (With On-Demand Translation)
# ============================================================
st.markdown('<div class="section-header">Narrative Discovery</div>', unsafe_allow_html=True)

st.markdown("""
<div style='background-color: #f5f5f5; padding: 10px; border-radius: 5px; margin-bottom: 10px; font-size: 0.9em;'>
    💡 <b>What is this?</b> This section uses algorithms to identify recurring storylines, answering: beyond macro categories like "Protests," what are the specific narrative focuses of the media?
</div>
""", unsafe_allow_html=True)

top_clusters = df_f['article_cluster_id'].value_counts().head(5)
if not top_clusters.empty:
    translate = st.checkbox("Enable Translation for Event Headlines")
    if translate:
        ts = get_translator()

    for cid, count in top_clusters.items():
        cluster_data = df_f[df_f['article_cluster_id'] == cid]
        if not cluster_data.empty:
            sample = cluster_data.iloc[0]
            label = sample.get('acled_event_type', 'Unknown')
            
            display_title = sample['title']
            if translate:
                try:
                    display_title = ts(display_title[:512])[0]['translation_text']
                except:
                    pass
                
            with st.expander(f"Event Cluster {cid}: {label} ({count} articles)"):
                st.write(f"**Headline:** {display_title}")
                if translate: 
                    st.caption(f"Original German: {sample['title']}")
                st.write(f"**Location:** {sample.get('detected_locations', 'Not specified')}")
                st.write(f"**Tone Intensity Score:** {sample.get('sentiment_numeric', 0):.2f}")
                st.progress(min(1.0, abs(sample.get('sentiment_numeric', 0))))

st.divider()
st.caption("Native German processing with English UI representation. The news were already preselected with conflict related news. Data source: processed_conflict_articles.csv")