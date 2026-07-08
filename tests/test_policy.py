import pytest
import yaml

from likeforlike.mart import render_sql
from likeforlike.policy import REASONS, CompPolicy, PolicyError, load_policy

from .conftest import ROOT


def test_default_policy_values(policy):
    assert policy == CompPolicy(restate=True, ramp_weeks=12, refurbishment=True)
    assert REASONS == (
        "no_prior_year",
        "extra_week",
        "refurbishment",
        "closed_store",
        "new_store",
        "comp",
    )


def _write(tmp_path, **changes):
    doc = yaml.safe_load((ROOT / "comp_policy.yaml").read_text())
    doc.update(changes)
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def test_disabled_ramp_becomes_zero_weeks(tmp_path):
    path = _write(tmp_path, ramp={"enabled": False, "trading_weeks": 12})
    assert load_policy(path).ramp_weeks == 0


def test_precedence_must_match_the_implemented_order(tmp_path):
    with pytest.raises(PolicyError, match="precedence"):
        load_policy(_write(tmp_path, precedence=["comp", "new_store"]))


def test_unknown_version_and_relocation_rule_are_rejected(tmp_path):
    with pytest.raises(PolicyError, match="version"):
        load_policy(_write(tmp_path, version=2))
    with pytest.raises(PolicyError, match="relocation"):
        load_policy(_write(tmp_path, relocation={"treat_as": "same_store"}))


def test_policy_parameters_reach_the_sql(policy):
    assert "<= 12" in render_sql("fct_store_week", policy)
    assert "ly_trading_week_no <=" not in render_sql("fct_store_week", policy.variant(ramp_weeks=0))
    assert "ly_week_key_naive" in render_sql("dim_fiscal_week", policy.variant(restate=False))
    assert "ly_week_key_restated" in render_sql("dim_fiscal_week", policy)
    assert "raw_closure" not in render_sql("fct_store_week", policy.variant(refurbishment=False))


def test_labels_describe_the_variant(policy):
    assert policy.variant(ramp_weeks=0).label() == "restated LY, no ramp exclusion"
    assert policy.variant(restate=False).label() == "no restatement, ramp 12w"
