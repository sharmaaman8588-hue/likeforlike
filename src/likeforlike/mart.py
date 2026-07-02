"""Build the DuckDB reference mart from raw data and export it as CSV for Power BI."""

from __future__ import annotations

from pathlib import Path

import duckdb
import jinja2
import pandas as pd

from .calendar import calendar_frame
from .policy import CompPolicy
from .resources import project_file
from .schema import TABLES
from .synth import RawData

SQL_STEPS = ("dim_store", "dim_fiscal_week", "fct_store_week", "fct_growth_bridge")


def _clean(frame: pd.DataFrame) -> pd.DataFrame:
    """Turn pandas missing values into None so DuckDB sees plain NULLs."""
    return frame.astype(object).where(frame.notna(), None)


def render_sql(step: str, policy: CompPolicy) -> str:
    template = project_file(f"sql/{step}.sql").read_text()
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True, autoescape=False
    )
    return env.from_string(template).render(
        restate=policy.restate,
        ramp_weeks=policy.ramp_weeks,
        refurbishment=policy.refurbishment,
    )


def fiscal_years_of(raw: RawData) -> list[int]:
    years = sorted({int(k) // 100 for k in raw.sales["week_key"].unique()})
    if years != list(range(years[0], years[-1] + 1)):
        raise ValueError(f"sales cover non-consecutive fiscal years: {years}")
    return years


def build_mart(
    raw: RawData, policy: CompPolicy, database: str | Path = ":memory:"
) -> duckdb.DuckDBPyConnection:
    """Create the raw_* tables and run the four SQL steps in order."""
    con = duckdb.connect(str(database))
    inputs = {
        "raw_store": raw.stores,
        "raw_closure": raw.closures,
        "raw_weekly_sales": raw.sales,
        "raw_fiscal_calendar": calendar_frame(fiscal_years_of(raw)),
    }
    for name, frame in inputs.items():
        con.register(f"{name}_df", _clean(frame))
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {name}_df")
        con.unregister(f"{name}_df")
    for step in SQL_STEPS:
        con.execute(render_sql(step, policy))
    return con


def export_mart(con: duckdb.DuckDBPyConnection, out_dir: Path) -> list[Path]:
    """Write one CSV per mart table, columns and order as defined in ``schema.TABLES``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for table in TABLES:
        path = out_dir / table.csv_name
        columns = ", ".join(table.column_names)
        con.execute(
            f"COPY (SELECT {columns} FROM {table.mart_table} ORDER BY {table.order_by}) "
            f"TO '{path}' (HEADER, DELIMITER ',')"
        )
        written.append(path)
    return written
