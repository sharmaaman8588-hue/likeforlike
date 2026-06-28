"""Queries over the mart: LFL by period, the growth bridge and policy what-ifs."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import pandas as pd

from . import metrics
from .mart import build_mart
from .policy import CompPolicy
from .schema import BUCKETS
from .synth import RawData

PERIODS: dict[str, dict[str, str]] = {
    "year": {"fiscal_year": "w.fiscal_year"},
    "quarter": {"fiscal_year": "w.fiscal_year", "fiscal_quarter": "w.fiscal_quarter"},
    "month": {
        "fiscal_year": "w.fiscal_year",
        "fiscal_month": "w.fiscal_month",
        "month_name": "w.month_name",
    },
    "week": {"fiscal_year": "w.fiscal_year", "fiscal_week": "w.fiscal_week"},
}

DECEMBER = 11  # fiscal month 11 is December in a Feb-Jan fiscal year
BUCKET_COLUMNS = {
    "comp": "comp_pp",
    "new_store": "new_store_pp",
    "closed_store": "closed_store_pp",
    "refurbishment": "refurbishment_pp",
    "extra_week": "extra_week_pp",
}
assert tuple(BUCKET_COLUMNS) == BUCKETS


def lfl_by(con: duckdb.DuckDBPyConnection, year: int, by: str = "month") -> pd.DataFrame:
    """Comp sales, comparable LY sales and LFL % for each period of a fiscal year."""
    if by not in PERIODS:
        raise ValueError(f"unknown period {by!r}; choose from {sorted(PERIODS)}")
    sql = metrics.select_sql(
        metrics.STORE_WEEK,
        PERIODS[by],
        ["Comp Sales TY", "Comp Sales LY", "LFL %"],
        where=f"w.fiscal_year = {int(year)}",
    )
    return con.execute(sql).df()


def bridge_by_bucket(con: duckdb.DuckDBPyConnection, year: int) -> pd.DataFrame:
    """Growth contribution of each bucket for a fiscal year, in points of LY sales."""
    sql = metrics.select_sql(
        metrics.GROWTH_BRIDGE,
        {"bucket": "f.bucket"},
        ["Bridge pp"],
        where=f"w.fiscal_year = {int(year)}",
    )
    frame = con.execute(sql).df().set_index("bucket").reindex(list(BUCKETS)).fillna(0.0)
    return frame.reset_index()


def total_growth(con: duckdb.DuckDBPyConnection, year: int) -> float:
    sql = metrics.select_sql(
        metrics.GROWTH_BRIDGE,
        {},
        ["Total Growth %"],
        where=f"w.fiscal_year = {int(year)}",
    )
    return float(con.execute(sql).fetchone()[0])


def bridge_residuals(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Total growth minus the sum of the five bucket contributions, per fiscal month and year."""
    names = ["Total Growth %", *(m.name for m in metrics.REGISTRY if m.sql_column.endswith("_pp"))]
    names = [n for n in names if n != "Bridge pp"]
    rows = []
    for level, group in (("month", PERIODS["month"]), ("year", PERIODS["year"])):
        frame = con.execute(metrics.select_sql(metrics.GROWTH_BRIDGE, group, names)).df()
        frame["level"] = level
        rows.append(frame)
    out = pd.concat(rows, ignore_index=True)
    pp_columns = list(BUCKET_COLUMNS.values())
    out["bucket_sum_pp"] = out[pp_columns].sum(axis=1)
    out["residual_pp"] = out["total_growth_pct"] * 100.0 - out["bucket_sum_pp"]
    return out


@dataclass(frozen=True)
class PolicyRow:
    label: str
    year_lfl: float
    december_lfl: float | None


def policy_diff(
    raw: RawData,
    base: CompPolicy,
    year: int,
    variants: dict[str, CompPolicy],
) -> pd.DataFrame:
    """LFL for the year and for fiscal December under the base policy and each variant."""
    rows = []
    for label, policy in {"baseline": base, **variants}.items():
        con = build_mart(raw, policy)
        try:
            yearly = lfl_by(con, year, "year")
            monthly = lfl_by(con, year, "month")
        finally:
            con.close()
        december = monthly.loc[monthly["fiscal_month"] == DECEMBER, "lfl_pct"]
        rows.append(
            {
                "policy": label,
                "setting": policy.label(),
                "year_lfl_pct": float(yearly["lfl_pct"].iloc[0]),
                "december_lfl_pct": float(december.iloc[0]),
            }
        )
    frame = pd.DataFrame(rows)
    frame["year_vs_baseline_pp"] = (frame["year_lfl_pct"] - frame["year_lfl_pct"].iloc[0]) * 100
    frame["december_vs_baseline_pp"] = (
        frame["december_lfl_pct"] - frame["december_lfl_pct"].iloc[0]
    ) * 100
    return frame
