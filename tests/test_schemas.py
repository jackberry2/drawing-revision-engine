from dre.models.schemas import (
    ChangeCategory,
    ChangeEvent,
    ClassifiedChange,
    ConfidenceScore,
    EntityRef,
    FinalChangeAlert,
    RawDetection,
)


def test_raw_detection_roundtrip():
    rd = RawDetection(
        id="rd_1",
        present_in="both_modified",
        geometry_description="rectangular symbol shifted left",
    )
    assert RawDetection.model_validate_json(rd.model_dump_json()) == rd


def test_classified_change_requires_materiality_reason():
    cc = ClassifiedChange(
        id="cc_1",
        raw_detection_id="rd_1",
        category=ChangeCategory.PANEL_RELOCATION,
        is_material=True,
        materiality_reason="Panel moved to a different wall.",
        trade_description="Panel LP-2 relocated from column 4 to column 7.",
        involved_entities=[EntityRef(entity_type="panel", identifier="LP-2")],
    )
    assert cc.involved_entities[0].identifier == "LP-2"


def test_change_event_bundles_change_ids():
    ce = ChangeEvent(
        id="ce_1",
        root_cause_change_id="cc_1",
        bundled_change_ids=["cc_1", "cc_2", "cc_3"],
        category=ChangeCategory.PANEL_RELOCATION,
        root_cause_summary="Panel LP-2 relocated.",
        downstream_implications=["Circuit 12 re-routes.", "Circuit 14 re-routes."],
    )
    assert len(ce.bundled_change_ids) == 3


def test_final_alert_carries_confidence_breakdown():
    conf = ConfidenceScore(
        change_event_id="ce_1",
        score=0.82,
        image_quality_factor=0.9,
        image_quality_note="Sharp scan.",
        cross_sheet_corroboration_factor=0.8,
        cross_sheet_corroboration_note="Panel schedule confirms new panel position note.",
        ambiguity_factor=0.75,
        ambiguity_factor_raw=0.75,
        ambiguity_note="Clearly redrawn in a new grid location.",
        rationale="High confidence from a sharp scan and schedule corroboration.",
    )
    alert = FinalChangeAlert(
        change_event_id="ce_1",
        category=ChangeCategory.PANEL_RELOCATION,
        headline="Panel LP-2 relocated",
        description="Panel LP-2 moved; circuits 12 and 14 re-route accordingly.",
        confidence=conf,
    )
    assert alert.confidence.score == 0.82
    assert 0.0 <= alert.confidence.image_quality_factor <= 1.0
