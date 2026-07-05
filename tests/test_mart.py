"""The SQL mart against independent Python, hand-checked cases and the injected patterns."""

from datetime import date

import pandas as pd
import pytest

from likeforlike import analysis
from likeforlike import calendar as cal
from likeforlike.mart import build_mart
from likeforlike.policy import REASONS
from likeforlike.schema import BUCKETS
from likeforlike.synth import FISCAL_YEARS

from .conftest import ROOT, as_date

BIG = 100_000


def reference_reasons(raw, restate: bool = True, ramp: int = 12) -> dict:
    """Classify every store-week in plain Python, straight from the raw tables."""
    years = list(FISCAL_YEARS)
    weeks = {w.key: w for y in years for w in cal.fiscal_weeks(y)}
    ly_of = cal.comparable_week_map(years, restate).ty_to_ly
    sales = {(r.store_id, r.week_key): r.sales for r in raw.sales.itertuples()}
    first_day = weeks[min(weeks)].week_start
    spans: dict[str, list[tuple[date, date]]] = {}
    for c in raw.closures.itertuples():
        spans.setdefault(c.store_id, []).append((as_date(c.closed_from), as_date(c.closed_to)))

    trading_no: dict[tuple[str, int], int] = {}
    for store in raw.stores.itertuples():
        keys = sorted(k for (s, k) in sales if s == store.store_id)
        offset = BIG if as_date(store.open_date) < first_day else 0
        for n, key in enumerate(keys, start=1):
            trading_no[(store.store_id, key)] = n + offset

    def closed_during(store_id, week):
        return week is not None and any(
            a <= week.week_end and b >= week.week_start for a, b in spans.get(store_id, [])
        )

    out = {}
    for store in raw.stores.itertuples():
        for key, week in weeks.items():
            ly_key = ly_of[key]
            ty = sales.get((store.store_id, key), 0)
            ly = sales.get((store.store_id, ly_key), 0) if ly_key else 0
            if ty == 0 and ly == 0:
                continue
            ly_week = weeks.get(ly_key)
            if week.fiscal_year == years[0]:
                reason = "no_prior_year"
            elif ly_key is None:
                reason = "extra_week"
            elif closed_during(store.store_id, week) or closed_during(store.store_id, ly_week):
                reason = "refurbishment"
            elif ty == 0:
                reason = "closed_store"
            elif ly == 0:
                reason = "new_store"
            elif ramp and (
                trading_no[(store.store_id, key)] <= ramp
                or trading_no[(store.store_id, ly_key)] <= ramp
            ):
                reason = "new_store"
            else:
                reason = "comp"
            out[(store.store_id, key)] = reason
    return out


def mart_reasons(con) -> dict:
    rows = con.execute("SELECT store_id, week_key, non_comp_reason FROM fct_store_week").fetchall()
    return {(s, k): r for s, k, r in rows}


def test_sql_classification_matches_independent_python(raw, con):
    assert mart_reasons(con) == reference_reasons(raw)


def test_every_store_week_has_exactly_one_reason(con):
    frame = con.execute("SELECT * FROM fct_store_week").df()
    assert not frame.duplicated(["store_id", "week_key"]).any()
    assert frame["non_comp_reason"].notna().all()
    assert set(frame["non_comp_reason"]) <= set(REASONS)
    assert ((frame["is_comp"] == 1) == (frame["non_comp_reason"] == "comp")).all()
    assert ((frame["sales_ty"] > 0) | (frame["sales_ly_comparable"] > 0)).all()


def test_store_week_row_count_covers_every_trading_week(con, raw):
    # a row per store-week with TY sales, plus rows for LY weeks whose TY week had no sales
    ty_rows = con.execute("SELECT count(*) FROM fct_store_week WHERE sales_ty > 0").fetchone()[0]
    assert ty_rows == len(raw.sales)


def test_week_53_store_weeks_are_extra_week_and_nothing_else_is(con):
    frame = con.execute("SELECT * FROM fct_store_week WHERE non_comp_reason = 'extra_week'").df()
    assert set(frame["week_key"]) == {202353}
    assert frame["ly_week_key"].isna().all() and (frame["sales_ly_comparable"] == 0).all()


def test_refurbishment_weeks_and_their_comparable_weeks_are_non_comp(raw, con):
    years = list(FISCAL_YEARS)
    weeks = {w.key: w for y in years for w in cal.fiscal_weeks(y)}
    ly_of = cal.comparable_week_map(years).ty_to_ly
    reasons = mart_reasons(con)
    expected = set()
    for c in raw.closures.itertuples():
        start, end = as_date(c.closed_from), as_date(c.closed_to)
        for key, week in weeks.items():
            ly_key = ly_of[key]
            touches_ty = week.week_start <= end and week.week_end >= start
            touches_ly = ly_key is not None and (
                weeks[ly_key].week_start <= end and weeks[ly_key].week_end >= start
            )
            if (touches_ty or touches_ly) and (c.store_id, key) in reasons:
                expected.add((c.store_id, key))
    flagged = {pair for pair, reason in reasons.items() if reason == "refurbishment"}
    # FY2022 weeks cannot be compared, so no_prior_year wins there; no closure falls in FY2022
    assert flagged == expected
    assert len(flagged) >= 40


def test_partially_closed_weeks_are_refurbishment_too(raw, con):
    c = raw.closures.iloc[0]
    start = as_date(c["closed_from"])
    key = next(
        w.key
        for y in FISCAL_YEARS
        for w in cal.fiscal_weeks(y)
        if w.week_start <= start <= w.week_end
    )
    row = con.execute(
        "SELECT sales_ty, non_comp_reason FROM fct_store_week WHERE store_id = ? AND week_key = ?",
        [c["store_id"], key],
    ).fetchone()
    assert row[1] == "refurbishment" and row[0] > 0  # traded part of the week


def test_relocated_store_stays_new_until_its_ly_week_leaves_the_ramp(raw, con):
    new = raw.stores[raw.stores["relocated_from"].notna()].iloc[0]["store_id"]
    ly_of = cal.comparable_week_map(list(FISCAL_YEARS)).ty_to_ly
    own = raw.sales[raw.sales["store_id"] == new].sort_values("week_key")
    number = {k: n for n, k in enumerate(own["week_key"], start=1)}
    frame = con.execute(
        "SELECT week_key, non_comp_reason FROM fct_store_week "
        "WHERE store_id = ? AND week_key BETWEEN 202401 AND 202452 ORDER BY week_key",
        [new],
    ).df()
    assert len(frame) == 52
    for key, reason in zip(frame["week_key"], frame["non_comp_reason"], strict=True):
        ly_no = number.get(ly_of[key])
        expected = "new_store" if ly_no is None or ly_no <= 12 else "comp"
        assert reason == expected, key
    first_comp = frame[frame["non_comp_reason"] == "comp"]["week_key"].min()
    assert first_comp == 202452  # LY week 53 is the store's 13th trading week
    assert (frame[frame["week_key"] < 202452]["non_comp_reason"] == "new_store").all()


def test_old_store_of_a_relocation_becomes_closed_store(raw, con):
    old = raw.stores[raw.stores["relocated_from"].notna()].iloc[0]["relocated_from"]
    frame = con.execute(
        "SELECT non_comp_reason, sum(sales_ty), sum(sales_ly_comparable) FROM fct_store_week "
        "WHERE store_id = ? AND week_key > 202340 AND week_key < 202400 GROUP BY 1",
        [old],
    ).fetchall()
    assert frame == [("closed_store", 0.0, pytest.approx(frame[0][2]))] and frame[0][2] > 0


def test_restatement_compares_christmas_week_with_christmas_week(con, policy, raw):
    def ratio(mart):
        row = mart.execute(
            "SELECT sum(sales_ty), sum(sales_ly_comparable) FROM fct_store_week "
            "WHERE week_key = 202447 AND is_comp = 1"
        ).fetchone()
        return row[0] / row[1]

    restated = ratio(con)
    unrestated = ratio(build_mart(raw, policy.variant(restate=False)))
    assert abs(restated - 1.02) < 0.01  # Christmas against Christmas
    assert abs(unrestated - 1.02 * 1.8) < 0.08  # Christmas against an ordinary week
    ly_key = con.execute(
        "SELECT ly_week_key FROM dim_fiscal_week WHERE week_key = 202447"
    ).fetchone()
    assert ly_key == (202348,)


def test_lfl_recovers_the_injected_two_percent(con):
    for year in (2023, 2024):
        lfl = analysis.lfl_by(con, year, "year")["lfl_pct"].iloc[0]
        assert abs(lfl - 0.02) <= 0.0015, (year, lfl)


def test_fy2024_total_growth_exceeds_lfl_by_at_least_two_points(con):
    lfl = analysis.lfl_by(con, 2024, "year")["lfl_pct"].iloc[0]
    assert analysis.total_growth(con, 2024) - lfl >= 0.02


def test_removing_ramp_exclusion_inflates_lfl(raw, policy):
    frame = analysis.policy_diff(raw, policy, 2024, {"x": policy.variant(ramp_weeks=0)})
    assert frame["year_vs_baseline_pp"].iloc[1] >= 0.4


def test_without_restatement_december_is_far_from_two_percent_and_with_it_close(raw, policy):
    frame = analysis.policy_diff(raw, policy, 2024, {"x": policy.variant(restate=False)})
    restated, naive = frame["december_lfl_pct"]
    assert abs(restated - 0.02) <= 0.005
    assert abs(naive - 0.02) >= 0.015


def test_sql_matches_python_without_ramp_and_without_restatement(raw, policy):
    for kwargs, ref in (({"ramp_weeks": 0}, {"ramp": 0}), ({"restate": False}, {"restate": False})):
        variant = build_mart(raw, policy.variant(**kwargs))
        assert mart_reasons(variant) == reference_reasons(raw, **ref)


def test_bridge_buckets_sum_to_total_growth_every_month_and_year(con):
    residuals = analysis.bridge_residuals(con)
    assert len(residuals) == 2 * 12 + 2
    assert residuals["residual_pp"].abs().max() < 1e-9


def test_bridge_assigns_every_sales_value_to_exactly_one_bucket(con, raw):
    bridge = con.execute("SELECT * FROM fct_growth_bridge").df()
    assert set(bridge["bucket"]) <= set(BUCKETS)
    sales = raw.sales.assign(fy=raw.sales["week_key"] // 100)
    for year in (2023, 2024):
        rows = bridge[bridge["ty_week_key"] // 100 == year]
        assert rows["sales_ty"].sum() == sales[sales["fy"] == year]["sales"].sum()
        assert rows["sales_ly"].sum() == sales[sales["fy"] == year - 1]["sales"].sum()
        # a TY store-week and an LY store-week each appear once
        assert not rows[rows["sales_ty"] > 0].duplicated(["store_id", "ty_week_key"]).any()
        assert not rows[rows["sales_ly"] > 0].duplicated(["store_id", "ly_week_key"]).any()
    assert bridge[bridge["sales_ty"] == 0]["sales_ly"].gt(0).all()


def test_unmatched_ly_week_enters_as_extra_week_in_fiscal_month_one(con, raw):
    rows = con.execute(
        "SELECT * FROM fct_growth_bridge WHERE ly_week_key = 202301 AND ty_week_key = 202401"
    ).df()
    assert (rows["bucket"] == "extra_week").all() and (rows["sales_ty"] == 0).all()
    assert rows["sales_ly"].sum() == raw.sales[raw.sales["week_key"] == 202301]["sales"].sum()
    months = con.execute(
        "SELECT fiscal_month FROM dim_fiscal_week WHERE week_key = 202401"
    ).fetchone()
    assert months == (1,)


def test_fy2023_extra_week_adds_and_fy2024_extra_week_subtracts(con):
    bridge = analysis.bridge_by_bucket(con, 2023).set_index("bucket")["bridge_pp"]
    assert bridge["extra_week"] > 1.5
    bridge = analysis.bridge_by_bucket(con, 2024).set_index("bucket")["bridge_pp"]
    assert bridge["extra_week"] < -1.5


def test_dim_fiscal_week_records_unmatched_weeks(con):
    frame = con.execute("SELECT * FROM dim_fiscal_week").df()
    assert frame.loc[frame["is_unmatched_ly"] == 1, "week_key"].tolist() == [202301]
    assert frame.loc[frame["ly_match"] == "unmatched_ty", "week_key"].tolist() == [202353]
    assert (frame[frame["fiscal_year"] == 2024]["ly_match"] == "restated").all()
    assert (frame[frame["fiscal_year"] == 2022]["ly_match"] == "no_ly_data").all()


def test_committed_mart_csvs_are_consistent_with_the_schema_and_the_rules(mart_dir):
    """The committed data is checked by its properties, not byte for byte: random streams may
    differ between numpy releases, but the schema, the rules and the recovered numbers must not."""
    from likeforlike.schema import TABLES

    committed = ROOT / "data" / "mart"
    for table in TABLES:
        frame = pd.read_csv(committed / table.csv_name)
        assert list(frame.columns) == table.column_names
        assert len(frame) > 0
    assert sum(p.stat().st_size for p in committed.glob("*.csv")) < 1_000_000
    # the calendar involves no random data, so it must be identical to a fresh build
    name = "FiscalCalendar.csv"
    assert (committed / name).read_bytes() == (mart_dir / name).read_bytes()

    store_week = pd.read_csv(committed / "StoreWeek.csv")
    assert set(store_week["non_comp_reason"]) <= set(REASONS)
    assert ((store_week["is_comp"] == 1) == (store_week["non_comp_reason"] == "comp")).all()
    assert not store_week.duplicated(["store_id", "week_key"]).any()

    bridge = pd.read_csv(committed / "GrowthBridge.csv")
    assert set(bridge["bucket"]) == set(BUCKETS)
    order = dict(zip(BUCKETS, range(1, 6), strict=True))
    assert (bridge["bucket"].map(order) == bridge["bucket_order"]).all()
    for year in (2023, 2024):
        ty = store_week[store_week["week_key"] // 100 == year]["sales_ty"].sum()
        assert bridge[bridge["ty_week_key"] // 100 == year]["sales_ty"].sum() == ty
        comp = store_week[(store_week["week_key"] // 100 == year) & (store_week["is_comp"] == 1)]
        assert abs(comp["sales_ty"].sum() / comp["sales_ly_comparable"].sum() - 1.02) <= 0.0015


def test_exported_headers_follow_the_schema(mart_dir):
    from likeforlike.schema import TABLES

    for table in TABLES:
        header = (mart_dir / table.csv_name).read_text().splitlines()[0]
        assert header.split(",") == table.column_names
    frame = pd.read_csv(mart_dir / "StoreWeek.csv")
    assert frame["is_comp"].isin([0, 1]).all()
