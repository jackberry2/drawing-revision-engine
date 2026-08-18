"""Shared between `reason` (two-image) and `reason_single` (single-sheet):
enforcing `identity_unresolved` propagation and its consequences in code
rather than trusting the model to apply them consistently — same principle
as the confidence-synthesis fix. `ClassifiedChange.identity_unresolved` is
set by the shared `classify` stage for both modes, so this needs to be
enforced for both, not just single-sheet mode.

Also holds `flag_cross_event_causal_risk` — a related but distinct
consequence of `identity_unresolved`, not about the unresolved event
itself but about *other* events in the same run. See
docs/pipeline_notes.md, "`reason` can fabricate a confident causal claim
while the real cause sits orphaned nearby, unlinked" for the real
production case this exists for (E-201: a circuit reroute got confidently
attributed to a panel relocation while the actual likely cause — a new
wall — sat in its own, unlinked `identity_unresolved` event in the same
run). Not a correctness guarantee the way `identity_unresolved`
enforcement is — code can't independently verify which cause is actually
right without redoing the same reasoning the model already got wrong.
What it can guarantee: whenever this specific shape recurs, the
overconfident event gets a real confidence discount and a code-authored
caveat, every time, rather than depending on the model noticing the risk
itself (see `confidence.py`'s `apply_mode_ceiling` for the discount side).
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


# Categories that assert a specific external cause for a device/circuit —
# the shape of claim the real E-201 bug got wrong (attributing a reroute to
# the wrong nearby event). Deliberately excludes device_added/device_removed
# (their own cause is normally the addition/removal itself, not something
# external to attribute) and schedule_label_edit/annotation_only/
# noise_non_material (none of these assert this kind of causal narrative).
_CAUSALLY_RISKY_CATEGORIES = frozenset(
    {
        ChangeCategory.PANEL_RELOCATION,
        ChangeCategory.DEVICE_RELOCATION,
        ChangeCategory.CIRCUIT_REROUTE,
        ChangeCategory.DEVICE_MODIFIED,
    }
)

_CROSS_EVENT_CAUSAL_RISK_NOTE = (
    "This sheet also has a separate, unidentified flagged item; if that item is "
    "actually related to this change, the stated cause here may need to be revisited."
)


def has_unresolved_sibling(change_events: list[ChangeEvent]) -> bool:
    """Does this run contain at least one honestly-hedged identity_unresolved
    event at all — the other half of the co-occurrence signal, checked
    against `event.identity_unresolved` (the enforced, authoritative value,
    not the model's raw self-report) so this must run after
    `enforce_identity_unresolved` has already corrected it."""
    return any(e.identity_unresolved for e in change_events)


def has_cross_event_causal_risk(event: ChangeEvent, change_events: list[ChangeEvent]) -> bool:
    """True when `event` asserts a specific external cause (a causally-risky
    category) for something, *and* this same run also has an unresolved,
    unlinked item that could plausibly be the real cause instead — the
    real, confirmed E-201 shape. Not a claim this specific event's cause is
    wrong, only that this run's shape is the one where that's already
    happened once for real. An event can't trigger its own risk (an
    identity_unresolved event is already honestly hedged on its own
    terms)."""
    return (
        not event.identity_unresolved
        and event.category in _CAUSALLY_RISKY_CATEGORIES
        and has_unresolved_sibling(change_events)
    )


def flag_cross_event_causal_risk(change_events: list[ChangeEvent]) -> list[ChangeEvent]:
    """Appends (never replaces — this is a real signal, not a certainty the
    way identity_unresolved is) a fixed, code-authored caveat to
    `downstream_implications` for every event where
    `has_cross_event_causal_risk` is true. Flows into `impact_note` for
    free through `describe.py`'s existing `_build_impact_note`, which
    already assembles it deterministically from `downstream_implications` —
    no `describe.py` change needed. Call this after
    `enforce_identity_unresolved` so `identity_unresolved` already reflects
    the enforced, authoritative value for every event in the list."""
    result = []
    for event in change_events:
        if not has_cross_event_causal_risk(event, change_events):
            result.append(event)
            continue
        if _CROSS_EVENT_CAUSAL_RISK_NOTE in event.downstream_implications:
            result.append(event)
            continue
        result.append(
            event.model_copy(
                update={
                    "downstream_implications": [
                        *event.downstream_implications,
                        _CROSS_EVENT_CAUSAL_RISK_NOTE,
                    ]
                }
            )
        )
    return result
