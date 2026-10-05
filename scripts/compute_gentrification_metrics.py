"""
Compute income/rent growth (2019 -> 2024) and a gentrification-pressure
metric per ward.

Flow:
  1. Load data/raw/census_rent_2019_essex.csv, census_rent_2024_essex.csv,
     and census_income_2019_essex.csv (ACS 5-Year Estimates, Essex County,
     Census Tract level, same wide-format layout as Stage 2), reshaping
     each with the shared parsing logic in census_utils.py. The matching
     2024 income vintage is pulled from the tract_demographics table
     (built in Stage 5) rather than re-parsed from CSV.
  2. Join 2019 and 2024 rent by GEOID, and 2019 and 2024 income by GEOID,
     computing pct_rent_growth and pct_income_growth = (2024 - 2019) /
     2019 * 100. A tract missing either vintage is left null.
  3. Restrict to the 88 Newark tracts and attach ward using the
     centroid-based assignment already stored in tract_demographics
     (Stage 5) -- no spatial join is recomputed here.
  4. Aggregate to ward level: avg_pct_rent_growth, avg_pct_income_growth,
     and rent_growth_minus_income_growth (positive = rent outpacing
     income = gentrification-pressure signal).
  5. Update output/ward_summary.csv in place with the three new columns
     and write a tract_growth table to data/nwk_banking_access.db.
"""

import sqlite3
import sys
from pathlib import Path

import pandas as pd

import census_utils as cu

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
OUTPUT_DIR = REPO_ROOT / "output"

RENT_2019_CSV = RAW_DIR / "census_rent_2019_essex.csv"
RENT_2024_CSV = RAW_DIR / "census_rent_2024_essex.csv"
INCOME_2019_CSV = RAW_DIR / "census_income_2019_essex.csv"

DB_PATH = REPO_ROOT / "data" / "nwk_banking_access.db"
SUMMARY_CSV = OUTPUT_DIR / "ward_summary.csv"

NEWARK_TRACT_COUNT = 88


def load_variable(path: Path, label_prefix: str, value_name: str, year: int) -> pd.DataFrame:
    """Reshape a single-variable wide CSV to long format and suffix its
    value columns with the vintage year, so 2019/2024 columns don't collide
    on merge."""
    df = cu.load_wide_csv(path)
    df = cu.drop_reference_columns(df)
    df = df[df["label"].str.startswith(label_prefix)]

    result = cu.reshape_estimate_row(df, value_name)
    result = result.rename(
        columns={c: f"{c}_{year}" for c in result.columns if c != "GEOID"}
    )
    print(f"{value_name} {year}: reshaped {len(result)} tracts from wide to long.")
    return result[["GEOID", f"{value_name}_{year}"]]


def load_income_2024_from_db() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT GEOID, median_household_income FROM tract_demographics", conn)
    return df.rename(columns={"median_household_income": "median_household_income_2024"})


def load_newark_wards() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT GEOID, ward FROM tract_demographics", conn)
    return df


def compute_growth(df: pd.DataFrame, early_col: str, late_col: str, growth_col: str) -> pd.DataFrame:
    df = df.copy()
    df[growth_col] = (df[late_col] - df[early_col]) / df[early_col] * 100
    return df


def build_tract_growth() -> pd.DataFrame:
    rent_2019 = load_variable(RENT_2019_CSV, "Median gross rent", "median_gross_rent", 2019)
    rent_2024 = load_variable(RENT_2024_CSV, "Median gross rent", "median_gross_rent", 2024)
    income_2019 = load_variable(
        INCOME_2019_CSV, "Median household income in the past 12 months", "median_household_income", 2019
    )
    income_2024 = load_income_2024_from_db()

    rent = rent_2019.merge(rent_2024, on="GEOID", how="outer")
    rent_overlap = (rent["median_gross_rent_2019"].notna() & rent["median_gross_rent_2024"].notna()).sum()
    print(
        f"Rent: {len(rent_2019)} tracts in 2019, {len(rent_2024)} tracts in 2024, "
        f"{rent_overlap} tracts (Essex County-wide) have both vintages by GEOID."
    )
    rent = compute_growth(rent, "median_gross_rent_2019", "median_gross_rent_2024", "pct_rent_growth")

    income = income_2019.merge(income_2024, on="GEOID", how="outer")
    income = compute_growth(
        income, "median_household_income_2019", "median_household_income_2024", "pct_income_growth"
    )

    tract_growth = rent.merge(income, on="GEOID", how="outer")
    return tract_growth


def restrict_to_newark(tract_growth: pd.DataFrame) -> pd.DataFrame:
    newark = load_newark_wards()
    newark_growth = newark.merge(tract_growth, on="GEOID", how="left")

    n_rent = newark_growth["pct_rent_growth"].notna().sum()
    n_income = newark_growth["pct_income_growth"].notna().sum()
    n_both = (newark_growth["pct_rent_growth"].notna() & newark_growth["pct_income_growth"].notna()).sum()

    print(
        f"\nOf the {NEWARK_TRACT_COUNT} Newark tracts: {n_rent} have both rent vintages, "
        f"{n_income} have both income vintages, {n_both} have both metrics available."
    )
    dropout = NEWARK_TRACT_COUNT - n_both
    if dropout > NEWARK_TRACT_COUNT * 0.1:
        print(
            f"WARNING: {dropout} of {NEWARK_TRACT_COUNT} Newark tracts are missing growth data "
            "-- this is a larger dropout than expected and likely reflects GEOID mismatches from "
            "tract boundary revisions between the 2019 and 2024 vintages (see Stage 2 caveat)."
        )
    return newark_growth


def aggregate_to_wards(newark_growth: pd.DataFrame) -> pd.DataFrame:
    ward_growth = (
        newark_growth.groupby("ward")
        .agg(
            avg_pct_rent_growth=("pct_rent_growth", "mean"),
            avg_pct_income_growth=("pct_income_growth", "mean"),
        )
        .reset_index()
    )
    ward_growth["rent_growth_minus_income_growth"] = (
        ward_growth["avg_pct_rent_growth"] - ward_growth["avg_pct_income_growth"]
    )
    return ward_growth


def update_ward_summary(ward_growth: pd.DataFrame) -> pd.DataFrame:
    summary = pd.read_csv(SUMMARY_CSV)
    new_cols = ["avg_pct_rent_growth", "avg_pct_income_growth", "rent_growth_minus_income_growth"]
    summary = summary.drop(columns=[c for c in new_cols if c in summary.columns])
    summary = summary.merge(ward_growth, on="ward", how="left")
    summary.to_csv(SUMMARY_CSV, index=False)
    print(f"\nUpdated {SUMMARY_CSV}")
    return summary


def save_tract_growth_table(newark_growth: pd.DataFrame) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        newark_growth.to_sql("tract_growth", conn, index=False, if_exists="replace")
    print(f"Wrote tract_growth table ({len(newark_growth)} rows) to {DB_PATH}")


def main() -> None:
    for path in (RENT_2019_CSV, RENT_2024_CSV, INCOME_2019_CSV, DB_PATH, SUMMARY_CSV):
        if not path.exists():
            print(f"ERROR: required input file missing: {path}")
            sys.exit(1)

    tract_growth = build_tract_growth()
    newark_growth = restrict_to_newark(tract_growth)
    ward_growth = aggregate_to_wards(newark_growth)

    save_tract_growth_table(newark_growth)
    summary = update_ward_summary(ward_growth)

    print("\n=== Updated ward summary ===")
    print(summary.sort_values("rent_growth_minus_income_growth", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
