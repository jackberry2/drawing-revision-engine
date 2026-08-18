from __future__ import annotations

from dre import config
from dre.llm.client import call_structured, dump_models, encode_image, load_prompt
from dre.models.schemas import ChangeEvent, ClassifiedChange, ReasonResponse
from dre.pipeline.base import PipelineContext, PipelineStep
from dre.pipeline.classify import SINGLE_SHEET_NOTE
from dre.pipeline.identity_resolution import enforce_identity_unresolved, flag_cross_event_causal_risk


class ReasonSingleStep(PipelineStep):
    """Single-sheet-mode reason: bundles material flagged changes into
    ChangeEvents using `schedule_consistency` (not `schedule_corroboration`)
    and forces `identity_unresolved`/`category` through from classify rather
    than trusting the model's own propagation of them (see
    `identity_resolution.enforce_identity_unresolved`).
    """

    name = "reason_single"
    version = "v1"
    model_used = config.REASONING_MODEL

    def _material_changes(self, ctx: PipelineContext) -> list[ClassifiedChange]:
        return [c for c in ctx.classified_changes if c.is_material]

    def input_for_log(self, ctx: PipelineContext) -> dict:
        assert ctx.detect_single_result is not None
        return {
            "material_changes": [c.model_dump(mode="json") for c in self._material_changes(ctx)],
            "extracted_tables": [
                t.model_dump(mode="json") for t in ctx.detect_single_result.extracted_tables
            ],
        }

    def execute(self, ctx: PipelineContext) -> list[ChangeEvent]:
        assert ctx.detect_single_result is not None
        material = self._material_changes(ctx)
        if not material:
            ctx.change_events = []
            return []

        user_content = [
            {"type": "text", "text": "Material classified changes (JSON):\n" + dump_models(material)},
            {
                "type": "text",
                "text": "Extracted schedule tables (JSON):\n"
                + dump_models(ctx.detect_single_result.extracted_tables),
            },
            {"type": "text", "text": SINGLE_SHEET_NOTE},
            encode_image(ctx.old_image_path),
        ]
        result = call_structured(
            system=load_prompt("reason_single"),
            user_content=user_content,
            response_model=ReasonResponse,
            model=self.model_used,
        )
        change_events = enforce_identity_unresolved(result.change_events, material)
        change_events = flag_cross_event_causal_risk(change_events)
        ctx.change_events = change_events
        return change_events
