"""Generate the Power BI project (PBIP): a TMDL semantic model and a three-page report.

Column layout comes from ``schema``; measures come from the metric registry. Nothing here is
read back by the validator in ``pbip_validate``, which parses the written files on its own.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from . import metrics
from .schema import RELATIONSHIPS, TABLES, Column, Table

PROJECT = "LikeForLike"
DEFAULT_DATA_FOLDER = r"C:\likeforlike\data\mart"
_NAMESPACE = uuid.UUID("5d1f2a9e-7c53-4f0a-9d7e-3b6c8a1e4f20")
_BARE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _guid(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "/".join(parts)))


def tmdl_name(name: str) -> str:
    """TMDL object names are bare when they are plain identifiers, otherwise single-quoted."""
    if _BARE.fullmatch(name):
        return name
    return "'" + name.replace("'", "''") + "'"


def _column_block(table: Table, col: Column) -> list[str]:
    lines = [
        f"\tcolumn {tmdl_name(col.name)}",
        f"\t\tdataType: {col.tmdl_type}",
    ]
    if col.format_string:
        lines.append(f"\t\tformatString: {col.format_string}")
    lines += [
        f"\t\tlineageTag: {_guid(table.name, col.name)}",
        f"\t\tsummarizeBy: {col.summarize_by}",
        f"\t\tsourceColumn: {col.name}",
    ]
    if col.sort_by:
        lines.append(f"\t\tsortByColumn: {tmdl_name(col.sort_by)}")
    return lines


def _measure_block(table: Table, metric: metrics.Metric) -> list[str]:
    return [
        f"\t/// {metric.description}",
        f"\tmeasure {tmdl_name(metric.name)} = {metric.dax}",
        f"\t\tformatString: {metric.format_string}",
        f"\t\tlineageTag: {_guid(table.name, 'measure', metric.name)}",
    ]


def _partition_block(table: Table) -> list[str]:
    types = ", ".join(f'{{"{c.name}", {c.m_type}}}' for c in table.columns)
    return [
        f"\tpartition {tmdl_name(table.name)} = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        "\t\t\t\t\tSource = Csv.Document(",
        f'\t\t\t\t\t\tFile.Contents(DataFolder & "\\{table.csv_name}"),',
        f'\t\t\t\t\t\t[Delimiter = ",", Columns = {len(table.columns)}, '
        "Encoding = 65001, QuoteStyle = QuoteStyle.Csv]",
        "\t\t\t\t\t),",
        "\t\t\t\t\tPromoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
        f"\t\t\t\t\tTyped = Table.TransformColumnTypes(Promoted, {{{types}}})",
        "\t\t\t\tin",
        "\t\t\t\t\tTyped",
    ]


def table_tmdl(table: Table) -> str:
    lines = [f"table {tmdl_name(table.name)}", f"\tlineageTag: {_guid(table.name)}", ""]
    for metric in metrics.REGISTRY:
        if metric.table == table.name:
            lines += _measure_block(table, metric) + [""]
    for col in table.columns:
        lines += _column_block(table, col) + [""]
    lines += _partition_block(table)
    return "\n".join(lines) + "\n"


def model_tmdl() -> str:
    lines = [
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tdiscourageImplicitMeasures",
        "",
    ]
    lines += [f"ref table {tmdl_name(t.name)}" for t in TABLES]
    lines += ["", "ref expression DataFolder"]
    return "\n".join(lines) + "\n"


def expressions_tmdl(data_folder: str) -> str:
    return (
        f'expression DataFolder = "{data_folder}" meta '
        '[IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]\n'
        f"\tlineageTag: {_guid('expression', 'DataFolder')}\n"
    )


def relationships_tmdl() -> str:
    blocks = []
    for rel in RELATIONSHIPS:
        key = f"{rel.from_table}.{rel.from_column}->{rel.to_table}.{rel.to_column}"
        blocks.append(
            f"relationship {_guid('relationship', key)}\n"
            f"\tfromColumn: {tmdl_name(rel.from_table)}.{tmdl_name(rel.from_column)}\n"
            f"\ttoColumn: {tmdl_name(rel.to_table)}.{tmdl_name(rel.to_column)}\n"
        )
    return "\n".join(blocks)


# ---------------------------------------------------------------------------------------------
# Report


def _field(alias: str, entity: str, prop: str, measure: bool) -> dict:
    kind = "Measure" if measure else "Column"
    return {
        kind: {"Expression": {"SourceRef": {"Source": alias}}, "Property": prop},
        "Name": f"{entity}.{prop}",
    }


def _visual(
    name: str,
    visual_type: str,
    box: tuple[int, int, int, int],
    roles: dict[str, list[tuple[str, str]]],
    title: str | None,
    order_by: tuple[str, str] | None = None,
) -> dict:
    """A visual container. ``roles`` maps a projection role to ``(table, field)`` pairs.

    ``order_by`` is an optional ``(table, column)`` the visual is sorted by, ascending.
    """
    x, y, width, height = box
    host = {m.name: m.table for m in metrics.REGISTRY}
    aliases: dict[str, str] = {}
    select: list[dict] = []
    projections: dict[str, list[dict]] = {}
    for role, fields in roles.items():
        for table, prop in fields:
            alias = aliases.setdefault(table, table[0].lower() + str(len(aliases)))
            is_measure = host.get(prop) == table
            item = _field(alias, table, prop, is_measure)
            select.append(item)
            projections.setdefault(role, []).append({"queryRef": item["Name"]})
    single: dict = {
        "visualType": visual_type,
        "projections": projections,
        "prototypeQuery": {
            "Version": 2,
            "From": [{"Name": a, "Entity": t, "Type": 0} for t, a in aliases.items()],
            "Select": select,
        },
        "drillFilterOtherVisuals": True,
    }
    if order_by:
        table, prop = order_by
        alias = aliases.setdefault(table, table[0].lower() + str(len(aliases)))
        single["prototypeQuery"]["From"] = [
            {"Name": a, "Entity": t, "Type": 0} for t, a in aliases.items()
        ]
        single["prototypeQuery"]["OrderBy"] = [
            {
                "Direction": 1,
                "Expression": {
                    "Column": {
                        "Expression": {"SourceRef": {"Source": alias}},
                        "Property": prop,
                    }
                },
            }
        ]
    if title:
        single["vcObjects"] = {
            "title": [
                {
                    "properties": {
                        "show": {"expr": {"Literal": {"Value": "true"}}},
                        "text": {"expr": {"Literal": {"Value": f"'{title}'"}}},
                    }
                }
            ]
        }
    config = {
        "name": name,
        "layouts": [
            {"id": 0, "position": {"x": x, "y": y, "z": 0, "width": width, "height": height}}
        ],
        "singleVisual": single,
    }
    return {
        "config": json.dumps(config, separators=(",", ":")),
        "filters": "[]",
        "height": height,
        "width": width,
        "x": x,
        "y": y,
        "z": 0,
    }


def _page(name: str, title: str, visuals: list[dict]) -> dict:
    return {
        "config": "{}",
        "displayName": title,
        "displayOption": 1,
        "filters": "[]",
        "height": 720,
        "name": name,
        "visualContainers": visuals,
        "width": 1280,
    }


def report_json() -> dict:
    year = ("FiscalCalendar", "fiscal_year")
    pages = [
        _page(
            "LflByMonth",
            "LFL by fiscal month",
            [
                _visual(
                    "lfl_year", "slicer", (24, 24, 220, 120), {"Values": [year]}, "Fiscal year"
                ),
                _visual(
                    "lfl_card",
                    "card",
                    (264, 24, 240, 120),
                    {"Values": [("StoreWeek", "LFL %")]},
                    None,
                ),
                _visual(
                    "lfl_month",
                    "clusteredColumnChart",
                    (24, 168, 1232, 528),
                    {
                        "Category": [("FiscalCalendar", "month_name")],
                        "Y": [("StoreWeek", "LFL %")],
                    },
                    "LFL % by fiscal month",
                ),
            ],
        ),
        _page(
            "GrowthBridge",
            "Growth bridge",
            [
                _visual(
                    "bridge_year", "slicer", (24, 24, 220, 120), {"Values": [year]}, "Fiscal year"
                ),
                _visual(
                    "bridge_card",
                    "card",
                    (264, 24, 240, 120),
                    {"Values": [("GrowthBridge", "Total Growth %")]},
                    None,
                ),
                _visual(
                    "bridge_waterfall",
                    "waterfallChart",
                    (24, 168, 1232, 528),
                    {
                        "Category": [("GrowthBridge", "bucket")],
                        "Y": [("GrowthBridge", "Bridge pp")],
                    },
                    "Growth bridge, points of last year's sales",
                    order_by=("GrowthBridge", "bucket_order"),
                ),
            ],
        ),
        _page(
            "StoreCompStatus",
            "Store comp status",
            [
                _visual(
                    "status_year", "slicer", (24, 24, 220, 120), {"Values": [year]}, "Fiscal year"
                ),
                _visual(
                    "status_table",
                    "tableEx",
                    (24, 168, 1232, 528),
                    {
                        "Values": [
                            ("Store", "store_id"),
                            ("Store", "store_name"),
                            ("StoreWeek", "non_comp_reason"),
                            ("StoreWeek", "Sales"),
                            ("StoreWeek", "Sales LY Comparable"),
                        ]
                    },
                    "Sales by store and comp status",
                ),
            ],
        ),
    ]
    config = {
        "version": "5.55",
        "themeCollection": {"baseTheme": {"name": "CY24SU10", "version": "5.55", "type": 2}},
    }
    return {
        "config": json.dumps(config, separators=(",", ":")),
        "layoutOptimization": 0,
        "resourcePackages": [],
        "sections": pages,
    }


def write_pbip(out_dir: Path, data_folder: str = DEFAULT_DATA_FOLDER) -> list[Path]:
    """Write the whole PBIP folder under ``out_dir`` and return the files written."""
    model = out_dir / f"{PROJECT}.SemanticModel"
    report = out_dir / f"{PROJECT}.Report"
    files: dict[Path, str] = {
        out_dir / f"{PROJECT}.pbip": _json(
            {
                "version": "1.0",
                "artifacts": [{"report": {"path": f"{PROJECT}.Report"}}],
                "settings": {"enableAutoRecovery": True},
            }
        ),
        out_dir / ".gitignore": "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n",
        model / "definition.pbism": _json({"version": "4.0", "settings": {}}),
        model / "definition" / "database.tmdl": "database\n\tcompatibilityLevel: 1567\n",
        model / "definition" / "model.tmdl": model_tmdl(),
        model / "definition" / "expressions.tmdl": expressions_tmdl(data_folder),
        model / "definition" / "relationships.tmdl": relationships_tmdl(),
        report / "definition.pbir": _json(
            {
                "version": "1.0",
                "datasetReference": {"byPath": {"path": f"../{PROJECT}.SemanticModel"}},
            }
        ),
        report / "report.json": _json(report_json()),
    }
    for table in TABLES:
        files[model / "definition" / "tables" / f"{table.name}.tmdl"] = table_tmdl(table)
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, newline="\n")
    return sorted(files)


def _json(doc: dict) -> str:
    return json.dumps(doc, indent=2) + "\n"
