"""Shared between `reason` (two-image) and `reason_single` (single-sheet):
enforcing `identity_unresolved` propagation and its consequences in code
rather than trusting the model to apply them consistently — same principle
as the confidence-synthesis fix. `ClassifiedChange.identity_unresolved` is
set by the shared `classify` stage for both modes, so this needs to be
enforced for both, not just single-sheet mode.
"""

from __future__ import annotations

from dre.models.schemas import ChangeCategory, ChangeEvent, ClassifiedChange

# Appended to root_cause_summary whenever identity_unresolved is true —
# guaranteed present regardless of whether the model's own prose already
# hedged appropriately. This is the same principle as impact_note being
# derived programmatically rather than trusted to the LLM: a real production
# bug (see docs/pipeline_notes.md) had the category correctly forced to
# `other` and confidence correctly scored low, while the free-text
# root_cause_summary still asserted "as also having moved" — a human reading
# the app sees the prose, not the category enum, so the prose has to carry
# the same honesty the structured fields already do, not just describe/
# reason's own unreinforced judgment each time.
_UNRESOLVED_IDENTITY_DISCLAIMER = (
    "This item's identity is unconfirmed, so the specific nature of any "
    "change to it — including whether it moved, was added, or was modified "
    "— cannot be confirmed from this sheet either; only that it's flagged."
)


def enforce_identity_unresolved(
    change_events: list[ChangeEvent], material: list[ClassifiedChange]
) -> list[ChangeEvent]:
    """Three things get forced, not just suggested:

    1. `ChangeEvent.identity_unresolved` — computed from whether any bundled
       classified change was itself unresolved, not trusted to the model's
       own propagation.
    2. `ChangeEvent.category` — forced to `OTHER` whenever
       `identity_unresolved` is true, regardless of what the model chose.
       A category like `device_relocation` or `device_added` asserts a
       *specific kind* of change; that's a stronger claim than is earned
       when the object itself hasn't been identified.
    3. `ChangeEvent.root_cause_summary` — gets `_UNRESOLVED_IDENTITY_DISCLAIMER`
       appended. `describe` builds the user-facing headline/description from
       this text, so a hedge that only lives in `category`/`identity_unresolved`
       (fields a human reviewing the app never sees directly) doesn't
       protect the prose they actually read from overclaiming.
    """
    material_by_id = {c.id: c for c in material}
    result = []
    for event in change_events:
        bundled = [material_by_id[cid] for cid in event.bundled_change_ids if cid in material_by_id]
        is_unresolved = event.identity_unresolved or any(c.identity_unresolved for c in bundled)

        updates: dict = {}
        if is_unresolved and not event.identity_unresolved:
            updates["identity_unresolved"] = True
        if is_unresolved and event.category != ChangeCategory.OTHER:
            updates["category"] = ChangeCategory.OTHER
        if is_unresolved and _UNRESOLVED_IDENTITY_DISCLAIMER not in event.root_cause_summary:
            updates["root_cause_summary"] = (
                event.root_cause_summary.rstrip() + " " + _UNRESOLVED_IDENTITY_DISCLAIMER
            )

        result.append(event.model_copy(update=updates) if updates else event)
    return result
