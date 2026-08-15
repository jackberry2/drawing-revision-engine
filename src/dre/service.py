"""The shared core used by both the FastAPI endpoint and the CLI: given an
`analysis_requests` row, fetch its drawing(s) from Supabase Storage, run the
reasoning pipeline, map the result onto `flagged_changes`, and record the
richer internal reasoning in `pipeline_change_events` alongside it.

Handles both modes transparently based on `analysis_requests.mode`:
two_image (old_drawing_id + new_drawing_id both set) and single_sheet (the
one sheet's drawing id in *either* old_drawing_id or new_drawing_id, with
the other column NULL — Lovable's own single-sheet requests populate
new_drawing_id and leave old_drawing_id NULL — see
docs/single_sheet_mode_findings.md). Callers don't need to know which mode
a request is or which column it used; this reads it off the row.

`dry_run=True` does everything read-only: fetches the real request/drawings,
downloads the real image(s), runs the real pipeline (including real Claude
calls) — but skips every write (no pipeline_runs/pipeline_steps row, no
flagged_changes/pipeline_change_events insert, no status update). Use it to
preview what a real run would produce before committing it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Optional

from dre.mapping import to_change_type, to_confidence_percentage, to_confidence_tier
from dre.pipeline.base import NullStepLogger, PipelineContext
from dre.pipeline.runner import build_pipeline
from dre.supa import repository as repo
from dre.supa.repository import SupabaseStepLogger


def _image_suffix(drawing: dict) -> str:
    return Path(drawing["file_path"]).suffix or ".png"


def _mapped_preview(alert) -> dict[str, Any]:
    """The exact shape a flagged_changes row would take for this alert."""
    return {
        "change_type": to_change_type(alert.category),
        "description": alert.description,
        "confidence_tier": to_confidence_tier(alert.confidence.score),
        "confidence_percentage": to_confidence_percentage(alert.confidence.score),
        "impact_note": alert.impact_note,
    }


def analyze_request(analysis_request_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    analysis_request = repo.get_analysis_request(analysis_request_id)
    mode = analysis_request.get("mode") or "two_image"

    old_drawing_id: Optional[str] = analysis_request.get("old_drawing_id")
    new_drawing_id: Optional[str] = analysis_request.get("new_drawing_id")

    if mode == "single_sheet":
        # The caller may populate either column with the one sheet being
        # reviewed — Lovable's own single-sheet requests set new_drawing_id
        # and leave old_drawing_id NULL, the opposite of what this service
        # originally assumed. Never pass a None id into a uuid-typed lookup
        # (postgrest-py stringifies it to the literal "None", which Postgres
        # then rejects with "invalid input syntax for type uuid").
        sheet_drawing_id = old_drawing_id or new_drawing_id
        if not sheet_drawing_id:
            raise ValueError(
                f"analysis_request {analysis_request_id} is mode='single_sheet' but "
                "neither old_drawing_id nor new_drawing_id is set"
            )
        old_drawing = repo.get_drawing(sheet_drawing_id)
        new_drawing = None
    else:
        if not old_drawing_id:
            raise ValueError(
                f"analysis_request {analysis_request_id} is mode={mode!r} but "
                "old_drawing_id is not set"
            )
        old_drawing = repo.get_drawing(old_drawing_id)
        new_drawing = repo.get_drawing(new_drawing_id) if new_drawing_id else None

    run_id = (
        f"dryrun_{analysis_request_id}"
        if dry_run
        else repo.create_pipeline_run(
            analysis_request_id=analysis_request_id,
            old_drawing_id=old_drawing["id"],
            new_drawing_id=new_drawing["id"] if new_drawing else None,
            mode=mode,
        )
    )
    logger = NullStepLogger() if dry_run else SupabaseStepLogger()

    try:
        with tempfile.TemporaryDirectory(prefix=f"dre_{run_id}_") as tmp:
            tmp_dir = Path(tmp)
            old_path = repo.download_drawing_image(
                old_drawing, tmp_dir / f"old{_image_suffix(old_drawing)}"
            )
            new_path = None
            if new_drawing is not None:
                new_path = repo.download_drawing_image(
                    new_drawing, tmp_dir / f"new{_image_suffix(new_drawing)}"
                )

            ctx = PipelineContext(
                run_id=run_id,
                old_image_path=old_path,
                new_image_path=new_path,
                mode=mode,
                sheet_ref=analysis_request["sheet_number"],
            )
            pipeline = build_pipeline(logger=logger, mode=mode)
            result = pipeline.run(ctx)
    except Exception:
        if not dry_run:
            repo.set_run_status(run_id, "failed")
        raise

    if dry_run:
        return {
            "run_id": run_id,
            "analysis_request_id": analysis_request_id,
            "mode": mode,
            "dry_run": True,
            "would_write_to_flagged_changes": [_mapped_preview(a) for a in result.alerts],
            "alerts": [a.model_dump(mode="json") for a in result.alerts],
        }

    change_events_by_id = {e.id: e for e in result.change_events}
    flagged_change_ids: list[str] = []
    # In single_sheet mode there's only one drawing — it's always loaded into
    # old_drawing above (regardless of which column it came from), so it's
    # what flagged_changes.drawing_id points at.
    result_drawing = new_drawing if new_drawing is not None else old_drawing

    for alert in result.alerts:
        change_event = change_events_by_id[alert.change_event_id]
        flagged_change_id = repo.save_flagged_change(
            project_id=analysis_request["project_id"],
            drawing_id=result_drawing["id"],
            analysis_request_id=analysis_request_id,
            sheet_number=analysis_request["sheet_number"],
            change_type=to_change_type(alert.category),
            description=alert.description,
            confidence_tier=to_confidence_tier(alert.confidence.score),
            confidence_percentage=to_confidence_percentage(alert.confidence.score),
            impact_note=alert.impact_note,
        )
        repo.save_pipeline_change_event(
            run_id=run_id,
            flagged_change_id=flagged_change_id,
            change_event=change_event,
            confidence_score=alert.confidence.score,
            confidence_rationale=alert.confidence.model_dump(mode="json"),
        )
        flagged_change_ids.append(flagged_change_id)

    repo.set_run_status(run_id, "completed")
    repo.set_analysis_status(analysis_request_id, "in_review")

    return {
        "run_id": run_id,
        "analysis_request_id": analysis_request_id,
        "mode": mode,
        "dry_run": False,
        "flagged_change_ids": flagged_change_ids,
        "alerts": [a.model_dump(mode="json") for a in result.alerts],
    }
