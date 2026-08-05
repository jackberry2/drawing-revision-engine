"""Verifies dre.supa.repository builds payloads that match the real
flagged_changes/human_reviews/pipeline_* column sets, using a fake
supabase-py client so no network/credentials are needed."""

from types import SimpleNamespace
from unittest.mock import patch

from dre.models.schemas import ChangeCategory, ChangeEvent, EntityRef
from dre.supa import repository as repo


class FakeQuery:
    def __init__(self, table_name: str, recorder: "FakeClient"):
        self.table_name = table_name
        self.recorder = recorder

    def insert(self, payload):
        self.recorder.inserts.append((self.table_name, payload))
        return self

    def update(self, payload):
        self.recorder.updates.append((self.table_name, payload))
        return self

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.recorder.fake_data.get(self.table_name))


class FakeClient:
    def __init__(self):
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict]] = []
        self.fake_data: dict[str, dict] = {}

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(name, self)


def test_save_flagged_change_matches_real_table_columns():
    fake = FakeClient()
    with patch("dre.supa.repository.get_client", return_value=fake):
        repo.save_flagged_change(
            project_id="proj-1",
            drawing_id="drawing-2",
            sheet_number="E-201",
            change_type="moved",
            description="Panel LP-2 relocated.",
            confidence_tier="high",
            confidence_percentage=95,
            impact_note="Circuit 14 re-routes.",
        )

    assert len(fake.inserts) == 1
    table_name, payload = fake.inserts[0]
    assert table_name == "flagged_changes"
    # flagged_changes columns we're allowed to write (id/reviewed/created_at
    # are either generated here or defaulted by the table itself).
    assert set(payload.keys()) == {
        "id",
        "project_id",
        "drawing_id",
        "sheet_number",
        "change_type",
        "description",
        "confidence_tier",
        "confidence_percentage",
        "impact_note",
    }
    assert payload["change_type"] == "moved"
    assert payload["confidence_percentage"] == 95


def test_log_step_serializes_pydantic_models_to_plain_json():
    fake = FakeClient()
    change_event = ChangeEvent(
        id="ce_1",
        root_cause_change_id="cc_1",
        bundled_change_ids=["cc_1"],
        category=ChangeCategory.PANEL_RELOCATION,
        root_cause_summary="Panel LP-2 relocated.",
        affected_entities=[EntityRef(entity_type="panel", identifier="LP-2")],
    )
    with patch("dre.supa.repository.get_client", return_value=fake):
        repo.log_step(
            run_id="run-1",
            step_name="reason",
            step_order=3,
            input_data=[change_event],
            output_data={"change_events": [change_event]},
            model_used="claude-sonnet-5",
            prompt_version="v1",
            latency_ms=1200,
        )

    table_name, payload = fake.inserts[0]
    assert table_name == "pipeline_steps"
    # Must be plain dict/list, not a pydantic BaseModel instance.
    assert isinstance(payload["input_json"], list)
    assert isinstance(payload["input_json"][0], dict)
    assert payload["input_json"][0]["category"] == "panel_relocation"
    assert isinstance(payload["output_json"]["change_events"][0], dict)


def test_record_human_review_confirmed_marks_flagged_change_reviewed():
    fake = FakeClient()
    with patch("dre.supa.repository.get_client", return_value=fake):
        repo.record_human_review(
            flagged_change_id="fc-1",
            run_id="run-1",
            reviewer="jack",
            verdict="confirmed",
        )

    assert [t for t, _ in fake.inserts] == ["human_reviews"]
    assert fake.updates == [("flagged_changes", {"reviewed": True})]


def test_record_human_review_false_positive_does_not_mark_reviewed():
    fake = FakeClient()
    with patch("dre.supa.repository.get_client", return_value=fake):
        repo.record_human_review(
            flagged_change_id="fc-1",
            run_id="run-1",
            reviewer="jack",
            verdict="false_positive",
        )

    assert [t for t, _ in fake.inserts] == ["human_reviews"]
    assert fake.updates == []
