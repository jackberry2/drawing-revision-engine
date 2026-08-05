from __future__ import annotations

from dre import config
from dre.llm.client import call_structured, dump_models, encode_image, load_prompt
from dre.models.schemas import ConfidenceResponse, ConfidenceScore
from dre.pipeline.base import PipelineContext, PipelineStep


class ConfidenceStep(PipelineStep):
    """Explicit, inspectable confidence scoring — its own step rather than a
    number folded into the reasoning stage's prompt, so it can be audited or
    swapped independently (e.g. replaced by a calibrated model trained on
    `human_reviews` corrections later).
    """

    name = "confidence"
    version = "v1"
    model_used = config.REASONING_MODEL

    def input_for_log(self, ctx: PipelineContext) -> dict:
        return {"change_events": [e.model_dump(mode="json") for e in ctx.change_events]}

    def execute(self, ctx: PipelineContext) -> list[ConfidenceScore]:
        if not ctx.change_events:
            ctx.confidence_scores = {}
            return []

        user_content = [
            {"type": "text", "text": "Change events (JSON):\n" + dump_models(ctx.change_events)},
            {"type": "text", "text": "OLD revision of the sheet:"},
            encode_image(ctx.old_image_path),
            {"type": "text", "text": "NEW revision of the sheet:"},
            encode_image(ctx.new_image_path),
        ]
        result = call_structured(
            system=load_prompt("confidence"),
            user_content=user_content,
            response_model=ConfidenceResponse,
            model=self.model_used,
        )
        ctx.confidence_scores = {s.change_event_id: s for s in result.scores}
        return result.scores
