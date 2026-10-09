-- Dashboard panel : FDIC Bank Branches Map (point-level export)
-- Feeds           : data/processed/fdic_branches_by_ward.csv (one dot per branch)
-- Upstream Python : scripts/spatial_join_wards.py (CITY = Newark filter, then
--                   geopandas point-in-polygon join to newark_wards.geojson)
--                   scripts/verify_branch_counts.py (coordinates-only re-join as a cross-check)
-- Source table    : fdic_branches (51 Newark branches)
--
-- The first nine columns reproduce the CSV exactly. The window columns add the
-- ward's branch total and a stable per-ward sequence number.

SELECT
    ADDRESS,
    CITY,
    ID,
    LATITUDE,
    LONGITUDE,
    NAME,
    STALP,
    ZIP,
    ward,
    COUNT(*)     OVER (PARTITION BY ward)                         AS ward_branch_count,
    ROW_NUMBER() OVER (PARTITION BY ward ORDER BY NAME, ADDRESS)  AS branch_seq_in_ward
FROM fdic_branches
ORDER BY ward, NAME, ADDRESS;
