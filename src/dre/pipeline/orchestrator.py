from __future__ import annotations

import time
from typing import Callable, Optional

from dre.models.schemas import PipelineRunResult
from dre.pipeline.base import PipelineContext, PipelineStep, StepLogger

_DETECT_STEP_NAMES = ("detect", "detect_single")


class Pipeline:
    """Runs the stage sequence and logs each stage's input/output via a
    `StepLogger`. Does not itself know about `flagged_changes` or any other
    destination for the final alerts — that mapping/persistence is the
    caller's job (see `dre.service`), keeping this class reusable for eval
    runs that never touch Supabase.
    """

    def __init__(self, steps: list[PipelineStep], logger: StepLogger):
        self.steps = steps
        self.logger = logger

    def run(
        self,
        ctx: PipelineContext,
        *,
        on_after_detect: Optional[Callable[[PipelineContext], None]] = None,
    ) -> PipelineRunResult:
        """`on_after_detect`, if given, runs once right after the detect/
        detect_single step completes and is logged, before classify runs —
        the hook docs/tiled_analysis_findings.md's tiling branch needs
        (checking §3a's trigger rule against the just-computed detect
        output and, if it fires, replacing `ctx.detect_single_result`
        in place before the rest of the pipeline reads it). A no-op for
        any pipeline that doesn't pass one — every other caller
        (eval harness, tests) is unaffected."""
        for order, step in enumerate(self.steps, start=1):
            input_snapshot = step.input_for_log(ctx)
            t0 = time.perf_counter()
            output = step.execute(ctx)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            self.logger.log_step(
                run_id=ctx.run_id,
                step_name=step.name,
                step_order=order,
                input_data=input_snapshot,
                output_data=output,
                model_used=step.model_used,
                prompt_version=step.version,
                latency_ms=latency_ms,
            )
            if on_after_detect is not None and step.name in _DETECT_STEP_NAMES:
                on_after_detect(ctx)

        assert ctx.detect_result is not None or ctx.detect_single_result is not None
        return PipelineRunResult(
            run_id=ctx.run_id,
            mode=ctx.mode,
            detect_result=ctx.detect_result,
            detect_single_result=ctx.detect_single_result,
            classified_changes=ctx.classified_changes,
            change_events=ctx.change_events,
            alerts=ctx.alerts,
        )
