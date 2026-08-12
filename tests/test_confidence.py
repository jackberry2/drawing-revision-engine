import pytest

from dre.mapping import to_confidence_tier
from dre.pipeline.confidence import synthesize_score

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
