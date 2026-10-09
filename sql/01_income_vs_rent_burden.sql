-- Dashboard panel : Income vs Rent Burden
-- Feeds           : output/ward_summary.csv -> avg_median_income, avg_pct_rent_burdened, tract_count
-- Upstream Python : scripts/clean_census_data.py (ACS B19013 + B25070 reshaped per tract)
--                   scripts/build_ward_database.py (tract centroid -> ward spatial join, SQLite load)
-- Source table    : tract_demographics (88 Newark tracts, each tagged with a ward)
--
-- Ward values are the unweighted mean of tract-level values (AVG ignores the
-- tracts with a suppressed income / rent-burden estimate; tract_count counts
-- all 88 tracts). The two RANK() columns are additions for readability and
-- are not part of the Tableau export.

WITH ward_demo AS (
    SELECT
        ward,
        COUNT(*)                       AS tract_count,
        AVG(median_household_income)   AS avg_median_income,
        AVG(pct_rent_burdened)         AS avg_pct_rent_burdened
    FROM tract_demographics
    GROUP BY ward
)
SELECT
    ward,
    tract_count,
    avg_median_income,
    avg_pct_rent_burdened,
    RANK() OVER (ORDER BY avg_median_income DESC)   AS income_rank_high_to_low,
    RANK() OVER (ORDER BY avg_pct_rent_burdened DESC) AS rent_burden_rank_high_to_low
FROM ward_demo
ORDER BY ward;
