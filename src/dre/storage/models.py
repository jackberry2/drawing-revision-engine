"""SQLAlchemy ORM models.

`pipeline_steps` is deliberately generic (step_name + input_json + output_json)
rather than one table per stage, so that swapping a stage's implementation
later (e.g. a custom-trained CV model replacing the prompted `detect` stage)
keeps landing in the same log shape. `human_reviews` is the correction-capture
table that becomes the fine-tuning dataset once there's enough of it.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    sheet_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    prev_image_path: Mapped[str] = mapped_column(String)
    revised_image_path: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")

    steps: Mapped[list["PipelineStepLog"]] = relationship(back_populates="run")
    change_events: Mapped[list["ChangeEventRecord"]] = relationship(back_populates="run")


class PipelineStepLog(Base):
    __tablename__ = "pipeline_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    step_name: Mapped[str] = mapped_column(String)
    step_order: Mapped[int] = mapped_column(Integer)
    model_used: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    input_json: Mapped[str] = mapped_column(Text)
    output_json: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    run: Mapped[Run] = relationship(back_populates="steps")


class ChangeEventRecord(Base):
    __tablename__ = "change_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    category: Mapped[str] = mapped_column(String)
    root_cause_description: Mapped[str] = mapped_column(Text)
    bundled_change_ids_json: Mapped[str] = mapped_column(Text)
    downstream_implications_json: Mapped[str] = mapped_column(Text)
    confidence_score: Mapped[float] = mapped_column(Float)
    confidence_rationale_json: Mapped[str] = mapped_column(Text)
    final_description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    run: Mapped[Run] = relationship(back_populates="change_events")
    human_reviews: Mapped[list["HumanReview"]] = relationship(back_populates="change_event")


class HumanReview(Base):
    """Human-in-the-loop correction capture — the future fine-tuning dataset."""

    __tablename__ = "human_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    change_event_id: Mapped[str] = mapped_column(ForeignKey("change_events.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    reviewer: Mapped[str] = mapped_column(String)
    verdict: Mapped[str] = mapped_column(String)  # confirmed | corrected | false_positive
    corrected_category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    corrected_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    corrected_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    change_event: Mapped[ChangeEventRecord] = relationship(back_populates="human_reviews")
