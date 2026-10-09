"""
Run every query in sql/ against the project's SQLite database, print each result
table, and check the panel-feeding queries against the exports Tableau reads.

Usage (from the repo root):
    python run_queries.py              # run all queries, print tables, validate
    python run_queries.py 03 05        # only the files whose names start with 03 / 05

Design notes:
  * The committed database (data/nwk_banking_access.db) is opened read-only and
    copied into memory, so nothing in the repo is modified.
  * Two inputs are not stored in that database, so they are staged into the
    in-memory copy from the pipeline's existing outputs:
      - credit_unions  <- data/processed/credit_unions_by_ward.csv
                          + data/processed/restricted_credit_unions_newark.csv
      - ward_headway   <- output/ward_summary.csv (avg_weekday_headway_minutes)
  * Standard library only (sqlite3, csv); SQLite 3.25+ is needed for window functions.
  * Exit code is 1 if any validation fails.
"""

import csv
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DB_PATH = REPO_ROOT / "data" / "nwk_banking_access.db"
SQL_DIR = REPO_ROOT / "sql"
PROCESSED = REPO_ROOT / "data" / "processed"
SUMMARY_CSV = REPO_ROOT / "output" / "ward_summary.csv"

# SQL file prefix -> (export CSV, key columns, columns that must match the export).
SUMMARY_KEY = ("ward",)
EXPORT_CHECKS = {
    "01": (SUMMARY_CSV, SUMMARY_KEY, ["tract_count", "avg_median_income", "avg_pct_rent_burdened"]),
    "02": (SUMMARY_CSV, SUMMARY_KEY, ["avg_pct_rent_growth", "avg_pct_income_growth",
                                      "rent_growth_minus_income_growth"]),
    "03": (SUMMARY_CSV, SUMMARY_KEY, ["transit_stop_count", "avg_weekday_headway_minutes"]),
    "04": (SUMMARY_CSV, SUMMARY_KEY, ["bank_branch_count", "alt_finance_count",
                                      "alt_finance_to_bank_ratio"]),
    "05": (SUMMARY_CSV, SUMMARY_KEY, ["bank_branch_count", "credit_union_count",
                                      "total_financial_access_count"]),
    "06": (PROCESSED / "fdic_branches_by_ward.csv", ("ID",),
           ["ADDRESS", "CITY", "ID", "LATITUDE", "LONGITUDE", "NAME", "STALP", "ZIP", "ward"]),
    "07": (PROCESSED / "alt_finance_by_ward.csv", ("name", "address"),
           ["name", "address", "category", "source_search", "latitude", "longitude",
            "geocode_status", "ward"]),
}

CREDIT_UNION_COLUMNS = ["CU_NUMBER", "CU_NAME", "SiteName", "fom_type",
                        "community_chartered", "latitude", "longitude", "ward"]


def read_csv(path: Path) -> list:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def open_database() -> sqlite3.Connection:
    """Read-only handle on the committed database, copied into memory."""
    source = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn = sqlite3.connect(":memory:")
    source.backup(conn)
    source.close()
    return conn


def stage_external_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE credit_unions (CU_NUMBER INTEGER, CU_NAME TEXT, SiteName TEXT, "
        "fom_type TEXT, community_chartered INTEGER, latitude REAL, longitude REAL, ward TEXT)"
    )
    for filename in ("credit_unions_by_ward.csv", "restricted_credit_unions_newark.csv"):
        for row in read_csv(PROCESSED / filename):
            values = [row[c] for c in CREDIT_UNION_COLUMNS]
            values[4] = 1 if row["community_chartered"] == "True" else 0
            conn.execute(f"INSERT INTO credit_unions VALUES ({','.join('?' * 8)})", values)

    conn.execute("CREATE TABLE ward_headway (ward TEXT, avg_weekday_headway_minutes REAL)")
    for row in read_csv(SUMMARY_CSV):
        conn.execute("INSERT INTO ward_headway VALUES (?, ?)",
                     (row["ward"], float(row["avg_weekday_headway_minutes"])))


def fmt(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return f"{value:,.3f}"
    return str(value)


def print_table(columns: list, rows: list, max_rows: int = 15) -> None:
    shown = rows[:max_rows]
    cells = [[fmt(v) for v in row] for row in shown]
    widths = [max(len(str(c)), *(len(r[i]) for r in cells)) if cells else len(str(c))
              for i, c in enumerate(columns)]
    print("  " + "  ".join(str(c).ljust(w) for c, w in zip(columns, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in cells:
        print("  " + "  ".join(v.ljust(w) for v, w in zip(r, widths)))
    if len(rows) > max_rows:
        print(f"  ... {len(rows) - max_rows} more rows ({len(rows)} total)")


def same(a, b) -> bool:
    """Compare a query value to a CSV string, numerically when both parse."""
    if a is None or a == "":
        return b is None or b == ""
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a) == str(b)


def validate(prefix: str, columns: list, rows: list) -> bool:
    path, key_cols, compare_cols = EXPORT_CHECKS[prefix]
    export = {tuple(r[k] for k in key_cols): r for r in read_csv(path)}
    result = {tuple(str(dict(zip(columns, r))[k]) for k in key_cols): dict(zip(columns, r))
              for r in rows}

    problems = []
    if set(result) != set(export):
        problems.append(f"key mismatch: {len(result)} query rows vs {len(export)} export rows")
    for key in set(result) & set(export):
        for col in compare_cols:
            if not same(result[key][col], export[key][col]):
                problems.append(f"{key} {col}: query={result[key][col]!r} export={export[key][col]!r}")

    label = path.relative_to(REPO_ROOT).as_posix()
    if problems:
        print(f"  VALIDATION FAILED vs {label}")
        for p in problems[:10]:
            print(f"    - {p}")
        return False
    print(f"  VALIDATED vs {label}: {len(export)} rows x {len(compare_cols)} columns match")
    return True


def main() -> int:
    for path in (DB_PATH, SUMMARY_CSV, PROCESSED / "credit_unions_by_ward.csv"):
        if not path.exists():
            print(f"ERROR: required file missing: {path}")
            return 1

    prefixes = sys.argv[1:]
    sql_files = sorted(p for p in SQL_DIR.glob("*.sql")
                       if not prefixes or p.name[:2] in prefixes)
    if not sql_files:
        print(f"No matching SQL files in {SQL_DIR}")
        return 1

    conn = open_database()
    stage_external_tables(conn)

    failures = 0
    for sql_file in sql_files:
        print(f"\n=== {sql_file.name} ===")
        header = [l[2:].strip() for l in sql_file.read_text().splitlines() if l.startswith("--")]
        if header:
            print(f"  {header[0]}")
        cursor = conn.execute(sql_file.read_text())
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
        print_table(columns, rows)

        prefix = sql_file.name[:2]
        if prefix in EXPORT_CHECKS and not validate(prefix, columns, rows):
            failures += 1
        if prefix == "08":
            failed = [r[0] for r in rows if r[3] != "PASS"]
            if failed:
                failures += 1
                print(f"  CHECKS FAILED: {failed}")
            else:
                print(f"  All {len(rows)} validation checks passed")

    print(f"\n{'ALL QUERIES RAN AND VALIDATED' if not failures else f'{failures} FILE(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
