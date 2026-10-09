-- Dashboard panel : Alt-Finance Map (point-level export, colored by category)
-- Categories      : check_cashing, money_transfer, pawn_shop, payday_loan
-- Feeds           : data/processed/alt_finance_by_ward.csv (one dot per business)
-- Upstream Python : scripts/geocode_alt_finance.py (Nominatim via geopy, Newark bounding-box
--                   check) -> scripts/spatial_join_wards.py (geopandas point-in-polygon)
-- Source table    : alt_finance (31 manually compiled businesses, all geocode_status = 'ok')
--
-- The first eight columns reproduce the CSV exactly. The window columns give the number
-- of same-category businesses in the ward and in the whole city.

SELECT
    name,
    address,
    category,
    source_search,
    latitude,
    longitude,
    geocode_status,
    ward,
    COUNT(*) OVER (PARTITION BY ward, category) AS ward_category_count,
    COUNT(*) OVER (PARTITION BY category)       AS citywide_category_count
FROM alt_finance
ORDER BY ward, category, name;
