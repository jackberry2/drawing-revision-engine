from __future__ import annotations

from dre import config
from dre.llm.client import call_structured, encode_image, load_prompt
from dre.models.schemas import DetectResult
from dre.pipeline.base import PipelineContext, PipelineStep


class DetectStep(PipelineStep):
    """Raw visual diffing + schedule-table extraction. No trade judgment.

    Deliberately the "dumbest" stage — the one most likely to be swapped for
    a custom-trained CV model later, so it stays free of any reasoning that
    would need to be reproduced by that replacement.
    """

    name = "detect"
    version = "v1"
    model_used = config.DETECT_MODEL

    def input_for_log(self, ctx: PipelineContext) -> dict:
        return {
            "old_image_path": str(ctx.old_image_path),
            "new_image_path": str(ctx.new_image_path),
        }

    def execute(self, ctx: PipelineContext) -> DetectResult:
        user_content = [
            {"type": "text", "text": "OLD revision of the sheet:"},
            encode_image(ctx.old_image_path),
            {"type": "text", "text": "NEW revision of the sheet:"},
            encode_image(ctx.new_image_path),
        ]
        result = call_structured(
            system=load_prompt("detect"),
            user_content=user_content,
            response_model=DetectResult,
            model=self.model_used,
            usage_sink=ctx.token_usage,
        )
        ctx.detect_result = result
        return result
