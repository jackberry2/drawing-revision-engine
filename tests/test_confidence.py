from unittest.mock import patch

import pytest

from dre.mapping import to_confidence_tier
from dre.models.schemas import ChangeCategory, ChangeEvent, ConfidenceFactors
from dre.pipeline.base import PipelineContext
from dre.pipeline.confidence import ConfidenceStep, _to_confidence_score, synthesize_score

# Real factor breakdowns logged from live runs of the E-201 "new wall forces
# C3 reroute" case (see pipeline_change_events for these run_ids). Confidence
# swung 85% -> 88% -> 88% -> 55% across runs on identical source images even
# though image_quality/corroboration stayed roughly stable each time - only
# ambiguity_factor moved (0.85/0.85/0.90 -> 0.40). The fix is (a) tightening
# that factor's own consistency (temperature, prompt scoping) and (b) making
# the score a deterministic function of the three factors so the same
# factor judgments always land in the same tier.
REAL_RUNS = [
    # (image_quality, corroboration, ambiguity, observed_tier)
    (0.97, 0.70, 0.85, "medium"),
    (0.95, 0.85, 0.85, "medium"),
    (0.95, 0.75, 0.90, "medium"),
    (0.97, 0.90, 0.95, "high"),
    (0.97, 0.97, 0.97, "high"),
    (0.90, 0.50, 0.40, "low"),
]


@pytest.mark.parametrize("image_quality,corroboration,ambiguity,observed_tier", REAL_RUNS)
def test_synthesize_score_matches_observed_tier_on_real_data(
    image_quality, corroboration, ambiguity, observed_tier
):
    score = synthesize_score(
        image_quality_factor=image_quality,
        cross_sheet_corroboration_factor=corroboration,
        ambiguity_factor=ambiguity,
    )
    assert to_confidence_tier(score) == observed_tier


def test_same_factors_always_produce_the_same_score():
    kwargs = dict(image_quality_factor=0.95, cross_sheet_corroboration_factor=0.7, ambiguity_factor=0.85)
    assert synthesize_score(**kwargs) == synthesize_score(**kwargs)


def test_textbook_clear_case_scores_high_regardless_of_absent_corroboration():
    score = synthesize_score(
        image_quality_factor=0.98, cross_sheet_corroboration_factor=0.5, ambiguity_factor=0.97
    )
    assert to_confidence_tier(score) == "high"


def test_genuinely_ambiguous_case_stays_low_even_with_perfect_scan():
    score = synthesize_score(
        image_quality_factor=1.0, cross_sheet_corroboration_factor=0.5, ambiguity_factor=0.2
    )
    assert to_confidence_tier(score) == "low"


def test_conflicting_corroboration_pulls_score_down():
    high_corroboration = synthesize_score(
        image_quality_factor=0.9, cross_sheet_corroboration_factor=0.9, ambiguity_factor=0.7
    )
    conflicting_corroboration = synthesize_score(
        image_quality_factor=0.9, cross_sheet_corroboration_factor=0.1, ambiguity_factor=0.7
    )
    assert conflicting_corroboration < high_corroboration


def test_score_is_always_in_valid_range():
    for iq in (0.0, 0.5, 1.0):
        for corr in (0.0, 0.5, 1.0):
            for amb in (0.0, 0.5, 1.0):
                score = synthesize_score(
                    image_quality_factor=iq, cross_sheet_corroboration_factor=corr, ambiguity_factor=amb
                )
                assert 0.0 <= score <= 1.0


def _factors(*, ambiguity_factor: float) -> ConfidenceFactors:
    return ConfidenceFactors(
        change_event_id="ce_1",
        image_quality_factor=0.55,
        image_quality_note="n/a",
        cross_sheet_corroboration_factor=0.5,
        cross_sheet_corroboration_note="n/a",
        ambiguity_factor=ambiguity_factor,
        ambiguity_note="n/a",
        rationale="n/a",
    )


def test_ambiguity_factor_raw_preserves_models_pre_cap_value():
    """The real E-101.2 case this exists for: three identity_unresolved
    items all scored an identical 40%, and there was no way to tell whether
    the model had genuinely converged on the same ambiguity judgment three
    times or whether the identity_unresolved cap had overwritten three
    different raw values down to the same capped one. ambiguity_factor_raw
    must carry the model's real, uncapped number through even when the
    capped ambiguity_factor used for scoring is forced down."""
    factors = _factors(ambiguity_factor=0.9)
    result = _to_confidence_score(factors, mode="two_image", identity_unresolved=True)

    assert result.ambiguity_factor == 0.3  # capped, used for scoring
    assert result.ambiguity_factor_raw == 0.9  # the model's real, uncapped judgment


def test_ambiguity_factor_raw_matches_capped_value_when_no_cap_applies():
    factors = _factors(ambiguity_factor=0.6)
    result = _to_confidence_score(factors, mode="two_image", identity_unresolved=False)

    assert result.ambiguity_factor == 0.6
    assert result.ambiguity_factor_raw == 0.6


def test_ambiguity_factor_raw_preserved_under_single_sheet_ceiling_too():
    factors = _factors(ambiguity_factor=0.95)
    result = _to_confidence_score(factors, mode="single_sheet", identity_unresolved=False)

    assert result.ambiguity_factor == 0.85  # single-sheet ceiling
    assert result.ambiguity_factor_raw == 0.95


# ---- cross_event_causal_risk ceiling (docs/pipeline_notes.md, "reason can
# fabricate a confident causal claim while the real cause sits orphaned
# nearby, unlinked" - the real E-201 case) --------------------------------


def test_cross_event_causal_risk_caps_ambiguity_below_the_textbook_clear_band():
    """E-201's real bad evt1 scored 0.8525 - squarely in the textbook-clear
    band - specifically because its ambiguity_factor was reported very
    high (0.9). The cap must keep that from happening again for a flagged
    event, regardless of how clean the rest of its evidence looks."""
    factors = _factors(ambiguity_factor=0.9)
    result = _to_confidence_score(
        factors, mode="two_image", identity_unresolved=False, cross_event_causal_risk=True
    )

    assert result.ambiguity_factor == 0.75  # capped
    assert result.ambiguity_factor_raw == 0.9  # model's real judgment preserved
    assert result.cross_event_causal_risk_flagged is True
    from dre.mapping import to_confidence_tier

    assert to_confidence_tier(result.score) != "high"


def test_cross_event_causal_risk_does_not_lower_an_already_low_ambiguity():
    factors = _factors(ambiguity_factor=0.4)
    result = _to_confidence_score(
        factors, mode="two_image", identity_unresolved=False, cross_event_causal_risk=True
    )

    assert result.ambiguity_factor == 0.4  # min() with 0.75 - unaffected


def test_identity_unresolved_cap_takes_precedence_over_cross_event_risk():
    """An event can't be both identity_unresolved and cross_event_causal_risk
    in practice (has_cross_event_causal_risk excludes unresolved events),
    but if both flags were ever passed together, the stricter, more
    certain identity_unresolved cap must win."""
    factors = _factors(ambiguity_factor=0.9)
    result = _to_confidence_score(
        factors, mode="two_image", identity_unresolved=True, cross_event_causal_risk=True
    )

    assert result.ambiguity_factor == 0.3  # identity_unresolved cap, not 0.75


def test_cross_event_causal_risk_flag_defaults_to_false_and_is_a_no_op():
    factors = _factors(ambiguity_factor=0.9)
    result = _to_confidence_score(factors, mode="two_image", identity_unresolved=False)

    assert result.cross_event_causal_risk_flagged is False
    assert result.ambiguity_factor == 0.9  # unaffected


def test_confidence_step_wires_cross_event_causal_risk_from_ctx_change_events(tmp_path):
    """Integration-level check on the actual wiring in ConfidenceStep.execute
    (not just the pure functions it calls) - the real E-201 shape: a
    panel_relocation event alongside a separate identity_unresolved event
    in the same ctx.change_events."""
    old = tmp_path / "old.png"
    new = tmp_path / "new.png"
    png_magic = b"\x89PNG\r\n\x1a\n"
    old.write_bytes(png_magic)
    new.write_bytes(png_magic)

    flagged_event = ChangeEvent(
        id="evt1",
        root_cause_change_id="c1",
        bundled_change_ids=["c1", "c4"],
        category=ChangeCategory.CIRCUIT_REROUTE,
        root_cause_summary="Panel relocated, forcing a reroute.",
        identity_unresolved=False,
    )
    unresolved_event = ChangeEvent(
        id="evt2",
        root_cause_change_id="c5",
        bundled_change_ids=["c5"],
        category=ChangeCategory.OTHER,
        root_cause_summary="An item on this sheet is flagged but unconfirmed.",
        identity_unresolved=True,
    )
    ctx = PipelineContext(run_id="run_test", old_image_path=old, new_image_path=new)
    ctx.change_events = [flagged_event, unresolved_event]

    def fake_call_structured(*, system, user_content, response_model, model, usage_sink=None):
        change_event_id = ctx.change_events[len(scored_so_far)].id
        scored_so_far.append(change_event_id)
        return ConfidenceFactors(
            change_event_id=change_event_id,
            image_quality_factor=0.95,
            image_quality_note="n/a",
            cross_sheet_corroboration_factor=0.7,
            cross_sheet_corroboration_note="n/a",
            ambiguity_factor=0.9,
            ambiguity_note="n/a",
            rationale="n/a",
        )

    scored_so_far: list[str] = []
    with patch("dre.pipeline.confidence.call_structured", side_effect=fake_call_structured):
        scores = ConfidenceStep().execute(ctx)

    by_id = {s.change_event_id: s for s in scores}
    assert by_id["evt1"].cross_event_causal_risk_flagged is True
    assert by_id["evt1"].ambiguity_factor == 0.75  # capped
    assert by_id["evt2"].cross_event_causal_risk_flagged is False  # the unresolved event itself
