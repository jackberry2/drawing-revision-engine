"""Covers dre.pipeline.identity_resolution's cross-event causal-risk
mechanism (docs/pipeline_notes.md, "reason can fabricate a confident causal
claim while the real cause sits orphaned nearby, unlinked") using the real
shape of the E-201 production bug: a panel_relocation event that wrongly
bundled circuit C3's reroute, alongside a separate identity_unresolved event
for the real likely cause (a new wall) that never got connected to it."""

from dre.models.schemas import ChangeCategory, ChangeEvent
from dre.pipeline.identity_resolution import (
    flag_cross_event_causal_risk,
    has_cross_event_causal_risk,
    has_unresolved_sibling,
)


def _panel_relocation_event(**overrides) -> ChangeEvent:
    defaults = dict(
        id="evt1",
        root_cause_change_id="c1",
        bundled_change_ids=["c1", "c3", "c4"],
        category=ChangeCategory.PANEL_RELOCATION,
        root_cause_summary="Panel P-2 relocated, forcing circuit reroutes.",
        downstream_implications=["Circuit C3's home run is rerouted from the new panel location."],
        identity_unresolved=False,
    )
    defaults.update(overrides)
    return ChangeEvent(**defaults)


def _unresolved_event(**overrides) -> ChangeEvent:
    defaults = dict(
        id="evt2",
        root_cause_change_id="c5",
        bundled_change_ids=["c5"],
        category=ChangeCategory.OTHER,
        root_cause_summary="An item on this sheet is flagged for revision, but its identity is unconfirmed.",
        downstream_implications=[],
        identity_unresolved=True,
    )
    defaults.update(overrides)
    return ChangeEvent(**defaults)


def test_has_unresolved_sibling_true_when_any_event_is_unresolved():
    events = [_panel_relocation_event(), _unresolved_event()]
    assert has_unresolved_sibling(events) is True


def test_has_unresolved_sibling_false_when_none_are():
    events = [_panel_relocation_event(), _panel_relocation_event(id="evt3")]
    assert has_unresolved_sibling(events) is False


def test_real_e201_shape_is_flagged():
    """The real bug: evt1 (panel_relocation, wrongly bundling C3) alongside
    evt2 (the orphaned unresolved item) in the same run."""
    events = [_panel_relocation_event(), _unresolved_event()]
    assert has_cross_event_causal_risk(events[0], events) is True


def test_unresolved_event_itself_is_never_flagged():
    """An identity_unresolved event is already honestly hedged on its own
    terms - it can't also trigger the cross-event risk on itself."""
    events = [_panel_relocation_event(), _unresolved_event()]
    assert has_cross_event_causal_risk(events[1], events) is False


def test_no_risk_without_an_unresolved_sibling():
    events = [_panel_relocation_event(), _panel_relocation_event(id="evt3")]
    assert has_cross_event_causal_risk(events[0], events) is False


def test_no_risk_for_a_non_causally_risky_category():
    """device_added's own cause is normally the addition itself, not
    something external to attribute - schedule_label_edit/annotation_only/
    noise_non_material similarly don't assert this kind of causal claim."""
    events = [
        _panel_relocation_event(category=ChangeCategory.DEVICE_ADDED),
        _unresolved_event(),
    ]
    assert has_cross_event_causal_risk(events[0], events) is False


def test_all_causally_risky_categories_are_flagged():
    for category in [
        ChangeCategory.PANEL_RELOCATION,
        ChangeCategory.DEVICE_RELOCATION,
        ChangeCategory.CIRCUIT_REROUTE,
        ChangeCategory.DEVICE_MODIFIED,
    ]:
        events = [_panel_relocation_event(category=category), _unresolved_event()]
        assert has_cross_event_causal_risk(events[0], events) is True, category


def test_flag_cross_event_causal_risk_appends_caveat_to_flagged_event_only():
    events = [_panel_relocation_event(), _unresolved_event()]
    result = flag_cross_event_causal_risk(events)

    flagged = next(e for e in result if e.id == "evt1")
    unresolved = next(e for e in result if e.id == "evt2")

    assert flagged.downstream_implications[-1] == (
        "This sheet also has a separate, unidentified flagged item; if that item is "
        "actually related to this change, the stated cause here may need to be revisited."
    )
    # The original implication is preserved, not replaced.
    assert "Circuit C3's home run is rerouted" in flagged.downstream_implications[0]
    # The unresolved event itself is untouched.
    assert unresolved.downstream_implications == []


def test_flag_cross_event_causal_risk_is_a_no_op_without_the_shape():
    events = [_panel_relocation_event(), _panel_relocation_event(id="evt3")]
    result = flag_cross_event_causal_risk(events)
    assert result == events


def test_flag_cross_event_causal_risk_is_idempotent():
    events = [_panel_relocation_event(), _unresolved_event()]
    once = flag_cross_event_causal_risk(events)
    twice = flag_cross_event_causal_risk(once)
    assert once == twice
