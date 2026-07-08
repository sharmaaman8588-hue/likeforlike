import json
import shutil
from pathlib import Path

import pytest

from likeforlike import metrics, pbip, pbip_validate
from likeforlike.schema import TABLES

from .conftest import ROOT


@pytest.fixture()
def project(tmp_path, mart_dir) -> tuple[Path, Path]:
    out = tmp_path / "powerbi"
    pbip.write_pbip(out)
    data = tmp_path / "mart"
    shutil.copytree(mart_dir, data)
    return out, data


def _model(root: Path) -> Path:
    return root / "LikeForLike.SemanticModel" / "definition"


def test_generated_project_has_the_expected_files(project):
    root, _ = project
    names = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    assert {
        "LikeForLike.pbip",
        "LikeForLike.SemanticModel/definition.pbism",
        "LikeForLike.SemanticModel/definition/model.tmdl",
        "LikeForLike.SemanticModel/definition/expressions.tmdl",
        "LikeForLike.SemanticModel/definition/relationships.tmdl",
        "LikeForLike.Report/definition.pbir",
        "LikeForLike.Report/report.json",
        *(
            f"LikeForLike.SemanticModel/definition/tables/{t}.tmdl"
            for t in ("Store", "FiscalCalendar", "StoreWeek", "GrowthBridge")
        ),
    } <= names
    assert "DataFolder" in (_model(root) / "expressions.tmdl").read_text()


def test_committed_project_validates_against_committed_data():
    result = pbip_validate.validate_pbip(ROOT / "powerbi", ROOT / "data" / "mart")
    assert result.errors == []
    assert result.measures_checked == len(metrics.REGISTRY)
    assert result.relationships_checked == 4 and result.visual_fields_checked >= 12


def test_committed_project_equals_a_fresh_generation(tmp_path):
    pbip.write_pbip(tmp_path)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert (ROOT / "powerbi" / path.relative_to(tmp_path)).read_text() == path.read_text()


def test_measure_names_and_dax_in_tmdl_equal_the_registry(project):
    root, _ = project
    seen = {}
    for path in (_model(root) / "tables").glob("*.tmdl"):
        table = pbip_validate.parse_table(path.read_text())
        seen.update({name: (table.name, dax) for name, dax in table.measures.items()})
    assert seen == {m.name: (m.table, m.dax) for m in metrics.REGISTRY}


def test_every_table_reads_its_csv_through_the_data_folder_parameter(project):
    root, _ = project
    for table in TABLES:
        parsed = pbip_validate.parse_table(
            (_model(root) / "tables" / f"{table.name}.tmdl").read_text()
        )
        assert parsed.csv_name == table.csv_name
        assert list(parsed.columns) == table.column_names


def test_report_has_three_pages_with_the_expected_visuals(project):
    root, _ = project
    report = json.loads((root / "LikeForLike.Report" / "report.json").read_text())
    pages = {s["displayName"]: s for s in report["sections"]}
    assert list(pages) == ["LFL by fiscal month", "Growth bridge", "Store comp status"]
    kinds = {
        name: [json.loads(v["config"])["singleVisual"]["visualType"] for v in s["visualContainers"]]
        for name, s in pages.items()
    }
    assert "waterfallChart" in kinds["Growth bridge"] and "tableEx" in kinds["Store comp status"]
    assert "clusteredColumnChart" in kinds["LFL by fiscal month"]


def test_a_renamed_csv_column_fails_validation_and_names_the_column(project):
    root, data = project
    path = data / "StoreWeek.csv"
    path.write_text(path.read_text().replace("sales_ty", "sales_this_year", 1))
    result = pbip_validate.validate_pbip(root, data)
    assert not result.ok
    joined = "\n".join(result.errors)
    assert "sales_ty" in joined and "StoreWeek.csv" in joined
    assert any("measure 'Sales'" in e and "sales_ty" in e for e in result.errors)


def test_a_changed_dax_expression_is_reported_against_the_registry(project):
    root, data = project
    path = _model(root) / "tables" / "StoreWeek.tmdl"
    path.write_text(
        path.read_text().replace(
            "= SUM('StoreWeek'[sales_ty])", "= SUM('StoreWeek'[sales_ly_comparable])"
        )
    )
    errors = pbip_validate.validate_pbip(root, data).errors
    assert any("measure 'Sales': DAX differs from the registry" in e for e in errors)


def test_a_missing_and_an_extra_measure_are_reported(project):
    root, data = project
    path = _model(root) / "tables" / "GrowthBridge.tmdl"
    text = path.read_text().replace("measure 'Extra Week pp'", "measure 'Surprise pp'")
    path.write_text(text)
    errors = "\n".join(pbip_validate.validate_pbip(root, data).errors)
    assert "registry measure 'Extra Week pp' is missing" in errors
    assert "model measure 'Surprise pp' is not in the metric registry" in errors


def test_a_reference_to_an_unknown_measure_or_table_is_reported(project):
    root, data = project
    path = _model(root) / "tables" / "StoreWeek.tmdl"
    text = path.read_text()
    path.write_text(text.replace("[Comp Sales LY], [Comp Sales LY])", "[Comp Sales LY], [Nope])"))
    errors = pbip_validate.validate_pbip(root, data).errors
    assert any("[Nope] is not a measure" in e for e in errors)


def test_a_relationship_to_a_missing_column_is_reported(project):
    root, data = project
    path = _model(root) / "relationships.tmdl"
    path.write_text(
        path.read_text().replace("fromColumn: StoreWeek.week_key", "fromColumn: StoreWeek.week_id")
    )
    errors = pbip_validate.validate_pbip(root, data).errors
    assert any("StoreWeek[week_id] is not in the model" in e for e in errors)


def test_a_visual_field_that_does_not_exist_is_reported(project):
    root, data = project
    path = root / "LikeForLike.Report" / "report.json"
    path.write_text(path.read_text().replace("month_name", "month_label"))
    errors = pbip_validate.validate_pbip(root, data).errors
    assert any("month_label" in e and "LFL by fiscal month" in e for e in errors)


def test_a_missing_csv_is_reported(project):
    root, data = project
    (data / "Store.csv").unlink()
    assert any("Store.csv not found" in e for e in pbip_validate.validate_pbip(root, data).errors)


def test_tmdl_parser_handles_quoted_names_and_multiline_measures():
    text = (
        "table T\n"
        "\tmeasure 'It''s %' =\n"
        "\t\t\tCALCULATE(\n"
        "\t\t\t\tSUM('T'[a]),\n"
        "\t\t\t\t'T'[b] = 1)\n"
        "\t\tformatString: 0.00\n"
        "\tcolumn a\n"
        "\t\tsourceColumn: a_src\n"
    )
    table = pbip_validate.parse_table(text)
    assert list(table.measures) == ["It's %"]
    assert table.measures["It's %"] == "CALCULATE(\nSUM('T'[a]),\n'T'[b] = 1)"
    assert table.columns == {"a": "a_src"}


def test_dax_reference_extraction_ignores_string_literals():
    qualified, bare = pbip_validate.dax_references(
        "CALCULATE([Total], 'Store Week'[bucket] = \"x[y]\", Other[c])"
    )
    assert qualified == [("Store Week", "bucket"), ("Other", "c")]
    assert bare == ["Total"]


def test_tmdl_name_quoting():
    assert pbip.tmdl_name("Sales") == "Sales"
    assert pbip.tmdl_name("LFL %") == "'LFL %'"
    assert pbip.tmdl_name("It's") == "'It''s'"


def test_a_query_group_that_model_tmdl_does_not_declare_is_reported(project):
    root, data = project
    path = _model(root) / "expressions.tmdl"
    path.write_text(path.read_text() + "\tqueryGroup: Parameters\n")
    errors = pbip_validate.validate_pbip(root, data).errors
    assert any("queryGroup 'Parameters' is not declared in model.tmdl" in e for e in errors)
    model = _model(root) / "model.tmdl"
    model.write_text(model.read_text() + "\nqueryGroup Parameters\n")
    assert pbip_validate.validate_pbip(root, data).ok


def test_generated_project_declares_every_query_group_it_uses(project):
    root, data = project
    assert "queryGroup" not in (_model(root) / "expressions.tmdl").read_text()
    assert pbip_validate.validate_pbip(root, data).ok


def test_waterfall_is_sorted_by_bridge_order_and_the_sort_column_is_validated(project):
    root, data = project
    report = json.loads((root / "LikeForLike.Report" / "report.json").read_text())
    waterfall = next(
        json.loads(v["config"])["singleVisual"]
        for s in report["sections"]
        for v in s["visualContainers"]
        if "waterfall" in json.loads(v["config"])["singleVisual"]["visualType"]
    )
    sort = waterfall["prototypeQuery"]["OrderBy"][0]["Expression"]["Column"]
    assert sort["Property"] == "bucket_order"
    table = pbip_validate.parse_table((_model(root) / "tables" / "GrowthBridge.tmdl").read_text())
    assert "bucket_order" in table.columns
    path = root / "LikeForLike.Report" / "report.json"
    path.write_text(path.read_text().replace("bucket_order", "bucket_rank"))
    errors = pbip_validate.validate_pbip(root, data).errors
    assert any("bucket_rank" in e for e in errors)
