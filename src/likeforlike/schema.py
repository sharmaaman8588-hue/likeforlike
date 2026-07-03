"""Column layout of the exported mart tables, shared by the CSV export and the TMDL generator."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    name: str
    tmdl_type: str  # string | int64 | double | dateTime
    summarize_by: str = "none"
    sort_by: str | None = None
    format_string: str | None = None

    @property
    def m_type(self) -> str:
        return {
            "string": "type text",
            "int64": "Int64.Type",
            "double": "type number",
            "dateTime": "type date",
        }[self.tmdl_type]


@dataclass(frozen=True)
class Table:
    name: str  # name in the semantic model, also the CSV file stem
    mart_table: str  # DuckDB table it is exported from
    order_by: str
    columns: tuple[Column, ...]

    @property
    def csv_name(self) -> str:
        return f"{self.name}.csv"

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


_STR = "string"
_INT = "int64"
_DATE = "dateTime"

STORE = Table(
    "Store",
    "dim_store",
    "store_id",
    (
        Column("store_id", _STR),
        Column("store_name", _STR),
        Column("region", _STR),
        Column("format", _STR),
        Column("size_sqm", _INT),
        Column("open_date", _DATE, format_string="yyyy-mm-dd"),
        Column("close_date", _DATE, format_string="yyyy-mm-dd"),
        Column("relocated_from", _STR),
    ),
)

FISCAL_CALENDAR = Table(
    "FiscalCalendar",
    "dim_fiscal_week",
    "week_key",
    (
        Column("week_key", _INT),
        Column("fiscal_year", _INT),
        Column("fiscal_week", _INT),
        Column("fiscal_quarter", _INT),
        Column("fiscal_month", _INT),
        Column("month_name", _STR, sort_by="fiscal_month"),
        Column("week_start", _DATE, format_string="yyyy-mm-dd"),
        Column("week_end", _DATE, format_string="yyyy-mm-dd"),
        Column("is_week_53", _INT),
        Column("ly_week_key", _INT),
        Column("ly_match", _STR),
        Column("is_unmatched_ly", _INT),
    ),
)

STORE_WEEK = Table(
    "StoreWeek",
    "fct_store_week",
    "week_key, store_id",
    (
        Column("store_id", _STR),
        Column("week_key", _INT),
        Column("sales_ty", _INT, "sum", format_string="#,0"),
        Column("ly_week_key", _INT),
        Column("sales_ly_comparable", _INT, "sum", format_string="#,0"),
        Column("is_comp", _INT),
        Column("non_comp_reason", _STR),
    ),
)

GROWTH_BRIDGE = Table(
    "GrowthBridge",
    "fct_growth_bridge",
    "ty_week_key, store_id, ly_week_key",
    (
        Column("store_id", _STR),
        Column("ty_week_key", _INT),
        Column("ly_week_key", _INT),
        Column("sales_ty", _INT, "sum", format_string="#,0"),
        Column("sales_ly", _INT, "sum", format_string="#,0"),
        Column("bucket", _STR, sort_by="bucket_order"),
        Column("bucket_order", _INT),
    ),
)

TABLES: tuple[Table, ...] = (STORE, FISCAL_CALENDAR, STORE_WEEK, GROWTH_BRIDGE)


@dataclass(frozen=True)
class Relationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str


RELATIONSHIPS: tuple[Relationship, ...] = (
    Relationship("StoreWeek", "store_id", "Store", "store_id"),
    Relationship("StoreWeek", "week_key", "FiscalCalendar", "week_key"),
    Relationship("GrowthBridge", "store_id", "Store", "store_id"),
    Relationship("GrowthBridge", "ty_week_key", "FiscalCalendar", "week_key"),
)

BUCKETS = ("comp", "new_store", "closed_store", "refurbishment", "extra_week")
