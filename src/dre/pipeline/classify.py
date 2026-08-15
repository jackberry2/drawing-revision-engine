from __future__ import annotations

from dre import config
from dre.llm.client import call_structured, dump_models, encode_image, load_prompt
from dre.models.schemas import ClassifiedChange, ClassifyResponse
from dre.pipeline.base import PipelineContext, PipelineStep

SINGLE_SHEET_NOTE = (
    "NOTE: single-sheet mode — this is one standalone sheet image, not a "
    "paired OLD/NEW comparison. There is no prior revision to compare "
    "against; the only evidence available is the drafter's own revision "
    "markup already on this sheet."
)


class ClassifyStep(PipelineStep):
    """Assigns each raw detection a trade category and a materiality verdict.

    Materiality judgment lives here, not in `detect`: an experienced
    estimator's "is this real or just scan noise" call is a trade judgment,
    not a geometric one. Shared between two_image and single_sheet mode —
    only the input shape and image(s) sent differ; the judgment itself
    (materiality, identity_unresolved) is the same kind of call either way.
    """

    name = "classify"
    version = "v1"
    model_used = config.REASONING_MODEL

    def _detections_for_log(self, ctx: PipelineContext) -> list[dict]:
        if ctx.mode == "single_sheet":
            assert ctx.detect_single_result is not None
            return [d.model_dump(mode="json") for d in ctx.detect_single_result.detections]
        assert ctx.detect_result is not None
        return [d.model_dump(mode="json") for d in ctx.detect_result.raw_detections]

    def input_for_log(self, ctx: PipelineContext) -> dict:
        return {"raw_detections": self._detections_for_log(ctx)}

    def execute(self, ctx: PipelineContext) -> list[ClassifiedChange]:
        if ctx.mode == "single_sheet":
            assert ctx.detect_single_result is not None
            detections = ctx.detect_single_result.detections
            user_content = [
                {"type": "text", "text": "Raw detections (JSON):\n" + dump_models(detections)},
                {"type": "text", "text": SINGLE_SHEET_NOTE},
                encode_image(ctx.old_image_path),
            ]
        else:
            assert ctx.detect_result is not None
            detections = ctx.detect_result.raw_detections
            assert ctx.new_image_path is not None
            user_content = [
                {"type": "text", "text": "Raw detections (JSON):\n" + dump_models(detections)},
                {"type": "text", "text": "OLD revision of the sheet:"},
                encode_image(ctx.old_image_path),
                {"type": "text", "text": "NEW revision of the sheet:"},
                encode_image(ctx.new_image_path),
            ]

        result = call_structured(
            system=load_prompt("classify"),
            user_content=user_content,
            response_model=ClassifyResponse,
            model=self.model_used,
        )
        ctx.classified_changes = result.classified_changes
        return result.classified_changes
