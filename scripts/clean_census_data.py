"""
Clean and reshape the Census income (B19013) and rent-burden (B25070) data
for Essex County, NJ, and join both to tract boundary geometry.

Flow:
  1. Load data/raw/census_income_2024_essex.csv and data/raw/census_rentburden_essex.csv,
     both ACS 2024 5-Year Estimates at the Census Tract level, downloaded from
     data.census.gov in wide format (variables-as-rows, tracts-as-columns).
  2. Reshape each to long format (one row per tract), dropping the "United
     States" / "New Jersey" reference columns and deriving an 11-digit GEOID
     from each tract's column header.
  3. Income: convert "250,000+" to 250000 with an income_topcoded flag, and
     "-" / "**" (insufficient sample) to NaN, keeping the margin of error
     alongside.
  4. Rent burden: calculate pct_rent_burdened per tract from the bracket
     counts (30% or more of income spent on rent), excluding "Not computed"
     households from the denominator.
  5. Merge both tables on GEOID, then merge with
     data/raw/essex_tract_boundaries.geojson (via geopandas) to attach
     geometry.
  6. Save to data/processed/tract_income_rent.geojson.
"""

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

import census_utils as cu

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

INCOME_CSV = RAW_DIR / "census_income_2024_essex.csv"
RENTBURDEN_CSV = RAW_DIR / "census_rentburden_essex.csv"
BOUNDARIES_GEOJSON = RAW_DIR / "essex_tract_boundaries.geojson"
OUTPUT_PATH = PROCESSED_DIR / "tract_income_rent.geojson"

RENT_BURDENED_BRACKETS = [
    "30.0 to 34.9 percent",
    "35.0 to 39.9 percent",
    "40.0 to 49.9 percent",
    "50.0 percent or more",
]
NOT_COMPUTED_BRACKET = "Not computed"
TOTAL_LABEL = "Total:"


def clean_income(path: Path) -> pd.DataFrame:
    df = cu.load_wide_csv(path)
    df = cu.drop_reference_columns(df)
    df = df[df["label"].str.startswith("Median household income in the past 12 months")]

    result = cu.reshape_estimate_row(df, "median_household_income")
    result = result.rename(columns={"median_household_income_topcoded": "income_topcoded"})

    print(f"Income: reshaped {len(result)} tracts from wide to long.")
    print(f"Income: {result['median_household_income'].isna().sum()} tracts have null income (insufficient sample).")
    print(f"Income: {result['income_topcoded'].sum()} tracts are top-coded at $250,000+.")
    return result


def clean_rent_burden(path: Path) -> pd.DataFrame:
    df = cu.load_wide_csv(path)
    df = cu.drop_reference_columns(df)

    tract_cols = [c for c in df.columns if c != "label"]
    est_cols = [c for c in tract_cols if c.endswith("!!Estimate")]

    records = []
    for est_col in est_cols:
        tract_label = est_col[: -len("!!Estimate")]
        geoid = cu.tract_number_to_geoid(cu.extract_tract_number(tract_label))

        values, _ = cu.clean_numeric(df.set_index("label")[est_col])

        burdened_total = values.reindex(RENT_BURDENED_BRACKETS).sum(skipna=True)
        total_households = values.get(TOTAL_LABEL, float("nan"))
        not_computed = values.get(NOT_COMPUTED_BRACKET, 0)
        computed_total = total_households - not_computed if pd.notna(total_households) else float("nan")

        pct_burdened = (
            (burdened_total / computed_total) * 100
            if pd.notna(computed_total) and computed_total > 0
            else float("nan")
        )

        records.append(
            {
                "GEOID": geoid,
                "renter_households_total": total_households,
                "renter_households_computed": computed_total,
                "pct_rent_burdened": pct_burdened,
            }
        )

    result = pd.DataFrame.from_records(records)
    for col in ["renter_households_total", "renter_households_computed", "pct_rent_burdened"]:
        result[col] = pd.to_numeric(result[col], errors="coerce")
    print(f"Rent burden: reshaped {len(est_cols)} tracts from wide to long.")
    print(f"Rent burden: {result['pct_rent_burdened'].isna().sum()} tracts have null pct_rent_burdened (no computed renter households).")
    return result


def merge_with_boundaries(income: pd.DataFrame, rent_burden: pd.DataFrame) -> gpd.GeoDataFrame:
    merged = income.merge(rent_burden, on="GEOID", how="outer")
    print(f"Merged income + rent burden: {len(merged)} tracts.")

    boundaries = gpd.read_file(BOUNDARIES_GEOJSON)[["GEOID", "NAMELSAD", "geometry"]]
    boundaries["GEOID"] = boundaries["GEOID"].astype(str)
    print(f"Loaded {len(boundaries)} tract boundaries.")

    unmatched_data = set(merged["GEOID"]) - set(boundaries["GEOID"])
    unmatched_geo = set(boundaries["GEOID"]) - set(merged["GEOID"])
    if unmatched_data:
        print(f"WARNING: {len(unmatched_data)} tracts in census data have no matching boundary: {sorted(unmatched_data)}")
    if unmatched_geo:
        print(f"WARNING: {len(unmatched_geo)} tract boundaries have no matching census data: {sorted(unmatched_geo)}")

    result = boundaries.merge(merged, on="GEOID", how="left")
    print(f"Final joined GeoDataFrame: {len(result)} tracts.")
    return result


def main() -> None:
    if not INCOME_CSV.exists() or not RENTBURDEN_CSV.exists() or not BOUNDARIES_GEOJSON.exists():
        print("ERROR: one or more required input files are missing in data/raw/.")
        sys.exit(1)

    income = clean_income(INCOME_CSV)
    rent_burden = clean_rent_burden(RENTBURDEN_CSV)
    result = merge_with_boundaries(income, rent_burden)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    result.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(f"Save success: wrote {OUTPUT_PATH}")
    print(f"Feature count: {len(result)}")


if __name__ == "__main__":
    main()
