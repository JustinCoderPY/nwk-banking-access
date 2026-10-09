-- Dashboard panel : Transit Headway & Stop Count
-- Feeds           : output/ward_summary.csv -> transit_stop_count, avg_weekday_headway_minutes
-- Upstream Python : scripts/spatial_join_wards.py (958 NJ Transit bus stops tagged to wards)
--                   scripts/compute_transit_frequency.py (weekday 6AM-8PM headway per stop,
--                   averaged to ward)
-- Source tables   : transit_stops (in the SQLite database)
--                   ward_headway  (STAGED by run_queries.py from output/ward_summary.csv)
--
-- NOTE: stop counts are aggregated here in SQL, but the headway itself is computed
-- in pandas from the raw GTFS stop_times file and is NOT stored in the database.
-- run_queries.py stages the ward-level result as ward_headway(ward,
-- avg_weekday_headway_minutes) in an in-memory copy of the database so it can be
-- joined and ranked in SQL. The committed .db file is never modified.
-- Headway is minutes between consecutive buses, so higher = worse service.

WITH stops AS (
    SELECT ward, COUNT(*) AS transit_stop_count
    FROM transit_stops
    GROUP BY ward
)
SELECT
    s.ward,
    s.transit_stop_count,
    h.avg_weekday_headway_minutes,
    RANK() OVER (ORDER BY h.avg_weekday_headway_minutes DESC) AS headway_rank_longest_wait,
    RANK() OVER (ORDER BY s.transit_stop_count DESC)          AS stop_count_rank
FROM stops s
JOIN ward_headway h ON h.ward = s.ward
ORDER BY s.ward;
