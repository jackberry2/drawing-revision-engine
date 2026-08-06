import pytest

from dre.mapping import to_change_type, to_confidence_percentage, to_confidence_tier
from dre.models.schemas import ChangeCategory


@pytest.mark.parametrize(
    "category,expected",
    [
        (ChangeCategory.PANEL_RELOCATION, "moved"),
        (ChangeCategory.DEVICE_RELOCATION, "moved"),
        (ChangeCategory.DEVICE_ADDED, "added"),
        (ChangeCategory.DEVICE_REMOVED, "removed"),
        (ChangeCategory.CIRCUIT_REROUTE, "modified"),
        (ChangeCategory.DEVICE_MODIFIED, "modified"),
        (ChangeCategory.SCHEDULE_LABEL_EDIT, "modified"),
        (ChangeCategory.ANNOTATION_ONLY, "modified"),
        (ChangeCategory.OTHER, "modified"),
    ],
)
def test_to_change_type(category, expected):
    assert to_change_type(category) == expected


def test_to_confidence_percentage_rounds_and_clamps():
    assert to_confidence_percentage(0.973) == 97
    assert to_confidence_percentage(0.85) == 85
    assert to_confidence_percentage(1.0) == 100
    assert to_confidence_percentage(0.0) == 0


@pytest.mark.parametrize(
    "score,expected_tier",
    [
        (0.97, "high"),
        (0.95, "high"),
        (0.92, "high"),
        (0.90, "high"),
        (0.85, "medium"),
        (0.70, "medium"),
        (0.69, "low"),
        (0.55, "low"),
    ],
)
def test_to_confidence_tier_matches_locked_bands(score, expected_tier):
    # Matches the user's own example data: 85->medium, 92/95/97->high, 55->low.
    assert to_confidence_tier(score) == expected_tier
