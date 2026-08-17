from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from dre import service
from dre.supa import repository as repo


@click.group()
def cli():
    """Detection engine for electrical drawing revision analysis."""


@cli.command()
@click.argument("analysis_request_id")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Run the real pipeline against the real request/images but write nothing to the database.",
)
def analyze(analysis_request_id: str, dry_run: bool):
    """Run the pipeline for a real analysis_requests row and write results
    to flagged_changes. Manual equivalent of POST /analyze/{id}."""
    result = service.analyze_request(analysis_request_id, dry_run=dry_run)
    click.echo(f"run_id: {result['run_id']}  (mode: {result['mode']})")
    if dry_run:
        click.echo("DRY RUN - nothing written to the database")
    if not result["alerts"]:
        click.echo("No material changes detected.")
        return
    for alert in result["alerts"]:
        click.echo("")
        click.echo(f"[{alert['category']}] {alert['headline']}")
        click.echo(
            f"  confidence: {alert['confidence']['score']:.2f}  "
            f"({alert['confidence']['rationale']})"
        )
        click.echo(f"  {alert['description']}")
        if alert.get("impact_note"):
            click.echo(f"  impact: {alert['impact_note']}")
    click.echo("")
    if dry_run:
        click.echo(f"Would write {len(result['would_write_to_flagged_changes'])} row(s):")
        for row in result["would_write_to_flagged_changes"]:
            click.echo(f"  {row}")
    else:
        click.echo(f"Wrote {len(result['flagged_change_ids'])} row(s) to flagged_changes.")


@cli.command("tile-detect")
@click.argument("pdf_path", type=click.Path(exists=True, path_type=Path))
@click.option("--sheet-width-in", type=float, required=True)
@click.option("--sheet-height-in", type=float, required=True)
@click.option("--dpi", type=float, required=True)
@click.option("--row", type=int, required=True)
@click.option("--col", type=int, required=True)
def tile_detect(
    pdf_path: Path, sheet_width_in: float, sheet_height_in: float, dpi: float, row: int, col: int
):
    """Tuning harness for docs/tiled_analysis_findings.md §3b/§5 — render
    one tile of a local PDF at a candidate DPI and run the real
    detect_single stage against it, so candidate DPI values can be tested
    against real sheets without a Supabase round trip."""
    from dre.tiling import compute_tile_grid
    from dre.pipeline.tile_tuning import run_detect_single_on_tile

    tiles = compute_tile_grid(
        sheet_width_in=sheet_width_in, sheet_height_in=sheet_height_in, target_dpi=dpi
    )
    tile = next((t for t in tiles if t.row == row and t.col == col), None)
    if tile is None:
        max_row = max(t.row for t in tiles)
        max_col = max(t.col for t in tiles)
        raise click.ClickException(
            f"No tile at row={row} col={col} — grid is {max_row + 1} rows x {max_col + 1} cols"
        )

    click.echo(
        f"Tile region: x={tile.region.x:.4f} y={tile.region.y:.4f} "
        f"w={tile.region.width:.4f} h={tile.region.height:.4f}"
    )
    click.echo(f"Render size: {tile.render_width_px}x{tile.render_height_px}px at {dpi} DPI\n")

    result = run_detect_single_on_tile(pdf_path.read_bytes(), tile, dpi=dpi)
    click.echo(f"{len(result.detections)} detection(s), {len(result.extracted_tables)} table(s)\n")
    for d in result.detections:
        click.echo(f"[{d.flagged_by}] region={d.region}")
        click.echo(f"  {d.geometry_description}\n")
    for t in result.extracted_tables:
        click.echo(f"Table: {t.title}")
        for r in t.rows:
            click.echo(f"  {r}")
        click.echo("")


@cli.command("tile-detect-grid")
@click.argument("pdf_path", type=click.Path(exists=True, path_type=Path))
@click.option("--sheet-width-in", type=float, required=True)
@click.option("--sheet-height-in", type=float, required=True)
@click.option("--dpi", type=float, required=True)
@click.option("--tile-edge-px", type=int, default=None, help="Override the default tile edge size (px).")
@click.option("--overlap-fraction", type=float, default=None, help="Override the default tile overlap fraction.")
def tile_detect_grid(
    pdf_path: Path,
    sheet_width_in: float,
    sheet_height_in: float,
    dpi: float,
    tile_edge_px: int | None,
    overlap_fraction: float | None,
):
    """Real-accumulation path for §5's merge-threshold calibration data
    (§3c) — runs detect_single against EVERY tile in the grid (real API
    cost scales with tile count, §4 — deliberate use only) and reports
    §3c's merge diagnostics across the full result: candidate pairs
    evaluated, which tokens they shared, and how many groups actually
    merged. There's no production tiled flow to log this from passively
    yet; this is how more real known-good/known-miss data points get
    collected until one exists."""
    from dre.pipeline.tile_merge import compute_merge_diagnostics
    from dre.pipeline.tile_tuning import run_detect_single_on_grid
    from dre.tiling import DEFAULT_OVERLAP_FRACTION, DEFAULT_TILE_EDGE_PX, compute_tile_grid

    grid_kwargs = {
        "tile_edge_px": tile_edge_px if tile_edge_px is not None else DEFAULT_TILE_EDGE_PX,
        "overlap_fraction": (
            overlap_fraction if overlap_fraction is not None else DEFAULT_OVERLAP_FRACTION
        ),
    }
    tiles = compute_tile_grid(
        sheet_width_in=sheet_width_in, sheet_height_in=sheet_height_in, target_dpi=dpi, **grid_kwargs
    )
    click.echo(f"Grid: {len(tiles)} tiles at {dpi} DPI — running detect_single on each...\n")

    detections = run_detect_single_on_grid(
        pdf_path.read_bytes(),
        sheet_width_in=sheet_width_in,
        sheet_height_in=sheet_height_in,
        dpi=dpi,
        **grid_kwargs,
    )
    click.echo(f"{len(detections)} total detections across the grid.\n")

    diagnostics = compute_merge_diagnostics(detections)
    click.echo(
        f"Merge diagnostics: {diagnostics.evaluated_pairs} adjacent pair(s) evaluated, "
        f"{diagnostics.matched_pairs} matched MIN_SHARED_TOKENS, "
        f"{diagnostics.groups} final group(s), {diagnostics.multi_member_groups} merged "
        f"(multi-member) group(s)\n"
    )
    for p in diagnostics.pairs:
        click.echo(
            f"  {'MATCH ' if p.matched else 'no match '}tile{p.tile_a}:{p.detection_id_a} <-> "
            f"tile{p.tile_b}:{p.detection_id_b}  shared_tokens={p.shared_tokens}"
        )


@cli.command()
def eval():
    """Run the eval harness against evals/cases/* and score vs expected output."""
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "evals" / "run_eval.py"
    result = subprocess.run([sys.executable, str(script)])
    sys.exit(result.returncode)


@cli.command()
@click.argument("flagged_change_id")
@click.option("--reviewer", default="unknown", help="Name/id of the person reviewing.")
def review(flagged_change_id: str, reviewer: str):
    """Interactively capture a human correction for one flagged_changes row."""
    change = repo.get_flagged_change(flagged_change_id)
    click.echo(f"[{change['change_type']}] {change['description']}")
    if change.get("impact_note"):
        click.echo(f"  impact: {change['impact_note']}")
    click.echo(
        f"  system confidence: {change['confidence_tier']} ({change['confidence_percentage']}%)"
    )

    verdict = click.prompt("verdict", type=click.Choice(["confirmed", "corrected", "false_positive"]))
    corrected_change_type = corrected_description = None
    corrected_confidence_percentage = None
    notes = click.prompt("notes (optional)", default="", show_default=False)
    if verdict == "corrected":
        corrected_change_type = (
            click.prompt(
                "corrected change_type",
                type=click.Choice(["added", "removed", "moved", "modified"]),
                default="",
                show_default=False,
            )
            or None
        )
        corrected_description = (
            click.prompt("corrected description", default="", show_default=False) or None
        )
        conf_str = click.prompt(
            "corrected confidence 0-100 (optional)", default="", show_default=False
        )
        corrected_confidence_percentage = int(conf_str) if conf_str else None

    repo.record_human_review(
        flagged_change_id=flagged_change_id,
        run_id=None,
        reviewer=reviewer,
        verdict=verdict,
        corrected_change_type=corrected_change_type,
        corrected_description=corrected_description,
        corrected_confidence_percentage=corrected_confidence_percentage,
        notes=notes or None,
    )
    click.echo("Review recorded.")


if __name__ == "__main__":
    cli()
