"""The shared core used by both the FastAPI endpoint and the CLI: given an
`analysis_requests` row, fetch its two drawings from Supabase Storage, run
the reasoning pipeline, map the result onto `flagged_changes`, and record the
richer internal reasoning in `pipeline_change_events` alongside it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dre.mapping import to_change_type, to_confidence_percentage, to_confidence_tier
from dre.pipeline.base import PipelineContext
from dre.pipeline.runner import build_pipeline
from dre.supa import repository as repo
from dre.supa.repository import SupabaseStepLogger


def _image_suffix(drawing: dict) -> str:
    path = urlparse(drawing["file_url"]).path
    return Path(path).suffix or ".png"


def analyze_request(analysis_request_id: str) -> dict[str, Any]:
    analysis_request = repo.get_analysis_request(analysis_request_id)
    old_drawing = repo.get_drawing(analysis_request["old_drawing_id"])
    new_drawing = repo.get_drawing(analysis_request["new_drawing_id"])

    run_id = repo.create_pipeline_run(
        analysis_request_id=analysis_request_id,
        old_drawing_id=old_drawing["id"],
        new_drawing_id=new_drawing["id"],
    )

    try:
        with tempfile.TemporaryDirectory(prefix=f"dre_{run_id}_") as tmp:
            tmp_dir = Path(tmp)
            old_path = repo.download_drawing_image(
                old_drawing, tmp_dir / f"old{_image_suffix(old_drawing)}"
            )
            new_path = repo.download_drawing_image(
                new_drawing, tmp_dir / f"new{_image_suffix(new_drawing)}"
            )

            ctx = PipelineContext(
                run_id=run_id,
                old_image_path=old_path,
                new_image_path=new_path,
                sheet_ref=analysis_request["sheet_number"],
            )
            pipeline = build_pipeline(logger=SupabaseStepLogger())
            result = pipeline.run(ctx)
    except Exception:
        repo.set_run_status(run_id, "failed")
        raise

    change_events_by_id = {e.id: e for e in result.change_events}
    flagged_change_ids: list[str] = []

    for alert in result.alerts:
        change_event = change_events_by_id[alert.change_event_id]
        flagged_change_id = repo.save_flagged_change(
            project_id=analysis_request["project_id"],
            drawing_id=new_drawing["id"],
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
        "flagged_change_ids": flagged_change_ids,
        "alerts": [a.model_dump(mode="json") for a in result.alerts],
    }
