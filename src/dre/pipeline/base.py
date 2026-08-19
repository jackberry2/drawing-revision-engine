"""Shared stage interface.

Every stage subclasses `PipelineStep` and only ever reads/writes fields on
`PipelineContext` via typed schema objects (see `dre.models.schemas`). The
orchestrator never inspects a stage's internals — it just calls `execute`
and logs whatever `input_for_log`/`execute` hand back. That's what lets a
stage be swapped (e.g. `detect` becoming a custom CV model instead of a
Claude vision call) without changing the orchestrator or any other stage.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Protocol

from dre.models.schemas import (
    ChangeEvent,
    ClassifiedChange,
    ConfidenceScore,
    DetectResult,
    FinalChangeAlert,
    SingleSheetDetectResult,
)


@dataclass
class PipelineContext:
    run_id: str
    old_image_path: Path
    mode: Literal["two_image", "single_sheet"] = "two_image"
    # Required in two_image mode, always None in single_sheet mode (there is
    # only one image — old_image_path holds it either way).
    new_image_path: Optional[Path] = None
    sheet_ref: Optional[str] = None

    detect_result: Optional[DetectResult] = None
    detect_single_result: Optional[SingleSheetDetectResult] = None
    classified_changes: list[ClassifiedChange] = field(default_factory=list)
    change_events: list[ChangeEvent] = field(default_factory=list)
    confidence_scores: dict[str, ConfidenceScore] = field(default_factory=dict)
    alerts: list[FinalChangeAlert] = field(default_factory=list)
    # Real per-call token usage, appended to by every `call_structured` call
    # this run makes (including the parallel per-tile calls under tiling —
    # see `dre.pipeline.tile_tuning`). One dict per real API attempt:
    # {"model": ..., "input_tokens": ..., "output_tokens": ...}.
    token_usage: list[dict] = field(default_factory=list)


class PipelineStep(ABC):
    name: str
    version: str = "v1"
    model_used: Optional[str] = None

    @abstractmethod
    def input_for_log(self, ctx: PipelineContext) -> Any:
        """JSON-serializable snapshot of what this step consumes from ctx."""

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> Any:
        """Do the work, mutate ctx with the result, and return the result
        (also JSON-serializable) for logging."""


class StepLogger(Protocol):
    """What the orchestrator needs to persist a stage's input/output. Kept
    separate from any concrete backend so the reasoning pipeline itself never
    depends on where logs end up (Supabase in production, nowhere in eval
    runs against local test images with no backing rows to attach to)."""

    def log_step(
        self,
        *,
        run_id: str,
        step_name: str,
        step_order: int,
        input_data: Any,
        output_data: Any,
        model_used: Optional[str],
        prompt_version: Optional[str],
        latency_ms: Optional[int],
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> None: ...


class NullStepLogger:
    """No-op logger — used by `dre eval`, which runs the pipeline against
    local test images with no run/analysis_request row to log against."""

    def log_step(self, **kwargs: Any) -> None:
        return None
