"""
Add community-chartered credit unions to the Newark banking-access dataset,
filtered by NCUA field-of-membership (FOM) type.

Flow:
  1. Load the three raw NCUA files and print their columns + a few sample
     rows first (nothing about their schema was assumed in advance):
       - data/raw/ncua_branches_2026q2.txt: national branch/site directory,
         one row per physical address, keyed by CU_NUMBER (+ SiteId).
       - data/raw/ncua_foicu_2026q2.txt: one row per credit union (keyed by
         CU_NUMBER) with a TOM_CODE field ("the credit union field of
         membership").
       - data/raw/ncua_foicudes_2026q2.txt: turns out to be a *field*
         dictionary (one row per column name + description across both
         files), not a value-level decode table -- it explains that
         TOM_CODE exists, but not what "34" or "53" mean. NCUA does not
         ship a value decode for TOM_CODE in these quarterly extracts, so
         the code -> FOM-category mapping below (TOM_CODE_MAP) is sourced
         from NCUA's published Type-of-Membership (TOM) code scheme
         (corroborated by two independent secondary write-ups: Callahan
         Associates' and CU Collaborate's TOM-code references). It checks
         out against this data: American Airlines FCU's Newark branch is
         TOM_CODE 22 (occupational/single-employer) and Local 1233's is 03
         (associational) -- exactly the FOM types their real-world charters
         are known to be.
  2. Join branches -> FOICU on CU_NUMBER, decode TOM_CODE into a
     human-readable fom_type label, and classify community_chartered =
     True only for the "Community" codes (00 / 66 / 77). Everything else
     (Occupational, Associational, Multiple Common Bond) is False. Rows
     with TOM_CODE 99 (state-chartered; NCUA only collects TOM codes from
     federally-chartered CUs) can't be confirmed as community charters
     either, so they're also False -- but kept visible in the restricted
     output rather than silently dropped, exactly like the other
     restricted types.
  3. Filter to New Jersey, then to PhysicalAddressCity == "Newark" --
     same approach as the FDIC filtering in spatial_join_wards.py (a naive
     city-name-only filter would also catch Newark, DE / OH / NY, which is
     why the state filter has to come first).
  4. Geocode every Newark branch (the raw file has no lat/long columns at
     all) using the same Nominatim/RateLimiter pattern as
     geocode_alt_finance.py.
  5. Spatial-join *all* Newark credit union branches (community and
     restricted) against newark_wards.geojson, same pattern as
     spatial_join_wards.py, so restricted branches can still be reported
     with a ward.
  6. Add credit_union_count (community-chartered only) and a recomputed
     total_financial_access_count (bank branches + community-chartered
     credit unions) to output/ward_summary.csv.
  7. Print the restricted-membership credit unions found in Newark
     (name, ward, FOM type) without folding them into any ward total --
     a closed-membership credit union building is still a real building,
     just not a public banking-access point.
  8. Save data/processed/credit_unions_by_ward.csv (community-chartered)
     and data/processed/restricted_credit_unions_newark.csv (excluded).
"""

import re
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
OUTPUT_DIR = REPO_ROOT / "output"

BRANCHES_TXT = RAW_DIR / "ncua_branches_2026q2.txt"
FOICU_TXT = RAW_DIR / "ncua_foicu_2026q2.txt"
FOICUDES_TXT = RAW_DIR / "ncua_foicudes_2026q2.txt"
WARDS_GEOJSON = RAW_DIR / "newark_wards.geojson"
SUMMARY_CSV = OUTPUT_DIR / "ward_summary.csv"

COMMUNITY_OUTPUT = PROCESSED_DIR / "credit_unions_by_ward.csv"
RESTRICTED_OUTPUT = PROCESSED_DIR / "restricted_credit_unions_newark.csv"

WARD_NAME_FIELD = "WARD_CODE"
WGS84 = "EPSG:4326"

# Rough bounding box for Newark, NJ (same as geocode_alt_finance.py).
NEWARK_LAT_MIN, NEWARK_LAT_MAX = 40.66, 40.80
NEWARK_LON_MIN, NEWARK_LON_MAX = -74.26, -74.12

# Trailing floor/suite fragment, e.g. "614 Frelinghuysen Ave Fl 1" -> "614
# Frelinghuysen Ave" (same idea as geocode_alt_finance.py's UNIT_SUFFIX_RE).
UNIT_SUFFIX_RE = re.compile(r"\s+(?:Fl|Floor|Ste|Suite|Unit|#)\s*\d+[A-Za-z]?$", re.IGNORECASE)

# NCUA Type-of-Membership (TOM) code -> (fom_type label, is_community).
# See module docstring for why this isn't sourced directly from
# ncua_foicudes_2026q2.txt.
TOM_CODE_MAP = {
    "00": ("Community", True),
    "66": ("Community - Rural District", True),
    "77": ("Community - CBSA(+) Adjacent", True),
    "01": ("Associational - Faith-Based", False),
    "02": ("Associational - Fraternal", False),
    "03": ("Associational - Other", False),
    "04": ("Occupational - Educational", False),
    "05": ("Occupational - Military", False),
    "06": ("Occupational - Federal/State/Local Government", False),
    "98": ("Multiple Common Bond - Other", False),
    "99": ("Unknown / State-Chartered (TOM code not federally reported)", False),
}
# 10-15 Manufacturing, 20-24 Service industries: both single-common-bond
# occupational categories. 34-54: Multiple Common Bond (grouped by
# industry/affiliation) per the same TOM code scheme.
MANUFACTURING_RANGE = range(10, 16)
SERVICE_RANGE = range(20, 25)
MCB_RANGE = range(34, 55)


def decode_tom_code(raw_code) -> tuple:
    code = str(raw_code).strip()
    if code in TOM_CODE_MAP:
        return TOM_CODE_MAP[code]
    try:
        code_int = int(code)
    except ValueError:
        return (f"Unknown TOM Code ({code!r})", False)

    if code_int in MANUFACTURING_RANGE:
        return (f"Occupational - Manufacturing (TOM {code})", False)
    if code_int in SERVICE_RANGE:
        return (f"Occupational - Service Industry (TOM {code})", False)
    if code_int in MCB_RANGE:
        return (f"Multiple Common Bond (TOM {code})", False)
    return (f"Unknown TOM Code ({code})", False)


def load_raw_files() -> tuple:
    branches = pd.read_csv(BRANCHES_TXT, dtype={"PhysicalAddressPostalCode": str})
    foicu = pd.read_csv(FOICU_TXT, dtype={"ZIP_CODE": str, "TOM_CODE": str})
    foicudes = pd.read_csv(FOICUDES_TXT)

    print(f"ncua_branches_2026q2.txt: {branches.shape[0]} rows, {branches.shape[1]} cols")
    print(f"  columns: {branches.columns.tolist()}")
    print(branches.head(3).to_string())

    print(f"\nncua_foicu_2026q2.txt: {foicu.shape[0]} rows, {foicu.shape[1]} cols")
    print(f"  columns: {foicu.columns.tolist()}")
    print(foicu.head(3).to_string())

    print(f"\nncua_foicudes_2026q2.txt: {foicudes.shape[0]} rows, {foicudes.shape[1]} cols")
    print(f"  columns: {foicudes.columns.tolist()}")
    tom_row = foicudes[foicudes["Field_Name"] == "TOM_CODE"]
    print("  TOM_CODE field description (no value-level decode present):")
    print(tom_row.to_string(index=False))

    return branches, foicu, foicudes


def join_and_classify(branches: pd.DataFrame, foicu: pd.DataFrame) -> pd.DataFrame:
    foicu_cols = foicu[["CU_NUMBER", "TOM_CODE"]].drop_duplicates(subset="CU_NUMBER")
    merged = branches.merge(foicu_cols, on="CU_NUMBER", how="left")

    unmatched = merged["TOM_CODE"].isna().sum()
    print(f"\nBranches joined to FOICU on CU_NUMBER: {unmatched} of {len(merged)} unmatched.")

    decoded = merged["TOM_CODE"].apply(decode_tom_code)
    merged["fom_type"] = decoded.apply(lambda t: t[0])
    merged["community_chartered"] = decoded.apply(lambda t: t[1])
    return merged


def filter_to_newark(merged: pd.DataFrame) -> pd.DataFrame:
    nj = merged[merged["PhysicalAddressStateCode"] == "NJ"]
    print(f"\nNJ branches: {len(nj)} of {len(merged)}.")

    newark = nj[nj["PhysicalAddressCity"].str.strip().str.lower() == "newark"].copy()
    print(f"Newark-city branches (NJ only, to avoid Newark, DE/OH/NY false matches): {len(newark)}")
    print(
        newark[
            ["CU_NUMBER", "CU_NAME", "SiteName", "PhysicalAddressLine1", "PhysicalAddressPostalCode", "TOM_CODE", "fom_type", "community_chartered"]
        ].to_string(index=False)
    )
    return newark


def geocode_branches(newark: pd.DataFrame) -> pd.DataFrame:
    newark = newark.copy()
    geolocator = Nominatim(user_agent="nwk-banking-access-geocoder")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)

    latitudes, longitudes, statuses = [], [], []
    for _, row in newark.iterrows():
        zip5 = str(row["PhysicalAddressPostalCode"]).split("-")[0]
        query = f"{row['PhysicalAddressLine1']}, Newark, NJ {zip5}"
        location = geocode(query)

        if location is None:
            latitudes.append(None)
            longitudes.append(None)
            statuses.append("not_found")
            continue

        lat, lon = location.latitude, location.longitude
        latitudes.append(lat)
        longitudes.append(lon)
        in_bbox = NEWARK_LAT_MIN <= lat <= NEWARK_LAT_MAX and NEWARK_LON_MIN <= lon <= NEWARK_LON_MAX
        statuses.append("ok" if in_bbox else "low_confidence")

    newark["latitude"] = latitudes
    newark["longitude"] = longitudes
    newark["geocode_status"] = statuses

    # Retry not_found rows with a trailing floor/suite fragment stripped.
    retry_idx = newark.index[newark["geocode_status"] == "not_found"]
    for idx in retry_idx:
        line1 = str(newark.at[idx, "PhysicalAddressLine1"])
        stripped = UNIT_SUFFIX_RE.sub("", line1)
        if stripped == line1:
            continue

        zip5 = str(newark.at[idx, "PhysicalAddressPostalCode"]).split("-")[0]
        location = geocode(f"{stripped}, Newark, NJ {zip5}")
        if location is None:
            continue

        lat, lon = location.latitude, location.longitude
        newark.at[idx, "latitude"] = lat
        newark.at[idx, "longitude"] = lon
        in_bbox = NEWARK_LAT_MIN <= lat <= NEWARK_LAT_MAX and NEWARK_LON_MIN <= lon <= NEWARK_LON_MAX
        newark.at[idx, "geocode_status"] = "ok" if in_bbox else "low_confidence"

    total = len(newark)
    ok = (newark["geocode_status"] == "ok").sum()
    not_found = (newark["geocode_status"] == "not_found").sum()
    low_confidence = (newark["geocode_status"] == "low_confidence").sum()
    print(f"\nGeocoded {total} Newark credit union branches: ok={ok}, not_found={not_found}, low_confidence={low_confidence}")
    if not_found or low_confidence:
        needs_review = newark[newark["geocode_status"] != "ok"]
        print("Rows needing manual review:")
        for _, row in needs_review.iterrows():
            print(f"  [{row['geocode_status']}] {row['CU_NAME']} - {row['PhysicalAddressLine1']}")

    return newark[newark["geocode_status"] != "not_found"].copy()


def load_wards(path: Path) -> gpd.GeoDataFrame:
    wards = gpd.read_file(path)
    if wards.crs is None or wards.crs.to_string() != WGS84:
        wards = wards.to_crs(WGS84)
    return wards[[WARD_NAME_FIELD, "geometry"]].rename(columns={WARD_NAME_FIELD: "ward"})


def spatial_join_wards(newark: pd.DataFrame, wards: gpd.GeoDataFrame) -> pd.DataFrame:
    points = gpd.GeoDataFrame(
        newark, geometry=gpd.points_from_xy(newark["longitude"], newark["latitude"]), crs=WGS84
    )
    joined = gpd.sjoin(points, wards, how="left", predicate="within")
    joined = joined.drop(columns=["index_right"], errors="ignore")

    matched = joined["ward"].notna().sum()
    unmatched = joined[joined["ward"].isna()]
    print(f"\nCredit unions: {matched} of {len(joined)} Newark branches matched a ward.")
    if len(unmatched):
        print(f"WARNING: {len(unmatched)} Newark credit union branches fell outside all 5 wards:")
        print(unmatched[["CU_NAME", "PhysicalAddressLine1", "latitude", "longitude"]].to_string(index=False))

    return pd.DataFrame(joined.drop(columns="geometry"))


OUTPUT_COLUMNS = [
    "CU_NUMBER", "CU_NAME", "SiteName", "SiteTypeName", "MainOffice",
    "PhysicalAddressLine1", "PhysicalAddressCity", "PhysicalAddressStateCode",
    "PhysicalAddressPostalCode", "TOM_CODE", "fom_type", "community_chartered",
    "latitude", "longitude", "ward",
]


def update_ward_summary(community: pd.DataFrame) -> pd.DataFrame:
    summary = pd.read_csv(SUMMARY_CSV)

    counts = community["ward"].value_counts().rename("credit_union_count")
    summary = summary.drop(columns=["credit_union_count", "total_financial_access_count"], errors="ignore")
    summary = summary.merge(counts, left_on="ward", right_index=True, how="left")
    summary["credit_union_count"] = summary["credit_union_count"].fillna(0).astype(int)
    summary["total_financial_access_count"] = summary["bank_branch_count"] + summary["credit_union_count"]

    summary.to_csv(SUMMARY_CSV, index=False)
    print(f"\nUpdated {SUMMARY_CSV}")
    return summary


def main() -> None:
    for path in (BRANCHES_TXT, FOICU_TXT, FOICUDES_TXT, WARDS_GEOJSON, SUMMARY_CSV):
        if not path.exists():
            print(f"ERROR: required input file missing: {path}")
            sys.exit(1)

    branches, foicu, _foicudes = load_raw_files()

    merged = join_and_classify(branches, foicu)
    newark = filter_to_newark(merged)

    newark_geocoded = geocode_branches(newark)

    wards = load_wards(WARDS_GEOJSON)
    newark_ward_tagged = spatial_join_wards(newark_geocoded, wards)

    community = newark_ward_tagged[newark_ward_tagged["community_chartered"]].copy()
    restricted = newark_ward_tagged[~newark_ward_tagged["community_chartered"]].copy()

    print(f"\n=== Restricted-membership credit unions in Newark ({len(restricted)}, excluded from ward totals) ===")
    if len(restricted):
        print(restricted[["CU_NAME", "SiteName", "ward", "fom_type"]].to_string(index=False))
    else:
        print("  (none)")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    community[OUTPUT_COLUMNS].to_csv(COMMUNITY_OUTPUT, index=False)
    restricted[OUTPUT_COLUMNS].to_csv(RESTRICTED_OUTPUT, index=False)
    print(f"\nSaved {COMMUNITY_OUTPUT} ({len(community)} community-chartered branches)")
    print(f"Saved {RESTRICTED_OUTPUT} ({len(restricted)} restricted-membership branches)")

    summary = update_ward_summary(community)
    print("\n=== Updated per-ward summary ===")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
