"""Metric registry: the single place where each measure is defined.

Every measure has a name, an output column for the SQL reference mart, a SQL expression and a DAX
expression. The SQL queries used by the CLI and the measures written to the TMDL model are both
generated from this registry, and the static validator compares the model back against it.

Conventions shared by SQL and DAX: ``%`` measures are fractions (0.02 is 2%); ``pp`` measures are
percentage points of last year's total sales (2.0 is two points).
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import BUCKETS

STORE_WEEK = "StoreWeek"
GROWTH_BRIDGE = "GrowthBridge"

_SW = "'StoreWeek'"
_GB = "'GrowthBridge'"


@dataclass(frozen=True)
class Metric:
    name: str
    sql_column: str
    sql_expr: str
    dax: str
    table: str  # table that hosts the measure in the semantic model
    format_string: str
    description: str


def _comp_sum(column: str) -> str:
    return f"SUM({column}) FILTER (WHERE is_comp = 1)"


def _bucket_pp(name: str, column: str, bucket: str, label: str) -> Metric:
    """Contribution of one bridge bucket to total growth, in points of last year's sales."""
    sql = (
        f"100.0 * (COALESCE(SUM(sales_ty) FILTER (WHERE bucket = '{bucket}'), 0)"
        f" - COALESCE(SUM(sales_ly) FILTER (WHERE bucket = '{bucket}'), 0))"
        f" / NULLIF(SUM(sales_ly), 0)"
    )
    dax = (
        f"CALCULATE(100 * DIVIDE(SUM({_GB}[sales_ty]) - SUM({_GB}[sales_ly]), "
        f"CALCULATE(SUM({_GB}[sales_ly]), REMOVEFILTERS({_GB}[bucket]))), "
        f'{_GB}[bucket] = "{bucket}")'
    )
    return Metric(name, column, sql, dax, GROWTH_BRIDGE, "0.00", label)


REGISTRY: tuple[Metric, ...] = (
    Metric(
        "Sales",
        "sales",
        "SUM(sales_ty)",
        f"SUM({_SW}[sales_ty])",
        STORE_WEEK,
        "#,0",
        "Sales this year, all store-weeks.",
    ),
    Metric(
        "Sales LY Comparable",
        "sales_ly_comparable",
        "SUM(sales_ly_comparable)",
        f"SUM({_SW}[sales_ly_comparable])",
        STORE_WEEK,
        "#,0",
        "Sales in each store-week's comparable last-year week.",
    ),
    Metric(
        "Comp Sales TY",
        "comp_sales_ty",
        _comp_sum("sales_ty"),
        f"CALCULATE(SUM({_SW}[sales_ty]), {_SW}[is_comp] = 1)",
        STORE_WEEK,
        "#,0",
        "This-year sales of comp store-weeks.",
    ),
    Metric(
        "Comp Sales LY",
        "comp_sales_ly",
        _comp_sum("sales_ly_comparable"),
        f"CALCULATE(SUM({_SW}[sales_ly_comparable]), {_SW}[is_comp] = 1)",
        STORE_WEEK,
        "#,0",
        "Comparable last-year sales of comp store-weeks.",
    ),
    Metric(
        "LFL %",
        "lfl_pct",
        f"({_comp_sum('sales_ty')} - {_comp_sum('sales_ly_comparable')})"
        f" / NULLIF({_comp_sum('sales_ly_comparable')}, 0)",
        "DIVIDE([Comp Sales TY] - [Comp Sales LY], [Comp Sales LY])",
        STORE_WEEK,
        "0.00%",
        "Like-for-like sales growth.",
    ),
    Metric(
        "Total Growth %",
        "total_growth_pct",
        "(SUM(sales_ty) - SUM(sales_ly)) / NULLIF(SUM(sales_ly), 0)",
        f"DIVIDE(SUM({_GB}[sales_ty]) - SUM({_GB}[sales_ly]), SUM({_GB}[sales_ly]))",
        GROWTH_BRIDGE,
        "0.00%",
        "Total sales growth, all stores and weeks including the extra week.",
    ),
    _bucket_pp("Comp pp", "comp_pp", "comp", "Growth from comparable store-weeks."),
    _bucket_pp("New Store pp", "new_store_pp", "new_store", "Growth from new stores and ramp."),
    _bucket_pp("Closed Store pp", "closed_store_pp", "closed_store", "Growth lost to closures."),
    _bucket_pp(
        "Refurbishment pp", "refurbishment_pp", "refurbishment", "Growth in refurbishment weeks."
    ),
    _bucket_pp("Extra Week pp", "extra_week_pp", "extra_week", "Growth from the 53rd week."),
    Metric(
        "Bridge pp",
        "bridge_pp",
        "100.0 * (SUM(sales_ty) - SUM(sales_ly))"
        " / NULLIF(SUM(SUM(sales_ly)) OVER ({partition}), 0)",
        "100 * DIVIDE(SUM("
        f"{_GB}[sales_ty]) - SUM({_GB}[sales_ly]), "
        f"CALCULATE(SUM({_GB}[sales_ly]), REMOVEFILTERS({_GB}[bucket])))",
        GROWTH_BRIDGE,
        "0.00",
        "Contribution of the bucket in context to total growth; the waterfall's value.",
    ),
)

BY_NAME = {m.name: m for m in REGISTRY}
BY_COLUMN = {m.sql_column: m for m in REGISTRY}

_SOURCES = {
    STORE_WEEK: "fct_store_week AS f JOIN dim_fiscal_week AS w ON w.week_key = f.week_key",
    GROWTH_BRIDGE: "fct_growth_bridge AS f JOIN dim_fiscal_week AS w ON w.week_key = f.ty_week_key",
}

assert {m.sql_column for m in REGISTRY} == set(BY_COLUMN)  # unique output names
assert all(f"{b}_pp" in BY_COLUMN or b == "comp" for b in BUCKETS)


def select_sql(
    table: str,
    group_by: dict[str, str],
    metric_names: list[str],
    where: str | None = None,
) -> str:
    """SQL over the mart for the given measures, grouped by ``{alias: expression}`` pairs.

    ``f`` is the fact table and ``w`` is dim_fiscal_week joined on the fact's TY week.
    """
    metrics = [BY_NAME[n] for n in metric_names]
    wrong = [m.name for m in metrics if m.table != table]
    if wrong:
        raise ValueError(f"measures {wrong} are not defined on {table}")
    partition = ""
    non_bucket = [expr for alias, expr in group_by.items() if alias != "bucket"]
    if non_bucket:
        partition = "PARTITION BY " + ", ".join(non_bucket)
    selected = [f"{expr} AS {alias}" for alias, expr in group_by.items()]
    selected += [f"{m.sql_expr.format(partition=partition)} AS {m.sql_column}" for m in metrics]
    parts = [f"SELECT {', '.join(selected)}", f"FROM {_SOURCES[table]}"]
    if where:
        parts.append(f"WHERE {where}")
    if group_by:
        parts.append("GROUP BY " + ", ".join(group_by.values()))
        parts.append("ORDER BY " + ", ".join(group_by.values()))
    return "\n".join(parts)
