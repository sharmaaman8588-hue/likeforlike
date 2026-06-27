-- Growth bridge: every TY and LY store-week sales value lands in exactly one bucket, so the
-- bucket contributions add up to total growth with no residual.
--
-- * Rows come from fct_store_week for every year that has an LY year in the data; the bucket is
--   the store-week's non_comp_reason.
-- * LY weeks that no TY week points at (week 1 after a restated 53-week year) have no store-week
--   row. They enter as 'extra_week' rows with zero TY sales, attached to the first week of the TY
--   year so they land in fiscal month 1.
CREATE OR REPLACE TABLE fct_growth_bridge AS
SELECT
    u.*,
    CASE u.bucket
        WHEN 'comp' THEN 1 WHEN 'new_store' THEN 2 WHEN 'closed_store' THEN 3
        WHEN 'refurbishment' THEN 4 ELSE 5
    END AS bucket_order
FROM (
SELECT
    f.store_id,
    f.week_key AS ty_week_key,
    f.ly_week_key,
    f.sales_ty,
    f.sales_ly_comparable AS sales_ly,
    f.non_comp_reason AS bucket
FROM fct_store_week AS f
WHERE f.non_comp_reason <> 'no_prior_year'
UNION ALL
SELECT
    sw.store_id,
    (lyw.fiscal_year + 1) * 100 + 1 AS ty_week_key,
    sw.week_key AS ly_week_key,
    0 AS sales_ty,
    sw.sales AS sales_ly,
    'extra_week' AS bucket
FROM raw_weekly_sales AS sw
JOIN dim_fiscal_week AS lyw ON lyw.week_key = sw.week_key
WHERE lyw.is_unmatched_ly = 1 AND sw.sales > 0
) AS u
ORDER BY u.ty_week_key, u.store_id, u.ly_week_key;
