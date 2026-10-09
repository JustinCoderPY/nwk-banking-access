-- Not a dashboard panel: data-quality and headline-figure checks behind the README.
-- Every row compares an expected value to a live query result; status should be PASS.
-- Uses the staged tables ward_headway and credit_unions (see 03 and 05).
-- Check 'tracts with both growth metrics' documents known data loss (88 tracts, 5 missing
-- a vintage) rather than an error.

WITH checks AS (
    SELECT 'FDIC branches loaded' AS check_name, '51' AS expected,
           CAST((SELECT COUNT(*) FROM fdic_branches) AS TEXT) AS actual
    UNION ALL SELECT 'Alt-finance businesses loaded', '31',
           CAST((SELECT COUNT(*) FROM alt_finance) AS TEXT)
    UNION ALL SELECT 'Alt-finance geocodes with status ok', '31',
           CAST((SELECT COUNT(*) FROM alt_finance WHERE geocode_status = 'ok') AS TEXT)
    UNION ALL SELECT 'Newark bus stops loaded', '958',
           CAST((SELECT COUNT(*) FROM transit_stops) AS TEXT)
    UNION ALL SELECT 'Newark tracts in tract_demographics', '88',
           CAST((SELECT COUNT(*) FROM tract_demographics) AS TEXT)
    UNION ALL SELECT 'Points or tracts with no ward assigned', '0',
           CAST((SELECT (SELECT COUNT(*) FROM fdic_branches WHERE ward IS NULL)
                      + (SELECT COUNT(*) FROM alt_finance WHERE ward IS NULL)
                      + (SELECT COUNT(*) FROM transit_stops WHERE ward IS NULL)
                      + (SELECT COUNT(*) FROM tract_demographics WHERE ward IS NULL)) AS TEXT)
    UNION ALL SELECT 'South Ward FDIC branches', '2',
           CAST((SELECT COUNT(*) FROM fdic_branches WHERE ward = 'South') AS TEXT)
    UNION ALL SELECT 'East Ward FDIC branches', '26',
           CAST((SELECT COUNT(*) FROM fdic_branches WHERE ward = 'East') AS TEXT)
    UNION ALL SELECT 'Wards with longer avg weekday headway than East', '0',
           CAST((SELECT COUNT(*) FROM ward_headway
                 WHERE avg_weekday_headway_minutes >
                       (SELECT avg_weekday_headway_minutes FROM ward_headway WHERE ward = 'East'))
                AS TEXT)
    UNION ALL SELECT 'East avg weekday headway (min, 1 dp)', '20.2',
           CAST((SELECT ROUND(avg_weekday_headway_minutes, 1) FROM ward_headway
                 WHERE ward = 'East') AS TEXT)
    UNION ALL SELECT 'East rent-minus-income growth gap (pts, 1 dp)', '-8.6',
           CAST((SELECT ROUND(AVG(pct_rent_growth) - AVG(pct_income_growth), 1)
                 FROM tract_growth WHERE ward = 'East') AS TEXT)
    UNION ALL SELECT 'Tracts with both growth metrics (documented loss)', '83',
           CAST((SELECT COUNT(*) FROM tract_growth
                 WHERE pct_rent_growth IS NOT NULL AND pct_income_growth IS NOT NULL) AS TEXT)
    UNION ALL SELECT 'Newark NCUA credit union branches', '13',
           CAST((SELECT COUNT(*) FROM credit_unions) AS TEXT)
    UNION ALL SELECT 'Community-chartered (public) credit union branches', '2',
           CAST((SELECT COUNT(*) FROM credit_unions WHERE community_chartered = 1) AS TEXT)
    UNION ALL SELECT 'Wards of the public credit union branches', 'Central,West',
           (SELECT group_concat(ward, ',') FROM
                (SELECT ward FROM credit_unions WHERE community_chartered = 1 ORDER BY ward))
)
SELECT
    check_name,
    expected,
    actual,
    CASE WHEN expected = actual THEN 'PASS' ELSE 'FAIL' END AS status
FROM checks;
