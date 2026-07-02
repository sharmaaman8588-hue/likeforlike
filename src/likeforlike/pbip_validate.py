"""Static validation of the Power BI project against the exported CSVs and the metric registry.

Power BI cannot run in CI, so this module checks what can be checked from the text files alone.
It parses the TMDL and ``report.json`` itself and does not use the generator in ``pbip`` or the
column layout in ``schema``. The only shared input is the metric registry, which is the
reference the model's measures must match.

Checks:

* every ``'Table'[Column]`` in a measure exists in that table's CSV header and in the model, and
  every bare ``[Measure]`` is a measure of the model;
* each table's declared source columns exist in its CSV header;
* relationships join columns that exist;
* measure names, host tables and DAX text equal the registry;
* every field a visual in ``report.json`` uses is a CSV column or a model measure.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import metrics

PROJECT = "LikeForLike"

_STRING_LITERAL = re.compile(r'"(?:[^"]|"")*"')
_QUALIFIED = re.compile(r"(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_]*))\[([^\]]+)\]")
_BARE_MEASURE = re.compile(r"\[([^\]]+)\]")
_CSV_SOURCE = re.compile(r'File\.Contents\(\s*DataFolder\s*&\s*"[\\/]?([^"\\/]+\.csv)"\s*\)')


@dataclass
class ModelTable:
    name: str
    columns: dict[str, str] = field(default_factory=dict)  # column name -> sourceColumn
    measures: dict[str, str] = field(default_factory=dict)  # measure name -> DAX text
    csv_name: str | None = None


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    measures_checked: int = 0
    references_checked: int = 0
    visual_fields_checked: int = 0
    relationships_checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------------------------
# TMDL parsing


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip("\t"))


def _read_name(text: str) -> tuple[str, str]:
    """Read one TMDL object name from the start of ``text``; return it and the remainder."""
    text = text.lstrip()
    if text.startswith("'"):
        out = []
        i = 1
        while i < len(text):
            if text[i] == "'":
                if text[i + 1 : i + 2] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), text[i + 1 :]
            out.append(text[i])
            i += 1
        raise ValueError(f"unterminated quoted name in {text!r}")
    match = re.match(r"[^\s=.]+", text)
    if not match:
        raise ValueError(f"no object name in {text!r}")
    return match.group(0), text[match.end() :]


def _block_lines(lines: list[str], start: int) -> list[str]:
    """Lines indented deeper than ``lines[start]`` that follow it directly."""
    base = _indent(lines[start])
    block = []
    for line in lines[start + 1 :]:
        if line.strip() and _indent(line) <= base:
            break
        block.append(line)
    return block


def _expression(header_rest: str, block: list[str], header_indent: int) -> str:
    """Expression of a measure: inline after ``=``, or the lines indented below the properties."""
    rest = header_rest.strip()
    if not rest.startswith("="):
        return ""
    inline = rest[1:].strip()
    if inline:
        return inline
    body = []
    for line in block:
        if line.strip() and _indent(line) <= header_indent + 1:
            break  # a property of the measure, not part of its expression
        if line.strip().startswith("```"):
            continue
        body.append(line.strip())
    return "\n".join(body).strip()


def parse_table(text: str) -> ModelTable:
    lines = text.splitlines()
    table: ModelTable | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("///"):
            i += 1
            continue
        stripped = line.strip()
        depth = _indent(line)
        if depth == 0 and stripped.startswith("table "):
            name, _ = _read_name(stripped[len("table ") :])
            table = ModelTable(name)
        elif table is not None and depth == 1:
            block = _block_lines(lines, i)
            if stripped.startswith("measure "):
                name, rest = _read_name(stripped[len("measure ") :])
                table.measures[name] = _expression(rest, block, depth)
            elif stripped.startswith("column "):
                name, _ = _read_name(stripped[len("column ") :])
                source = name
                for prop in block:
                    m = re.match(r"\s*sourceColumn:\s*(.+?)\s*$", prop)
                    if m:
                        source = m.group(1).strip("'\"")
                table.columns[name] = source
            elif stripped.startswith("partition "):
                found = _CSV_SOURCE.search("\n".join(block))
                if found:
                    table.csv_name = found.group(1)
        i += 1
    if table is None:
        raise ValueError("no table declaration found")
    return table


def parse_relationships(text: str) -> list[tuple[str, str, str, str]]:
    """Return ``(from_table, from_column, to_table, to_column)`` for each relationship."""
    out = []
    current: dict[str, tuple[str, str]] = {}

    def flush() -> None:
        if current:
            out.append((*current["fromColumn"], *current["toColumn"]))
            current.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("relationship "):
            flush()
        m = re.match(r"(fromColumn|toColumn):\s*(.+)", stripped)
        if m:
            table, rest = _read_name(m.group(2))
            column, _ = _read_name(rest.lstrip(".").strip())
            current[m.group(1)] = (table, column)
    flush()
    return out


def parse_expressions(text: str) -> dict[str, str]:
    names = {}
    for line in text.splitlines():
        if line.startswith("expression "):
            name, rest = _read_name(line[len("expression ") :])
            names[name] = rest
    return names


def dax_references(dax: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Qualified ``(table, column)`` references and bare ``[measure]`` references of a DAX text."""
    code = _STRING_LITERAL.sub('""', dax)
    qualified = [
        ((m.group(1) or m.group(2)).replace("''", "'"), m.group(3))
        for m in _QUALIFIED.finditer(code)
    ]
    bare = _BARE_MEASURE.findall(_QUALIFIED.sub("", code))
    return qualified, bare


# ---------------------------------------------------------------------------------------------
# Report parsing


def visual_fields(report: dict) -> list[tuple[str, str, str, bool]]:
    """``(page, table, property, is_measure)`` for every field used by a visual in the report."""
    out = []
    for section in report.get("sections", []):
        page = section.get("displayName", section.get("name", "?"))
        for container in section.get("visualContainers", []):
            config = container.get("config", "{}")
            single = (json.loads(config) if isinstance(config, str) else config).get(
                "singleVisual", {}
            )
            query = single.get("prototypeQuery", {})
            entities = {f["Name"]: f["Entity"] for f in query.get("From", [])}
            refs = {
                p["queryRef"]
                for projection in single.get("projections", {}).values()
                for p in projection
            }
            names = set()
            for item in query.get("Select", []):
                kind = "Measure" if "Measure" in item else "Column"
                node = item[kind]
                source = node["Expression"]["SourceRef"]["Source"]
                out.append(
                    (page, entities.get(source, f"?{source}"), node["Property"], kind == "Measure")
                )
                names.add(item.get("Name"))
            for sort in query.get("OrderBy", []):
                node = sort["Expression"]["Column"]
                source = node["Expression"]["SourceRef"]["Source"]
                out.append((page, entities.get(source, f"?{source}"), node["Property"], False))
            for ref in sorted(refs - names):
                out.append((page, "?", ref, False))
    return out


# ---------------------------------------------------------------------------------------------


def _read_header(path: Path) -> list[str] | None:
    if not path.exists():
        return None
    with path.open(newline="") as handle:
        return next(csv.reader(handle), [])


def validate_pbip(pbip_dir: Path, data_dir: Path) -> ValidationResult:
    """Validate the PBIP folder ``pbip_dir`` against the mart CSVs in ``data_dir``."""
    result = ValidationResult()
    err = result.errors.append
    model_dir = pbip_dir / f"{PROJECT}.SemanticModel"
    definition = model_dir / "definition"
    report_dir = pbip_dir / f"{PROJECT}.Report"

    required = [
        pbip_dir / f"{PROJECT}.pbip",
        model_dir / "definition.pbism",
        definition / "model.tmdl",
        definition / "expressions.tmdl",
        definition / "relationships.tmdl",
        report_dir / "definition.pbir",
        report_dir / "report.json",
    ]
    missing = [p for p in required if not p.exists()]
    for path in missing:
        err(f"missing file: {path.relative_to(pbip_dir)}")
    if missing:
        return result

    # --- parameter and tables ---
    expressions = parse_expressions((definition / "expressions.tmdl").read_text())
    if "DataFolder" not in expressions:
        err("expressions.tmdl: parameter DataFolder is not defined")
    elif "IsParameterQuery" not in expressions["DataFolder"]:
        err("expressions.tmdl: DataFolder is not a parameter query")

    tables: dict[str, ModelTable] = {}
    for path in sorted((definition / "tables").glob("*.tmdl")):
        table = parse_table(path.read_text())
        tables[table.name] = table
    model_text = (definition / "model.tmdl").read_text()
    declared_groups = {
        _read_name(m)[0] for m in re.findall(r"^queryGroup (.+)$", model_text, flags=re.M)
    }
    group_files = [definition / "expressions.tmdl", *sorted((definition / "tables").glob("*.tmdl"))]
    for path in group_files:
        for m in re.findall(r"^\s*queryGroup:\s*(.+?)\s*$", path.read_text(), flags=re.M):
            if m.strip("'\"") not in declared_groups:
                err(f"{path.name}: queryGroup '{m}' is not declared in model.tmdl")
    for name in re.findall(r"^ref table (.+)$", model_text, flags=re.M):
        if _read_name(name)[0] not in tables:
            err(f"model.tmdl: ref table {name} has no file in definition/tables")
    for name in tables:
        if not re.search(rf"^ref table '?{re.escape(name)}'?$", model_text, flags=re.M):
            err(f"model.tmdl: table {name} is not referenced")

    headers: dict[str, list[str]] = {}
    for table in tables.values():
        if not table.csv_name:
            err(f"table {table.name}: partition does not read a CSV through DataFolder")
            continue
        header = _read_header(data_dir / table.csv_name)
        if header is None:
            err(f"table {table.name}: {table.csv_name} not found in {data_dir}")
            continue
        headers[table.name] = header
        for column, source in table.columns.items():
            if source not in header:
                err(
                    f"{table.csv_name}: column '{source}' (model column {table.name}[{column}]) "
                    f"is missing from the CSV header {header}"
                )

    # --- measures: references and registry ---
    all_measures = {m for t in tables.values() for m in t.measures}
    for table in tables.values():
        for name, dax in table.measures.items():
            result.measures_checked += 1
            qualified, bare = dax_references(dax)
            for ref_table, ref_column in qualified:
                result.references_checked += 1
                target = tables.get(ref_table)
                if target is None:
                    err(
                        f"measure '{name}': table '{ref_table}' in '{ref_table}'[{ref_column}] "
                        "does not exist"
                    )
                    continue
                if ref_column in target.measures:
                    continue
                if ref_table in headers and ref_column not in headers[ref_table]:
                    err(
                        f"measure '{name}': column '{ref_table}'[{ref_column}] is not in "
                        f"{target.csv_name} (header: {headers[ref_table]})"
                    )
                elif ref_column not in target.columns:
                    err(f"measure '{name}': column '{ref_table}'[{ref_column}] is not in the model")
            for ref in bare:
                result.references_checked += 1
                if ref not in all_measures:
                    err(f"measure '{name}': [{ref}] is not a measure in the model")

    registry = {m.name: m for m in metrics.REGISTRY}
    in_model = {name: (t.name, dax) for t in tables.values() for name, dax in t.measures.items()}
    for name in sorted(set(registry) - set(in_model)):
        err(f"registry measure '{name}' is missing from the model")
    for name in sorted(set(in_model) - set(registry)):
        err(f"model measure '{name}' is not in the metric registry")
    for name in sorted(set(registry) & set(in_model)):
        host, dax = in_model[name]
        if host != registry[name].table:
            err(f"measure '{name}' is on table {host}, registry says {registry[name].table}")
        if dax != registry[name].dax:
            err(
                f"measure '{name}': DAX differs from the registry\n"
                f"  model:    {dax}\n  registry: {registry[name].dax}"
            )

    # --- relationships ---
    for from_t, from_c, to_t, to_c in parse_relationships(
        (definition / "relationships.tmdl").read_text()
    ):
        result.relationships_checked += 1
        for side_t, side_c in ((from_t, from_c), (to_t, to_c)):
            if side_t not in tables:
                err(f"relationship {from_t}.{from_c} -> {to_t}.{to_c}: table {side_t} is unknown")
            elif side_c not in tables[side_t].columns:
                err(
                    f"relationship {from_t}.{from_c} -> {to_t}.{to_c}: column {side_t}[{side_c}] "
                    "is not in the model"
                )
            elif side_t in headers and tables[side_t].columns[side_c] not in headers[side_t]:
                err(
                    f"relationship {from_t}.{from_c} -> {to_t}.{to_c}: column {side_c} is not in "
                    f"{tables[side_t].csv_name}"
                )

    # --- report ---
    pbir = json.loads((report_dir / "definition.pbir").read_text())
    ref = pbir.get("datasetReference", {}).get("byPath", {}).get("path")
    if not ref or not (report_dir / ref).resolve().exists():
        err(f"definition.pbir: dataset path {ref!r} does not exist")
    report = json.loads((report_dir / "report.json").read_text())
    if not report.get("sections"):
        err("report.json: no pages")
    for page, table_name, prop, is_measure in visual_fields(report):
        result.visual_fields_checked += 1
        table = tables.get(table_name)
        if table is None:
            err(f"report page '{page}': field {table_name}.{prop} uses an unknown table")
        elif is_measure:
            if prop not in table.measures:
                err(f"report page '{page}': measure {table_name}[{prop}] is not in the model")
        elif table_name in headers and prop not in headers[table_name]:
            err(f"report page '{page}': column {table_name}[{prop}] is not in {table.csv_name}")
        elif prop not in table.columns:
            err(f"report page '{page}': column {table_name}[{prop}] is not in the model")
    return result
