import pytest

from likeforlike import analysis, metrics
from likeforlike.schema import BUCKETS

from .conftest import ROOT  # noqa: F401

EXPECTED_NAMES = {
    "Sales",
    "Sales LY Comparable",
    "Comp Sales TY",
    "Comp Sales LY",
    "LFL %",
    "Total Growth %",
    "New Store pp",
    "Closed Store pp",
    "Refurbishment pp",
    "Extra Week pp",
}


def test_registry_holds_the_required_measures_with_unique_names():
    names = [m.name for m in metrics.REGISTRY]
    assert EXPECTED_NAMES <= set(names)
    assert len(set(names)) == len(names)
    assert len({m.sql_column for m in metrics.REGISTRY}) == len(names)


def test_every_bucket_has_a_contribution_measure():
    assert {m.name for m in metrics.REGISTRY if m.sql_column.endswith("_pp")} >= {
        "Comp pp",
        "New Store pp",
        "Closed Store pp",
        "Refurbishment pp",
        "Extra Week pp",
    }
    assert len(BUCKETS) == 5


def test_dax_is_single_line_and_references_only_known_tables():
    for m in metrics.REGISTRY:
        assert "\n" not in m.dax
        assert m.table in {metrics.STORE_WEEK, metrics.GROWTH_BRIDGE}


def test_sql_output_columns_come_from_the_registry(con):
    frame = con.execute(
        metrics.select_sql(
            metrics.STORE_WEEK,
            {"fiscal_year": "w.fiscal_year"},
            ["Sales", "Comp Sales TY", "LFL %"],
        )
    ).df()
    assert list(frame.columns) == ["fiscal_year", "sales", "comp_sales_ty", "lfl_pct"]


def test_select_sql_rejects_a_measure_from_the_other_table():
    with pytest.raises(ValueError, match="not defined on StoreWeek"):
        metrics.select_sql(metrics.STORE_WEEK, {}, ["Total Growth %"])


def test_lfl_is_comp_ty_over_comp_ly_minus_one(con):
    row = con.execute(
        "SELECT sum(sales_ty) FILTER (WHERE is_comp = 1), "
        "sum(sales_ly_comparable) FILTER (WHERE is_comp = 1) "
        "FROM fct_store_week f JOIN dim_fiscal_week w USING (week_key) WHERE w.fiscal_year = 2024"
    ).fetchone()
    expected = row[0] / row[1] - 1
    assert analysis.lfl_by(con, 2024, "year")["lfl_pct"].iloc[0] == pytest.approx(
        expected, abs=1e-12
    )


def test_bridge_pp_by_bucket_equals_the_named_contribution_measures(con):
    by_bucket = analysis.bridge_by_bucket(con, 2024).set_index("bucket")["bridge_pp"]
    named = (
        con.execute(
            metrics.select_sql(
                metrics.GROWTH_BRIDGE,
                {},
                [
                    m.name
                    for m in metrics.REGISTRY
                    if m.sql_column.endswith("_pp") and m.name != "Bridge pp"
                ],
                where="w.fiscal_year = 2024",
            )
        )
        .df()
        .iloc[0]
    )
    for bucket in BUCKETS:
        assert by_bucket[bucket] == pytest.approx(named[f"{bucket}_pp"], abs=1e-12)


def test_unknown_period_is_rejected(con):
    with pytest.raises(ValueError, match="unknown period"):
        analysis.lfl_by(con, 2024, "fortnight")
