"""Convenience entrypoint used by both the CLI and the eval harness so the
stage list only lives in one place."""

from __future__ import annotations

from pathlib import Path

from dre.models.schemas import PipelineRunResult
from dre.pipeline.base import PipelineContext
from dre.pipeline.classify import ClassifyStep
from dre.pipeline.confidence import ConfidenceStep
from dre.pipeline.describe import DescribeStep
from dre.pipeline.detect import DetectStep
from dre.pipeline.orchestrator import Pipeline
from dre.pipeline.reason import ReasonStep
from dre.storage import repository
from dre.storage.files import store_run_images


def build_pipeline() -> Pipeline:
    return Pipeline([DetectStep(), ClassifyStep(), ReasonStep(), ConfidenceStep(), DescribeStep()])


def run_pipeline(
    prev_image: Path, revised_image: Path, sheet_id: str | None = None
) -> PipelineRunResult:
    run_id = repository.new_id("run")
    stored_prev, stored_revised = store_run_images(run_id, prev_image, revised_image)
    repository.create_run(
        prev_image_path=str(stored_prev),
        revised_image_path=str(stored_revised),
        run_id=run_id,
        sheet_id=sheet_id,
    )

    ctx = PipelineContext(
        run_id=run_id,
        prev_image_path=stored_prev,
        revised_image_path=stored_revised,
        sheet_ref=sheet_id,
    )
    result = build_pipeline().run(ctx)
    repository.set_run_status(run_id, "completed")
    return result
