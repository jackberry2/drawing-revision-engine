"""Shared between `reason` (two-image) and `reason_single` (single-sheet):
enforcing `identity_unresolved` propagation and its consequences in code
rather than trusting the model to apply them consistently — same principle
as the confidence-synthesis fix. `ClassifiedChange.identity_unresolved` is
set by the shared `classify` stage for both modes, so this needs to be
enforced for both, not just single-sheet mode.
"""

from __future__ import annotations

from dre.models.schemas import ChangeCategory, ChangeEvent, ClassifiedChange


def enforce_identity_unresolved(
    change_events: list[ChangeEvent], material: list[ClassifiedChange]
) -> list[ChangeEvent]:
    """Two things get forced, not just suggested:

    1. `ChangeEvent.identity_unresolved` — computed from whether any bundled
       classified change was itself unresolved, not trusted to the model's
       own propagation.
    2. `ChangeEvent.category` — forced to `OTHER` whenever
       `identity_unresolved` is true, regardless of what the model chose.
       A category like `device_relocation` or `device_added` asserts a
       *specific kind* of change; that's a stronger claim than is earned
       when the object itself hasn't been identified. This was a real
       production bug (see docs/pipeline_notes.md): an unidentified symbol
       correctly got a low confidence number but was still labeled
       `device_relocation` because the model attributed a nearby
       "RELOCATED PER RFI #14" annotation to it that actually belonged to a
       different, separate item. A low score attached to an unearned
       specific claim still asserts more than is actually known — the
       category itself needs to reflect the uncertainty, not just the
       number.
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

        result.append(event.model_copy(update=updates) if updates else event)
    return result
