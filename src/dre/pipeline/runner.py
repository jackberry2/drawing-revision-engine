"""Builds the stage pipeline. Shared by the live service (`dre.service`) and
the eval harness (`evals/run_eval.py`) so the stage list only lives in one
place — everything specific to *where the images/logs come from* stays in
the caller (Supabase for the service, local files + a no-op logger for eval).
"""

from __future__ import annotations

from typing import Literal

from dre.pipeline.base import StepLogger
from dre.pipeline.classify import ClassifyStep
from dre.pipeline.confidence import ConfidenceStep
from dre.pipeline.describe import DescribeStep
from dre.pipeline.detect import DetectStep
from dre.pipeline.detect_single import DetectSingleStep
from dre.pipeline.orchestrator import Pipeline
from dre.pipeline.reason import ReasonStep
from dre.pipeline.reason_single import ReasonSingleStep


def build_pipeline(logger: StepLogger, mode: Literal["two_image", "single_sheet"] = "two_image") -> Pipeline:
    if mode == "single_sheet":
        steps = [DetectSingleStep(), ClassifyStep(), ReasonSingleStep(), ConfidenceStep(), DescribeStep()]
    else:
        steps = [DetectStep(), ClassifyStep(), ReasonStep(), ConfidenceStep(), DescribeStep()]
    return Pipeline(steps, logger=logger)
