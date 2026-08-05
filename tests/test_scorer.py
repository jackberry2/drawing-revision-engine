import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from scorer import ExpectedAlert, ExpectedCase, MappedAlert, score_case  # noqa: E402


def _alert(change_type, description, impact_note=None, tier="high", entities=None) -> MappedAlert:
    return MappedAlert(
        change_type=change_type,
        description=description,
        impact_note=impact_note,
        confidence_tier=tier,
        confidence_percentage={"high": 95, "medium": 80, "low": 55}[tier],
        entity_identifiers=entities or [],
    )


def test_matching_alert_passes():
    alerts = [
        _alert(
            "moved",
            "Panel LP-2 relocated.",
            impact_note="Circuit 14 re-routes as a result.",
            entities=["LP-2"],
        )
    ]
    expected = ExpectedCase(
        alerts=[
            ExpectedAlert(
                change_type="moved",
                required_entities=["LP-2", "circuit 14"],
                description_keywords=["re-routes"],
                confidence_tier="high",
            )
        ]
    )
    score = score_case("case_x", alerts, expected)
    assert score.passed
    assert not score.missed
    assert not score.hallucinated


def test_missed_alert_fails():
    expected = ExpectedCase(
        alerts=[ExpectedAlert(change_type="moved", required_entities=["LP-2"])]
    )
    score = score_case("case_x", [], expected)
    assert not score.passed
    assert len(score.missed) == 1


def test_hallucinated_alert_fails():
    alerts = [_alert("modified", "Minor line jitter, not material.")]
    expected = ExpectedCase(alerts=[])
    score = score_case("case_x", alerts, expected)
    assert not score.passed
    assert score.hallucinated == ["Minor line jitter, not material."]


def test_wrong_confidence_tier_fails():
    alerts = [_alert("modified", "Circuit 9 reroute due to panel move.", tier="low")]
    expected = ExpectedCase(alerts=[ExpectedAlert(change_type="modified", confidence_tier="high")])
    score = score_case("case_x", alerts, expected)
    assert not score.passed
    assert len(score.missed) == 1


def test_wrong_change_type_fails():
    alerts = [_alert("added", "New outlet O5 added.")]
    expected = ExpectedCase(alerts=[ExpectedAlert(change_type="removed")])
    score = score_case("case_x", alerts, expected)
    assert not score.passed
    assert len(score.missed) == 1
    assert len(score.hallucinated) == 1
