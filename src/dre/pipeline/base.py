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
from typing import Any, Optional

from dre.models.schemas import (
    ChangeEvent,
    ClassifiedChange,
    ConfidenceScore,
    DetectResult,
    FinalChangeAlert,
)


@dataclass
class PipelineContext:
    run_id: str
    prev_image_path: Path
    revised_image_path: Path
    sheet_ref: Optional[str] = None

    detect_result: Optional[DetectResult] = None
    classified_changes: list[ClassifiedChange] = field(default_factory=list)
    change_events: list[ChangeEvent] = field(default_factory=list)
    confidence_scores: dict[str, ConfidenceScore] = field(default_factory=dict)
    alerts: list[FinalChangeAlert] = field(default_factory=list)


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
