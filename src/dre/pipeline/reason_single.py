from __future__ import annotations

from dre import config
from dre.llm.client import call_structured, dump_models, encode_image, load_prompt
from dre.models.schemas import ChangeEvent, ClassifiedChange, ReasonResponse
from dre.pipeline.base import PipelineContext, PipelineStep
from dre.pipeline.classify import SINGLE_SHEET_NOTE


def _force_identity_unresolved(
    change_events: list[ChangeEvent], material: list[ClassifiedChange]
) -> list[ChangeEvent]:
    """Don't trust the model to consistently propagate
    ClassifiedChange.identity_unresolved into ChangeEvent.identity_unresolved
    on its own — compute it from data we already have. Same principle as the
    confidence-stage fix: enforce in code what can be computed, rather than
    hoping the model self-reports it the same way every time."""
    material_by_id = {c.id: c for c in material}
    result = []
    for event in change_events:
        bundled = [material_by_id[cid] for cid in event.bundled_change_ids if cid in material_by_id]
        if any(c.identity_unresolved for c in bundled) and not event.identity_unresolved:
            event = event.model_copy(update={"identity_unresolved": True})
        result.append(event)
    return result


class ReasonSingleStep(PipelineStep):
    """Single-sheet-mode reason: bundles material flagged changes into
    ChangeEvents using `schedule_consistency` (not `schedule_corroboration`)
    and forces `identity_unresolved` through from classify rather than
    trusting the model's own propagation of it.
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
        change_events = _force_identity_unresolved(result.change_events, material)
        ctx.change_events = change_events
        return change_events
