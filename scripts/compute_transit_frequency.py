"""
Compute weekday bus service frequency per ward, and export the route-line
geometry needed to draw actual bus routes on the Tableau map.

Flow:
  1. Load data/raw/gtfs-bus/calendar.txt and keep only "weekday" service_ids
     -- Monday through Friday all == 1 -- which excludes Saturday/Sunday-only
     schedules. Join to trips.txt to get the weekday trips, then to
     routes.txt and keep only route_type == 3 (bus; the feed also carries
     a handful of light-rail routes -- HBLR/NLR/RvLN -- that aren't part of
     the bus network this analysis is about).
  2. Join those weekday bus trips to stop_times.txt and keep only stop-time
     events at the 958 Newark-area stops in
     data/processed/transit_stops_by_ward.csv (from Stage 4). For each
     stop, sort arrival times falling in a 6 AM-8 PM daytime window and
     average the gap between consecutive buses -- that average gap is the
     stop's headway in minutes (lower = more frequent/convenient service).
  3. Aggregate stop-level headways to ward level (avg_weekday_headway_minutes)
     and merge into output/ward_summary.csv.
  4. Separately (all service, not just weekday): find every trip that stops
     at a Newark-area stop, restrict to bus routes, and export the distinct
     shape_ids used by those trips as LineString features to
     data/processed/newark_bus_routes.geojson, for Tableau to draw as a
     route-line layer alongside the existing stop-point layer.
"""

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw" / "gtfs-bus"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
OUTPUT_DIR = REPO_ROOT / "output"

CALENDAR_TXT = RAW_DIR / "calendar.txt"
TRIPS_TXT = RAW_DIR / "trips.txt"
ROUTES_TXT = RAW_DIR / "routes.txt"
STOP_TIMES_TXT = RAW_DIR / "stop_times.txt"
SHAPES_TXT = RAW_DIR / "shapes.txt"
WARD_STOPS_CSV = PROCESSED_DIR / "transit_stops_by_ward.csv"

SUMMARY_CSV = OUTPUT_DIR / "ward_summary.csv"
ROUTES_GEOJSON = PROCESSED_DIR / "newark_bus_routes.geojson"

BUS_ROUTE_TYPE = 3
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]
WINDOW_START_MIN = 6 * 60
WINDOW_END_MIN = 20 * 60


def load_bus_routes() -> pd.DataFrame:
    routes = pd.read_csv(ROUTES_TXT)
    bus_routes = routes[routes["route_type"] == BUS_ROUTE_TYPE].copy()
    print(f"Routes: {len(bus_routes)} of {len(routes)} routes are buses (route_type == {BUS_ROUTE_TYPE}).")
    return bus_routes


def load_weekday_bus_trips(bus_routes: pd.DataFrame) -> pd.DataFrame:
    calendar = pd.read_csv(CALENDAR_TXT)
    weekday_mask = (calendar[WEEKDAYS] == 1).all(axis=1)
    weekday_services = calendar[weekday_mask]
    print(f"Calendar: {len(weekday_services)} of {len(calendar)} service_ids run Monday-Friday.")

    trips = pd.read_csv(TRIPS_TXT)
    weekday_trips = trips[trips["service_id"].isin(weekday_services["service_id"])]
    weekday_bus_trips = weekday_trips[weekday_trips["route_id"].isin(bus_routes["route_id"])]
    print(
        f"Trips: {len(weekday_trips)} of {len(trips)} trips run on a weekday service, "
        f"{len(weekday_bus_trips)} of those are bus trips."
    )
    return weekday_bus_trips


def gtfs_time_to_minutes(time_col: pd.Series) -> pd.Series:
    parts = time_col.str.split(":", expand=True).astype(int)
    return parts[0] * 60 + parts[1] + parts[2] / 60


def compute_stop_headways(weekday_bus_trips: pd.DataFrame, ward_stops: pd.DataFrame) -> pd.DataFrame:
    stop_times = pd.read_csv(
        STOP_TIMES_TXT, usecols=["trip_id", "arrival_time", "stop_id"], dtype={"stop_id": str}
    )
    newark_stop_ids = set(ward_stops["stop_id"].astype(str))

    events = stop_times[stop_times["stop_id"].isin(newark_stop_ids)]
    events = events[events["trip_id"].isin(weekday_bus_trips["trip_id"])]
    print(f"Stop-times: {len(events)} weekday bus stop-time events at Newark-area stops.")

    events = events.copy()
    events["arrival_min"] = gtfs_time_to_minutes(events["arrival_time"])
    daytime = events[(events["arrival_min"] >= WINDOW_START_MIN) & (events["arrival_min"] <= WINDOW_END_MIN)]
    print(
        f"Stop-times: {len(daytime)} of those fall in the {WINDOW_START_MIN // 60}AM-"
        f"{WINDOW_END_MIN // 60 - 12}PM daytime window."
    )

    def stop_headway(group: pd.DataFrame) -> float:
        times = group["arrival_min"].sort_values()
        gaps = times.diff().dropna()
        return gaps.mean() if len(gaps) else float("nan")

    headways = daytime.groupby("stop_id").apply(stop_headway, include_groups=False)
    headways = headways.rename("avg_weekday_headway_minutes").reset_index()

    n_no_headway = len(newark_stop_ids) - len(headways.dropna(subset=["avg_weekday_headway_minutes"]))
    if n_no_headway:
        print(
            f"WARNING: {n_no_headway} of {len(newark_stop_ids)} Newark-area stops have fewer than 2 "
            "daytime weekday buses and have no computable headway -- excluded from ward averages."
        )
    return headways


def aggregate_headway_to_ward(stop_headways: pd.DataFrame, ward_stops: pd.DataFrame) -> pd.DataFrame:
    ward_stops = ward_stops.copy()
    ward_stops["stop_id"] = ward_stops["stop_id"].astype(str)
    merged = ward_stops.merge(stop_headways, on="stop_id", how="left")
    ward_headway = (
        merged.groupby("ward")["avg_weekday_headway_minutes"]
        .mean()
        .reset_index()
    )
    return ward_headway


def update_ward_summary(ward_headway: pd.DataFrame) -> pd.DataFrame:
    summary = pd.read_csv(SUMMARY_CSV)
    summary = summary.drop(columns=["avg_weekday_headway_minutes"], errors="ignore")
    summary = summary.merge(ward_headway, on="ward", how="left")
    summary.to_csv(SUMMARY_CSV, index=False)
    print(f"\nUpdated {SUMMARY_CSV}")
    return summary


def export_newark_bus_route_shapes(bus_routes: pd.DataFrame, ward_stops: pd.DataFrame) -> int:
    stop_times = pd.read_csv(STOP_TIMES_TXT, usecols=["trip_id", "stop_id"], dtype={"stop_id": str})
    newark_stop_ids = set(ward_stops["stop_id"].astype(str))
    newark_trip_ids = set(stop_times.loc[stop_times["stop_id"].isin(newark_stop_ids), "trip_id"])
    print(f"Shapes: {len(newark_trip_ids)} distinct trips (any service, not just weekday) stop at a Newark-area stop.")

    trips = pd.read_csv(TRIPS_TXT, usecols=["trip_id", "route_id", "shape_id"]).dropna(subset=["shape_id"])
    newark_bus_trips = trips[
        trips["trip_id"].isin(newark_trip_ids) & trips["route_id"].isin(bus_routes["route_id"])
    ]

    shape_routes = newark_bus_trips.drop_duplicates(subset=["shape_id"])[["shape_id", "route_id"]]
    shape_routes = shape_routes.merge(
        bus_routes[["route_id", "route_short_name", "route_long_name"]], on="route_id", how="left"
    )
    print(f"Shapes: {len(shape_routes)} distinct route/shape combinations serve a Newark-area stop.")

    shapes = pd.read_csv(SHAPES_TXT)
    shapes = shapes[shapes["shape_id"].isin(shape_routes["shape_id"])]
    shapes = shapes.sort_values(["shape_id", "shape_pt_sequence"])

    features = []
    for shape_id, group in shapes.groupby("shape_id"):
        coords = list(zip(group["shape_pt_lon"], group["shape_pt_lat"]))
        if len(coords) < 2:
            continue
        route_row = shape_routes.loc[shape_routes["shape_id"] == shape_id].iloc[0]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "shape_id": shape_id,
                    "route_id": route_row["route_id"],
                    "route_short_name": route_row["route_short_name"],
                    "route_long_name": route_row["route_long_name"],
                },
            }
        )

    geojson = {"type": "FeatureCollection", "features": features}

    import json

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(ROUTES_GEOJSON, "w") as f:
        json.dump(geojson, f)
    print(f"Saved {ROUTES_GEOJSON}")

    return len(features)


def main() -> None:
    for path in (CALENDAR_TXT, TRIPS_TXT, ROUTES_TXT, STOP_TIMES_TXT, SHAPES_TXT, WARD_STOPS_CSV, SUMMARY_CSV):
        if not path.exists():
            print(f"ERROR: required input file missing: {path}")
            sys.exit(1)

    ward_stops = pd.read_csv(WARD_STOPS_CSV, dtype={"stop_id": str})
    bus_routes = load_bus_routes()

    weekday_bus_trips = load_weekday_bus_trips(bus_routes)
    stop_headways = compute_stop_headways(weekday_bus_trips, ward_stops)
    ward_headway = aggregate_headway_to_ward(stop_headways, ward_stops)
    summary = update_ward_summary(ward_headway)

    n_shapes = export_newark_bus_route_shapes(bus_routes, ward_stops)

    print("\n=== Avg weekday headway by ward (minutes, lower = more frequent) ===")
    print(
        summary[["ward", "avg_weekday_headway_minutes"]]
        .sort_values("avg_weekday_headway_minutes")
        .to_string(index=False)
    )
    print(f"\nExported {n_shapes} distinct bus route shapes to {ROUTES_GEOJSON}")


if __name__ == "__main__":
    main()
