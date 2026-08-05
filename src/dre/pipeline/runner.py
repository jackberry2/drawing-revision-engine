"""Builds the stage pipeline. Shared by the live service (`dre.service`) and
the eval harness (`evals/run_eval.py`) so the stage list only lives in one
place — everything specific to *where the images/logs come from* stays in
the caller (Supabase for the service, local files + a no-op logger for eval).
"""

from __future__ import annotations

from dre.pipeline.base import StepLogger
from dre.pipeline.classify import ClassifyStep
from dre.pipeline.confidence import ConfidenceStep
from dre.pipeline.describe import DescribeStep
from dre.pipeline.detect import DetectStep
from dre.pipeline.orchestrator import Pipeline
from dre.pipeline.reason import ReasonStep


def build_pipeline(logger: StepLogger) -> Pipeline:
    return Pipeline(
        [DetectStep(), ClassifyStep(), ReasonStep(), ConfidenceStep(), DescribeStep()],
        logger=logger,
    )
