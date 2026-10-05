"""Geocode the manually-collected alt-finance business list for Newark.

Reads data/raw/alt_financial_services_newark.csv, geocodes each address with
Nominatim (OpenStreetMap), and writes lat/long plus a geocode_status column
to data/processed/alt_finance_geocoded.csv.
"""

import re
import sys
from pathlib import Path

import pandas as pd
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

RAW_PATH = Path("data/raw/alt_financial_services_newark.csv")
OUT_PATH = Path("data/processed/alt_finance_geocoded.csv")

# Rough bounding box for Newark, NJ.
NEWARK_LAT_MIN, NEWARK_LAT_MAX = 40.66, 40.80
NEWARK_LON_MIN, NEWARK_LON_MAX = -74.26, -74.12

# Trailing unit/suite fragment on the street portion of an address, e.g.
# "75 1st St B" -> "75 1st St", "24 Commerce St #534" -> "24 Commerce St".
UNIT_SUFFIX_RE = re.compile(r"\s+(?:#\s*\d+[A-Za-z]?|\d+[A-Za-z]?|[A-Za-z])$")


def in_newark_bbox(lat, lon):
    return (
        NEWARK_LAT_MIN <= lat <= NEWARK_LAT_MAX
        and NEWARK_LON_MIN <= lon <= NEWARK_LON_MAX
    )


def normalize_address(address: str) -> str:
    address = address.strip()
    if "newark, nj" not in address.lower():
        address = f"{address}, Newark, NJ"
    return address


def strip_unit_suffix(address: str) -> str:
    """Drop a trailing suite/unit fragment from the street part of an address."""
    street, sep, rest = address.partition(",")
    street = UNIT_SUFFIX_RE.sub("", street.strip())
    return street + sep + rest


def main():
    if not RAW_PATH.exists():
        sys.exit(f"Input file not found: {RAW_PATH}")

    df = pd.read_csv(RAW_PATH)

    geolocator = Nominatim(user_agent="nwk-banking-access-geocoder")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)

    latitudes = []
    longitudes = []
    statuses = []

    for address in df["address"]:
        query = normalize_address(str(address))
        location = geocode(query)

        if location is None:
            latitudes.append(None)
            longitudes.append(None)
            statuses.append("not_found")
            continue

        lat, lon = location.latitude, location.longitude
        latitudes.append(lat)
        longitudes.append(lon)
        statuses.append("ok" if in_newark_bbox(lat, lon) else "low_confidence")

    df["latitude"] = latitudes
    df["longitude"] = longitudes
    df["geocode_status"] = statuses

    # Retry rows that failed, stripping suite/unit fragments Nominatim can't parse.
    retry_idx = df.index[df["geocode_status"] == "not_found"]
    for idx in retry_idx:
        stripped_query = strip_unit_suffix(normalize_address(str(df.at[idx, "address"])))
        if stripped_query == normalize_address(str(df.at[idx, "address"])):
            continue  # nothing to strip, retrying would just repeat the same query

        location = geocode(stripped_query)
        if location is None:
            continue

        lat, lon = location.latitude, location.longitude
        df.at[idx, "latitude"] = lat
        df.at[idx, "longitude"] = lon
        df.at[idx, "geocode_status"] = "ok" if in_newark_bbox(lat, lon) else "low_confidence"

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)

    total = len(df)
    ok = (df["geocode_status"] == "ok").sum()
    not_found = (df["geocode_status"] == "not_found").sum()
    low_confidence = (df["geocode_status"] == "low_confidence").sum()

    print(f"Geocoded {total} rows:")
    print(f"  ok:             {ok}")
    print(f"  not_found:      {not_found}")
    print(f"  low_confidence: {low_confidence}")
    if not_found or low_confidence:
        print("\nRows needing manual review:")
        needs_review = df[df["geocode_status"] != "ok"]
        for _, row in needs_review.iterrows():
            print(f"  [{row['geocode_status']}] {row['name']} - {row['address']}")

    print(f"\nSaved to {OUT_PATH}")


if __name__ == "__main__":
    main()
