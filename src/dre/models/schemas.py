"""Pydantic contracts passed between pipeline stages.

Every stage (`pipeline/*.py`) must consume and produce only these types,
regardless of what implementation backs the stage (prompted LLM today, a
custom-trained model later). Keeping the contracts here — separate from any
stage's implementation — is what makes a stage swappable without touching
its neighbors.

Sheet revisions are always "old" (the prior version) and "new" (the revised
version), explicitly labeled end to end — matching how the caller identifies
them (drawings.old_drawing_id / new_drawing_id) — and never inferred from
file order or content.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ChangeCategory(str, Enum):
    PANEL_RELOCATION = "panel_relocation"
    DEVICE_RELOCATION = "device_relocation"  # a device/equipment (not a panel) physically moved
    CIRCUIT_REROUTE = "circuit_reroute"  # includes conduit run/path changes
    DEVICE_ADDED = "device_added"
    DEVICE_REMOVED = "device_removed"
    DEVICE_MODIFIED = "device_modified"
    SCHEDULE_LABEL_EDIT = "schedule_label_edit"
    ANNOTATION_ONLY = "annotation_only"
    NOISE_NON_MATERIAL = "noise_non_material"
    OTHER = "other"


class BBox(BaseModel):
    """Normalized (0-1) region, resolution-independent across scan qualities."""

    x: float
    y: float
    width: float
    height: float


class EntityRef(BaseModel):
    entity_type: Literal["panel", "circuit", "device", "conduit", "other"]
    identifier: str


# ---- Stage 1: detect ----------------------------------------------------


class RawDetection(BaseModel):
    id: str
    sheet_ref: Optional[str] = None
    present_in: Literal["old_only", "new_only", "both_modified"]
    region_old: Optional[BBox] = None
    region_new: Optional[BBox] = None
    geometry_description: str = Field(
        ..., description="Terse geometric description, no trade judgment yet."
    )


class ExtractedTable(BaseModel):
    id: str
    table_type: Literal["panel_schedule", "device_schedule", "legend", "other"]
    sheet_version: Literal["old", "new"]
    title: Optional[str] = None
    region: Optional[BBox] = None
    rows: list[dict[str, str]] = Field(default_factory=list)


class DetectResult(BaseModel):
    raw_detections: list[RawDetection]
    extracted_tables: list[ExtractedTable]


# ---- Stage 1 (single-sheet mode): detect_single --------------------------
#
# A single-sheet analysis has no prior revision to diff against, only the
# drafter's own markup on the one sheet available. `present_in` has no valid
# answer here (it describes a two-image comparison outcome); `flagged_by`
# describes how the drafter indicated this is a change instead. See
# docs/single_sheet_mode_findings.md.


class SingleSheetDetection(BaseModel):
    id: str
    sheet_ref: Optional[str] = None
    flagged_by: Literal["revision_cloud", "revision_tag", "annotation_note", "unmarked"]
    region: Optional[BBox] = None
    geometry_description: str = Field(
        ..., description="Terse geometric description, no trade judgment yet."
    )


class SingleSheetDetectResult(BaseModel):
    detections: list[SingleSheetDetection]
    extracted_tables: list[ExtractedTable]


# ---- Stage 2: classify ---------------------------------------------------


class ClassifiedChange(BaseModel):
    id: str
    raw_detection_id: str
    category: ChangeCategory
    is_material: bool
    materiality_reason: str = Field(
        ..., description="Why an estimator would/wouldn't flag this."
    )
    trade_description: str = Field(
        ..., description="Plain trade-language description of this single change."
    )
    involved_entities: list[EntityRef] = Field(default_factory=list)
    identity_unresolved: bool = Field(
        default=False,
        description=(
            "True when this item is flagged as a change but its identity/purpose "
            "can't be confirmed against a legend, schedule, or legible label — "
            "e.g. an unlabeled symbol with no matching table row. Not the same as "
            "materiality: an unresolved item can still be material."
        ),
    )


class ClassifyResponse(BaseModel):
    classified_changes: list[ClassifiedChange]


# ---- Stage 3: reason / bundle --------------------------------------------


class ChangeEvent(BaseModel):
    id: str
    root_cause_change_id: str
    bundled_change_ids: list[str] = Field(
        ..., description="Includes the root cause id plus every downstream change id."
    )
    category: ChangeCategory
    root_cause_summary: str
    downstream_implications: list[str] = Field(default_factory=list)
    affected_entities: list[EntityRef] = Field(default_factory=list)
    schedule_corroboration: Optional[str] = Field(
        None,
        description=(
            "Two-image mode only: what, if anything, comparing the old/new schedule "
            "tables confirmed about this change (a real before/after transition)."
        ),
    )
    schedule_consistency: Optional[str] = Field(
        None,
        description=(
            "Single-sheet mode only: whether the current (single) schedule snapshot "
            "is consistent with what the markup claims changed — weaker than "
            "schedule_corroboration, since there's no before/after to compare."
        ),
    )
    identity_unresolved: bool = Field(
        default=False,
        description=(
            "True when this event's root cause is a flagged item whose identity/"
            "purpose couldn't be confirmed (see ClassifiedChange.identity_unresolved). "
            "Forces confidence scoring to treat it as genuinely low-confidence rather "
            "than trusting the model to self-report that consistently."
        ),
    )
    sheet_ref: Optional[str] = None


class ReasonResponse(BaseModel):
    change_events: list[ChangeEvent]


# ---- Stage 4: confidence ---------------------------------------------------


class ConfidenceFactors(BaseModel):
    """What the LLM itself assesses. Deliberately excludes the overall
    `score` — that's synthesized deterministically in code from these three
    factors (see `pipeline/confidence.py`), not computed by the model, so
    the same three judgments always produce the same final score regardless
    of sampling variance in how the model would narrate the synthesis."""

    change_event_id: str
    image_quality_factor: float = Field(..., ge=0.0, le=1.0)
    image_quality_note: str
    cross_sheet_corroboration_factor: float = Field(..., ge=0.0, le=1.0)
    cross_sheet_corroboration_note: str
    ambiguity_factor: float = Field(..., ge=0.0, le=1.0)
    ambiguity_note: str
    rationale: str = Field(..., description="Human-readable summary of the three factor assessments.")


class ConfidenceScore(BaseModel):
    """The final, usable confidence — ConfidenceFactors plus the
    deterministically-computed `score`."""

    change_event_id: str
    score: float = Field(..., ge=0.0, le=1.0)
    image_quality_factor: float = Field(..., ge=0.0, le=1.0)
    image_quality_note: str
    cross_sheet_corroboration_factor: float = Field(..., ge=0.0, le=1.0)
    cross_sheet_corroboration_note: str
    ambiguity_factor: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "The value actually used in scoring, after apply_mode_ceiling's "
            "clamps (single_sheet ceiling, identity_unresolved cap). See "
            "ambiguity_factor_raw for what the model itself assessed."
        ),
    )
    ambiguity_factor_raw: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "The model's own ambiguity_factor before any code-level cap is "
            "applied — kept for debugging only, never used in scoring. A "
            "real case (E-101.2) needed this: three identity_unresolved "
            "items all scored an identical 40%, and without this field "
            "there was no way to tell whether the model had genuinely "
            "converged on the same judgment three times or whether the "
            "identity_unresolved cap had simply overwritten three "
            "different raw values down to the same capped one."
        ),
    )
    ambiguity_note: str
    rationale: str = Field(..., description="Human-readable summary of the three factor assessments.")


# ---- Stage 5: describe -----------------------------------------------------


class FinalChangeAlert(BaseModel):
    change_event_id: str
    category: ChangeCategory
    headline: str
    description: str = Field(
        ..., description="Root-cause change in plain trade language. No coordinates/geometry."
    )
    impact_note: Optional[str] = Field(
        None,
        description=(
            "Downstream consequences (derived from ChangeEvent.downstream_implications "
            "and schedule_corroboration), kept separate from `description`."
        ),
    )
    affected_entities: list[EntityRef] = Field(default_factory=list)
    confidence: ConfidenceScore
    sheet_ref: Optional[str] = None


class DescribeItem(BaseModel):
    """What the describe stage's LLM call produces. Confidence is computed by
    the (separate, earlier) confidence stage and merged in afterward, and
    impact_note is derived programmatically from the ChangeEvent rather than
    re-authored by the LLM — this step only writes the root-cause headline
    and description."""

    change_event_id: str
    category: ChangeCategory
    headline: str
    description: str = Field(
        ..., description="Root-cause change in plain trade language. No coordinates/geometry."
    )
    affected_entities: list[EntityRef] = Field(default_factory=list)
    sheet_ref: Optional[str] = None


class DescribeResponse(BaseModel):
    items: list[DescribeItem]


class PipelineRunResult(BaseModel):
    run_id: str
    mode: Literal["two_image", "single_sheet"] = "two_image"
    detect_result: Optional[DetectResult] = None
    detect_single_result: Optional[SingleSheetDetectResult] = None
    classified_changes: list[ClassifiedChange]
    change_events: list[ChangeEvent]
    alerts: list[FinalChangeAlert]
