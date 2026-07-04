from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest

from likeforlike.mart import build_mart, export_mart
from likeforlike.policy import CompPolicy, load_policy
from likeforlike.synth import RawData, generate

ROOT = Path(__file__).resolve().parents[1]


def as_date(value) -> date | None:
    if value is None or value != value:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))


@pytest.fixture(scope="session")
def raw() -> RawData:
    return generate(7)


@pytest.fixture(scope="session")
def policy() -> CompPolicy:
    return load_policy(ROOT / "comp_policy.yaml")


@pytest.fixture(scope="session")
def con(raw: RawData, policy: CompPolicy) -> duckdb.DuckDBPyConnection:
    return build_mart(raw, policy)


@pytest.fixture(scope="session")
def mart_dir(con, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("mart")
    export_mart(con, out)
    return out
