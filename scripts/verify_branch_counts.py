"""
Diagnostic: verify FDIC branch-to-ward counts and check for a city-name
filtering gap.

fdic_branches_by_ward.csv was built by first filtering fdic_branches_EC.csv
to CITY == "Newark" (case-insensitive), then spatial-joining the survivors
against newark_wards.geojson. A bank's listed CITY can be wrong relative to
where its coordinates actually place it, so that ordering could both wrongly
exclude branches (real Newark coordinates, different listed city) and
wrongly include branches (listed as "Newark", coordinates outside all 5
wards) in any ward.

This script:
  1. Prints full rows for every branch in each of the 5 wards from the
     current fdic_branches_by_ward.csv.
  2. Re-joins ALL of fdic_branches_EC.csv (no city pre-filter) against the
     ward polygons by coordinates alone, and prints per-ward counts.
  3. Compares the two approaches side by side per ward.
  4. For any ward with a nonzero difference, prints the specific branches
     that differ and their actual CITY value.

Diagnostic only -- does not overwrite fdic_branches_by_ward.csv. Writes
data/processed/branch_filter_diagnostic.csv only if discrepancies are found.
"""

from pathlib import Path

import geopandas as gpd
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

WARDS_GEOJSON = RAW_DIR / "newark_wards.geojson"
FDIC_CSV = RAW_DIR / "fdic_branches_EC.csv"
BY_WARD_CSV = PROCESSED_DIR / "fdic_branches_by_ward.csv"
DIAGNOSTIC_OUTPUT = PROCESSED_DIR / "branch_filter_diagnostic.csv"

WARD_NAME_FIELD = "WARD_CODE"
WGS84 = "EPSG:4326"

DISPLAY_COLS = ["NAME", "ADDRESS", "CITY", "LATITUDE", "LONGITUDE", "ward"]


def load_wards() -> gpd.GeoDataFrame:
    wards = gpd.read_file(WARDS_GEOJSON)
    return wards[[WARD_NAME_FIELD, "geometry"]].rename(columns={WARD_NAME_FIELD: "ward"})


def points_to_gdf(df: pd.DataFrame, lon_col: str, lat_col: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df[lon_col], df[lat_col]), crs=WGS84
    )


def print_current_ward_rows(by_ward: pd.DataFrame, ward_names: list) -> None:
    print("=== Step 1: full rows per ward, from current fdic_branches_by_ward.csv ===\n")
    for ward in ward_names:
        rows = by_ward[by_ward["ward"] == ward]
        print(f"--- {ward} ward ({len(rows)} branches) ---")
        print(rows[DISPLAY_COLS].to_string(index=False))
        print()


def coordinates_only_join(wards: gpd.GeoDataFrame) -> pd.DataFrame:
    print("=== Step 2: coordinates-only join, all of fdic_branches_EC.csv (no city filter) ===\n")
    df = pd.read_csv(FDIC_CSV)
    print(f"Loaded {len(df)} Essex County branches (no CITY filter applied).")

    points = points_to_gdf(df, "LONGITUDE", "LATITUDE")
    joined = gpd.sjoin(points, wards, how="left", predicate="within").drop(
        columns=["index_right"], errors="ignore"
    )
    joined = pd.DataFrame(joined.drop(columns="geometry"))

    counts = joined["ward"].value_counts()
    print("\nCoordinates-only per-ward counts:")
    print(counts.to_string())
    print()
    return joined


def compare_counts(ward_names: list, by_ward: pd.DataFrame, coord_only: pd.DataFrame) -> pd.DataFrame:
    print("=== Step 3: comparison table ===\n")
    city_counts = by_ward["ward"].value_counts()
    coord_counts = coord_only["ward"].value_counts()

    comparison = pd.DataFrame({"ward": ward_names}).set_index("ward")
    comparison["count_from_city_filter"] = city_counts
    comparison["count_from_coordinates_only"] = coord_counts
    comparison = comparison.fillna(0).astype(int)
    comparison["difference"] = (
        comparison["count_from_coordinates_only"] - comparison["count_from_city_filter"]
    )

    print(comparison.to_string())
    print()
    return comparison


def find_discrepant_branches(
    ward_names: list, by_ward: pd.DataFrame, coord_only: pd.DataFrame
) -> pd.DataFrame:
    print("=== Step 4: branch-level discrepancies ===\n")

    city_ids = set(by_ward["ID"])
    coord_ids = set(coord_only[coord_only["ward"].notna()]["ID"])

    diagnostic_rows = []
    any_discrepancy = False

    for ward in ward_names:
        city_ward_ids = set(by_ward[by_ward["ward"] == ward]["ID"])
        coord_ward_ids = set(coord_only[coord_only["ward"] == ward]["ID"])

        if city_ward_ids == coord_ward_ids:
            continue

        any_discrepancy = True
        print(f"--- {ward} ward discrepancies ---")

        wrongly_excluded = coord_ward_ids - city_ward_ids
        if wrongly_excluded:
            rows = coord_only[coord_only["ID"].isin(wrongly_excluded)]
            print(f"Wrongly EXCLUDED from {ward} by the city-name filter "
                  f"(coordinates place them in {ward}, but CITY != Newark or excluded upstream):")
            print(rows[["NAME", "ADDRESS", "CITY", "LATITUDE", "LONGITUDE", "ward"]].to_string(index=False))
            for _, r in rows.iterrows():
                diagnostic_rows.append({
                    "ward": ward, "issue": "wrongly_excluded", "ID": r["ID"], "NAME": r["NAME"],
                    "ADDRESS": r["ADDRESS"], "CITY": r["CITY"], "LATITUDE": r["LATITUDE"],
                    "LONGITUDE": r["LONGITUDE"],
                })

        wrongly_included = city_ward_ids - coord_ward_ids
        if wrongly_included:
            rows = by_ward[by_ward["ID"].isin(wrongly_included)]
            print(f"Wrongly INCLUDED in {ward} by the city-name filter "
                  f"(listed CITY == Newark, but coordinates place them outside all 5 wards):")
            print(rows[["NAME", "ADDRESS", "CITY", "LATITUDE", "LONGITUDE", "ward"]].to_string(index=False))
            for _, r in rows.iterrows():
                diagnostic_rows.append({
                    "ward": ward, "issue": "wrongly_included", "ID": r["ID"], "NAME": r["NAME"],
                    "ADDRESS": r["ADDRESS"], "CITY": r["CITY"], "LATITUDE": r["LATITUDE"],
                    "LONGITUDE": r["LONGITUDE"],
                })
        print()

    if not any_discrepancy:
        print("No discrepancies found in any ward -- the two methods agree exactly.\n")

    return pd.DataFrame(diagnostic_rows)


def main() -> None:
    for path in (WARDS_GEOJSON, FDIC_CSV, BY_WARD_CSV):
        if not path.exists():
            raise SystemExit(f"ERROR: required input file missing: {path}")

    wards = load_wards()
    ward_names = sorted(wards["ward"])

    by_ward = pd.read_csv(BY_WARD_CSV)

    print_current_ward_rows(by_ward, ward_names)

    coord_only = coordinates_only_join(wards)

    compare_counts(ward_names, by_ward, coord_only)

    diagnostic = find_discrepant_branches(ward_names, by_ward, coord_only)

    if len(diagnostic):
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        diagnostic.to_csv(DIAGNOSTIC_OUTPUT, index=False)
        print(f"Wrote {len(diagnostic)} discrepant rows to {DIAGNOSTIC_OUTPUT}")
    else:
        print("No diagnostic file written (no discrepancies found).")


if __name__ == "__main__":
    main()
