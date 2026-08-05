from __future__ import annotations

import time

from dre.models.schemas import PipelineRunResult
from dre.pipeline.base import PipelineContext, PipelineStep
from dre.storage import repository


class Pipeline:
    def __init__(self, steps: list[PipelineStep]):
        self.steps = steps

    def run(self, ctx: PipelineContext) -> PipelineRunResult:
        for order, step in enumerate(self.steps, start=1):
            input_snapshot = step.input_for_log(ctx)
            t0 = time.perf_counter()
            output = step.execute(ctx)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            repository.log_step(
                run_id=ctx.run_id,
                step_name=step.name,
                step_order=order,
                input_data=input_snapshot,
                output_data=output,
                model_used=step.model_used,
                prompt_version=step.version,
                latency_ms=latency_ms,
            )

        change_events_by_id = {e.id: e for e in ctx.change_events}
        for alert in ctx.alerts:
            change_event = change_events_by_id.get(alert.change_event_id)
            if change_event is None:
                raise RuntimeError(
                    f"alert references unknown change_event_id {alert.change_event_id!r}"
                )
            repository.save_alert(ctx.run_id, change_event, alert)

        assert ctx.detect_result is not None
        return PipelineRunResult(
            run_id=ctx.run_id,
            detect_result=ctx.detect_result,
            classified_changes=ctx.classified_changes,
            change_events=ctx.change_events,
            alerts=ctx.alerts,
        )
