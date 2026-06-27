-- One row per store and TY week in which the store or its comparable LY week traded.
-- Every row gets exactly one non_comp_reason ('comp' for like-for-like weeks); the first rule
-- below that applies wins, in the order set by comp_policy.yaml.
CREATE OR REPLACE TABLE fct_store_week AS
WITH trading AS (
    -- Trading week number of each store-week with sales. Stores that opened before the data
    -- starts are treated as long-established.
    SELECT
        sw.store_id,
        sw.week_key,
        sw.sales,
        row_number() OVER (PARTITION BY sw.store_id ORDER BY sw.week_key)
            + CASE WHEN d.opened_before_data = 1 THEN 100000 ELSE 0 END AS trading_week_no
    FROM raw_weekly_sales AS sw
    JOIN dim_store AS d USING (store_id)
    WHERE sw.sales > 0
),
paired AS (
    SELECT
        d.store_id,
        w.week_key,
        w.fiscal_year,
        w.week_start,
        w.week_end,
        w.ly_week_key,
        w.ly_match,
        lyw.week_start AS ly_week_start,
        lyw.week_end AS ly_week_end,
        coalesce(ty.sales, 0) AS sales_ty,
        ty.trading_week_no AS ty_trading_week_no,
        coalesce(ly.sales, 0) AS sales_ly_comparable,
        ly.trading_week_no AS ly_trading_week_no
    FROM dim_store AS d
    CROSS JOIN dim_fiscal_week AS w
    LEFT JOIN dim_fiscal_week AS lyw ON lyw.week_key = w.ly_week_key
    LEFT JOIN trading AS ty ON ty.store_id = d.store_id AND ty.week_key = w.week_key
    LEFT JOIN trading AS ly ON ly.store_id = d.store_id AND ly.week_key = w.ly_week_key
),
classified AS (
    SELECT
        p.*,
        CASE
            WHEN p.ly_match = 'no_ly_data' THEN 'no_prior_year'
            WHEN p.ly_match = 'unmatched_ty' THEN 'extra_week'
{% if refurbishment %}
            WHEN EXISTS (
                SELECT 1 FROM raw_closure AS c
                WHERE c.store_id = p.store_id
                  AND CAST(c.closed_from AS DATE) <= p.week_end
                  AND CAST(c.closed_to AS DATE) >= p.week_start
            ) OR EXISTS (
                SELECT 1 FROM raw_closure AS c
                WHERE c.store_id = p.store_id
                  AND CAST(c.closed_from AS DATE) <= p.ly_week_end
                  AND CAST(c.closed_to AS DATE) >= p.ly_week_start
            ) THEN 'refurbishment'
{% endif %}
            WHEN p.sales_ty = 0 THEN 'closed_store'
            WHEN p.sales_ly_comparable = 0 THEN 'new_store'
{% if ramp_weeks > 0 %}
            WHEN p.ty_trading_week_no <= {{ ramp_weeks }}
              OR p.ly_trading_week_no <= {{ ramp_weeks }} THEN 'new_store'
{% endif %}
            ELSE 'comp'
        END AS non_comp_reason
    FROM paired AS p
    WHERE p.sales_ty > 0 OR p.sales_ly_comparable > 0
)
SELECT
    store_id,
    week_key,
    sales_ty,
    ly_week_key,
    sales_ly_comparable,
    CASE WHEN non_comp_reason = 'comp' THEN 1 ELSE 0 END AS is_comp,
    non_comp_reason
FROM classified
ORDER BY week_key, store_id;
