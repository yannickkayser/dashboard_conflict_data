from pathlib import Path
import sqlite3
import json
import urllib.request

import numpy as np
import pandas as pd
import streamlit as st
import pydeck as pdk
import geopandas as gpd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_CONFLICT = PROJECT_ROOT / "data" / "conflict_data.db"

st.set_page_config(page_title="Underrepresentation Map", layout="wide")


@st.cache_data
def load_indices() -> pd.DataFrame:
    con = sqlite3.connect(DB_CONFLICT)
    df = pd.read_sql_query("SELECT * FROM country_indices;", con)
    con.close()
    return df


@st.cache_data
def load_world() -> gpd.GeoDataFrame:
    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(exist_ok=True)

    # Natural Earth Admin 0 countries (110m) zip (hosted by NACIS CDN)
    url = "https://naciscdn.org/naturalearth/110m/cultural/ne_110m_admin_0_countries.zip"
    local_zip = data_dir / "ne_110m_admin_0_countries.zip"

    if not local_zip.exists():
        urllib.request.urlretrieve(url, local_zip)

    world = gpd.read_file(local_zip)

    # Normalize likely column names
    cols = {c.lower(): c for c in world.columns}
    name_col = cols.get("name") or cols.get("admin")
    iso_col = cols.get("iso_a3")
    adm0_col = cols.get("adm0_a3")
    sov_col  = cols.get("sov_a3")

    if not name_col:
        raise KeyError("Natural Earth file does not contain a 'name' or 'admin' column for country names.")

    keep = [name_col, "geometry"]
    if iso_col: keep.insert(1, iso_col)
    if adm0_col: keep.insert(2, adm0_col)
    if sov_col:  keep.insert(3, sov_col)
        
    
    world = world[keep].copy()
    world = world.rename(columns={name_col: "name"})
    if iso_col:
        world = world.rename(columns={iso_col: "iso_a3"})
    else:
        world["iso_a3"] = None
    if adm0_col: world = world.rename(columns={adm0_col: "adm0_a3"})
    if sov_col:  world = world.rename(columns={sov_col: "sov_a3"})

    world["iso_a3"] = world["iso_a3"].astype(str)

    # Fix Natural Earth -99 placeholders
    if "adm0_a3" in world.columns:
        m = world["iso_a3"].eq("-99")
        world.loc[m, "iso_a3"] = world.loc[m, "adm0_a3"].astype(str)

    if "sov_a3" in world.columns:
        m = world["iso_a3"].eq("-99")
        world.loc[m, "iso_a3"] = world.loc[m, "sov_a3"].astype(str)

    # Optional normalization for Kosovo (depends what your DB uses)
    world["iso_a3"] = world["iso_a3"].replace({"KOS": "XKX"})

    world = world.set_crs(epsg=4326, allow_override=True).to_crs(epsg=4326)
    return world


def coverage_to_rgba(cov: float) -> list[int]:
    """
    Map coverage_index in [0,1] to an RGBA color.
    Low coverage -> greenish, high coverage -> reddish.
    Missing/invalid -> grey.
    """
    if cov is None or not np.isfinite(cov):
        return [200, 200, 200, 120]
    cov = float(np.clip(cov, 0.0, 1.0))
    r = int(255 * cov)
    g = int(255 * (1.0 - cov))
    b = 80
    a = 180
    return [r, g, b, a]


def build_geojson(world: gpd.GeoDataFrame, df_plot: pd.DataFrame, color_gamma: float = 0.6) -> tuple[dict, gpd.GeoDataFrame]:
    # Merge by ISO3 code
    merged = world.merge(df_plot, on="iso_a3", how="left")

    # Only fill what we actually use for rendering
    merged["severity_share"] = merged["severity_share"].fillna(0.0)

    cov_nonnull = merged["share_articles"].dropna()
    if cov_nonnull.empty:
        merged["fill_color"] = [[200, 200, 200, 120]] * len(merged)
        return json.loads(merged.to_json()), merged

    eps = 1e-6
    vals = np.log10(cov_nonnull + eps)
    lo, hi = vals.quantile([0.05, 0.95])
    hi = max(hi, lo + 1e-9)

    def cov_to_color(cov: float) -> list[int]:
        if cov is None or not np.isfinite(cov):
            return [200, 200, 200, 120]
        x = (np.log10(cov + eps) - lo) / (hi - lo)
        x = float(np.clip(x, 0.0, 1.0))
        x = x ** color_gamma

        # x=0 (low coverage) -> pink, x=1 (high coverage) -> gray
        pink = np.array([255, 105, 180], dtype=float)
        gray = np.array([180, 180, 180], dtype=float)
        rgb = (1.0 - x) * pink + x * gray

        return [int(rgb[0]), int(rgb[1]), int(rgb[2]), 200]

    merged["fill_color"] = merged["share_articles"].apply(cov_to_color)


    return json.loads(merged.to_json()), merged

def gap_to_color(g: float, clip: float = 0.01, gamma: float = 1.0) -> list[int]:
    if g is None or not np.isfinite(g):
        return [200, 200, 200, 120]
    g = float(np.clip(g, -clip, clip)) / clip  # -> [-1, 1]
    g = np.sign(g) * (abs(g) ** gamma)

    grey = np.array([180, 180, 180], dtype=float)
    pink = np.array([255, 105, 180], dtype=float)  # undercovered
    blue = np.array([80, 130, 255], dtype=float)   # overcovered

    if g < 0:
        t = -g
        rgb = (1 - t) * grey + t * pink
    else:
        t = g
        rgb = (1 - t) * grey + t * blue

    return [int(rgb[0]), int(rgb[1]), int(rgb[2]), 200]


def build_geojson_underrep(world: gpd.GeoDataFrame, df_plot: pd.DataFrame, clip: float, gamma: float) -> tuple[dict, gpd.GeoDataFrame]:
    merged = world.merge(df_plot, on="iso_a3", how="left")
    merged["underrep_share"] = pd.to_numeric(merged["underrep_share"], errors="coerce")
    merged["fill_color"] = merged["underrep_share"].apply(lambda x: gap_to_color(x, clip=clip, gamma=gamma))
    return json.loads(merged.to_json()), merged


st.title("Underrepresentation World Map")
st.caption("Height = conflict_index_scaled (extrusion). Color = coverage_index.")

#df_idx = load_indices()
df_plot = load_indices()
world = load_world()

# Global controls
st.sidebar.header("Global metric")
w = st.sidebar.slider(
    "Weight on fatalities (w)",
    0.0, 1.0, 0.5, 0.05,
    help="0 = only events share, 1 = only fatalities share"
)

# compute once, right after w exists
df_plot["severity_share"] = (1 - w) * df_plot["share_events"] + w * df_plot["share_fatalities"]
df_plot["underrep_share"] = df_plot["share_articles"] - df_plot["severity_share"]


# STRUCTURE OF PAGE IN TABS
tab2d, tab3d = st.tabs(["2D Map", "3D Map"])

with tab2d:
    st.subheader("2D map: Under/Overrepresentation")
    st.caption("Color = share_articles − severity_share (negative = undercovered)")

    # Controls
    clip = st.slider("2D color clip", 
                     min_value=0.001, 
                     max_value=0.1, 
                     value=0.01, 
                     step=0.01,
                     help="Values beyond ±clip are saturated to the max color.")
    
    gamma = st.slider("2D color gamma", 
                      min_value=0.2, 
                      max_value=2.0, 
                      value=1.0, 
                      step=0.05,
                      help="Lower (<1) boosts contrast among low coverage values; higher (>1) compresses.")

    geojson2d, merged2d = build_geojson_underrep(world, df_plot, clip=clip, gamma=gamma)

    layer2d = pdk.Layer(
        "GeoJsonLayer",
        data=geojson2d,
        stroked=True,
        filled=True,
        extruded=False,
        get_fill_color="properties.fill_color",
        get_line_color=[80, 80, 80, 80],
        pickable=True,
    )

    view_state_2d = pdk.ViewState(latitude=20, longitude=0, zoom=1.1, pitch=0)

    tooltip2d = {
        "html": """
        <b>{name}</b><br/>
        underrep_share: {underrep_share}<br/>
        severity_share: {severity_share}<br/>
        share_articles: {share_articles}<br/>
        share_events: {share_events}<br/>
        share_fatalities: {share_fatalities}
        """,
        "style": {"backgroundColor": "white", "color": "black"},
    }

    st.pydeck_chart(
        pdk.Deck(layers=[layer2d], initial_view_state=view_state_2d, tooltip=tooltip2d, map_style=None),
        width="stretch",
    )


with tab3d:

    # Controls
    col1, col2 = st.columns([1, 2])

    with col1:
        height_scale = st.slider(
            "Height scale",
            min_value=100_000,
            max_value=5_000_000,
            value=3_000_000,
            step=100_000,
            help="DeckGL elevation is in 'meters' visually; this is a multiplier.",
        )
        height_gamma = st.slider(
            "Height exponent (gamma)", 
            min_value=0.2, 
            max_value=2.0, 
            value=0.5, 
            step=0.05,
            help="Lower (<1) boosts contrast among low severity values; higher (>1) compresses."
        )
        color_gamma = st.slider(
            "Color exponent (gamma)",
            min_value=0.2, 
            max_value=2.0, 
            value=1.0, 
            step=0.05,
            help="Lower (<1) boosts contrast among low coverage values; higher (>1) compresses."
        )

    with col2:
        pitch = st.slider("3D pitch", 0, 70, 45, 1)
        opacity = st.slider("Opacity", 0.1, 1.0, 0.9, 0.05)


    geojson, merged = build_geojson(world, df_plot, color_gamma=color_gamma)

    # compute elevation in Python (more robust than JS expressions)
    merged["elevation"] = (merged["severity_share"] ** height_gamma) * height_scale


    layer = pdk.Layer(
        "GeoJsonLayer",
        data=json.loads(merged.to_json()),
        opacity=opacity,
        stroked=True,
        filled=True,
        extruded=True,
        wireframe=True,
        get_fill_color="properties.fill_color",

        # stronger borders for 3D
        get_line_color=[20, 20, 20, 220],
        line_width_min_pixels=1,
        line_width_max_pixels=3,

        get_elevation="properties.elevation",
        pickable=True,
    )

    view_state = pdk.ViewState(
        latitude=20,
        longitude=0,
        zoom=1.1,
        pitch=pitch,
    )

    tooltip3d = {
        "html": """
        <b>{name}</b><br/>
        severity_share (height): {severity_share}<br/>
        share_articles (color): {share_articles}<br/>
        share_events: {share_events}<br/>
        share_fatalities: {share_fatalities}<br/>
        underrep_share: {underrep_share}
        """,
        "style": {"backgroundColor": "white", "color": "black"},
        }

    deck = pdk.Deck(
        layers=[layer],
        initial_view_state=view_state,
        tooltip=tooltip3d,
        map_style=None,
    )

    st.pydeck_chart(deck, width="stretch")


#st.subheader("Join diagnostics")
#unmatched = (
#    merged[merged["country"].isna()][["name", "iso_a3"]]
#    .drop_duplicates()
#    .sort_values("name")
#)
#st.write(f"Unmatched world polygons (likely naming differences): {len(unmatched)}")
#st.dataframe(unmatched.head(50), use_container_width=True)

#st.write("DB iso_a3 for Kosovo:",
#         df_idx[df_idx["country"].str.contains("Kosovo", na=False)][["country","iso_a3"]].drop_duplicates())

#st.write("Natural Earth iso for Kosovo:",
#         world[world["name"].str.contains("Kosovo", na=False)][["name","iso_a3"]].drop_duplicates())

st.subheader("Top 10 countries by severity_share")
st.dataframe(
    df_plot.sort_values("severity_share", ascending=False).head(10),
    use_container_width=True,
)
