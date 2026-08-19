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
from dre.pipeline.tiled_detect import decide_and_apply_tiling, estimate_analysis_duration
from dre.supa import repository as repo
from dre.supa.repository import SupabaseStepLogger
from dre.tiling_trigger import compute_tiling_trigger_diagnostics


def _mapped_preview(alert) -> dict[str, Any]:
    """The exact shape a flagged_changes row would take for this alert."""
    return {
        "change_type": to_change_type(alert.category),
        "description": alert.description,
        "confidence_tier": to_confidence_tier(alert.confidence.score),
        "confidence_percentage": to_confidence_percentage(alert.confidence.score),
        "impact_note": alert.impact_note,
    }


def estimate_duration(analysis_request_id: str) -> dict[str, Any]:
    """docs/tiled_analysis_findings.md §3e: a cheap, no-Claude-call estimate
    Lovable can fetch before (or instead of waiting on) the real analyze
    call, so a tiled sheet's longer processing time doesn't look like a
    hang. single_sheet mode only — mirrors `analyze_request`'s own
    old/new-drawing-id resolution for that mode; two_image tiling doesn't
    exist (see docs/tiled_analysis_findings.md §5), so this is scoped the
    same way `/analyze-single` itself already is."""
    analysis_request = repo.get_analysis_request(analysis_request_id)
    mode = analysis_request.get("mode") or "two_image"
    if mode != "single_sheet":
        raise ValueError(
            f"analysis_request {analysis_request_id} has mode={mode!r}, not 'single_sheet'"
        )

    sheet_drawing_id = analysis_request.get("old_drawing_id") or analysis_request.get(
        "new_drawing_id"
    )
    if not sheet_drawing_id:
        raise ValueError(
            f"analysis_request {analysis_request_id} is mode='single_sheet' but "
            "neither old_drawing_id nor new_drawing_id is set"
        )
    drawing = repo.get_drawing(sheet_drawing_id)
    raw_bytes = repo.download_drawing_raw_bytes(drawing)
    estimate = estimate_analysis_duration(raw_bytes)
    return {"analysis_request_id": analysis_request_id, **estimate.model_dump(mode="json")}


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

    tiling_outcome_holder: dict[str, Any] = {}

    try:
        with tempfile.TemporaryDirectory(prefix=f"dre_{run_id}_") as tmp:
            tmp_dir = Path(tmp)
            raw_old_bytes: Optional[bytes] = None
            if mode == "single_sheet":
                # Tiling (docs/tiled_analysis_findings.md §3b) re-rasterizes
                # from the *original* PDF, not the already-resolution-capped
                # full-page PNG — so the raw bytes need to survive past this
                # download, only for the one mode that can ever tile.
                old_path, raw_old_bytes = repo.download_drawing_image_and_raw_bytes(
                    old_drawing, tmp_dir, "old"
                )
            else:
                old_path = repo.download_drawing_image(old_drawing, tmp_dir, "old")
            new_path = None
            if new_drawing is not None:
                new_path = repo.download_drawing_image(new_drawing, tmp_dir, "new")

            ctx = PipelineContext(
                run_id=run_id,
                old_image_path=old_path,
                new_image_path=new_path,
                mode=mode,
                sheet_ref=analysis_request["sheet_number"],
            )
            pipeline = build_pipeline(logger=logger, mode=mode)

            def _on_after_detect(pipeline_ctx: PipelineContext) -> None:
                # single_sheet only — two_image tiling has no design or
                # validation behind it (docs/tiled_analysis_findings.md §5).
                if pipeline_ctx.mode != "single_sheet":
                    return
                usage_before = len(pipeline_ctx.token_usage)
                outcome = decide_and_apply_tiling(pipeline_ctx, raw_pdf_bytes=raw_old_bytes)
                tiling_outcome_holder["outcome"] = outcome
                if outcome.path == "tiled":
                    # Only the tile calls made *during this hook* (the
                    # per-tile detect_single calls) — not the original
                    # single-pass detect_single call, which was already
                    # attributed to the "detect_single" step's own log row
                    # by the orchestrator before this hook ran. Slicing from
                    # usage_before avoids double-counting that call here.
                    tile_usage = pipeline_ctx.token_usage[usage_before:]
                    # Logged separately from the detect_single step it
                    # replaces, so pipeline_steps shows both the original
                    # single-pass output AND what tiling produced instead —
                    # not just the final, already-swapped-in result.
                    logger.log_step(
                        run_id=pipeline_ctx.run_id,
                        step_name="tile_merge",
                        step_order=1,
                        input_data={
                            "trigger_diagnostics": outcome.trigger_diagnostics,
                            "volume_cap_diagnostics": outcome.volume_cap_diagnostics,
                        },
                        output_data=pipeline_ctx.detect_single_result,
                        model_used=None,
                        prompt_version="v1",
                        latency_ms=None,
                        input_tokens=sum(u["input_tokens"] for u in tile_usage) if tile_usage else None,
                        output_tokens=sum(u["output_tokens"] for u in tile_usage) if tile_usage else None,
                    )

            result = pipeline.run(ctx, on_after_detect=_on_after_detect)
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

    # Data collection for docs/tiled_analysis_findings.md §3a — logs whether
    # that document's tiling trigger rule fired against this real run's
    # *original single-pass* detect output. single_sheet mode: the rule was
    # already checked (and, if it fired, acted on) by decide_and_apply_
    # tiling via the on_after_detect hook above — reuse that outcome's
    # diagnostics rather than recomputing against ctx.detect_single_result,
    # which may now be the already-replaced tiled result, not the signal
    # the rule actually evaluated. two_image mode: tiling has no branch
    # built for it yet (§5), so this stays passive-logging-only, same as
    # before. Never allowed to break the real write path below it: this is
    # auxiliary data collection (or, for single_sheet, already-computed data
    # being persisted), not part of the guarantee callers depend on, so a
    # failure here (e.g. a migration not yet applied) is swallowed rather
    # than failing the whole analysis.
    if mode == "single_sheet":
        tiling_outcome = tiling_outcome_holder.get("outcome")
        tiling_diagnostics_dict = tiling_outcome.trigger_diagnostics if tiling_outcome else None
        tiling_path = tiling_outcome.path if tiling_outcome else "single_pass"
    else:
        try:
            tiling_diagnostics_dict = compute_tiling_trigger_diagnostics(
                ctx.detect_result
            ).model_dump(mode="json")
        except Exception:
            tiling_diagnostics_dict = None
        tiling_path = "not_applicable"

    if tiling_diagnostics_dict is not None:
        try:
            repo.log_tiling_trigger_diagnostics(run_id, tiling_diagnostics_dict)
        except Exception:
            pass
    try:
        repo.set_tiling_path(run_id, tiling_path)
    except Exception:
        pass

    # Clear this request's prior flagged_changes now that the new pipeline
    # run has actually succeeded — a re-analysis always supersedes, never
    # accumulates alongside, an earlier run's output for the same request.
    repo.delete_flagged_changes_for_analysis_request(analysis_request_id)

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
