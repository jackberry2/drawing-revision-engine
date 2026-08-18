"""Exercises the orchestrator's plumbing (stage sequencing, step logging via
a fake StepLogger) with fake stages, so it's covered without needing real
Claude calls or a live Supabase project."""

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


class FakeDetect(PipelineStep):
    name = "detect"
    version = "test"

    def input_for_log(self, ctx):
        return {"old": str(ctx.old_image_path), "new": str(ctx.new_image_path)}

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
                downstream_implications=["Circuit 14 re-routes."],
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
                ambiguity_factor_raw=0.8,
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
                impact_note="Circuit 14 re-routes.",
                affected_entities=[EntityRef(entity_type="panel", identifier="LP-2")],
                confidence=ctx.confidence_scores["ce_1"],
            )
        ]
        ctx.alerts = alerts
        return alerts


class RecordingStepLogger:
    def __init__(self):
        self.calls: list[dict] = []

    def log_step(self, **kwargs):
        self.calls.append(kwargs)


def test_pipeline_end_to_end(tmp_path):
    old = tmp_path / "old.png"
    new = tmp_path / "new.png"
    old.write_bytes(b"fake")
    new.write_bytes(b"fake")

    logger = RecordingStepLogger()
    ctx = PipelineContext(run_id="run_test", old_image_path=old, new_image_path=new)
    pipeline = Pipeline([FakeDetect(), FakeClassify(), FakeReason(), FakeConfidence(), FakeDescribe()], logger)
    result = pipeline.run(ctx)

    assert result.run_id == "run_test"
    assert len(result.alerts) == 1
    assert result.alerts[0].headline == "Panel LP-2 relocated"
    assert result.alerts[0].impact_note == "Circuit 14 re-routes."

    assert [c["step_name"] for c in logger.calls] == [
        "detect",
        "classify",
        "reason",
        "confidence",
        "describe",
    ]
    assert all(c["run_id"] == "run_test" for c in logger.calls)


def test_on_after_detect_hook_fires_once_after_detect_and_before_classify(tmp_path):
    """docs/tiled_analysis_findings.md's production tiling branch hangs off
    this hook: it needs to run exactly once, after detect/detect_single has
    populated ctx.detect_result (so there's something to check the §3a
    trigger rule against) but before classify reads it (so a replacement
    result actually reaches the rest of the pipeline)."""
    old = tmp_path / "old.png"
    new = tmp_path / "new.png"
    old.write_bytes(b"fake")
    new.write_bytes(b"fake")

    calls: list[str] = []

    def on_after_detect(ctx):
        calls.append("hook")
        assert ctx.detect_result is not None
        assert ctx.classified_changes == []

    logger = RecordingStepLogger()
    ctx = PipelineContext(run_id="run_test", old_image_path=old, new_image_path=new)
    pipeline = Pipeline([FakeDetect(), FakeClassify(), FakeReason(), FakeConfidence(), FakeDescribe()], logger)
    pipeline.run(ctx, on_after_detect=on_after_detect)

    assert calls == ["hook"]


def test_omitting_on_after_detect_hook_is_unaffected(tmp_path):
    """Every existing caller (eval harness, every other test) doesn't pass
    this hook at all — must stay a pure no-op default, not a behavior
    change for anyone who doesn't opt in."""
    old = tmp_path / "old.png"
    new = tmp_path / "new.png"
    old.write_bytes(b"fake")
    new.write_bytes(b"fake")

    logger = RecordingStepLogger()
    ctx = PipelineContext(run_id="run_test", old_image_path=old, new_image_path=new)
    pipeline = Pipeline([FakeDetect(), FakeClassify(), FakeReason(), FakeConfidence(), FakeDescribe()], logger)
    result = pipeline.run(ctx)

    assert len(result.alerts) == 1
