from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import BaseModel

from dre.storage.db import get_session
from dre.storage.models import ChangeEventRecord, HumanReview, PipelineStepLog, Run


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _dump(obj: Any) -> str:
    return json.dumps(_to_jsonable(obj))


def create_run(
    prev_image_path: str,
    revised_image_path: str,
    run_id: str | None = None,
    sheet_id: str | None = None,
) -> Run:
    session = get_session()
    with session:
        run = Run(
            id=run_id or new_id("run"),
            prev_image_path=prev_image_path,
            revised_image_path=revised_image_path,
            sheet_id=sheet_id,
            status="running",
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run


def set_run_status(run_id: str, status: str) -> None:
    session = get_session()
    with session:
        run = session.get(Run, run_id)
        if run is None:
            raise ValueError(f"unknown run_id: {run_id}")
        run.status = status
        session.commit()


def log_step(
    run_id: str,
    step_name: str,
    step_order: int,
    input_data: Any,
    output_data: Any,
    model_used: str | None = None,
    prompt_version: str | None = None,
    latency_ms: int | None = None,
) -> None:
    session = get_session()
    with session:
        session.add(
            PipelineStepLog(
                run_id=run_id,
                step_name=step_name,
                step_order=step_order,
                model_used=model_used,
                prompt_version=prompt_version,
                input_json=_dump(input_data),
                output_json=_dump(output_data),
                latency_ms=latency_ms,
            )
        )
        session.commit()


def save_alert(run_id: str, change_event, alert) -> None:
    """change_event: models.schemas.ChangeEvent, alert: models.schemas.FinalChangeAlert"""
    session = get_session()
    with session:
        session.add(
            ChangeEventRecord(
                id=alert.change_event_id,
                run_id=run_id,
                category=alert.category.value,
                root_cause_description=change_event.root_cause_summary,
                bundled_change_ids_json=json.dumps(change_event.bundled_change_ids),
                downstream_implications_json=json.dumps(change_event.downstream_implications),
                confidence_score=alert.confidence.score,
                confidence_rationale_json=alert.confidence.model_dump_json(),
                final_description=alert.description,
            )
        )
        session.commit()


def record_human_review(
    change_event_id: str,
    run_id: str,
    reviewer: str,
    verdict: str,
    corrected_category: str | None = None,
    corrected_description: str | None = None,
    corrected_confidence: float | None = None,
    notes: str | None = None,
) -> None:
    session = get_session()
    with session:
        session.add(
            HumanReview(
                change_event_id=change_event_id,
                run_id=run_id,
                reviewer=reviewer,
                verdict=verdict,
                corrected_category=corrected_category,
                corrected_description=corrected_description,
                corrected_confidence=corrected_confidence,
                notes=notes,
            )
        )
        session.commit()


def get_change_events_for_run(run_id: str) -> list[ChangeEventRecord]:
    session = get_session()
    with session:
        run = session.get(Run, run_id)
        if run is None:
            raise ValueError(f"unknown run_id: {run_id}")
        return list(run.change_events)
