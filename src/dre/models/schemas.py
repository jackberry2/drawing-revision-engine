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
    CIRCUIT_REROUTE = "circuit_reroute"
    DEVICE_ADDED = "device_added"
    DEVICE_REMOVED = "device_removed"
    DEVICE_MODIFIED = "device_modified"
    CONDUIT_RUN_CHANGE = "conduit_run_change"
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
        None, description="What, if anything, an extracted schedule table confirmed."
    )
    sheet_ref: Optional[str] = None


class ReasonResponse(BaseModel):
    change_events: list[ChangeEvent]


# ---- Stage 4: confidence ---------------------------------------------------


class ConfidenceScore(BaseModel):
    change_event_id: str
    score: float = Field(..., ge=0.0, le=1.0)
    image_quality_factor: float = Field(..., ge=0.0, le=1.0)
    image_quality_note: str
    cross_sheet_corroboration_factor: float = Field(..., ge=0.0, le=1.0)
    cross_sheet_corroboration_note: str
    ambiguity_factor: float = Field(..., ge=0.0, le=1.0)
    ambiguity_note: str
    rationale: str = Field(..., description="Human-readable synthesis of the three factors.")


class ConfidenceResponse(BaseModel):
    scores: list[ConfidenceScore]


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
    detect_result: DetectResult
    classified_changes: list[ClassifiedChange]
    change_events: list[ChangeEvent]
    alerts: list[FinalChangeAlert]
