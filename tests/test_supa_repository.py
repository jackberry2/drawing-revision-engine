"""Verifies dre.supa.repository builds payloads that match the real
flagged_changes/human_reviews/pipeline_* column sets, using a fake
supabase-py client so no network/credentials are needed."""

from types import SimpleNamespace
from unittest.mock import patch

from dre.models.schemas import ChangeCategory, ChangeEvent, EntityRef
from dre.supa import repository as repo


class FakeBucket:
    def __init__(self, expected_path: str, data: bytes):
        self.expected_path = expected_path
        self.data = data
        self.requested_paths: list[str] = []

    def download(self, path: str) -> bytes:
        self.requested_paths.append(path)
        assert path == self.expected_path
        return self.data


class FakeStorage:
    def __init__(self, bucket: FakeBucket):
        self.bucket = bucket

    def from_(self, bucket_name: str) -> FakeBucket:
        return self.bucket


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

    def delete(self):
        self.recorder.deletes.append([self.table_name, {}])
        self._pending_delete = True
        return self

    def select(self, *args, **kwargs):
        return self

    def eq(self, column, value, **kwargs):
        if getattr(self, "_pending_delete", False):
            self.recorder.deletes[-1][1][column] = value
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.recorder.fake_data.get(self.table_name))


class FakeClient:
    def __init__(self):
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict]] = []
        self.deletes: list[list] = []
        self.fake_data: dict[str, dict] = {}

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(name, self)


def test_save_flagged_change_matches_real_table_columns():
    fake = FakeClient()
    with patch("dre.supa.repository.get_client", return_value=fake):
        repo.save_flagged_change(
            project_id="proj-1",
            drawing_id="drawing-2",
            analysis_request_id="ar-1",
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
        "analysis_request_id",
        "sheet_number",
        "change_type",
        "description",
        "confidence_tier",
        "confidence_percentage",
        "impact_note",
    }
    assert payload["change_type"] == "moved"
    assert payload["confidence_percentage"] == 95


def test_delete_flagged_changes_for_analysis_request_filters_by_request_id():
    """A real production bug: re-analyzing the same analysis_request_id
    appended new flagged_changes rows alongside an earlier run's, rather
    than superseding them — two independently-triggered runs against one
    request left both sets of rows sitting side by side, which looked like
    single-run over-segmentation until traced back through pipeline_steps.
    A re-analysis must always clear the prior run's rows for that request
    first, scoped only to that request (never a blanket delete)."""
    fake = FakeClient()
    with patch("dre.supa.repository.get_client", return_value=fake):
        repo.delete_flagged_changes_for_analysis_request("ar-1")

    assert fake.deletes == [["flagged_changes", {"analysis_request_id": "ar-1"}]]


def test_create_pipeline_run_supports_single_sheet_mode():
    fake = FakeClient()
    with patch("dre.supa.repository.get_client", return_value=fake):
        repo.create_pipeline_run(
            analysis_request_id="ar-1",
            old_drawing_id="drawing-1",
            new_drawing_id=None,
            mode="single_sheet",
        )

    table_name, payload = fake.inserts[0]
    assert table_name == "pipeline_runs"
    assert payload["new_drawing_id"] is None
    assert payload["mode"] == "single_sheet"


def test_save_pipeline_change_event_includes_schedule_consistency_and_identity_unresolved():
    fake = FakeClient()
    change_event = ChangeEvent(
        id="ce_1",
        root_cause_change_id="cc_1",
        bundled_change_ids=["cc_1"],
        category=ChangeCategory.DEVICE_ADDED,
        root_cause_summary="Unlabeled symbol flagged with a revision cloud.",
        schedule_consistency=None,
        identity_unresolved=True,
    )
    with patch("dre.supa.repository.get_client", return_value=fake):
        repo.save_pipeline_change_event(
            run_id="run-1",
            flagged_change_id="fc-1",
            change_event=change_event,
            confidence_score=0.2,
            confidence_rationale={"rationale": "unresolved identity"},
        )

    table_name, payload = fake.inserts[0]
    assert table_name == "pipeline_change_events"
    assert payload["identity_unresolved"] is True
    assert payload["schedule_consistency"] is None


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
            input_tokens=1500,
            output_tokens=300,
        )

    table_name, payload = fake.inserts[0]
    assert table_name == "pipeline_steps"
    # Must be plain dict/list, not a pydantic BaseModel instance.
    assert isinstance(payload["input_json"], list)
    assert isinstance(payload["input_json"][0], dict)
    assert payload["input_json"][0]["category"] == "panel_relocation"
    assert isinstance(payload["output_json"]["change_events"][0], dict)
    assert payload["input_tokens"] == 1500
    assert payload["output_tokens"] == 300


def test_log_step_defaults_token_fields_to_none():
    """Callers that don't have real usage to report (or old call sites not
    yet updated) must not silently write 0 — 0 would be indistinguishable
    from a real zero-token call."""
    fake = FakeClient()
    with patch("dre.supa.repository.get_client", return_value=fake):
        repo.log_step(
            run_id="run-1",
            step_name="tile_merge",
            step_order=1,
            input_data={},
            output_data={},
        )

    _, payload = fake.inserts[0]
    assert payload["input_tokens"] is None
    assert payload["output_tokens"] is None


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


_REAL_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake but real-signature png bytes"


def test_download_drawing_image_uses_storage_api_not_direct_url(tmp_path):
    """drawings.file_path is a path inside a private Storage bucket, not a
    fetchable URL - download must go through client.storage, not httpx."""
    bucket = FakeBucket(expected_path="proj-1/E-201/123-E-201.png", data=_REAL_PNG_BYTES)
    fake = SimpleNamespace(storage=FakeStorage(bucket))
    drawing = {"id": "drawing-1", "file_path": "proj-1/E-201/123-E-201.png"}

    with patch("dre.supa.repository.get_client", return_value=fake):
        result_path = repo.download_drawing_image(drawing, tmp_path, "old")

    assert result_path == tmp_path / "old.png"
    assert result_path.read_bytes() == _REAL_PNG_BYTES
    assert bucket.requested_paths == ["proj-1/E-201/123-E-201.png"]


def test_download_drawing_image_ignores_file_path_extension_uses_real_content(tmp_path):
    """A real production bug: drawings.file_path claimed .pdf (correctly, in
    that case) but the destination filename must be decided from actual
    downloaded bytes, not the stored path's extension — here a mislabeled
    .pdf path actually contains PNG bytes, and the real content must win."""
    bucket = FakeBucket(
        expected_path="proj-1/E-201/mislabeled.pdf", data=_REAL_PNG_BYTES
    )
    fake = SimpleNamespace(storage=FakeStorage(bucket))
    drawing = {"id": "drawing-1", "file_path": "proj-1/E-201/mislabeled.pdf"}

    with patch("dre.supa.repository.get_client", return_value=fake):
        result_path = repo.download_drawing_image(drawing, tmp_path, "old")

    assert result_path == tmp_path / "old.png"
    assert result_path.read_bytes() == _REAL_PNG_BYTES


def test_download_drawing_image_rasterizes_real_pdf(tmp_path):
    """The actual real-world bug report: a genuinely PDF drawing sheet
    upload must be rasterized to PNG, since Claude's vision API doesn't
    accept application/pdf as an image content block."""
    import fitz

    pdf_doc = fitz.open()
    page = pdf_doc.new_page(width=200, height=100)
    page.insert_text((10, 50), "TEST SHEET")
    pdf_bytes = pdf_doc.tobytes()
    pdf_doc.close()

    bucket = FakeBucket(expected_path="proj-1/E-101.3/sheet.pdf", data=pdf_bytes)
    fake = SimpleNamespace(storage=FakeStorage(bucket))
    drawing = {"id": "drawing-1", "file_path": "proj-1/E-101.3/sheet.pdf"}

    with patch("dre.supa.repository.get_client", return_value=fake):
        result_path = repo.download_drawing_image(drawing, tmp_path, "old")

    assert result_path == tmp_path / "old.png"
    assert result_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


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
