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

    if not name_col:
        raise KeyError("Natural Earth file does not contain a 'name' or 'admin' column for country names.")

    keep = [name_col, "geometry"]
    if iso_col:
        keep.insert(1, iso_col)

    world = world[keep].copy()
    world = world.rename(columns={name_col: "name"})
    if iso_col:
        world = world.rename(columns={iso_col: "iso_a3"})
    else:
        world["iso_a3"] = None

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


def build_geojson(world: gpd.GeoDataFrame, df_idx: pd.DataFrame, color_gamma: float = 0.6) -> tuple[dict, gpd.GeoDataFrame]:
    # Merge by country name (can be replaced with ISO3 mapping later)
    merged = world.merge(df_idx, left_on="name", right_on="country", how="left")

    # Only fill what we actually use for rendering
    merged["conflict_index_scaled"] = merged["conflict_index_scaled"].fillna(0.0)

    cov_nonnull = merged["coverage_index"].dropna()
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

    merged["fill_color"] = merged["coverage_index"].apply(cov_to_color)


    return json.loads(merged.to_json()), merged


st.title("Underrepresentation World Map")
st.caption("Height = conflict_index_scaled (extrusion). Color = coverage_index.")

df_idx = load_indices()
world = load_world()

# Controls
col1, col2 = st.columns([1, 2])

with col1:
    height_scale = st.slider(
        "Height scale",
        min_value=1_000,
        max_value=500_000,
        value=80_000,
        step=1_000,
        help="DeckGL elevation is in 'meters' visually; this is a multiplier.",
    )
    height_gamma = st.slider(
        "Height exponent (gamma)", 
        0.2, 2.0, 0.6, 0.05
    )
    color_gamma = st.slider(
        "Color exponent (gamma)",
        0.2, 2.0, 0.6, 0.05,
        help="Lower (<1) boosts contrast among low coverage values; higher (>1) compresses."
    )

with col2:
    pitch = st.slider("3D pitch", 0, 70, 45, 1)
    opacity = st.slider("Opacity", 0.1, 1.0, 0.9, 0.05)


geojson, merged = build_geojson(world, df_idx, color_gamma=color_gamma)

# compute elevation in Python (more robust than JS expressions)
merged["elevation"] = (merged["conflict_index_scaled"] ** height_gamma) * height_scale


layer = pdk.Layer(
    "GeoJsonLayer",
    data=json.loads(merged.to_json()),
    opacity=opacity,
    stroked=True,
    filled=True,
    extruded=True,
    wireframe=True,
    get_fill_color="properties.fill_color",
    get_line_color=[80, 80, 80, 120],
    get_elevation="properties.elevation",
    pickable=True,
)

view_state = pdk.ViewState(
    latitude=20,
    longitude=0,
    zoom=1.1,
    pitch=pitch,
)

tooltip = {
    "html": """
    <b>{name}</b><br/>
    conflict_index_scaled: {conflict_index_scaled}<br/>
    coverage_index: {coverage_index}<br/>
    n_events: {n_events}<br/>
    total_fatalities: {total_fatalities}<br/>
    n_articles: {n_articles}
    """,
    "style": {"backgroundColor": "white", "color": "black"},
}

deck = pdk.Deck(
    layers=[layer],
    initial_view_state=view_state,
    tooltip=tooltip,
    map_style=None,
)

st.pydeck_chart(deck, width="stretch")

st.subheader("Join diagnostics")
unmatched = (
    merged[merged["country"].isna()][["name", "iso_a3"]]
    .drop_duplicates()
    .sort_values("name")
)
st.write(f"Unmatched world polygons (likely naming differences): {len(unmatched)}")
st.dataframe(unmatched.head(50), use_container_width=True)

st.subheader("Top countries by conflict_index_scaled")
st.dataframe(
    df_idx.sort_values("conflict_index_scaled", ascending=False).head(30),
    use_container_width=True,
)
