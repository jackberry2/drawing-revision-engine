from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from dre.pipeline.runner import run_pipeline
from dre.storage import repository


@click.group()
def cli():
    """Detection engine for electrical drawing revision analysis."""


@cli.command()
@click.argument("prev_image", type=click.Path(exists=True, path_type=Path))
@click.argument("revised_image", type=click.Path(exists=True, path_type=Path))
@click.option("--sheet-id", default=None, help="Optional sheet identifier for this run.")
def run(prev_image: Path, revised_image: Path, sheet_id: str | None):
    """Run the pipeline once on a previous/revised image pair."""
    result = run_pipeline(prev_image, revised_image, sheet_id=sheet_id)
    click.echo(f"run_id: {result.run_id}")
    if not result.alerts:
        click.echo("No material changes detected.")
        return
    for alert in result.alerts:
        click.echo("")
        click.echo(f"[{alert.category.value}] {alert.headline}")
        click.echo(f"  confidence: {alert.confidence.score:.2f}  ({alert.confidence.rationale})")
        click.echo(f"  {alert.description}")


@cli.command()
def eval():
    """Run the eval harness against evals/cases/* and score vs expected output."""
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "evals" / "run_eval.py"
    result = subprocess.run([sys.executable, str(script)])
    sys.exit(result.returncode)


@cli.command()
@click.argument("run_id")
@click.option("--reviewer", default="unknown", help="Name/id of the person reviewing.")
def review(run_id: str, reviewer: str):
    """Interactively capture human corrections for a past run's change events."""
    change_events = repository.get_change_events_for_run(run_id)
    if not change_events:
        click.echo(f"No change events found for run {run_id!r}.")
        return

    for ce in change_events:
        click.echo("")
        click.echo(f"[{ce.category}] {ce.final_description}")
        click.echo(f"  system confidence: {ce.confidence_score:.2f}")
        verdict = click.prompt(
            "  verdict", type=click.Choice(["confirmed", "corrected", "false_positive"])
        )
        corrected_category = corrected_description = None
        corrected_confidence = None
        notes = click.prompt("  notes (optional)", default="", show_default=False)
        if verdict == "corrected":
            corrected_category = click.prompt("  corrected category", default="", show_default=False) or None
            corrected_description = (
                click.prompt("  corrected description", default="", show_default=False) or None
            )
            conf_str = click.prompt("  corrected confidence 0-1 (optional)", default="", show_default=False)
            corrected_confidence = float(conf_str) if conf_str else None

        repository.record_human_review(
            change_event_id=ce.id,
            run_id=run_id,
            reviewer=reviewer,
            verdict=verdict,
            corrected_category=corrected_category,
            corrected_description=corrected_description,
            corrected_confidence=corrected_confidence,
            notes=notes or None,
        )

    click.echo("")
    click.echo("Review recorded.")


if __name__ == "__main__":
    cli()
