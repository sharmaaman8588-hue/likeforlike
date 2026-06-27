-- One row per store id. A relocation is a new id, so relocated_from links old and new.
CREATE OR REPLACE TABLE dim_store AS
SELECT
    s.store_id,
    s.store_name,
    s.region,
    s."format" AS format,
    s.size_sqm,
    CAST(s.open_date AS DATE) AS open_date,
    CAST(s.close_date AS DATE) AS close_date,
    s.relocated_from,
    CASE WHEN CAST(s.open_date AS DATE) < (SELECT min(week_start) FROM raw_fiscal_calendar)
         THEN 1 ELSE 0 END AS opened_before_data
FROM raw_store AS s
ORDER BY s.store_id;
