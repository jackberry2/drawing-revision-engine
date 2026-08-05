"""Exercises the orchestrator's plumbing (stage sequencing, step logging,
change-event persistence) with fake stages, so it's covered without needing
real Claude calls."""

from dre.models.schemas import (
    ChangeCategory,
    ChangeEvent,
    ClassifiedChange,
    ConfidenceScore,
    DetectResult,
    EntityRef,
    FinalChangeAlert,
    RawDetection,
)
from dre.pipeline.base import PipelineContext, PipelineStep
from dre.pipeline.orchestrator import Pipeline
from dre.storage import repository


class FakeDetect(PipelineStep):
    name = "detect"
    version = "test"

    def input_for_log(self, ctx):
        return {"prev": str(ctx.prev_image_path), "revised": str(ctx.revised_image_path)}

    def execute(self, ctx):
        result = DetectResult(
            raw_detections=[
                RawDetection(id="rd_1", present_in="both_modified", geometry_description="moved")
            ],
            extracted_tables=[],
        )
        ctx.detect_result = result
        return result


class FakeClassify(PipelineStep):
    name = "classify"
    version = "test"

    def input_for_log(self, ctx):
        return {"n": len(ctx.detect_result.raw_detections)}

    def execute(self, ctx):
        changes = [
            ClassifiedChange(
                id="cc_1",
                raw_detection_id="rd_1",
                category=ChangeCategory.PANEL_RELOCATION,
                is_material=True,
                materiality_reason="clearly relocated, not redraw noise",
                trade_description="Panel LP-2 relocated.",
                involved_entities=[EntityRef(entity_type="panel", identifier="LP-2")],
            )
        ]
        ctx.classified_changes = changes
        return changes


class FakeReason(PipelineStep):
    name = "reason"
    version = "test"

    def input_for_log(self, ctx):
        return {"n": len(ctx.classified_changes)}

    def execute(self, ctx):
        events = [
            ChangeEvent(
                id="ce_1",
                root_cause_change_id="cc_1",
                bundled_change_ids=["cc_1"],
                category=ChangeCategory.PANEL_RELOCATION,
                root_cause_summary="Panel LP-2 relocated.",
                downstream_implications=[],
                affected_entities=[EntityRef(entity_type="panel", identifier="LP-2")],
            )
        ]
        ctx.change_events = events
        return events


class FakeConfidence(PipelineStep):
    name = "confidence"
    version = "test"

    def input_for_log(self, ctx):
        return {"n": len(ctx.change_events)}

    def execute(self, ctx):
        scores = [
            ConfidenceScore(
                change_event_id="ce_1",
                score=0.75,
                image_quality_factor=0.9,
                image_quality_note="n/a",
                cross_sheet_corroboration_factor=0.5,
                cross_sheet_corroboration_note="n/a",
                ambiguity_factor=0.8,
                ambiguity_note="n/a",
                rationale="n/a",
            )
        ]
        ctx.confidence_scores = {s.change_event_id: s for s in scores}
        return scores


class FakeDescribe(PipelineStep):
    name = "describe"
    version = "test"

    def input_for_log(self, ctx):
        return {"n": len(ctx.change_events)}

    def execute(self, ctx):
        alerts = [
            FinalChangeAlert(
                change_event_id="ce_1",
                category=ChangeCategory.PANEL_RELOCATION,
                headline="Panel LP-2 relocated",
                description="Panel LP-2 relocated to a new location.",
                affected_entities=[EntityRef(entity_type="panel", identifier="LP-2")],
                confidence=ctx.confidence_scores["ce_1"],
            )
        ]
        ctx.alerts = alerts
        return alerts


def test_pipeline_end_to_end(tmp_path):
    prev = tmp_path / "prev.png"
    revised = tmp_path / "revised.png"
    prev.write_bytes(b"fake")
    revised.write_bytes(b"fake")

    run_id = repository.new_id("run")
    repository.create_run(str(prev), str(revised), run_id=run_id)

    ctx = PipelineContext(run_id=run_id, prev_image_path=prev, revised_image_path=revised)
    pipeline = Pipeline([FakeDetect(), FakeClassify(), FakeReason(), FakeConfidence(), FakeDescribe()])
    result = pipeline.run(ctx)

    assert result.run_id == run_id
    assert len(result.alerts) == 1
    assert result.alerts[0].headline == "Panel LP-2 relocated"

    stored = repository.get_change_events_for_run(run_id)
    assert len(stored) == 1
    assert stored[0].category == "panel_relocation"
