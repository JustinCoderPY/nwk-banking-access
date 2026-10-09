-- Dashboard panel : Bank vs Alt-Finance Count
-- Feeds           : output/ward_summary.csv -> bank_branch_count, alt_finance_count,
--                   alt_finance_to_bank_ratio
-- Upstream Python : scripts/spatial_join_wards.py (FDIC branches, filtered to Newark then
--                   point-in-polygon joined to wards)
--                   scripts/geocode_alt_finance.py (31 hand-compiled alternative-finance
--                   businesses, geocoded with Nominatim) -> spatial_join_wards.py
--                   scripts/build_ward_database.py (loads both tables into SQLite)
-- Source tables   : fdic_branches, alt_finance, tract_demographics (ward list only)
--
-- Alt-finance = check cashing, payday/installment lending, money transfer, pawn.
-- Ratio = alt-finance locations per FDIC branch; NULLIF guards a ward with zero banks.
-- The ward spine comes from tract_demographics so a ward with no points still appears.

WITH wards AS (
    SELECT DISTINCT ward FROM tract_demographics
),
banks AS (
    SELECT ward, COUNT(*) AS bank_branch_count
    FROM fdic_branches
    GROUP BY ward
),
alts AS (
    SELECT ward, COUNT(*) AS alt_finance_count
    FROM alt_finance
    GROUP BY ward
),
combined AS (
    SELECT
        w.ward,
        COALESCE(b.bank_branch_count, 0) AS bank_branch_count,
        COALESCE(a.alt_finance_count, 0) AS alt_finance_count
    FROM wards w
    LEFT JOIN banks b ON b.ward = w.ward
    LEFT JOIN alts  a ON a.ward = w.ward
)
SELECT
    ward,
    bank_branch_count,
    alt_finance_count,
    CAST(alt_finance_count AS REAL) / NULLIF(bank_branch_count, 0) AS alt_finance_to_bank_ratio,
    RANK() OVER (ORDER BY bank_branch_count ASC)  AS fewest_banks_rank,
    RANK() OVER (ORDER BY alt_finance_count DESC) AS most_alt_finance_rank
FROM combined
ORDER BY ward;
