from __future__ import annotations

from dre import config
from dre.llm.client import call_structured, dump_models, encode_image, load_prompt
from dre.models.schemas import ClassifiedChange, ClassifyResponse
from dre.pipeline.base import PipelineContext, PipelineStep


class ClassifyStep(PipelineStep):
    """Assigns each raw detection a trade category and a materiality verdict.

    Materiality judgment lives here, not in `detect`: an experienced
    estimator's "is this real or just scan noise" call is a trade judgment,
    not a geometric one.
    """

    name = "classify"
    version = "v1"
    model_used = config.REASONING_MODEL

    def input_for_log(self, ctx: PipelineContext) -> dict:
        assert ctx.detect_result is not None
        return {"raw_detections": [d.model_dump(mode="json") for d in ctx.detect_result.raw_detections]}

    def execute(self, ctx: PipelineContext) -> list[ClassifiedChange]:
        assert ctx.detect_result is not None
        user_content = [
            {
                "type": "text",
                "text": "Raw detections (JSON):\n" + dump_models(ctx.detect_result.raw_detections),
            },
            {"type": "text", "text": "Previous version of the sheet:"},
            encode_image(ctx.prev_image_path),
            {"type": "text", "text": "Revised version of the sheet:"},
            encode_image(ctx.revised_image_path),
        ]
        result = call_structured(
            system=load_prompt("classify"),
            user_content=user_content,
            response_model=ClassifyResponse,
            model=self.model_used,
        )
        ctx.classified_changes = result.classified_changes
        return result.classified_changes
