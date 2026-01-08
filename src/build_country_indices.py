from pathlib import Path
import sqlite3
import pandas as pd
import numpy as np

import country_converter as coco
import pycountry
from functools import lru_cache

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_CONFLICT = PROJECT_ROOT / "data" / "conflict_data.db"
DB_MATCH = PROJECT_ROOT / "data" / "matching_country.db"

EPS = 1e-9

def minmax01(x):
    x = x.astype(float)
    lo, hi = x.min(), x.max()
    if (hi - lo) < EPS:
        return x * 0.0
    return (x - lo) / (hi - lo)

def harmonic_mean(a: pd.Series, b: pd.Series) -> pd.Series:
    a = a.fillna(0).astype(float)
    b = b.fillna(0).astype(float)
    hm = np.where((a > 0) & (b > 0), 2.0 / ((1.0 / (a + EPS)) + (1.0 / (b + EPS))), 0.0)
    return pd.Series(hm, index=a.index)


_cc = coco.CountryConverter()

# Small “known pain points” alias map (keep tiny; let the libs do the heavy lifting)
ALIASES = {
    "UK": "GBR",          # ISO alpha-2 is GB, not UK
    "UAE": "ARE",
    "Russia": "RUS",
    "South Korea": "KOR",
    "North Korea": "PRK",
}

@lru_cache(maxsize=10_000)
def country_name_to_iso3(name: str) -> str | None:
    if name is None:
        return None
    s = str(name).strip()
    if not s:
        return None

    # 0) quick aliases
    if s in ALIASES:
        return ALIASES[s]

    # 1) try country_converter (handles lots of variants)
    iso3 = _cc.convert(names=s, to="ISO3", not_found=None)
    # coco sometimes returns a string like "not found" depending on args; we forced None
    if iso3 and iso3 != "not found":
        return iso3

    # 2) fallback: pycountry fuzzy search (official ISO + approximate matching)
    try:
        hit = pycountry.countries.search_fuzzy(s)[0]
        return getattr(hit, "alpha_3", None)
    except LookupError:
        return None


def main():
    if not DB_CONFLICT.exists():
        raise FileNotFoundError(f"Missing DB: {DB_CONFLICT}")
    if not DB_MATCH.exists():
        raise FileNotFoundError(f"Missing DB: {DB_MATCH}")

    con_conf = sqlite3.connect(DB_CONFLICT)
    con_match = sqlite3.connect(DB_MATCH)

    conflict = pd.read_sql_query("""
        SELECT
            TRIM(country) AS country,
            SUM(n_events) AS n_events,
            SUM(total_fatalities) AS total_fatalities
        FROM conflict_country
        WHERE country IS NOT NULL AND TRIM(country) != ''
        GROUP BY TRIM(country);
    """, con_conf)

    coverage = pd.read_sql_query("""
        SELECT
            TRIM(country) AS country,
            SUM(n_articles) AS n_articles
        FROM coverage_country
        GROUP BY TRIM(country);
    """, con_match)

    con_match.close()

    df = conflict.merge(coverage, on="country", how="outer")

    df["n_events"] = df["n_events"].fillna(0)
    df["total_fatalities"] = df["total_fatalities"].fillna(0)
    df["n_articles"] = df["n_articles"].fillna(0)
    df["iso_a3"] = df["country"].map(country_name_to_iso3)

    # Shares
    df["share_events"] = df["n_events"] / df["n_events"].sum()
    df["share_fatalities"] = df["total_fatalities"] / df["total_fatalities"].sum()
    df["share_articles"] = df["n_articles"] / df["n_articles"].sum()

    # Indices
    df["conflict_index_raw"] = harmonic_mean(df["n_events"], df["total_fatalities"])
    df["conflict_index_scaled"] = minmax01(np.log1p(df["conflict_index_raw"]))

    total_articles = float(df["n_articles"].sum())
    df["coverage_index"] = (df["n_articles"] / total_articles) if total_articles > 0 else 0.0

    # Write final table
    cur = con_conf.cursor()
    cur.execute("DROP TABLE IF EXISTS country_indices;")

    df_out = df[[
        "country",
        "iso_a3",
        "share_events",
        "share_fatalities",
        "share_articles",
        "n_events",
        "total_fatalities",
        "n_articles",
        "conflict_index_raw",
        "conflict_index_scaled",
        "coverage_index",
    ]].copy()

    df_out.to_sql("country_indices", con_conf, index=False)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_country_indices_country ON country_indices(country);")

    con_conf.commit()
    con_conf.close()

    print(f"Wrote country_indices into {DB_CONFLICT}")

if __name__ == "__main__":
    main()
