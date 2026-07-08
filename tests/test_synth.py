import json
import math
from datetime import date, timedelta

import pandas as pd
import pytest

from likeforlike import calendar as cal
from likeforlike import synth

from .conftest import as_date


def _fiscal_week_of(day: date) -> tuple[int, int]:
    for fy in synth.FISCAL_YEARS:
        for wk in cal.fiscal_weeks(fy):
            if wk.week_start <= day <= wk.week_end:
                return fy, wk.fiscal_week
    raise AssertionError(day)


def test_generation_is_deterministic_per_seed(raw):
    again = synth.generate(7)
    pd.testing.assert_frame_equal(raw.sales, again.sales)
    pd.testing.assert_frame_equal(raw.stores, again.stores)
    other = synth.generate(8)
    assert not raw.sales["sales"].equals(other.sales["sales"])


def test_chain_size_and_store_weeks(raw):
    first_day = cal.fiscal_year_start(2022)
    opened_before = [as_date(d) < first_day for d in raw.stores["open_date"]]
    assert sum(opened_before) == 60
    assert len(raw.stores) == 70
    assert 9_000 <= len(raw.sales) <= 10_500
    assert raw.sales["sales"].gt(0).all()
    assert raw.sales.duplicated(["store_id", "week_key"]).sum() == 0


def test_openings_are_six_in_fy2023_q3_and_three_in_fy2024_q1_plus_one_relocation(raw):
    first_day = cal.fiscal_year_start(2022)
    opened = raw.stores[[as_date(d) >= first_day for d in raw.stores["open_date"]]]
    relocated = opened[opened["relocated_from"].notna()]
    regular = opened[opened["relocated_from"].isna()]
    placed = [_fiscal_week_of(as_date(d)) for d in regular["open_date"]]
    assert sum(1 for fy, w in placed if fy == 2023 and 27 <= w <= 39) == 6
    assert sum(1 for fy, w in placed if fy == 2024 and w <= 13) == 3
    assert len(regular) == 9 and len(relocated) == 1


def test_closures_by_quarter_and_relocation_geometry(raw):
    closed = raw.stores[raw.stores["close_date"].notna()]
    relocated = raw.stores[raw.stores["relocated_from"].notna()].iloc[0]
    old = raw.stores[raw.stores["store_id"] == relocated["relocated_from"]].iloc[0]
    others = closed[closed["store_id"] != old["store_id"]]
    placed = sorted(_fiscal_week_of(as_date(d)) for d in others["close_date"])
    assert len(others) == 3
    assert placed[0][0] == 2023 and 14 <= placed[0][1] <= 26  # FY2023 Q2
    assert all(fy == 2024 and w >= 40 for fy, w in placed[1:])  # FY2024 Q4
    assert as_date(old["close_date"]) + timedelta(days=1) == as_date(relocated["open_date"])
    dlat = math.radians(relocated["lat"] - old["lat"])
    dlon = math.radians(relocated["lon"] - old["lon"]) * math.cos(math.radians(old["lat"]))
    assert abs(6371.0 * math.hypot(dlat, dlon) - 2.0) < 0.05


def test_four_refurbishments_of_six_to_ten_weeks_leave_no_sales_while_closed(raw):
    assert len(raw.closures) == 4
    sales = {(r.store_id, r.week_key) for r in raw.sales.itertuples()}
    weeks = [wk for fy in synth.FISCAL_YEARS for wk in cal.fiscal_weeks(fy)]
    for c in raw.closures.itertuples():
        start, end = as_date(c.closed_from), as_date(c.closed_to)
        assert 42 <= (end - start).days + 1 <= 70
        fully_closed = [w for w in weeks if start <= w.week_start and w.week_end <= end]
        assert fully_closed
        assert all((c.store_id, w.key) not in sales for w in fully_closed)


def test_new_store_ramps_from_half_to_full_over_twelve_weeks():
    factors = [synth.ramp_factor(k) for k in range(14)]
    assert factors[0] == 0.5 and factors[11] == 1.0 and factors[13] == 1.0
    assert all(b > a for a, b in zip(factors[:11], factors[1:12], strict=True))


def test_ramp_is_visible_in_generated_sales(raw):
    store = raw.stores[raw.stores["open_date"].map(as_date) == date(2023, 8, 6)]
    sid = store["store_id"].iloc[0]
    sales = raw.sales[raw.sales["store_id"] == sid].set_index("week_key")["sales"]
    keys = sorted(sales.index)

    def adjusted(key):
        wk = next(w for w in cal.fiscal_weeks(key // 100) if w.key == key)
        return sales[key] / synth.seasonal_index(wk.week_start) / synth.growth_factor(key // 100)

    assert 0.4 < adjusted(keys[0]) / adjusted(keys[14]) < 0.6


def test_seasonal_index_is_anchored_to_calendar_dates():
    assert synth.black_friday(2023) == date(2023, 11, 24)
    assert synth.black_friday(2024) == date(2024, 11, 29)
    assert synth.seasonal_index(date(2023, 11, 19)) == 1.6  # week containing 24 Nov 2023
    assert synth.seasonal_index(date(2024, 11, 24)) == 1.6  # week containing 29 Nov 2024
    assert synth.seasonal_index(date(2023, 12, 24)) == 1.8  # Christmas week
    assert synth.seasonal_index(date(2024, 12, 22)) == 1.8
    assert synth.seasonal_index(date(2024, 12, 29)) == 0.8  # week ending 4 Jan
    assert synth.seasonal_index(date(2025, 1, 5)) == 0.8  # week ending 11 Jan
    assert synth.seasonal_index(date(2025, 1, 12)) == 1.0  # week ending 18 Jan


def test_holiday_weeks_show_in_sales_of_established_stores(raw):
    old = set(raw.stores["store_id"][:40])
    chain = raw.sales[raw.sales["store_id"].isin(old)]
    weekly = chain.groupby("week_key")["sales"].sum()
    # FY2023: week 43 holds Black Friday, week 48 holds Christmas, week 49 ends 6 January.
    assert 1.45 < weekly[202343] / weekly[[202341, 202342]].mean() < 1.75
    assert 1.65 < weekly[202348] / weekly[[202346, 202347]].mean() < 1.95
    assert 0.7 < weekly[202349] / weekly[[202346, 202347]].mean() < 0.9


def test_trading_stores_grow_two_percent_a_year(raw):
    special = set(raw.closures["store_id"]) | set(
        raw.stores["store_id"][raw.stores["close_date"].notna()]
    )
    steady = raw.stores[raw.stores["open_date"].map(as_date) < date(2022, 1, 1)]["store_id"]
    steady = set(steady) - special
    s = raw.sales[raw.sales["store_id"].isin(steady)]
    # weeks 2-30 of FY2023 against the same week of FY2022: no 53rd-week shift, no holidays
    ty = s[s["week_key"].between(202302, 202330)]["sales"].sum()
    ly = s[s["week_key"].between(202202, 202230)]["sales"].sum()
    assert abs(ty / ly - 1.02) < 0.002
    assert synth.growth_factor(2024) == 1.02**2


def test_raw_files_round_trip(raw, tmp_path):
    synth.write_raw(raw, tmp_path)
    text = (tmp_path / "truth.json").read_text()
    json.loads(text, parse_constant=lambda name: pytest.fail(f"{name} in truth.json"))
    loaded = synth.load_raw(tmp_path)
    assert len(loaded.sales) == len(raw.sales)
    assert loaded.sales["sales"].sum() == raw.sales["sales"].sum()
    assert loaded.truth["true_lfl_growth"] == 0.02
    assert len(loaded.truth["openings"]) == 10
    assert len(loaded.truth["permanent_closures"]) == 4
