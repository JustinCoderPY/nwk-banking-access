-- Dashboard panel : Ward Overview Map (Total Financial Access Count)
-- Feeds           : output/ward_summary.csv -> credit_union_count, total_financial_access_count
-- Upstream Python : scripts/spatial_join_wards.py (FDIC branches -> wards)
--                   scripts/add_credit_unions.py (NCUA branches joined to field-of-membership,
--                   geocoded, spatial-joined to wards; community-chartered flag)
-- Source tables   : fdic_branches, tract_demographics (ward list)
--                   credit_unions (STAGED by run_queries.py from
--                   data/processed/credit_unions_by_ward.csv and
--                   data/processed/restricted_credit_unions_newark.csv)
--
-- NOTE: credit unions are not stored in the SQLite database (add_credit_unions.py writes
-- CSVs only), so run_queries.py stages all 13 Newark NCUA branches into an in-memory copy
-- as credit_unions(..., community_chartered 1/0). The committed .db is never modified.
-- Only community-chartered branches (open to the general public) count toward access;
-- the 11 occupational / associational / multiple-common-bond branches are excluded.

WITH wards AS (
    SELECT DISTINCT ward FROM tract_demographics
),
banks AS (
    SELECT ward, COUNT(*) AS bank_branch_count
    FROM fdic_branches
    GROUP BY ward
),
public_cus AS (
    SELECT ward, COUNT(*) AS credit_union_count
    FROM credit_unions
    WHERE community_chartered = 1
    GROUP BY ward
),
combined AS (
    SELECT
        w.ward,
        COALESCE(b.bank_branch_count, 0)  AS bank_branch_count,
        COALESCE(c.credit_union_count, 0) AS credit_union_count,
        COALESCE(b.bank_branch_count, 0) + COALESCE(c.credit_union_count, 0)
                                          AS total_financial_access_count
    FROM wards w
    LEFT JOIN banks      b ON b.ward = w.ward
    LEFT JOIN public_cus c ON c.ward = w.ward
)
SELECT
    ward,
    bank_branch_count,
    credit_union_count,
    total_financial_access_count,
    RANK() OVER (ORDER BY total_financial_access_count DESC) AS access_rank_most_to_least,
    ROUND(100.0 * total_financial_access_count
          / SUM(total_financial_access_count) OVER (), 1)    AS pct_of_citywide_access_points
FROM combined
ORDER BY ward;
