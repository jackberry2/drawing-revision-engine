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
from dre.imaging import extension_for_media_type, normalize_drawing_bytes
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


def download_drawing_image(drawing: dict, dest_dir: Path, basename: str) -> Path:
    """`drawings.file_path` is a path inside the (private) drawings Storage
    bucket, not a directly-fetchable URL — download it via the Storage API
    using the service role key, which bypasses bucket RLS.

    The destination filename/extension is decided from the real downloaded
    content, not from `drawings.file_path`'s extension or Storage's reported
    mimetype — a real upload (E-101.3) came through as an honestly-labeled
    PDF, which Claude's vision API can't accept as an `image` block, so PDFs
    are rasterized to PNG here before ever reaching the pipeline. Every
    other format is passed through unchanged once its real content is
    confirmed to be one Claude actually accepts (see `dre.imaging`).
    """
    data = get_client().storage.from_(config.SUPABASE_DRAWINGS_BUCKET).download(
        drawing["file_path"]
    )
    image_bytes, media_type = normalize_drawing_bytes(data)
    dest_path = dest_dir / f"{basename}{extension_for_media_type(media_type)}"
    dest_path.write_bytes(image_bytes)
    return dest_path


def download_drawing_image_and_raw_bytes(
    drawing: dict, dest_dir: Path, basename: str
) -> tuple[Path, bytes]:
    """Same as `download_drawing_image`, but also returns the raw
    pre-normalization bytes as downloaded from Storage. Only single_sheet
    mode's sheet drawing needs this — tiling (docs/tiled_analysis_
    findings.md §3b) re-rasterizes tiles from the *original* PDF, not from
    the already-resolution-capped full-page PNG `normalize_drawing_bytes`
    produces, so those raw bytes have to survive somewhere. A dedicated
    function rather than changing `download_drawing_image`'s return shape
    keeps every other caller (two_image mode's old/new fetches, which never
    need raw bytes) untouched, and avoids downloading the same file twice
    over the network for the one case that does."""
    data = get_client().storage.from_(config.SUPABASE_DRAWINGS_BUCKET).download(
        drawing["file_path"]
    )
    image_bytes, media_type = normalize_drawing_bytes(data)
    dest_path = dest_dir / f"{basename}{extension_for_media_type(media_type)}"
    dest_path.write_bytes(image_bytes)
    return dest_path, data


def download_drawing_raw_bytes(drawing: dict) -> bytes:
    """Raw pre-normalization bytes only, no local file written — for
    callers that just need to inspect the source (e.g. §3e's duration
    estimate, which only needs the PDF's page size) without running it
    through the real pipeline."""
    return get_client().storage.from_(config.SUPABASE_DRAWINGS_BUCKET).download(drawing["file_path"])


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


def log_tiling_trigger_diagnostics(run_id: str, diagnostics: dict) -> None:
    """Passive data collection for docs/tiled_analysis_findings.md §3a — see
    that doc and dre.tiling_trigger for what this records and why. No
    commitment to build tiling is implied by logging this."""
    get_client().table("pipeline_runs").update(
        {"tiling_trigger_diagnostics": diagnostics}
    ).eq("id", run_id).execute()


def set_tiling_path(run_id: str, path: str) -> None:
    """Which real path a run actually took — 'single_pass', 'tiled',
    'tiled_failed_fallback', or 'not_applicable' (two_image mode). See
    migration 0005_tiling_path.sql. Distinct from `would_trigger` in
    `tiling_trigger_diagnostics`: that's what the rule said; this is what
    actually happened (it can differ — e.g. the rule fires but the source
    isn't a tileable PDF, or tiling is attempted and falls back)."""
    get_client().table("pipeline_runs").update({"tiling_path": path}).eq(
        "id", run_id
    ).execute()


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
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
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
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    ).execute()


class SupabaseStepLogger:
    """`StepLogger` implementation used by the live service (see
    `dre.pipeline.base.StepLogger`)."""

    def log_step(self, **kwargs: Any) -> None:
        log_step(**kwargs)


# ---- flagged_changes / pipeline_change_events ----


def delete_flagged_changes_for_analysis_request(analysis_request_id: str) -> None:
    """Clears this request's prior flagged_changes before a re-analysis
    writes new ones, so a request's rows always reflect only its latest
    analysis run — not an accumulation across retries. A real production
    bug: two independently-triggered analyze calls against the same
    analysis_request_id each wrote their own rows with nothing superseding
    the earlier ones, so both sets sat side by side and looked like a single
    run's over-segmentation until traced back through pipeline_steps.

    Note on cascade behavior: pipeline_change_events.flagged_change_id is
    ON DELETE SET NULL (that row survives, orphaned, for the training-data
    record), but human_reviews.flagged_change_id is ON DELETE CASCADE — a
    human review already recorded against a flagged_change that gets
    superseded here is deleted along with it. Re-analyzing an
    already-reviewed request destroys that review; nothing here guards
    against it.
    """
    get_client().table("flagged_changes").delete().eq(
        "analysis_request_id", analysis_request_id
    ).execute()


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


# ---- subscriptions / credit_usage (Lovable-managed; read/write only, we
# don't own the schema — see dre.credits) ----


def get_credit_balance(user_id: str) -> Optional[dict]:
    """Calls the real, pre-existing `get_credit_balance(_user_id)` Postgres
    RPC (built independently, discovered via migration 0008's diagnostic
    introspection — see dre.credits's module docstring) — the single
    source of truth for a user's balance, also callable directly by
    Lovable's frontend as the authenticated user. SECURITY INVOKER, so
    passing an explicit user_id is safe here specifically because this
    service calls it via the service role, which bypasses RLS by design —
    same trust level as every other service-role read this backend does.

    None means no subscription row exists at all for this user; a row
    with an ineligible status (canceled and expired, etc.) is still
    returned — dre.credits applies that eligibility check itself, since
    it isn't encoded in this RPC (only in consume_credits, which this
    service can't call — see dre.credits)."""
    resp = get_client().rpc("get_credit_balance", {"_user_id": user_id}).execute()
    return resp.data[0] if resp.data else None


def record_credit_usage(*, user_id: str, analysis_request_id: str, credits_used: int) -> None:
    get_client().table("credit_usage").insert(
        {
            "user_id": user_id,
            "analysis_request_id": analysis_request_id,
            "credits_used": credits_used,
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
