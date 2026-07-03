"""Seeded synthetic data for a 60-store homeware chain over FY2022-FY2024 (70 store ids).

Weekly sales = store base x seasonal index x growth x opening ramp x open-day fraction x noise.

Documented properties and the patterns injected on purpose:

* The seasonal index is anchored to calendar dates, not fiscal week numbers: the week containing
  Black Friday is x1.6, the week containing 25 December is x1.8, weeks ending 1-14 January are
  x0.8 and summer carries a mild lift. This is what makes the 53rd-week restatement testable.
* Every store that is trading and not ramping grows by exactly +2.0% a year (``GROWTH``).
* 6 stores open in FY2023 Q3 and 3 in FY2024 Q1 and ramp from 50% to 100% of mature sales over
  their first 12 weeks. A relocation closes one store and opens a new id 2 km away.
* 1 permanent closure in FY2023 Q2 and 2 in FY2024 Q4; 4 refurbishment closures of 6-10 weeks
  that start on arbitrary days, so some store-weeks are only partly closed.
* FY2023 is a 53-week year.

Noise is uniform in +/-3% per store-week. All amounts are whole dollars.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .calendar import fiscal_weeks, fiscal_year_start

DEFAULT_SEED = 7
FISCAL_YEARS = (2022, 2023, 2024)
TRUE_LFL_GROWTH = 0.02
NOISE_HALF_WIDTH = 0.03
RAMP_WEEKS = 12
RAMP_START = 0.5
BLACK_FRIDAY_MULTIPLIER = 1.6
CHRISTMAS_MULTIPLIER = 1.8
JANUARY_MULTIPLIER = 0.8
SUMMER_LIFT = 0.05
DOLLARS_PER_SQM_WEEK = 32.0

N_INITIAL_STORES = 60
REGIONS = ("North", "South", "East", "West", "Central")
REGION_CENTRES = {
    "North": (54.0, -1.6),
    "South": (51.0, -1.2),
    "East": (52.4, 0.8),
    "West": (51.6, -3.0),
    "Central": (52.5, -1.5),
}
TOWNS = (
    "Ashford", "Bedford", "Carlisle", "Dunmore", "Eastleigh", "Fairhaven", "Glenrock", "Harlow",
    "Ironbridge", "Kendal", "Lakeside", "Marlow", "Northgate", "Oakham", "Penrith", "Quayside",
    "Redcar", "Stamford", "Thornbury", "Upton", "Verwood", "Whitby", "Yarmouth", "Aldgate",
    "Brackley", "Colwyn", "Dalton", "Epsom", "Farnham", "Grantham", "Hexham", "Ilkley",
    "Jarrow", "Keswick", "Louth", "Morley", "Newbury", "Ormskirk", "Poole", "Ramsey",
    "Sandwich", "Tamworth", "Ulverston", "Wantage", "Alnwick", "Buxton", "Crewe", "Deal",
    "Ely", "Frome", "Gosport", "Halifax", "Ipswich", "Kidwell", "Lewes", "Maldon",
    "Nantwich", "Oswestry", "Pickering", "Rugby", "Sleaford", "Tenbury", "Uckfield", "Vale",
    "Wetherby", "Yeovil", "Alford", "Bourne", "Cleator", "Driffield",
)  # fmt: skip


@dataclass
class RawData:
    stores: pd.DataFrame
    closures: pd.DataFrame
    sales: pd.DataFrame
    truth: dict


def black_friday(year: int) -> date:
    """The day after US Thanksgiving (the fourth Thursday of November)."""
    first = date(year, 11, 1)
    first_thursday = first + timedelta(days=(3 - first.weekday()) % 7)
    return first_thursday + timedelta(days=22)


def seasonal_index(week_start: date) -> float:
    """Seasonal multiplier of the Sunday-Saturday week starting on ``week_start``."""
    week_end = week_start + timedelta(days=6)
    index = 1.0
    if week_start <= black_friday(week_start.year) <= week_end:
        index *= BLACK_FRIDAY_MULTIPLIER
    if week_start <= date(week_start.year, 12, 25) <= week_end:
        index *= CHRISTMAS_MULTIPLIER
    if week_end.month == 1 and week_end.day <= 14:
        index *= JANUARY_MULTIPLIER
    day_of_year = (week_start + timedelta(days=3)).timetuple().tm_yday
    if 152 <= day_of_year <= 244:
        index *= 1.0 + SUMMER_LIFT * math.sin(math.pi * (day_of_year - 152) / 92)
    return index


def growth_factor(fiscal_year: int) -> float:
    return (1.0 + TRUE_LFL_GROWTH) ** (fiscal_year - FISCAL_YEARS[0])


def ramp_factor(weeks_since_open: int) -> float:
    """Share of mature sales in the n-th week after opening (0 = first week)."""
    if weeks_since_open >= RAMP_WEEKS:
        return 1.0
    return RAMP_START + (1.0 - RAMP_START) * weeks_since_open / (RAMP_WEEKS - 1)


def _fiscal_week_start(fiscal_year: int, fiscal_week: int) -> date:
    return fiscal_year_start(fiscal_year) + timedelta(days=7 * (fiscal_week - 1))


def _build_stores(rng: np.random.Generator) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Store master, the ledger of injected events, and the refurbishment closures."""
    first_data_day = fiscal_year_start(FISCAL_YEARS[0])
    base = date(2008, 1, 6)
    max_weeks = (first_data_day - base).days // 7 - 60
    initial_open = sorted(
        base + timedelta(weeks=int(k)) for k in rng.integers(0, max_weeks, N_INITIAL_STORES)
    )

    roles = [int(i) for i in rng.permutation(N_INITIAL_STORES)]
    reloc_old, closure23, *rest = roles
    closure24 = rest[:2]
    refurbs = rest[2:6]

    close_dates: dict[int, date] = {}
    events: dict = {"permanent_closures": [], "refurbishments": [], "openings": []}

    week = int(rng.integers(15, 26))
    close_dates[closure23] = _fiscal_week_start(2023, week) + timedelta(days=6)
    for idx, w in zip(
        closure24, rng.choice([46, 47, 48, 49, 50, 51], 2, replace=False), strict=True
    ):
        close_dates[idx] = _fiscal_week_start(2024, int(w)) + timedelta(days=6)

    relocation_close = _fiscal_week_start(2023, 40) + timedelta(days=6)  # Saturday 4 Nov 2023
    relocation_open = relocation_close + timedelta(days=1)
    close_dates[reloc_old] = relocation_close

    closure_rows = []
    for position, idx in enumerate(refurbs):
        fy = 2023 if position < 2 else 2024
        start_week = int(rng.integers(6, 30 if fy == 2024 else 34))
        start = _fiscal_week_start(fy, start_week) + timedelta(days=int(rng.integers(0, 7)))
        length = int(rng.integers(42, 71))  # 6 to 10 weeks
        closure_rows.append((idx, start, start + timedelta(days=length - 1)))

    openings: list[tuple[date, str]] = []
    for w in rng.choice(np.arange(27, 40), 6, replace=False):
        openings.append((_fiscal_week_start(2023, int(w)), "opening"))
    for w in rng.choice(np.arange(2, 13), 3, replace=False):
        openings.append((_fiscal_week_start(2024, int(w)), "opening"))
    openings.append((relocation_open, "relocation"))
    openings.sort(key=lambda item: item[0])

    rows = []
    for i, open_date in enumerate(initial_open):
        rows.append({"index": i, "open_date": open_date, "relocated_from": None})
    for j, (open_date, kind) in enumerate(openings):
        rows.append(
            {
                "index": N_INITIAL_STORES + j,
                "open_date": open_date,
                "relocated_from": f"S{reloc_old + 1:03d}" if kind == "relocation" else None,
            }
        )

    stores = []
    for row in rows:
        i = row["index"]
        region = str(rng.choice(REGIONS))
        size = int(np.clip(rng.lognormal(math.log(1600), 0.3), 800, 3200) // 10 * 10)
        lat0, lon0 = REGION_CENTRES[region]
        lat = lat0 + float(rng.normal(0, 0.4))
        lon = lon0 + float(rng.normal(0, 0.5))
        stores.append(
            {
                "store_id": f"S{i + 1:03d}",
                "store_name": f"{TOWNS[i]} Home",
                "region": region,
                "format": "compact" if size < 1300 else ("standard" if size < 2100 else "flagship"),
                "size_sqm": size,
                "productivity": round(float(rng.uniform(0.85, 1.15)), 4),
                "open_date": row["open_date"],
                "close_date": close_dates.get(i),
                "relocated_from": row["relocated_from"],
                "lat": round(lat, 5),
                "lon": round(lon, 5),
            }
        )
    frame = pd.DataFrame(stores)

    # The relocated store sits 2 km north of the store it replaces.
    new_idx = frame.index[frame["relocated_from"].notna()][0]
    old_idx = int(frame.loc[new_idx, "relocated_from"][1:]) - 1
    frame.loc[new_idx, "lat"] = round(frame.loc[old_idx, "lat"] + 2.0 / 111.195, 5)
    frame.loc[new_idx, "lon"] = frame.loc[old_idx, "lon"]
    frame.loc[new_idx, "region"] = frame.loc[old_idx, "region"]

    def sid(i: int) -> str:
        return f"S{i + 1:03d}"

    closures = pd.DataFrame(
        [
            {
                "store_id": sid(idx),
                "closed_from": start,
                "closed_to": end,
                "reason": "refurbishment",
            }
            for idx, start, end in closure_rows
        ]
    )
    events["permanent_closures"] = [
        {"store_id": sid(i), "last_trading_day": d.isoformat()}
        for i, d in sorted(close_dates.items())
    ]
    events["refurbishments"] = [
        {
            "store_id": r.store_id,
            "closed_from": r.closed_from.isoformat(),
            "closed_to": r.closed_to.isoformat(),
        }
        for r in closures.itertuples()
    ]
    events["openings"] = [
        {
            "store_id": r.store_id,
            "open_date": r.open_date.isoformat(),
            "relocated_from": None if pd.isna(r.relocated_from) else r.relocated_from,
        }
        for r in frame[frame["open_date"] >= first_data_day].itertuples()
    ]
    return frame, events, closures


def generate(seed: int = DEFAULT_SEED) -> RawData:
    """Build the stores, refurbishment closures and weekly sales for the whole chain."""
    rng = np.random.default_rng(seed)
    stores, events, closures = _build_stores(rng)

    closure_spans: dict[str, list[tuple[date, date]]] = {}
    for r in closures.itertuples():
        closure_spans.setdefault(r.store_id, []).append((r.closed_from, r.closed_to))

    weeks = [wk for fy in FISCAL_YEARS for wk in fiscal_weeks(fy)]
    first_day = weeks[0].week_start
    sales_rows = []
    for store in stores.itertuples():
        base = store.size_sqm * DOLLARS_PER_SQM_WEEK * store.productivity
        spans = closure_spans.get(store.store_id, [])
        for wk in weeks:
            noise = 1.0 + float(rng.uniform(-NOISE_HALF_WIDTH, NOISE_HALF_WIDTH))
            open_days = 0
            for offset in range(7):
                day = wk.week_start + timedelta(days=offset)
                if day < store.open_date:
                    continue
                if store.close_date is not None and day > store.close_date:
                    continue
                if any(a <= day <= b for a, b in spans):
                    continue
                open_days += 1
            if open_days == 0:
                continue
            ramp = 1.0
            if store.open_date >= first_day:
                ramp = ramp_factor((wk.week_start - store.open_date).days // 7)
            amount = (
                base
                * seasonal_index(wk.week_start)
                * growth_factor(wk.fiscal_year)
                * ramp
                * (open_days / 7)
                * noise
            )
            sales_rows.append((store.store_id, wk.key, int(round(amount))))
    sales = pd.DataFrame(sales_rows, columns=["store_id", "week_key", "sales"])

    truth = {
        "seed": seed,
        "fiscal_years": list(FISCAL_YEARS),
        "true_lfl_growth": TRUE_LFL_GROWTH,
        "noise_half_width": NOISE_HALF_WIDTH,
        "ramp_weeks": RAMP_WEEKS,
        "week_53_year": 2023,
        "multipliers": {
            "black_friday_week": BLACK_FRIDAY_MULTIPLIER,
            "christmas_week": CHRISTMAS_MULTIPLIER,
            "first_two_weeks_of_january": JANUARY_MULTIPLIER,
        },
        **events,
    }
    return RawData(stores.drop(columns=["productivity"]), closures, sales, truth)


def write_raw(raw: RawData, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / "stores.csv", out_dir / "closures.csv", out_dir / "weekly_sales.csv"]
    raw.stores.to_csv(paths[0], index=False)
    raw.closures.to_csv(paths[1], index=False)
    raw.sales.to_csv(paths[2], index=False)
    truth_path = out_dir / "truth.json"
    truth_path.write_text(json.dumps(raw.truth, indent=2) + "\n")
    return [*paths, truth_path]


def load_raw(directory: Path) -> RawData:
    """Read the files written by :func:`write_raw`. Dates stay ISO strings."""
    stores = pd.read_csv(directory / "stores.csv", dtype={"close_date": "string"})
    closures = pd.read_csv(directory / "closures.csv")
    sales = pd.read_csv(directory / "weekly_sales.csv")
    truth = json.loads((directory / "truth.json").read_text())
    return RawData(stores, closures, sales, truth)
