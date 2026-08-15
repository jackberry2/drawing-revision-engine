"""Shared between `reason` (two-image) and `reason_single` (single-sheet):
enforcing `identity_unresolved` propagation and its consequences in code
rather than trusting the model to apply them consistently — same principle
as the confidence-synthesis fix. `ClassifiedChange.identity_unresolved` is
set by the shared `classify` stage for both modes, so this needs to be
enforced for both, not just single-sheet mode.
"""

from __future__ import annotations

from dre.models.schemas import ChangeCategory, ChangeEvent, ClassifiedChange

# Replaces root_cause_summary/downstream_implications/schedule_consistency/
# schedule_corroboration wholesale whenever identity_unresolved is true,
# rather than appending a caveat to whatever free text the model wrote.
#
# An earlier version of this function only appended a disclaimer after the
# model's own root_cause_summary. That stopped one failure mode (asserting a
# specific change-type verb like "also having moved") but a second, distinct
# one showed up on the identical test image in a later run: detect_single's
# `geometry_description` guessed the flagged bar "appears to represent a wall
# or partition segment" — a specific *identity* claim, not a change-type
# claim, and one detect_single's own schema explicitly says that field should
# never make ("no trade judgment yet"). That one noun then propagated
# untouched through classify's trade_description, reason's
# root_cause_summary and downstream_implications, and into the final
# describe headline/description/impact_note — an append-only disclaimer
# after the sentence doesn't stop a wrong noun sitting in the sentence's own
# subject position. Free-text identity guesses can originate at any upstream
# stage and take unpredictable forms, so the only guarantee that actually
# holds is discarding the model's identity-describing text entirely for
# these items, not trying to detect and patch around whatever specific wrong
# word it used this run.
_UNRESOLVED_IDENTITY_ROOT_CAUSE_SUMMARY = (
    "An item on this sheet is flagged for revision, but its identity and "
    "purpose cannot be confirmed against any schedule, legend, or label "
    "here. Whether it represents a new, moved, or modified condition — and "
    "what, if anything, it is — cannot be determined from this sheet alone; "
    "only that it's flagged."
)
_UNRESOLVED_IDENTITY_DOWNSTREAM_IMPLICATION = (
    "Because this item's identity is unconfirmed, any scope, wiring, or "
    "cost impact tied to it cannot be determined until it is identified — "
    "field or drafter verification is needed."
)


def enforce_identity_unresolved(
    change_events: list[ChangeEvent], material: list[ClassifiedChange]
) -> list[ChangeEvent]:
    """Four things get forced, not just suggested:

    1. `ChangeEvent.identity_unresolved` — computed from whether any bundled
       classified change was itself unresolved, not trusted to the model's
       own propagation.
    2. `ChangeEvent.category` — forced to `OTHER` whenever
       `identity_unresolved` is true, regardless of what the model chose.
       A category like `device_relocation` or `device_added` asserts a
       *specific kind* of change; that's a stronger claim than is earned
       when the object itself hasn't been identified.
    3. `ChangeEvent.root_cause_summary` and `.downstream_implications` — both
       replaced outright with fixed, code-authored neutral text (see module
       docstring above for why append-only wasn't enough).
    4. `ChangeEvent.schedule_corroboration` / `.schedule_consistency` —
       cleared. Whatever the model wrote there was reasoning about the same
       unresolved identity (e.g. "no schedule entry for a wall or
       partition"), so it carries the same risk of a leaked identity guess.

    `describe` builds the user-facing headline/description/impact_note from
    these fields, so a hedge that only lives in `category`/
    `identity_unresolved` (fields a human reviewing the app never sees
    directly) doesn't protect the prose they actually read from overclaiming.
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
        if is_unresolved and event.root_cause_summary != _UNRESOLVED_IDENTITY_ROOT_CAUSE_SUMMARY:
            updates["root_cause_summary"] = _UNRESOLVED_IDENTITY_ROOT_CAUSE_SUMMARY
        if is_unresolved and event.downstream_implications != [
            _UNRESOLVED_IDENTITY_DOWNSTREAM_IMPLICATION
        ]:
            updates["downstream_implications"] = [_UNRESOLVED_IDENTITY_DOWNSTREAM_IMPLICATION]
        if is_unresolved and event.schedule_corroboration is not None:
            updates["schedule_corroboration"] = None
        if is_unresolved and event.schedule_consistency is not None:
            updates["schedule_consistency"] = None

        result.append(event.model_copy(update=updates) if updates else event)
    return result
