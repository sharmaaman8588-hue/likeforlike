-- One row per fiscal week with its comparable LY week under the active restatement setting.
CREATE OR REPLACE TABLE dim_fiscal_week AS
WITH base AS (
    SELECT
        week_key, fiscal_year, fiscal_week, fiscal_quarter, fiscal_month, month_name,
        CAST(week_start AS DATE) AS week_start,
        CAST(week_end AS DATE) AS week_end,
        is_week_53,
{% if restate %}
        ly_week_key_restated AS ly_week_key
{% else %}
        ly_week_key_naive AS ly_week_key
{% endif %}
    FROM raw_fiscal_calendar
),
first_year AS (SELECT min(fiscal_year) AS y FROM base)
SELECT
    b.week_key, b.fiscal_year, b.fiscal_week, b.fiscal_quarter, b.fiscal_month, b.month_name,
    b.week_start, b.week_end, b.is_week_53, b.ly_week_key,
    CASE
        WHEN b.ly_week_key IS NULL AND b.fiscal_year = (SELECT y FROM first_year) THEN 'no_ly_data'
        WHEN b.ly_week_key IS NULL THEN 'unmatched_ty'
        WHEN b.ly_week_key % 100 = b.fiscal_week THEN 'matched'
        ELSE 'restated'
    END AS ly_match,
    CASE
        WHEN b.fiscal_year + 1 <= (SELECT max(fiscal_year) FROM base)
         AND NOT EXISTS (SELECT 1 FROM base AS n WHERE n.ly_week_key = b.week_key)
        THEN 1 ELSE 0
    END AS is_unmatched_ly
FROM base AS b
ORDER BY b.week_key;
