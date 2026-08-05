import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from scorer import ExpectedAlert, ExpectedCase, score_case  # noqa: E402

from dre.models.schemas import ChangeCategory, ConfidenceScore, FinalChangeAlert  # noqa: E402


def _alert(category, headline, description, entities=None, score=0.8) -> FinalChangeAlert:
    return FinalChangeAlert(
        change_event_id=f"ce_{headline}",
        category=category,
        headline=headline,
        description=description,
        affected_entities=entities or [],
        confidence=ConfidenceScore(
            change_event_id=f"ce_{headline}",
            score=score,
            image_quality_factor=0.9,
            image_quality_note="n/a",
            cross_sheet_corroboration_factor=0.5,
            cross_sheet_corroboration_note="n/a",
            ambiguity_factor=0.9,
            ambiguity_note="n/a",
            rationale="n/a",
        ),
    )


def test_matching_alert_passes():
    alerts = [
        _alert(
            ChangeCategory.PANEL_RELOCATION,
            "Panel LP-2 relocated",
            "Panel LP-2 relocated; circuit 14 re-routes as a result.",
        )
    ]
    expected = ExpectedCase(
        alerts=[
            ExpectedAlert(
                category="panel_relocation",
                required_entities=["LP-2", "circuit 14"],
                description_keywords=["re-routes"],
                min_confidence=0.5,
            )
        ]
    )
    score = score_case("case_x", alerts, expected)
    assert score.passed
    assert not score.missed
    assert not score.hallucinated


def test_missed_alert_fails():
    expected = ExpectedCase(
        alerts=[ExpectedAlert(category="panel_relocation", required_entities=["LP-2"])]
    )
    score = score_case("case_x", [], expected)
    assert not score.passed
    assert len(score.missed) == 1


def test_hallucinated_alert_fails():
    alerts = [
        _alert(ChangeCategory.NOISE_NON_MATERIAL, "Redraw jitter", "Minor line jitter, not material.")
    ]
    expected = ExpectedCase(alerts=[])
    score = score_case("case_x", alerts, expected)
    assert not score.passed
    assert score.hallucinated == ["Redraw jitter"]


def test_low_confidence_does_not_satisfy_min_confidence():
    alerts = [
        _alert(
            ChangeCategory.CIRCUIT_REROUTE,
            "Circuit 9 reroute",
            "Circuit 9 reroute due to panel move.",
            score=0.2,
        )
    ]
    expected = ExpectedCase(
        alerts=[ExpectedAlert(category="circuit_reroute", min_confidence=0.6)]
    )
    score = score_case("case_x", alerts, expected)
    assert not score.passed
    assert len(score.missed) == 1
