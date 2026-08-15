"""Supabase-backed persistence.

Reads/writes the caller's existing `drawings` / `analysis_requests` /
`flagged_changes` tables exactly as they're defined in their project, plus
our four additive logging tables (see
`supabase/migrations/0001_pipeline_logging.sql`) that hold the richer
internal reasoning and human corrections `flagged_changes` alone can't carry.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from dre import config
from dre.models.schemas import ChangeEvent
from dre.supa.client import get_client


def new_id() -> str:
    return str(uuid.uuid4())


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


# ---- drawings / analysis_requests (read-only from this service's POV) ----


def get_analysis_request(analysis_request_id: str) -> dict:
    resp = (
        get_client()
        .table("analysis_requests")
        .select("*")
        .eq("id", analysis_request_id)
        .single()
        .execute()
    )
    return resp.data


def get_drawing(drawing_id: str) -> dict:
    resp = get_client().table("drawings").select("*").eq("id", drawing_id).single().execute()
    return resp.data


def download_drawing_image(drawing: dict, dest_path: Path) -> Path:
    """`drawings.file_path` is a path inside the (private) drawings Storage
    bucket, not a directly-fetchable URL — download it via the Storage API
    using the service role key, which bypasses bucket RLS."""
    data = get_client().storage.from_(config.SUPABASE_DRAWINGS_BUCKET).download(
        drawing["file_path"]
    )
    dest_path.write_bytes(data)
    return dest_path


def set_analysis_status(analysis_request_id: str, status: str) -> None:
    get_client().table("analysis_requests").update({"status": status}).eq(
        "id", analysis_request_id
    ).execute()


# ---- pipeline_runs / pipeline_steps (proprietary logging) ----


def create_pipeline_run(
    analysis_request_id: str,
    old_drawing_id: str,
    new_drawing_id: Optional[str] = None,
    mode: str = "two_image",
) -> str:
    run_id = new_id()
    get_client().table("pipeline_runs").insert(
        {
            "id": run_id,
            "analysis_request_id": analysis_request_id,
            "old_drawing_id": old_drawing_id,
            "new_drawing_id": new_drawing_id,
            "mode": mode,
            "status": "running",
        }
    ).execute()
    return run_id


def set_run_status(run_id: str, status: str) -> None:
    get_client().table("pipeline_runs").update({"status": status}).eq("id", run_id).execute()


def log_step(
    *,
    run_id: str,
    step_name: str,
    step_order: int,
    input_data: Any,
    output_data: Any,
    model_used: Optional[str] = None,
    prompt_version: Optional[str] = None,
    latency_ms: Optional[int] = None,
) -> None:
    get_client().table("pipeline_steps").insert(
        {
            "run_id": run_id,
            "step_name": step_name,
            "step_order": step_order,
            "model_used": model_used,
            "prompt_version": prompt_version,
            "input_json": _to_jsonable(input_data),
            "output_json": _to_jsonable(output_data),
            "latency_ms": latency_ms,
        }
    ).execute()


class SupabaseStepLogger:
    """`StepLogger` implementation used by the live service (see
    `dre.pipeline.base.StepLogger`)."""

    def log_step(self, **kwargs: Any) -> None:
        log_step(**kwargs)


# ---- flagged_changes / pipeline_change_events ----


def save_flagged_change(
    *,
    project_id: str,
    drawing_id: Optional[str],
    analysis_request_id: Optional[str],
    sheet_number: str,
    change_type: str,
    description: str,
    confidence_tier: str,
    confidence_percentage: int,
    impact_note: Optional[str],
) -> str:
    flagged_change_id = new_id()
    get_client().table("flagged_changes").insert(
        {
            "id": flagged_change_id,
            "project_id": project_id,
            "drawing_id": drawing_id,
            "analysis_request_id": analysis_request_id,
            "sheet_number": sheet_number,
            "change_type": change_type,
            "description": description,
            "confidence_tier": confidence_tier,
            "confidence_percentage": confidence_percentage,
            "impact_note": impact_note,
        }
    ).execute()
    return flagged_change_id


def save_pipeline_change_event(
    *,
    run_id: str,
    flagged_change_id: str,
    change_event: ChangeEvent,
    confidence_score: float,
    confidence_rationale: dict,
) -> None:
    get_client().table("pipeline_change_events").insert(
        {
            "run_id": run_id,
            "flagged_change_id": flagged_change_id,
            "category": change_event.category.value,
            "root_cause_summary": change_event.root_cause_summary,
            "bundled_change_ids": change_event.bundled_change_ids,
            "downstream_implications": change_event.downstream_implications,
            "schedule_corroboration": change_event.schedule_corroboration,
            "schedule_consistency": change_event.schedule_consistency,
            "identity_unresolved": change_event.identity_unresolved,
            "confidence_score": confidence_score,
            "confidence_rationale": _to_jsonable(confidence_rationale),
        }
    ).execute()


# ---- human_reviews ----


def record_human_review(
    *,
    flagged_change_id: str,
    run_id: Optional[str],
    reviewer: str,
    verdict: str,
    corrected_change_type: Optional[str] = None,
    corrected_description: Optional[str] = None,
    corrected_confidence_percentage: Optional[int] = None,
    notes: Optional[str] = None,
) -> None:
    get_client().table("human_reviews").insert(
        {
            "flagged_change_id": flagged_change_id,
            "run_id": run_id,
            "reviewer": reviewer,
            "verdict": verdict,
            "corrected_change_type": corrected_change_type,
            "corrected_description": corrected_description,
            "corrected_confidence_percentage": corrected_confidence_percentage,
            "notes": notes,
        }
    ).execute()
    if verdict in ("confirmed", "corrected"):
        get_client().table("flagged_changes").update({"reviewed": True}).eq(
            "id", flagged_change_id
        ).execute()


def get_flagged_changes_for_analysis_request(analysis_request_id: str) -> list[dict]:
    resp = (
        get_client()
        .table("flagged_changes")
        .select("*")
        .eq("analysis_request_id", analysis_request_id)
        .execute()
    )
    return resp.data


def get_flagged_change(flagged_change_id: str) -> dict:
    resp = (
        get_client()
        .table("flagged_changes")
        .select("*")
        .eq("id", flagged_change_id)
        .single()
        .execute()
    )
    return resp.data
