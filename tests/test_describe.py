from dre.models.schemas import ChangeCategory, ChangeEvent
from dre.pipeline.describe import _build_impact_note


def _change_event(downstream_implications, schedule_corroboration=None) -> ChangeEvent:
    return ChangeEvent(
        id="ce_1",
        root_cause_change_id="cc_1",
        bundled_change_ids=["cc_1"],
        category=ChangeCategory.PANEL_RELOCATION,
        root_cause_summary="Panel P-2 relocated.",
        downstream_implications=downstream_implications,
        schedule_corroboration=schedule_corroboration,
    )


def test_impact_note_none_when_nothing_to_report():
    assert _build_impact_note(_change_event([])) is None


def test_impact_note_adds_missing_terminal_punctuation_between_sentences():
    ce = _change_event(
        [
            "C1 home-run re-routed from the panel's new location down to receptacle O1",
            "C4 conduit lengthened from the trunk line's new position down to receptacle O4",
        ]
    )
    note = _build_impact_note(ce)
    assert note == (
        "C1 home-run re-routed from the panel's new location down to receptacle O1. "
        "C4 conduit lengthened from the trunk line's new position down to receptacle O4."
    )


def test_impact_note_does_not_double_punctuate():
    ce = _change_event(["Circuit C3 reroutes around the new wall."])
    note = _build_impact_note(ce)
    assert note == "Circuit C3 reroutes around the new wall."


def test_impact_note_appends_schedule_corroboration_last():
    ce = _change_event(
        ["Circuit C5 added"],
        schedule_corroboration="Panel schedule confirms new C5 row at 20A",
    )
    note = _build_impact_note(ce)
    assert note == "Circuit C5 added. Panel schedule confirms new C5 row at 20A."
