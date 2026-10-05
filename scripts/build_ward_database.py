"""
Assign Essex County census tracts to Newark's 5 wards and build the final
SQLite database + per-ward summary table for Tableau.

Flow:
  1. Load data/processed/tract_income_rent.geojson (211 Essex County tracts
     with median_household_income and pct_rent_burdened). Reproject to a
     local projected CRS to compute an accurate centroid per tract, then
     sjoin those centroids (predicate="within") against
     data/raw/newark_wards.geojson. Tracts whose centroid falls outside all
     5 wards are not Newark tracts and are dropped; the drop count is
     printed.
  2. Build data/nwk_banking_access.db (SQLite) with four tables:
     fdic_branches, alt_finance, transit_stops, tract_demographics --
     loaded directly from the Stage 4/5 processed CSVs/GeoDataFrame, with
     geometry columns dropped (no longer needed once ward assignment is
     done).
  3. Run a SQL query aggregating all four tables by ward into the final
     summary: ward, bank_branch_count, alt_finance_count,
     alt_finance_to_bank_ratio, transit_stop_count, avg_median_income,
     avg_pct_rent_burdened, tract_count.
  4. Save the summary to output/ward_summary.csv and print it to console.
"""

import sqlite3
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
OUTPUT_DIR = REPO_ROOT / "output"

WARDS_GEOJSON = RAW_DIR / "newark_wards.geojson"
TRACTS_GEOJSON = PROCESSED_DIR / "tract_income_rent.geojson"
FDIC_CSV = PROCESSED_DIR / "fdic_branches_by_ward.csv"
ALT_FINANCE_CSV = PROCESSED_DIR / "alt_finance_by_ward.csv"
TRANSIT_CSV = PROCESSED_DIR / "transit_stops_by_ward.csv"

DB_PATH = REPO_ROOT / "data" / "nwk_banking_access.db"
SUMMARY_CSV = OUTPUT_DIR / "ward_summary.csv"

WARD_NAME_FIELD = "WARD_CODE"
WGS84 = "EPSG:4326"


def load_wards(path: Path) -> gpd.GeoDataFrame:
    wards = gpd.read_file(path)
    if wards.crs is None or wards.crs.to_string() != WGS84:
        wards = wards.to_crs(WGS84)
    print(f"Loaded {len(wards)} ward polygons: {sorted(wards[WARD_NAME_FIELD])}")
    return wards[[WARD_NAME_FIELD, "geometry"]].rename(columns={WARD_NAME_FIELD: "ward"})


def assign_tracts_to_wards(wards: gpd.GeoDataFrame) -> pd.DataFrame:
    tracts = gpd.read_file(TRACTS_GEOJSON)
    print(f"Tracts: {len(tracts)} Essex County tracts loaded.")

    # Compute centroids in a projected (local UTM) CRS for accuracy, then
    # bring the centroid points back to WGS84 to match the ward polygons.
    projected = tracts.to_crs(tracts.estimate_utm_crs())
    centroids = projected.geometry.centroid.to_crs(WGS84)
    centroid_points = gpd.GeoDataFrame(
        tracts.drop(columns="geometry"), geometry=centroids, crs=WGS84
    )

    joined = gpd.sjoin(centroid_points, wards, how="left", predicate="within")
    joined = joined.drop(columns=["index_right"], errors="ignore")

    newark_tracts = joined[joined["ward"].notna()].copy()
    dropped = len(joined) - len(newark_tracts)
    print(
        f"Tracts: {len(newark_tracts)} of {len(joined)} tract centroids fall "
        f"within a Newark ward. Dropped {dropped} non-Newark tracts."
    )

    return pd.DataFrame(newark_tracts.drop(columns="geometry"))


def build_database(
    fdic: pd.DataFrame,
    alt_finance: pd.DataFrame,
    transit: pd.DataFrame,
    tract_demographics: pd.DataFrame,
) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    with sqlite3.connect(DB_PATH) as conn:
        fdic.to_sql("fdic_branches", conn, index=False)
        alt_finance.to_sql("alt_finance", conn, index=False)
        transit.to_sql("transit_stops", conn, index=False)
        tract_demographics.to_sql("tract_demographics", conn, index=False)

    print(f"\nBuilt SQLite database at {DB_PATH}")


SUMMARY_QUERY = """
SELECT
    w.ward AS ward,
    COALESCE(b.bank_branch_count, 0) AS bank_branch_count,
    COALESCE(a.alt_finance_count, 0) AS alt_finance_count,
    CAST(COALESCE(a.alt_finance_count, 0) AS REAL)
        / NULLIF(b.bank_branch_count, 0) AS alt_finance_to_bank_ratio,
    COALESCE(t.transit_stop_count, 0) AS transit_stop_count,
    d.avg_median_income AS avg_median_income,
    d.avg_pct_rent_burdened AS avg_pct_rent_burdened,
    COALESCE(d.tract_count, 0) AS tract_count
FROM (SELECT DISTINCT ward FROM tract_demographics) w
LEFT JOIN (
    SELECT ward, COUNT(*) AS bank_branch_count
    FROM fdic_branches GROUP BY ward
) b ON b.ward = w.ward
LEFT JOIN (
    SELECT ward, COUNT(*) AS alt_finance_count
    FROM alt_finance GROUP BY ward
) a ON a.ward = w.ward
LEFT JOIN (
    SELECT ward, COUNT(*) AS transit_stop_count
    FROM transit_stops GROUP BY ward
) t ON t.ward = w.ward
LEFT JOIN (
    SELECT
        ward,
        AVG(median_household_income) AS avg_median_income,
        AVG(pct_rent_burdened) AS avg_pct_rent_burdened,
        COUNT(*) AS tract_count
    FROM tract_demographics GROUP BY ward
) d ON d.ward = w.ward
ORDER BY w.ward;
"""


def build_summary() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        summary = pd.read_sql(SUMMARY_QUERY, conn)
    return summary


def main() -> None:
    for path in (WARDS_GEOJSON, TRACTS_GEOJSON, FDIC_CSV, ALT_FINANCE_CSV, TRANSIT_CSV):
        if not path.exists():
            print(f"ERROR: required input file missing: {path}")
            sys.exit(1)

    wards = load_wards(WARDS_GEOJSON)
    tract_demographics = assign_tracts_to_wards(wards)

    fdic = pd.read_csv(FDIC_CSV)
    alt_finance = pd.read_csv(ALT_FINANCE_CSV)
    transit = pd.read_csv(TRANSIT_CSV)

    build_database(fdic, alt_finance, transit, tract_demographics)

    summary = build_summary()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary.to_csv(SUMMARY_CSV, index=False)
    print(f"Saved {SUMMARY_CSV}")

    print("\n=== Per-ward summary ===")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
