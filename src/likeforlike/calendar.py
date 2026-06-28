"""NRF-style 4-5-4 fiscal calendar and the comparable-week map.

A fiscal year ends on the Saturday nearest 31 January and runs Sunday to Saturday. Fiscal year
``N`` is the year that starts in January/February of calendar year ``N`` (FY2024 begins on
4 February 2024). Every quarter has 13 weeks split 4-5-4 into months; a 53rd week, when the year
needs one, is added to the last month (January).

The comparable-week map pairs each "this year" (TY) week with the "last year" (LY) week it is
judged against. After a 53-week year the whole year is shifted by one week (restatement), so
TY week ``w`` compares with LY week ``w + 1`` and the same calendar days are compared. Weeks
that have no partner on one side are recorded, not silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

MONTH_NAMES = ("Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan")
MONTH_WEEKS = (4, 5, 4)  # weeks in the three months of every quarter


def week_key(fiscal_year: int, fiscal_week: int) -> int:
    """Integer key for a fiscal week, e.g. FY2024 week 7 -> 202407."""
    return fiscal_year * 100 + fiscal_week


def split_week_key(key: int) -> tuple[int, int]:
    return key // 100, key % 100


def nearest_saturday(day: date) -> date:
    """The Saturday closest to ``day`` (ties are impossible: a week has an odd number of days)."""
    offset = (5 - day.weekday()) % 7  # days forward to the next Saturday (0 if already Saturday)
    return day + timedelta(days=offset if offset <= 3 else offset - 7)


def fiscal_year_end(fiscal_year: int) -> date:
    """Last day of the fiscal year: the Saturday nearest 31 January of the next calendar year."""
    return nearest_saturday(date(fiscal_year + 1, 1, 31))


def fiscal_year_start(fiscal_year: int) -> date:
    """First day (a Sunday) of the fiscal year."""
    return fiscal_year_end(fiscal_year - 1) + timedelta(days=1)


def weeks_in_year(fiscal_year: int) -> int:
    days = (fiscal_year_end(fiscal_year) - fiscal_year_start(fiscal_year)).days + 1
    return days // 7


def month_of_week(fiscal_week: int) -> int:
    """Fiscal month (1 = February ... 12 = January) of a week; week 53 belongs to January."""
    if fiscal_week >= 53:
        return 12
    quarter, in_quarter = divmod(fiscal_week - 1, 13)
    if in_quarter < MONTH_WEEKS[0]:
        month_in_quarter = 0
    elif in_quarter < MONTH_WEEKS[0] + MONTH_WEEKS[1]:
        month_in_quarter = 1
    else:
        month_in_quarter = 2
    return quarter * 3 + month_in_quarter + 1


def quarter_of_week(fiscal_week: int) -> int:
    return min((fiscal_week - 1) // 13, 3) + 1


@dataclass(frozen=True)
class FiscalWeek:
    fiscal_year: int
    fiscal_week: int
    week_start: date
    week_end: date

    @property
    def key(self) -> int:
        return week_key(self.fiscal_year, self.fiscal_week)

    @property
    def fiscal_month(self) -> int:
        return month_of_week(self.fiscal_week)

    @property
    def fiscal_quarter(self) -> int:
        return quarter_of_week(self.fiscal_week)

    @property
    def month_name(self) -> str:
        return MONTH_NAMES[self.fiscal_month - 1]


def fiscal_weeks(fiscal_year: int) -> list[FiscalWeek]:
    start = fiscal_year_start(fiscal_year)
    return [
        FiscalWeek(
            fiscal_year,
            w,
            start + timedelta(days=7 * (w - 1)),
            start + timedelta(days=7 * (w - 1) + 6),
        )
        for w in range(1, weeks_in_year(fiscal_year) + 1)
    ]


@dataclass(frozen=True)
class ComparableMap:
    """Mapping of TY week keys to LY week keys for one restatement setting."""

    ty_to_ly: dict[int, int | None]
    unmatched_ty: list[int] = field(default_factory=list)
    unmatched_ly: list[int] = field(default_factory=list)


def comparable_week_map(fiscal_years: list[int], restate: bool = True) -> ComparableMap:
    """Pair every TY week with its LY week.

    With ``restate`` the year after a 53-week year maps TY week ``w`` to LY week ``w + 1``;
    every other year, and every year when ``restate`` is false, maps ``w`` to ``w``.
    The first year has no LY in the calendar, so its weeks map to ``None`` without being
    recorded as unmatched. A TY week whose partner does not exist is ``unmatched_ty``; an LY week
    that no TY week points at is ``unmatched_ly``.
    """
    years = sorted(fiscal_years)
    ty_to_ly: dict[int, int | None] = {}
    unmatched_ty: list[int] = []
    unmatched_ly: list[int] = []
    for ty in years:
        ly = ty - 1
        ty_weeks = weeks_in_year(ty)
        if ly not in years:
            for w in range(1, ty_weeks + 1):
                ty_to_ly[week_key(ty, w)] = None
            continue
        ly_weeks = weeks_in_year(ly)
        shift = 1 if (restate and ly_weeks == 53) else 0
        image: set[int] = set()
        for w in range(1, ty_weeks + 1):
            partner = w + shift
            key = week_key(ty, w)
            if partner <= ly_weeks:
                ty_to_ly[key] = week_key(ly, partner)
                image.add(partner)
            else:
                ty_to_ly[key] = None
                unmatched_ty.append(key)
        unmatched_ly.extend(week_key(ly, w) for w in range(1, ly_weeks + 1) if w not in image)
    return ComparableMap(ty_to_ly, unmatched_ty, unmatched_ly)


def calendar_frame(fiscal_years: list[int]) -> pd.DataFrame:
    """One row per fiscal week with both the restated and the naive (same week number) LY key."""
    restated = comparable_week_map(fiscal_years, restate=True).ty_to_ly
    naive = comparable_week_map(fiscal_years, restate=False).ty_to_ly
    rows = []
    for fy in sorted(fiscal_years):
        for wk in fiscal_weeks(fy):
            rows.append(
                {
                    "week_key": wk.key,
                    "fiscal_year": fy,
                    "fiscal_week": wk.fiscal_week,
                    "fiscal_quarter": wk.fiscal_quarter,
                    "fiscal_month": wk.fiscal_month,
                    "month_name": wk.month_name,
                    "week_start": wk.week_start,
                    "week_end": wk.week_end,
                    "is_week_53": int(wk.fiscal_week == 53),
                    "ly_week_key_restated": restated[wk.key],
                    "ly_week_key_naive": naive[wk.key],
                }
            )
    frame = pd.DataFrame(rows)
    for col in ("ly_week_key_restated", "ly_week_key_naive"):
        frame[col] = frame[col].astype("Int64")
    return frame
