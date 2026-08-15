from __future__ import annotations

from dre import config
from dre.llm.client import call_structured, encode_image, load_prompt
from dre.models.schemas import ChangeEvent, ConfidenceFactors, ConfidenceScore
from dre.pipeline.base import PipelineContext, PipelineStep
from dre.pipeline.classify import SINGLE_SHEET_NOTE

# Note: temperature is NOT used as a consistency lever here. claude-sonnet-5
# (the current model) rejects the parameter outright (400 "temperature is
# deprecated for this model") - confirmed against the real API, not assumed.
# The two levers actually available are (a) this deterministic synthesis, so
# the same three factor judgments always produce the same score, and (b) the
# tighter scoping in confidence.md's ambiguity_factor guidance, which is
# where the real variance traced back to (see tests/test_confidence.py).

# Ambiguity below this is treated as genuinely inconclusive: the score stays
# capped low regardless of scan quality.
_LOW_AMBIGUITY_CUTOFF = 0.5
# Ambiguity/quality/corroboration all at or above these means "textbook
# clear" — deliberately set above the bare 0.9 the prompt asks the model to
# reach for, so a borderline 0.90 assessment doesn't tip into the highest
# band on its own.
_CLEAR_AMBIGUITY_CUTOFF = 0.93
_CLEAR_IMAGE_QUALITY_CUTOFF = 0.9
_NEUTRAL_CORROBORATION = 0.5

# Single-sheet mode structural ceiling (see docs/single_sheet_mode_findings.md
# #2): the underlying evidence is inherently weaker than a verified pixel
# difference — trusting the drafter's markup being complete and accurate,
# not an independently-detected change. Clamped in code, not left to the
# model to self-report lower factors, for the same reason the confidence
# math itself moved out of the model's hands: a guarantee that has to hold
# shouldn't depend on the model consistently choosing to honor it.
_SINGLE_SHEET_AMBIGUITY_CEILING = 0.85
_SINGLE_SHEET_CORROBORATION_CEILING = 0.7
# identity_unresolved forces ambiguity below the low-cutoff outright — an
# item whose identity can't even be pinned down is never "textbook clear",
# regardless of how the model itself scored it.
_UNRESOLVED_IDENTITY_AMBIGUITY_CAP = 0.3


def synthesize_score(
    *, image_quality_factor: float, cross_sheet_corroboration_factor: float, ambiguity_factor: float
) -> float:
    """Deterministic synthesis of the three model-assessed factors into a
    final confidence score. Moved out of the model's own math entirely: the
    same three factor judgments always produce the same score, so any
    remaining run-to-run variance comes only from the model's *factor*
    judgments (already tightened via low temperature and tighter prompt
    scoping) rather than also compounding with variance in how it narrates
    the synthesis step.

    Rules mirror what used to be prose guidance in confidence.md:
    - Genuinely ambiguous visual evidence (ambiguity < 0.5) stays capped low
      no matter how good the scan is.
    - Textbook-clear evidence (high ambiguity + high image quality, and
      corroboration that isn't actively conflicting) scores confidently high
      regardless of exactly how strong corroboration is - absent/neutral
      corroboration doesn't need to hold a clear case back.
    - Otherwise, a weighted blend with ambiguity dominant; corroboration
      only pulls the score down when it's actually below neutral
      (conflicting), never merely absent.

    Callers apply mode-specific clamps to the inputs (see
    `_apply_mode_ceiling`) before calling this — this function itself has no
    notion of "mode", only of the three factors it's given.
    """
    if ambiguity_factor < _LOW_AMBIGUITY_CUTOFF:
        score = 0.25 + 0.5 * ambiguity_factor
    elif (
        ambiguity_factor >= _CLEAR_AMBIGUITY_CUTOFF
        and image_quality_factor >= _CLEAR_IMAGE_QUALITY_CUTOFF
        and cross_sheet_corroboration_factor >= _NEUTRAL_CORROBORATION
    ):
        score = (
            0.90
            + 0.5 * (ambiguity_factor - 0.9)
            + 0.3 * (image_quality_factor - 0.9)
        )
    else:
        effective_corroboration = min(cross_sheet_corroboration_factor, _NEUTRAL_CORROBORATION)
        score = (
            0.60 * ambiguity_factor
            + 0.25 * image_quality_factor
            + 0.15 * effective_corroboration
        )

    return round(min(max(score, 0.0), 1.0), 4)


def apply_mode_ceiling(
    *,
    image_quality_factor: float,
    cross_sheet_corroboration_factor: float,
    ambiguity_factor: float,
    mode: str,
    identity_unresolved: bool,
) -> tuple[float, float, float]:
    """Clamps the model-reported factors before synthesis. single_sheet mode
    can never reach the two-image ceiling regardless of how clean the
    markup is; identity_unresolved items can never be "textbook clear"
    regardless of mode. image_quality_factor is untouched — scan quality is
    a real, independently-assessable property either way."""
    if mode == "single_sheet":
        ambiguity_factor = min(ambiguity_factor, _SINGLE_SHEET_AMBIGUITY_CEILING)
        cross_sheet_corroboration_factor = min(
            cross_sheet_corroboration_factor, _SINGLE_SHEET_CORROBORATION_CEILING
        )
    if identity_unresolved:
        ambiguity_factor = min(ambiguity_factor, _UNRESOLVED_IDENTITY_AMBIGUITY_CAP)
    return image_quality_factor, cross_sheet_corroboration_factor, ambiguity_factor


def _to_confidence_score(
    factors: ConfidenceFactors, *, mode: str, identity_unresolved: bool
) -> ConfidenceScore:
    image_quality_factor, cross_sheet_corroboration_factor, ambiguity_factor = apply_mode_ceiling(
        image_quality_factor=factors.image_quality_factor,
        cross_sheet_corroboration_factor=factors.cross_sheet_corroboration_factor,
        ambiguity_factor=factors.ambiguity_factor,
        mode=mode,
        identity_unresolved=identity_unresolved,
    )
    score = synthesize_score(
        image_quality_factor=image_quality_factor,
        cross_sheet_corroboration_factor=cross_sheet_corroboration_factor,
        ambiguity_factor=ambiguity_factor,
    )
    return ConfidenceScore(
        change_event_id=factors.change_event_id,
        score=score,
        image_quality_factor=image_quality_factor,
        image_quality_note=factors.image_quality_note,
        cross_sheet_corroboration_factor=cross_sheet_corroboration_factor,
        cross_sheet_corroboration_note=factors.cross_sheet_corroboration_note,
        ambiguity_factor=ambiguity_factor,
        ambiguity_note=factors.ambiguity_note,
        rationale=factors.rationale,
    )


def _user_content_for(ctx: PipelineContext, event: ChangeEvent) -> list[dict]:
    if ctx.mode == "single_sheet":
        return [
            {"type": "text", "text": "Change event (JSON):\n" + event.model_dump_json()},
            {"type": "text", "text": SINGLE_SHEET_NOTE},
            encode_image(ctx.old_image_path),
        ]
    assert ctx.new_image_path is not None
    return [
        {"type": "text", "text": "Change event (JSON):\n" + event.model_dump_json()},
        {"type": "text", "text": "OLD revision of the sheet:"},
        encode_image(ctx.old_image_path),
        {"type": "text", "text": "NEW revision of the sheet:"},
        encode_image(ctx.new_image_path),
    ]


class ConfidenceStep(PipelineStep):
    """Explicit, inspectable confidence scoring — its own step rather than a
    number folded into the reasoning stage's prompt, so it can be audited or
    swapped independently (e.g. replaced by a calibrated model trained on
    `human_reviews` corrections later). The model only assesses the three
    underlying factors; the final score is computed deterministically from
    them (see `synthesize_score`), with a mode/identity_unresolved ceiling
    clamp applied first (see `apply_mode_ceiling`).

    One Claude call per `ChangeEvent`, not one batched call for all of them.
    A real production run surfaced cross-contamination between two events
    scored in the same batched response — internally coherent-looking notes
    and numbers that had actually been generated for a *different* event
    than the one they ended up attached to, undetectable by schema
    validation since every individual field was still well-formed. See
    docs/pipeline_notes.md. Scoring one event per call costs more API calls
    but makes that failure mode structurally impossible: there's nothing
    else in the same response for one event's content to get confused with.
    """

    name = "confidence"
    version = "v3"
    model_used = config.REASONING_MODEL

    def input_for_log(self, ctx: PipelineContext) -> dict:
        return {"change_events": [e.model_dump(mode="json") for e in ctx.change_events]}

    def execute(self, ctx: PipelineContext) -> list[ConfidenceScore]:
        if not ctx.change_events:
            ctx.confidence_scores = {}
            return []

        scores = []
        for event in ctx.change_events:
            factors = call_structured(
                system=load_prompt("confidence"),
                user_content=_user_content_for(ctx, event),
                response_model=ConfidenceFactors,
                model=self.model_used,
            )
            scores.append(
                _to_confidence_score(factors, mode=ctx.mode, identity_unresolved=event.identity_unresolved)
            )

        ctx.confidence_scores = {s.change_event_id: s for s in scores}
        return scores
