from dre.mapping import to_confidence_tier
from dre.models.schemas import SingleSheetDetectResult, SingleSheetDetection
from dre.pipeline.confidence import (
    _SINGLE_SHEET_AMBIGUITY_CEILING,
    _SINGLE_SHEET_CORROBORATION_CEILING,
    _UNRESOLVED_IDENTITY_AMBIGUITY_CAP,
    apply_mode_ceiling,
    synthesize_score,
)
from dre.models.schemas import ChangeCategory, ChangeEvent, ClassifiedChange
from dre.pipeline.identity_resolution import enforce_identity_unresolved
from dre.pipeline.runner import build_pipeline
from dre.pipeline.base import NullStepLogger


def test_single_sheet_detection_round_trips():
    result = SingleSheetDetectResult(
        detections=[
            SingleSheetDetection(
                id="det1",
                flagged_by="revision_cloud",
                geometry_description="Outlet symbol inside a revision cloud.",
            )
        ],
        extracted_tables=[],
    )
    assert SingleSheetDetectResult.model_validate_json(result.model_dump_json()) == result


def test_apply_mode_ceiling_clamps_single_sheet_factors():
    image_quality, corroboration, ambiguity = apply_mode_ceiling(
        image_quality_factor=1.0,
        cross_sheet_corroboration_factor=1.0,
        ambiguity_factor=1.0,
        mode="single_sheet",
        identity_unresolved=False,
    )
    assert image_quality == 1.0  # untouched — scan quality is real either way
    assert corroboration == _SINGLE_SHEET_CORROBORATION_CEILING
    assert ambiguity == _SINGLE_SHEET_AMBIGUITY_CEILING


def test_apply_mode_ceiling_leaves_two_image_factors_untouched():
    image_quality, corroboration, ambiguity = apply_mode_ceiling(
        image_quality_factor=0.97,
        cross_sheet_corroboration_factor=0.95,
        ambiguity_factor=0.97,
        mode="two_image",
        identity_unresolved=False,
    )
    assert (image_quality, corroboration, ambiguity) == (0.97, 0.95, 0.97)


def test_single_sheet_mode_can_never_reach_high_tier_even_at_perfect_factors():
    image_quality, corroboration, ambiguity = apply_mode_ceiling(
        image_quality_factor=1.0,
        cross_sheet_corroboration_factor=1.0,
        ambiguity_factor=1.0,
        mode="single_sheet",
        identity_unresolved=False,
    )
    score = synthesize_score(
        image_quality_factor=image_quality,
        cross_sheet_corroboration_factor=corroboration,
        ambiguity_factor=ambiguity,
    )
    assert to_confidence_tier(score) != "high"
    assert score < 0.90


def test_identity_unresolved_forces_low_ambiguity_regardless_of_mode():
    image_quality, corroboration, ambiguity = apply_mode_ceiling(
        image_quality_factor=1.0,
        cross_sheet_corroboration_factor=1.0,
        ambiguity_factor=1.0,
        mode="two_image",
        identity_unresolved=True,
    )
    assert ambiguity == _UNRESOLVED_IDENTITY_AMBIGUITY_CAP
    score = synthesize_score(
        image_quality_factor=image_quality,
        cross_sheet_corroboration_factor=corroboration,
        ambiguity_factor=ambiguity,
    )
    assert to_confidence_tier(score) == "low"


def _classified_change(id_, identity_unresolved) -> ClassifiedChange:
    return ClassifiedChange(
        id=id_,
        raw_detection_id=f"det_{id_}",
        category=ChangeCategory.DEVICE_ADDED,
        is_material=True,
        materiality_reason="flagged by markup",
        trade_description="test",
        identity_unresolved=identity_unresolved,
    )


def test_enforce_identity_unresolved_overrides_model_when_unresolved_change_bundled():
    material = [_classified_change("cc1", identity_unresolved=True)]
    event = ChangeEvent(
        id="e1",
        root_cause_change_id="cc1",
        bundled_change_ids=["cc1"],
        category=ChangeCategory.DEVICE_ADDED,
        root_cause_summary="unidentified symbol",
        identity_unresolved=False,  # model forgot to set it
    )
    [fixed] = enforce_identity_unresolved([event], material)
    assert fixed.identity_unresolved is True


def test_enforce_identity_unresolved_leaves_resolved_events_alone():
    material = [_classified_change("cc1", identity_unresolved=False)]
    event = ChangeEvent(
        id="e1",
        root_cause_change_id="cc1",
        bundled_change_ids=["cc1"],
        category=ChangeCategory.DEVICE_ADDED,
        root_cause_summary="outlet added",
        identity_unresolved=False,
    )
    [unchanged] = enforce_identity_unresolved([event], material)
    assert unchanged.identity_unresolved is False
    assert unchanged.category == ChangeCategory.DEVICE_ADDED


def test_enforce_identity_unresolved_forces_category_to_other():
    """The real E-501 bug: the model asserted `device_relocation` for an
    item whose identity was unresolved, because it misattributed a nearby
    annotation to it. A low confidence number alone doesn't fix that - the
    category itself has to stop asserting a specific claim type."""
    material = [_classified_change("cc1", identity_unresolved=True)]
    event = ChangeEvent(
        id="e1",
        root_cause_change_id="cc1",
        bundled_change_ids=["cc1"],
        category=ChangeCategory.DEVICE_RELOCATION,
        root_cause_summary="unidentified symbol, possibly relocated",
        identity_unresolved=True,
    )
    [fixed] = enforce_identity_unresolved([event], material)
    assert fixed.category == ChangeCategory.OTHER


def test_enforce_identity_unresolved_already_other_is_left_alone():
    material = [_classified_change("cc1", identity_unresolved=True)]
    event = ChangeEvent(
        id="e1",
        root_cause_change_id="cc1",
        bundled_change_ids=["cc1"],
        category=ChangeCategory.OTHER,
        root_cause_summary="unidentified symbol",
        identity_unresolved=True,
    )
    [unchanged] = enforce_identity_unresolved([event], material)
    assert unchanged.category == ChangeCategory.OTHER


def test_enforce_identity_unresolved_replaces_prose_that_asserts_a_change_type():
    """The first real E-501 bug: category was correctly forced to `other` and
    identity_unresolved to True, but root_cause_summary prose still asserted
    the item "also moved" — a mismatch between the honest structured fields
    and what a human actually reads. An append-only disclaimer isn't a
    strong enough guarantee (see the identity-noun regression test below),
    so the field gets replaced outright."""
    material = [_classified_change("cc1", identity_unresolved=True)]
    event = ChangeEvent(
        id="e1",
        root_cause_change_id="cc1",
        bundled_change_ids=["cc1"],
        category=ChangeCategory.DEVICE_RELOCATION,
        root_cause_summary="An unlabeled bar's cloud sits near O1's note, flagged as also having moved.",
        identity_unresolved=False,
    )
    [fixed] = enforce_identity_unresolved([event], material)
    assert "identity" in fixed.root_cause_summary
    assert "also having moved" not in fixed.root_cause_summary


def test_enforce_identity_unresolved_replaces_prose_that_asserts_a_specific_identity():
    """The second real E-501 bug, on the identical test image: detect_single's
    geometry_description speculated an unlabeled shape "appears to represent
    a wall or partition segment" — a specific *identity* claim, not a
    change-type claim, so the previous append-only-disclaimer fix (which only
    targeted change-type verbs) didn't catch it. That noun propagated
    unedited through classify/reason all the way to the final alert prose.
    Replacing root_cause_summary/downstream_implications outright, regardless
    of what specific wrong word the model used, is what actually guarantees
    this can't leak through to the reviewer-facing text."""
    material = [_classified_change("cc1", identity_unresolved=True)]
    event = ChangeEvent(
        id="e1",
        root_cause_change_id="cc1",
        bundled_change_ids=["cc1"],
        category=ChangeCategory.OTHER,
        root_cause_summary=(
            "A wall/partition segment near the top of the Conference Room is "
            "flagged with a revision cloud."
        ),
        downstream_implications=[
            "If this wall/partition is new or moved, it could affect circuit routing.",
        ],
        schedule_consistency="The panel schedule contains no entry for a wall or partition.",
        identity_unresolved=True,
    )
    [fixed] = enforce_identity_unresolved([event], material)
    assert "wall" not in fixed.root_cause_summary.lower()
    assert "partition" not in fixed.root_cause_summary.lower()
    assert all("wall" not in d.lower() and "partition" not in d.lower() for d in fixed.downstream_implications)
    assert fixed.schedule_consistency is None


def test_enforce_identity_unresolved_is_idempotent_on_replaced_summary():
    material = [_classified_change("cc1", identity_unresolved=True)]
    event = ChangeEvent(
        id="e1",
        root_cause_change_id="cc1",
        bundled_change_ids=["cc1"],
        category=ChangeCategory.OTHER,
        root_cause_summary="unidentified symbol flagged",
        identity_unresolved=True,
    )
    [once] = enforce_identity_unresolved([event], material)
    [twice] = enforce_identity_unresolved([once], material)
    assert once.root_cause_summary == twice.root_cause_summary
    assert once.downstream_implications == twice.downstream_implications


def test_enforce_identity_unresolved_leaves_resolved_summary_untouched():
    material = [_classified_change("cc1", identity_unresolved=False)]
    event = ChangeEvent(
        id="e1",
        root_cause_change_id="cc1",
        bundled_change_ids=["cc1"],
        category=ChangeCategory.DEVICE_ADDED,
        root_cause_summary="outlet added",
        identity_unresolved=False,
    )
    [unchanged] = enforce_identity_unresolved([event], material)
    assert unchanged.root_cause_summary == "outlet added"


def test_build_pipeline_selects_single_sheet_stages():
    pipeline = build_pipeline(logger=NullStepLogger(), mode="single_sheet")
    assert [s.name for s in pipeline.steps] == [
        "detect_single",
        "classify",
        "reason_single",
        "confidence",
        "describe",
    ]


def test_build_pipeline_selects_two_image_stages_by_default():
    pipeline = build_pipeline(logger=NullStepLogger())
    assert [s.name for s in pipeline.steps] == [
        "detect",
        "classify",
        "reason",
        "confidence",
        "describe",
    ]
