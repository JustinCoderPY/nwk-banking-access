"""
Download the Census TIGER/Line 2024 tract boundary shapefile for New Jersey,
filter to Essex County (FIPS 013), and save as GeoJSON.

Flow:
  1. Download tl_2024_34_tract.zip from the Census Bureau TIGER/Line archive
  2. Load the statewide tract shapefile with geopandas
  3. Filter to tracts where COUNTYFP == "013" (Essex County)
  4. Save the result to data/raw/essex_tract_boundaries.geojson

Vintage 2024 is used to match the 2024 ACS 5-Year data already collected in
data/raw/census_income_essex.csv and data/raw/census_rentburden_essex.csv.
"""

import shutil
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import requests

STATE_FIPS = "34"  # New Jersey
COUNTY_FIPS = "013"  # Essex County
YEAR = "2024"

TIGER_URL = f"https://www2.census.gov/geo/tiger/TIGER{YEAR}/TRACT/tl_{YEAR}_{STATE_FIPS}_tract.zip"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST_DIR = REPO_ROOT / "data" / "raw"
ZIP_PATH = DEST_DIR / f"tl_{YEAR}_{STATE_FIPS}_tract.zip"
EXTRACT_DIR = DEST_DIR / f"tl_{YEAR}_{STATE_FIPS}_tract"
OUTPUT_PATH = DEST_DIR / "essex_tract_boundaries.geojson"


def download_shapefile() -> None:
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    response = requests.get(TIGER_URL)
    response.raise_for_status()

    if not response.content.startswith(b"PK"):
        print(f"ERROR: response does not look like a zip file from {TIGER_URL}.")
        sys.exit(1)

    ZIP_PATH.write_bytes(response.content)
    print(f"Download success: received {len(response.content):,} bytes from {TIGER_URL}.")


def extract_shapefile() -> Path:
    with zipfile.ZipFile(ZIP_PATH) as zf:
        zf.extractall(EXTRACT_DIR)
        extracted = zf.namelist()

    ZIP_PATH.unlink()
    print(f"Extracted {len(extracted)} file(s) into {EXTRACT_DIR}.")

    shp_files = list(EXTRACT_DIR.glob("*.shp"))
    if not shp_files:
        print(f"ERROR: no .shp file found in {EXTRACT_DIR}.")
        sys.exit(1)
    return shp_files[0]


def filter_to_essex_county(shp_path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(shp_path)
    print(f"Loaded statewide shapefile: {len(gdf)} New Jersey tracts.")

    essex_gdf = gdf[gdf["COUNTYFP"] == COUNTY_FIPS].copy()
    print(f"Filter success: {len(essex_gdf)} tracts in Essex County (FIPS {COUNTY_FIPS}).")
    return essex_gdf


def save_geojson(gdf: gpd.GeoDataFrame) -> None:
    gdf.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(f"Save success: wrote {OUTPUT_PATH}")
    print(f"Feature count: {len(gdf)}")


def cleanup_statewide_shapefile() -> None:
    shutil.rmtree(EXTRACT_DIR)
    print(f"Cleaned up intermediate statewide shapefile at {EXTRACT_DIR}.")


def main() -> None:
    download_shapefile()
    shp_path = extract_shapefile()
    essex_gdf = filter_to_essex_county(shp_path)
    save_geojson(essex_gdf)
    cleanup_statewide_shapefile()


if __name__ == "__main__":
    main()
