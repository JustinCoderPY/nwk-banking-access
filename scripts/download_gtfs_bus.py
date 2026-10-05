"""
Download the NJ Transit Bus GTFS dataset via the GTFSG2 API.

Flow:
  1. POST authenticateUser (multipart/form-data: username, password) -> JSON { "UserToken": "...", "Authenticated": "..." }
  2. POST getGTFS (multipart/form-data: token) -> binary zip file
  3. Save the zip and extract it into data/raw/gtfs-bus/

Credentials are read from .env (NJT_USERNAME, NJT_PASSWORD) and never hardcoded.
"""

import os
import sys
import zipfile
from pathlib import Path

import requests
from dotenv import load_dotenv

AUTH_URL = "https://pcsdata.njtransit.com/api/GTFSG2/authenticateUser"
GTFS_URL = "https://pcsdata.njtransit.com/api/GTFSG2/getGTFS"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST_DIR = REPO_ROOT / "data" / "raw" / "gtfs-bus"
ZIP_PATH = DEST_DIR / "gtfs-bus.zip"


def check_env_is_gitignored() -> None:
    gitignore = REPO_ROOT / ".gitignore"
    if not gitignore.exists() or ".env" not in gitignore.read_text().splitlines():
        print("ERROR: .env is not listed in .gitignore. Refusing to proceed.")
        sys.exit(1)
    print("Confirmed .env is present in .gitignore.")


def authenticate(username: str, password: str) -> str:
    response = requests.post(
        AUTH_URL,
        files={"username": (None, username), "password": (None, password)},
    )
    response.raise_for_status()
    data = response.json()

    token = data.get("UserToken")
    if not token:
        print(f"ERROR: could not find 'UserToken' in authenticateUser response: {data}")
        sys.exit(1)

    print(f"Auth success: received token (expires in ~24h). Authenticated={data.get('Authenticated')}")
    return token


def download_gtfs(token: str) -> bytes:
    response = requests.post(GTFS_URL, files={"token": (None, token)})
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if "zip" not in content_type and not response.content.startswith(b"PK"):
        print(f"ERROR: response does not look like a zip file (Content-Type: {content_type}).")
        sys.exit(1)

    print(f"Download success: received {len(response.content):,} bytes.")
    return response.content


def save_and_unzip(zip_bytes: bytes) -> None:
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    ZIP_PATH.write_bytes(zip_bytes)

    with zipfile.ZipFile(ZIP_PATH) as zf:
        zf.extractall(DEST_DIR)
        extracted = zf.namelist()

    ZIP_PATH.unlink()

    print(f"Unzip success: extracted {len(extracted)} file(s) into {DEST_DIR}:")
    for name in sorted(extracted):
        print(f"  - {name}")


def main() -> None:
    check_env_is_gitignored()

    load_dotenv(REPO_ROOT / ".env")
    username = os.environ.get("NJT_USERNAME")
    password = os.environ.get("NJT_PASSWORD")
    if not username or not password:
        print("ERROR: NJT_USERNAME and/or NJT_PASSWORD not found in .env.")
        sys.exit(1)

    token = authenticate(username, password)
    zip_bytes = download_gtfs(token)
    save_and_unzip(zip_bytes)


if __name__ == "__main__":
    main()
