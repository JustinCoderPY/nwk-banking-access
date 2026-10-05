"""
Restrict the FDIC branch, alt-finance, and GTFS bus-stop point datasets to
Newark and assign each point to one of the city's 5 wards via point-in-polygon
spatial join.

Flow:
  1. Load data/raw/newark_wards.geojson (5 ward polygons). The human-readable
     ward name lives in WARD_CODE ("Central", "East", "North", "South",
     "West") -- WARD_KEY is a compound key (county-muni-ward code) and
     MUN_NAME is just "Newark City" for every feature, so WARD_CODE is the
     field used consistently downstream.
  2. FDIC branches (data/raw/fdic_branches_EC.csv, county-wide): filter to
     CITY == "Newark" (case-insensitive), then sjoin(predicate="within")
     against the ward polygons using LONGITUDE/LATITUDE.
  3. Alt-finance businesses (data/processed/alt_finance_geocoded.csv, already
     Newark-only): sjoin against the ward polygons using longitude/latitude.
  4. GTFS bus stops (data/raw/gtfs-bus/stops.txt, NJ Transit system-wide):
     sjoin against the ward polygons using stop_lon/stop_lat and keep only
     stops that matched a ward (inner join) -- this is the Newark filter.
  5. Save each ward-tagged dataset to data/processed/*_by_ward.csv and print
     a final per-ward summary table of branch / alt-finance / transit-stop
     counts.
"""

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

WARDS_GEOJSON = RAW_DIR / "newark_wards.geojson"
FDIC_CSV = RAW_DIR / "fdic_branches_EC.csv"
ALT_FINANCE_CSV = PROCESSED_DIR / "alt_finance_geocoded.csv"
GTFS_STOPS_TXT = RAW_DIR / "gtfs-bus" / "stops.txt"

FDIC_OUTPUT = PROCESSED_DIR / "fdic_branches_by_ward.csv"
ALT_FINANCE_OUTPUT = PROCESSED_DIR / "alt_finance_by_ward.csv"
TRANSIT_OUTPUT = PROCESSED_DIR / "transit_stops_by_ward.csv"

WARD_NAME_FIELD = "WARD_CODE"
WGS84 = "EPSG:4326"


def load_wards(path: Path) -> gpd.GeoDataFrame:
    wards = gpd.read_file(path)
    print(f"Loaded {len(wards)} ward polygons: {sorted(wards[WARD_NAME_FIELD])}")
    return wards[[WARD_NAME_FIELD, "geometry"]].rename(columns={WARD_NAME_FIELD: "ward"})


def points_to_gdf(df: pd.DataFrame, lon_col: str, lat_col: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df[lon_col], df[lat_col]), crs=WGS84
    )


def join_to_wards(points: gpd.GeoDataFrame, wards: gpd.GeoDataFrame, how: str = "left") -> gpd.GeoDataFrame:
    joined = gpd.sjoin(points, wards, how=how, predicate="within")
    return joined.drop(columns=["index_right"], errors="ignore")


def process_fdic(wards: gpd.GeoDataFrame) -> pd.DataFrame:
    df = pd.read_csv(FDIC_CSV)
    newark = df[df["CITY"].str.strip().str.lower() == "newark"].copy()
    print(f"FDIC: {len(df)} branches in Essex County, {len(newark)} in Newark.")

    points = points_to_gdf(newark, "LONGITUDE", "LATITUDE")
    joined = join_to_wards(points, wards, how="left")

    matched = joined["ward"].notna().sum()
    unmatched = joined[joined["ward"].isna()]
    print(f"FDIC: {matched} of {len(joined)} Newark branches matched a ward.")
    if len(unmatched):
        print(f"WARNING: {len(unmatched)} Newark branches fell outside all 5 wards (check lat/long):")
        print(unmatched[["NAME", "ADDRESS", "LATITUDE", "LONGITUDE"]].to_string(index=False))

    return pd.DataFrame(joined.drop(columns="geometry"))


def process_alt_finance(wards: gpd.GeoDataFrame) -> pd.DataFrame:
    df = pd.read_csv(ALT_FINANCE_CSV)
    print(f"Alt-finance: {len(df)} geocoded points loaded.")

    points = points_to_gdf(df, "longitude", "latitude")
    joined = join_to_wards(points, wards, how="left")

    matched = joined["ward"].notna().sum()
    unmatched = joined[joined["ward"].isna()]
    print(f"Alt-finance: {matched} of {len(joined)} points matched a ward.")
    if len(unmatched):
        print(f"WARNING: {len(unmatched)} alt-finance points fell outside all 5 wards (possible geocoding error):")
        print(unmatched[["name", "address", "latitude", "longitude"]].to_string(index=False))

    return pd.DataFrame(joined.drop(columns="geometry"))


def process_transit(wards: gpd.GeoDataFrame) -> pd.DataFrame:
    df = pd.read_csv(GTFS_STOPS_TXT)
    print(f"Transit: {len(df)} GTFS stops loaded (system-wide).")

    points = points_to_gdf(df, "stop_lon", "stop_lat")
    joined = join_to_wards(points, wards, how="inner")
    print(f"Transit: {len(joined)} of {len(df)} stops fall within a Newark ward (kept).")

    return pd.DataFrame(joined.drop(columns="geometry"))


def print_ward_summary(wards: gpd.GeoDataFrame, fdic: pd.DataFrame, alt_finance: pd.DataFrame, transit: pd.DataFrame) -> None:
    ward_names = sorted(wards["ward"])
    summary = pd.DataFrame({"ward": ward_names}).set_index("ward")
    summary["bank_branches"] = fdic["ward"].value_counts()
    summary["alt_finance_businesses"] = alt_finance["ward"].value_counts()
    summary["transit_stops"] = transit["ward"].value_counts()
    summary = summary.fillna(0).astype(int)

    print("\n=== Per-ward summary ===")
    print(summary.to_string())


def main() -> None:
    for path in (WARDS_GEOJSON, FDIC_CSV, ALT_FINANCE_CSV, GTFS_STOPS_TXT):
        if not path.exists():
            print(f"ERROR: required input file missing: {path}")
            sys.exit(1)

    wards = load_wards(WARDS_GEOJSON)

    fdic = process_fdic(wards)
    alt_finance = process_alt_finance(wards)
    transit = process_transit(wards)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    fdic.to_csv(FDIC_OUTPUT, index=False)
    alt_finance.to_csv(ALT_FINANCE_OUTPUT, index=False)
    transit.to_csv(TRANSIT_OUTPUT, index=False)
    print(f"\nSaved {FDIC_OUTPUT}")
    print(f"Saved {ALT_FINANCE_OUTPUT}")
    print(f"Saved {TRANSIT_OUTPUT}")

    print_ward_summary(wards, fdic, alt_finance, transit)


if __name__ == "__main__":
    main()
