from __future__ import annotations

from dre import config
from dre.llm.client import call_structured, encode_image, load_prompt
from dre.models.schemas import SingleSheetDetectResult
from dre.pipeline.base import PipelineContext, PipelineStep


class DetectSingleStep(PipelineStep):
    """Single-sheet-mode detect: finds the drafter's own revision markup
    (clouds, tags, annotation notes) on one standalone sheet — there is no
    prior revision to diff against. See docs/single_sheet_mode_findings.md
    for why this is a structurally different, lower-ceiling task than
    two-image `DetectStep`, not a smaller version of the same one.
    """

    name = "detect_single"
    version = "v1"
    model_used = config.DETECT_MODEL

    def input_for_log(self, ctx: PipelineContext) -> dict:
        return {"old_image_path": str(ctx.old_image_path)}

    def execute(self, ctx: PipelineContext) -> SingleSheetDetectResult:
        user_content = [
            {"type": "text", "text": "Sheet image:"},
            encode_image(ctx.old_image_path),
        ]
        result = call_structured(
            system=load_prompt("detect_single"),
            user_content=user_content,
            response_model=SingleSheetDetectResult,
            model=self.model_used,
        )
        ctx.detect_single_result = result
        return result
