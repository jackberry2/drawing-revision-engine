from __future__ import annotations

from dre import config
from dre.llm.client import call_structured, dump_models, load_prompt
from dre.models.schemas import ChangeEvent, DescribeResponse, FinalChangeAlert
from dre.pipeline.base import PipelineContext, PipelineStep


def _build_impact_note(change_event: ChangeEvent) -> str | None:
    """Derived programmatically from the ChangeEvent the reasoning stage
    already produced — not re-authored by the LLM, so it can't drift from
    what `reason` actually bundled."""
    parts = list(change_event.downstream_implications)
    if change_event.schedule_corroboration:
        parts.append(change_event.schedule_corroboration)
    if not parts:
        return None
    normalized = []
    for part in parts:
        part = part.strip()
        if part and part[-1] not in ".!?":
            part += "."
        normalized.append(part)
    return " ".join(normalized)


class DescribeStep(PipelineStep):
    """Turns each ChangeEvent into the final plain-language estimator alert.

    Confidence is computed by the earlier, separate confidence stage and
    merged in here rather than re-derived — this step only writes prose.
    """

    name = "describe"
    version = "v1"
    model_used = config.REASONING_MODEL

    def input_for_log(self, ctx: PipelineContext) -> dict:
        return {"change_events": [e.model_dump(mode="json") for e in ctx.change_events]}

    def execute(self, ctx: PipelineContext) -> list[FinalChangeAlert]:
        if not ctx.change_events:
            ctx.alerts = []
            return []

        user_content = [
            {"type": "text", "text": "Change events (JSON):\n" + dump_models(ctx.change_events)},
        ]
        result = call_structured(
            system=load_prompt("describe"),
            user_content=user_content,
            response_model=DescribeResponse,
            model=self.model_used,
        )

        change_events_by_id = {e.id: e for e in ctx.change_events}

        alerts: list[FinalChangeAlert] = []
        for item in result.items:
            confidence = ctx.confidence_scores.get(item.change_event_id)
            if confidence is None:
                raise RuntimeError(
                    f"describe stage produced an item for {item.change_event_id!r} "
                    "with no matching confidence score"
                )
            change_event = change_events_by_id.get(item.change_event_id)
            if change_event is None:
                raise RuntimeError(
                    f"describe stage produced an item for unknown change_event_id "
                    f"{item.change_event_id!r}"
                )
            alerts.append(
                FinalChangeAlert(
                    change_event_id=item.change_event_id,
                    category=item.category,
                    headline=item.headline,
                    description=item.description,
                    impact_note=_build_impact_note(change_event),
                    affected_entities=item.affected_entities,
                    confidence=confidence,
                    sheet_ref=item.sheet_ref,
                )
            )

        ctx.alerts = alerts
        return alerts
