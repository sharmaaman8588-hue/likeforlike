"""Command line: ``likeforlike generate | build | lfl | bridge | policy-diff | validate-pbip``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import duckdb
import typer
from rich.console import Console
from rich.table import Table

from . import analysis
from .mart import build_mart, export_mart
from .pbip import DEFAULT_DATA_FOLDER, write_pbip
from .pbip_validate import validate_pbip
from .policy import CompPolicy, PolicyError, load_policy
from .synth import DEFAULT_SEED, RawData, generate, load_raw, write_raw

app = typer.Typer(
    help="Like-for-like retail sales on a 4-5-4 calendar with a growth bridge that sums exactly.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console(width=100)

RawOpt = Annotated[Path, typer.Option("--raw", help="Folder with the generated raw data.")]
PolicyOpt = Annotated[
    Path | None, typer.Option("--policy", help="Comp policy YAML (default: comp_policy.yaml).")
]
YearOpt = Annotated[int, typer.Option("--year", help="Fiscal year (FY2024 starts 4 Feb 2024).")]


def _policy(path: Path | None) -> CompPolicy:
    try:
        return load_policy(path)
    except (PolicyError, FileNotFoundError) as exc:
        console.print(f"[red]policy error:[/red] {exc}")
        raise typer.Exit(2) from exc


def _raw(path: Path) -> RawData:
    if not (path / "weekly_sales.csv").exists():
        console.print(f"[red]no raw data in {path}[/red]; run `likeforlike generate` first")
        raise typer.Exit(2)
    return load_raw(path)


def _mart(raw_dir: Path, policy_path: Path | None) -> duckdb.DuckDBPyConnection:
    return build_mart(_raw(raw_dir), _policy(policy_path))


def _pct(value: float | None) -> str:
    return "" if value is None or value != value else f"{value * 100:.2f}%"


def _money(value: float | None) -> str:
    return "" if value is None or value != value else f"{value:,.0f}"


@app.command("generate")
def generate_cmd(
    seed: Annotated[int, typer.Option("--seed")] = DEFAULT_SEED,
    out: Annotated[Path, typer.Option("--out")] = Path("data/raw"),
) -> None:
    """Write the seeded synthetic stores, closures and weekly sales."""
    raw = generate(seed)
    paths = write_raw(raw, out)
    console.print(
        f"seed {seed}: {len(raw.stores)} stores, {len(raw.sales):,} store-weeks, "
        f"{len(raw.closures)} refurbishment closures"
    )
    for path in paths:
        console.print(f"  wrote {path}")


@app.command("build")
def build_cmd(
    raw: RawOpt = Path("data/raw"),
    out: Annotated[Path, typer.Option("--out", help="Where the mart CSVs go.")] = Path("data/mart"),
    pbip: Annotated[Path, typer.Option("--pbip", help="Where the PBIP project goes.")] = Path(
        "powerbi"
    ),
    data_folder: Annotated[
        str, typer.Option("--data-folder", help="Default of the model's DataFolder parameter.")
    ] = DEFAULT_DATA_FOLDER,
    policy: PolicyOpt = None,
    database: Annotated[
        Path | None, typer.Option("--db", help="Also keep the mart as a DuckDB file.")
    ] = None,
) -> None:
    """Build the SQL mart, export it to CSV and write the Power BI project."""
    con = build_mart(_raw(raw), _policy(policy), database or ":memory:")
    residual = analysis.bridge_residuals(con)["residual_pp"].abs().max()
    csvs = export_mart(con, out)
    con.close()
    if residual >= 1e-9:
        console.print(
            f"[red]growth bridge does not sum to total growth (residual {residual:.3e} pp)"
        )
        raise typer.Exit(1)
    files = write_pbip(pbip, data_folder)
    console.print(f"mart: {len(csvs)} CSVs in {out}; growth bridge residual below 1e-9 pp")
    console.print(f"pbip: {len(files)} files in {pbip}")
    result = validate_pbip(pbip, out)
    if not result.ok:
        for message in result.errors:
            console.print(f"[red]error:[/red] {message}")
        raise typer.Exit(1)


@app.command("lfl")
def lfl_cmd(
    year: YearOpt = 2024,
    by: Annotated[str, typer.Option("--by", help="year | quarter | month | week")] = "month",
    raw: RawOpt = Path("data/raw"),
    policy: PolicyOpt = None,
) -> None:
    """Like-for-like sales growth for each period of a fiscal year."""
    con = _mart(raw, policy)
    try:
        frame = analysis.lfl_by(con, year, by)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    table = Table(title=f"FY{year} like-for-like by fiscal {by}")
    label_of = {
        "year": lambda r: f"FY{r.fiscal_year}",
        "quarter": lambda r: f"Q{r.fiscal_quarter}",
        "month": lambda r: r.month_name,
        "week": lambda r: f"W{r.fiscal_week:02d}",
    }[by]
    table.add_column(by.capitalize())
    for name in ("Comp sales TY", "Comp sales LY", "LFL %"):
        table.add_column(name, justify="right")
    for row in frame.itertuples():
        table.add_row(
            label_of(row),
            _money(row.comp_sales_ty),
            _money(row.comp_sales_ly),
            _pct(row.lfl_pct),
        )
    console.print(table)


@app.command("bridge")
def bridge_cmd(
    year: YearOpt = 2024, raw: RawOpt = Path("data/raw"), policy: PolicyOpt = None
) -> None:
    """Split total growth for a fiscal year into the five bridge buckets."""
    con = _mart(raw, policy)
    frame = analysis.bridge_by_bucket(con, year)
    total = analysis.total_growth(con, year) * 100
    table = Table(title=f"FY{year} growth bridge (pp)")
    table.add_column("Bucket")
    table.add_column("pp", justify="right")
    for row in frame.itertuples():
        table.add_row(row.bucket, f"{row.bridge_pp:+.3f}")
    table.add_section()
    table.add_row("sum of buckets", f"{frame['bridge_pp'].sum():+.3f}")
    table.add_row("total growth", f"{total:+.3f}")
    residual = abs(total - frame["bridge_pp"].sum())
    table.add_row("residual", "< 1e-9" if residual < 1e-9 else f"{residual:.3e}")
    console.print(table)


@app.command("policy-diff")
def policy_diff_cmd(
    no_ramp: Annotated[
        bool, typer.Option("--no-ramp", help="Show LFL without the ramp exclusion.")
    ] = False,
    no_restate: Annotated[
        bool, typer.Option("--no-restate", help="Show LFL without the 53rd-week restatement.")
    ] = False,
    year: YearOpt = 2024,
    raw: RawOpt = Path("data/raw"),
    policy: PolicyOpt = None,
) -> None:
    """How each comp rule moves LFL. With neither flag, both variants are shown."""
    base = _policy(policy)
    data = _raw(raw)
    variants: dict[str, CompPolicy] = {}
    if no_ramp or not no_restate:
        variants["no ramp exclusion"] = base.variant(ramp_weeks=0)
    if no_restate or not no_ramp:
        variants["no restatement"] = base.variant(restate=False)
    frame = analysis.policy_diff(data, base, year, variants)
    table = Table(title=f"FY{year} LFL under different comp policies")
    table.add_column("Policy")
    table.add_column("Year LFL", justify="right")
    table.add_column("vs base (pp)", justify="right")
    table.add_column("Dec LFL", justify="right")
    table.add_column("vs base (pp)", justify="right")
    for row in frame.itertuples():
        table.add_row(
            row.policy,
            _pct(row.year_lfl_pct),
            f"{row.year_vs_baseline_pp:+.2f}",
            _pct(row.december_lfl_pct),
            f"{row.december_vs_baseline_pp:+.2f}",
        )
    console.print(table)


@app.command("validate-pbip")
def validate_cmd(
    pbip: Annotated[Path, typer.Option("--pbip")] = Path("powerbi"),
    data: Annotated[Path, typer.Option("--data")] = Path("data/mart"),
) -> None:
    """Statically check the PBIP model and report against the mart CSVs and the metric registry."""
    result = validate_pbip(pbip, data)
    if result.ok:
        console.print(
            f"[green]ok[/green]: {result.measures_checked} measures, "
            f"{result.references_checked} DAX references, {result.relationships_checked} "
            f"relationships, {result.visual_fields_checked} visual fields"
        )
        return
    for message in result.errors:
        console.print(f"[red]error:[/red] {message}", markup=True, highlight=False)
    raise typer.Exit(1)
