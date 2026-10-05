"""
Shared helpers for reshaping wide-format ACS CSVs from data.census.gov
(variables-as-rows, tracts-as-columns) into long format keyed by an
11-digit tract GEOID.

Used by clean_census_data.py (Stage 2: income + rent burden) and
compute_gentrification_metrics.py (Stage 6: income/rent growth).
"""

import re
from pathlib import Path

import pandas as pd

STATE_FIPS = "34"  # New Jersey
COUNTY_FIPS = "013"  # Essex County

INSUFFICIENT_SAMPLE_MARKERS = {"-", "**", "***", "N", "(X)"}
TOPCODE_VALUE = "250,000+"

# Matches "Census Tract 1" in either the semicolon-separated header format
# used by newer data.census.gov exports ("Census Tract 1; Essex County; New
# Jersey") or the comma-separated format used by older exports
# ("Census Tract 1, Essex County, New Jersey").
TRACT_NUMBER_RE = re.compile(r"Census Tract ([\d.]+)")


def tract_number_to_geoid(tract_number: str) -> str:
    """Convert a tract number like '1' or '42.01' to an 11-digit GEOID."""
    whole, _, frac = tract_number.strip().partition(".")
    tractce = whole.zfill(4) + (frac.ljust(2, "0")[:2] if frac else "00")
    return f"{STATE_FIPS}{COUNTY_FIPS}{tractce}"


def extract_tract_number(tract_label: str) -> str:
    """Pull the tract number out of a column header's tract portion."""
    match = TRACT_NUMBER_RE.match(tract_label.strip())
    if not match:
        raise ValueError(f"Could not find a tract number in label: {tract_label!r}")
    return match.group(1)


def load_wide_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    df = df.rename(columns={df.columns[0]: "label"})
    df["label"] = df["label"].astype(str).str.replace("\xa0", "", regex=False).str.strip()
    return df


def drop_reference_columns(df: pd.DataFrame) -> pd.DataFrame:
    keep = ["label"] + [
        c for c in df.columns[1:]
        if not c.startswith("United States") and not c.startswith("New Jersey")
    ]
    return df[keep]


def clean_numeric(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (numeric values, insufficient-sample flag) for an Estimate/MOE column."""
    raw = series.astype(str).str.strip()
    is_topcoded = raw == TOPCODE_VALUE
    is_insufficient = raw.isin(INSUFFICIENT_SAMPLE_MARKERS)
    cleaned = raw.str.replace(",", "", regex=False).str.replace("±", "", regex=False)
    cleaned = cleaned.mask(is_topcoded, "250000")
    cleaned = cleaned.mask(is_insufficient, pd.NA)
    numeric = pd.to_numeric(cleaned, errors="coerce")
    return numeric, is_topcoded


def reshape_estimate_row(df: pd.DataFrame, value_name: str) -> pd.DataFrame:
    """Reshape a single-line-item wide-format table (one variable, with an
    Estimate/MOE column pair per tract) to long format: one row per tract
    with GEOID, <value_name>, <value_name>_moe, and <value_name>_topcoded.

    `df` should already be filtered down to the row(s) for the desired
    variable (label filtering happens upstream, since label text can vary
    e.g. by inflation-adjustment year).
    """
    tract_cols = [c for c in df.columns if c != "label"]
    est_cols = [c for c in tract_cols if c.endswith("!!Estimate")]

    records = []
    for est_col in est_cols:
        tract_label = est_col[: -len("!!Estimate")]
        moe_col = f"{tract_label}!!Margin of Error"
        geoid = tract_number_to_geoid(extract_tract_number(tract_label))

        est_value, topcoded = clean_numeric(df[est_col])
        moe_value, _ = clean_numeric(df[moe_col])

        records.append(
            {
                "GEOID": geoid,
                value_name: est_value.iloc[0],
                f"{value_name}_moe": moe_value.iloc[0],
                f"{value_name}_topcoded": bool(topcoded.iloc[0]),
            }
        )

    return pd.DataFrame.from_records(records)
