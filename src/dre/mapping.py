"""Maps the pipeline's internal, fine-grained schema onto the exact shape the
existing `flagged_changes` table expects. The internal taxonomy (ChangeCategory)
and raw 0-1 confidence score stay richer than what the production table can
hold — that richer detail is preserved in `pipeline_change_events`, not lost.
"""

from __future__ import annotations

from typing import Literal

from dre.models.schemas import ChangeCategory

ChangeType = Literal["added", "removed", "moved", "modified"]
ConfidenceTier = Literal["high", "medium", "low"]

_CATEGORY_TO_CHANGE_TYPE: dict[ChangeCategory, ChangeType] = {
    ChangeCategory.PANEL_RELOCATION: "moved",
    ChangeCategory.DEVICE_ADDED: "added",
    ChangeCategory.DEVICE_REMOVED: "removed",
    ChangeCategory.CIRCUIT_REROUTE: "modified",
    ChangeCategory.DEVICE_MODIFIED: "modified",
    ChangeCategory.CONDUIT_RUN_CHANGE: "modified",
    ChangeCategory.SCHEDULE_LABEL_EDIT: "modified",
    ChangeCategory.ANNOTATION_ONLY: "modified",
    ChangeCategory.NOISE_NON_MATERIAL: "modified",
    ChangeCategory.OTHER: "modified",
}


def to_change_type(category: ChangeCategory) -> ChangeType:
    return _CATEGORY_TO_CHANGE_TYPE[category]


def to_confidence_percentage(score: float) -> int:
    return max(0, min(100, round(score * 100)))


def to_confidence_tier(score: float) -> ConfidenceTier:
    percentage = to_confidence_percentage(score)
    if percentage >= 90:
        return "high"
    if percentage >= 70:
        return "medium"
    return "low"
