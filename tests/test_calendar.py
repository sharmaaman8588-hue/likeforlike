from datetime import date, timedelta

import pytest

from likeforlike import calendar as cal


def test_fiscal_years_match_the_nrf_boundaries():
    assert (cal.fiscal_year_start(2022), cal.fiscal_year_end(2022)) == (
        date(2022, 1, 30),
        date(2023, 1, 28),
    )
    assert (cal.fiscal_year_start(2023), cal.fiscal_year_end(2023)) == (
        date(2023, 1, 29),
        date(2024, 2, 3),
    )
    assert (cal.fiscal_year_start(2024), cal.fiscal_year_end(2024)) == (
        date(2024, 2, 4),
        date(2025, 2, 1),
    )
    assert [cal.weeks_in_year(y) for y in (2022, 2023, 2024)] == [52, 53, 52]


def test_week_53_is_28_january_to_3_february_2024():
    last = cal.fiscal_weeks(2023)[-1]
    assert last.fiscal_week == 53
    assert (last.week_start, last.week_end) == (date(2024, 1, 28), date(2024, 2, 3))
    assert last.fiscal_month == 12 and last.month_name == "Jan"


@pytest.mark.parametrize("year", range(1995, 2060))
def test_every_year_is_whole_sunday_to_saturday_weeks_near_31_january(year):
    start, end = cal.fiscal_year_start(year), cal.fiscal_year_end(year)
    assert start.weekday() == 6 and end.weekday() == 5
    assert abs((end - date(year + 1, 1, 31)).days) <= 3
    assert cal.weeks_in_year(year) in (52, 53)
    assert cal.fiscal_year_start(year + 1) == end + timedelta(days=1)


def _weeks_per_month(year: int) -> list[int]:
    counts = [0] * 12
    for wk in cal.fiscal_weeks(year):
        counts[wk.fiscal_month - 1] += 1
    return counts


def test_months_are_4_5_4_and_the_53rd_week_extends_january():
    assert _weeks_per_month(2022) == [4, 5, 4] * 4
    assert _weeks_per_month(2024) == [4, 5, 4] * 4
    assert _weeks_per_month(2023) == [4, 5, 4, 4, 5, 4, 4, 5, 4, 4, 5, 5]


def test_quarters_have_13_weeks_and_week_53_is_in_the_fourth():
    quarters = [cal.quarter_of_week(w) for w in range(1, 54)]
    assert [quarters.count(q) for q in (1, 2, 3, 4)] == [13, 13, 13, 14]


def test_restated_map_after_a_53_week_year():
    m = cal.comparable_week_map([2022, 2023, 2024], restate=True)
    ty = {w.fiscal_week: w for w in cal.fiscal_weeks(2024)}
    ly = {w.fiscal_week: w for w in cal.fiscal_weeks(2023)}
    assert m.ty_to_ly[202401] == 202302
    assert (ty[1].week_start, ty[1].week_end) == (date(2024, 2, 4), date(2024, 2, 10))
    assert (ly[2].week_start, ly[2].week_end) == (date(2023, 2, 5), date(2023, 2, 11))
    assert m.ty_to_ly[202452] == 202353
    assert m.unmatched_ly == [202301]  # FY2023 week 1 has no FY2024 partner
    assert m.unmatched_ty == [202353]  # FY2023 week 53 has no FY2022 partner


def test_restated_weeks_compare_the_same_364_day_distance():
    m = cal.comparable_week_map([2022, 2023, 2024])
    starts = {w.key: w.week_start for y in (2022, 2023, 2024) for w in cal.fiscal_weeks(y)}
    for ty_key, ly_key in m.ty_to_ly.items():
        if ly_key is not None:
            assert (starts[ty_key] - starts[ly_key]).days == 364


def test_same_week_number_when_the_prior_year_has_52_weeks():
    m = cal.comparable_week_map([2022, 2023, 2024])
    assert all(m.ty_to_ly[202300 + w] == 202200 + w for w in range(1, 53))


def test_without_restatement_weeks_pair_by_number_and_week_53_of_ly_is_unmatched():
    m = cal.comparable_week_map([2022, 2023, 2024], restate=False)
    assert m.ty_to_ly[202401] == 202301 and m.ty_to_ly[202452] == 202352
    assert m.unmatched_ly == [202353]


def test_first_year_has_no_ly_and_is_not_reported_as_unmatched():
    m = cal.comparable_week_map([2022, 2023])
    assert all(m.ty_to_ly[202200 + w] is None for w in range(1, 53))
    assert not any(k // 100 == 2022 for k in m.unmatched_ty)


def test_calendar_frame_carries_both_maps():
    frame = cal.calendar_frame([2022, 2023, 2024])
    assert len(frame) == 52 + 53 + 52
    row = frame[frame["week_key"] == 202401].iloc[0]
    assert (row["ly_week_key_restated"], row["ly_week_key_naive"]) == (202302, 202301)
    assert frame["week_key"].is_unique
