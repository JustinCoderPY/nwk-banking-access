-- Dashboard panel : Gentrification Pressure Gap
-- Feeds           : output/ward_summary.csv -> avg_pct_rent_growth, avg_pct_income_growth,
--                   rent_growth_minus_income_growth
-- Upstream Python : scripts/compute_gentrification_metrics.py (ACS 2019 vs 2024 median gross
--                   rent and median household income, joined by GEOID, written to tract_growth)
-- Source table    : tract_growth (88 Newark tracts, ward-tagged)
--
-- pct_*_growth = (2024 - 2019) / 2019 * 100 per tract. Ward figures are the mean of
-- tract growth rates; each average skips tracts missing that metric (4 lack rent
-- growth, 5 lack income growth; 83 have both). Gap is in percentage points:
-- positive = rent outpacing income; every ward is negative, so the "pressure"
-- rank orders wards from least negative (1) to most negative (5).

WITH ward_growth AS (
    SELECT
        ward,
        COUNT(*) AS tract_count,
        SUM(pct_rent_growth IS NOT NULL AND pct_income_growth IS NOT NULL)
                                       AS tracts_with_both_metrics,
        AVG(pct_rent_growth)           AS avg_pct_rent_growth,
        AVG(pct_income_growth)         AS avg_pct_income_growth
    FROM tract_growth
    GROUP BY ward
),
gap AS (
    SELECT
        *,
        avg_pct_rent_growth - avg_pct_income_growth AS rent_growth_minus_income_growth
    FROM ward_growth
)
SELECT
    ward,
    tract_count,
    tracts_with_both_metrics,
    avg_pct_rent_growth,
    avg_pct_income_growth,
    rent_growth_minus_income_growth,
    RANK() OVER (ORDER BY rent_growth_minus_income_growth DESC) AS pressure_rank,
    rent_growth_minus_income_growth
        - AVG(rent_growth_minus_income_growth) OVER ()          AS gap_vs_ward_average
FROM gap
ORDER BY ward;
