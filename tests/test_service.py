"""Covers the real production bug: Lovable's single-sheet requests populate
new_drawing_id and leave old_drawing_id NULL, the opposite of what
service.analyze_request originally assumed — passing that None straight into
a uuid-typed Supabase lookup produced 'invalid input syntax for type uuid:
"None"'. These tests exercise analyze_request's drawing-id resolution
directly, with the pipeline itself and all I/O mocked out."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dre import service
from dre.models.schemas import DetectResult, SingleSheetDetectResult


def _fake_pipeline_run(ctx):
    if ctx.mode == "single_sheet":
        ctx.detect_single_result = SingleSheetDetectResult(detections=[], extracted_tables=[])
    else:
        ctx.detect_result = DetectResult(raw_detections=[], extracted_tables=[])
    return SimpleNamespace(alerts=[], change_events=[])


def _patched(analysis_request: dict, drawings: dict[str, dict]):
    return (
        patch("dre.service.repo.get_analysis_request", return_value=analysis_request),
        patch("dre.service.repo.get_drawing", side_effect=lambda did: drawings[did]),
        patch("dre.service.repo.download_drawing_image", side_effect=lambda d, dest: dest),
        patch("dre.service.build_pipeline", return_value=SimpleNamespace(run=_fake_pipeline_run)),
    )


def test_single_sheet_uses_new_drawing_id_when_old_is_null():
    """Lovable's actual convention: old_drawing_id NULL, new_drawing_id set."""
    analysis_request = {
        "id": "ar1",
        "mode": "single_sheet",
        "old_drawing_id": None,
        "new_drawing_id": "d-new",
        "project_id": "p1",
        "sheet_number": "E-501",
    }
    drawings = {"d-new": {"id": "d-new", "file_path": "sheet.png"}}
    with patch("dre.service.repo.get_analysis_request", return_value=analysis_request), patch(
        "dre.service.repo.get_drawing", side_effect=lambda did: drawings[did]
    ) as mock_get_drawing, patch(
        "dre.service.repo.download_drawing_image",
        side_effect=lambda d, dest_dir, basename: dest_dir / f"{basename}.png",
    ), patch(
        "dre.service.build_pipeline", return_value=SimpleNamespace(run=_fake_pipeline_run)
    ):
        result = service.analyze_request("ar1", dry_run=True)

    mock_get_drawing.assert_called_once_with("d-new")
    assert result["mode"] == "single_sheet"


def test_single_sheet_uses_old_drawing_id_when_set():
    """The other valid convention: old_drawing_id set, new_drawing_id NULL."""
    analysis_request = {
        "id": "ar2",
        "mode": "single_sheet",
        "old_drawing_id": "d-old",
        "new_drawing_id": None,
        "project_id": "p1",
        "sheet_number": "E-501",
    }
    drawings = {"d-old": {"id": "d-old", "file_path": "sheet.png"}}
    with patch("dre.service.repo.get_analysis_request", return_value=analysis_request), patch(
        "dre.service.repo.get_drawing", side_effect=lambda did: drawings[did]
    ) as mock_get_drawing, patch(
        "dre.service.repo.download_drawing_image",
        side_effect=lambda d, dest_dir, basename: dest_dir / f"{basename}.png",
    ), patch(
        "dre.service.build_pipeline", return_value=SimpleNamespace(run=_fake_pipeline_run)
    ):
        result = service.analyze_request("ar2", dry_run=True)

    mock_get_drawing.assert_called_once_with("d-old")
    assert result["mode"] == "single_sheet"


def test_single_sheet_raises_clearly_when_both_drawing_ids_null():
    analysis_request = {
        "id": "ar3",
        "mode": "single_sheet",
        "old_drawing_id": None,
        "new_drawing_id": None,
        "project_id": "p1",
        "sheet_number": "E-501",
    }
    with patch("dre.service.repo.get_analysis_request", return_value=analysis_request):
        with pytest.raises(ValueError, match="neither old_drawing_id nor new_drawing_id"):
            service.analyze_request("ar3", dry_run=True)


def test_two_image_mode_raises_clearly_when_old_drawing_id_missing():
    analysis_request = {
        "id": "ar4",
        "mode": "two_image",
        "old_drawing_id": None,
        "new_drawing_id": "d-new",
        "project_id": "p1",
        "sheet_number": "E-501",
    }
    with patch("dre.service.repo.get_analysis_request", return_value=analysis_request):
        with pytest.raises(ValueError, match="old_drawing_id is not set"):
            service.analyze_request("ar4", dry_run=True)


def test_two_image_mode_still_loads_both_drawings_normally():
    analysis_request = {
        "id": "ar5",
        "mode": "two_image",
        "old_drawing_id": "d-old",
        "new_drawing_id": "d-new",
        "project_id": "p1",
        "sheet_number": "E-501",
    }
    drawings = {
        "d-old": {"id": "d-old", "file_path": "old.png"},
        "d-new": {"id": "d-new", "file_path": "new.png"},
    }
    with patch("dre.service.repo.get_analysis_request", return_value=analysis_request), patch(
        "dre.service.repo.get_drawing", side_effect=lambda did: drawings[did]
    ) as mock_get_drawing, patch(
        "dre.service.repo.download_drawing_image",
        side_effect=lambda d, dest_dir, basename: dest_dir / f"{basename}.png",
    ), patch(
        "dre.service.build_pipeline", return_value=SimpleNamespace(run=_fake_pipeline_run)
    ):
        result = service.analyze_request("ar5", dry_run=True)

    mock_get_drawing.assert_any_call("d-old")
    mock_get_drawing.assert_any_call("d-new")
    assert result["mode"] == "two_image"


def test_real_write_deletes_prior_flagged_changes_before_writing_new_ones():
    """The actual production bug this fixes: two independently-triggered
    analyze calls against the same analysis_request_id each wrote their own
    flagged_changes rows with nothing superseding the earlier ones, so both
    sets accumulated and looked like single-run over-segmentation. A real
    (non-dry-run) run must clear the request's prior rows before writing
    the new run's, even when the new run finds nothing (empty alerts here)
    — a superseded run's stale rows shouldn't survive a re-analysis either."""
    analysis_request = {
        "id": "ar6",
        "mode": "single_sheet",
        "old_drawing_id": "d-old",
        "new_drawing_id": None,
        "project_id": "p1",
        "sheet_number": "E-501",
    }
    drawings = {"d-old": {"id": "d-old", "file_path": "sheet.png"}}

    with patch("dre.service.repo.get_analysis_request", return_value=analysis_request), patch(
        "dre.service.repo.get_drawing", side_effect=lambda did: drawings[did]
    ), patch(
        "dre.service.repo.download_drawing_image",
        side_effect=lambda d, dest_dir, basename: dest_dir / f"{basename}.png",
    ), patch(
        "dre.service.build_pipeline", return_value=SimpleNamespace(run=_fake_pipeline_run)
    ), patch(
        "dre.service.repo.create_pipeline_run", return_value="run-1"
    ), patch("dre.service.repo.set_run_status"), patch(
        "dre.service.repo.set_analysis_status"
    ), patch(
        "dre.service.repo.delete_flagged_changes_for_analysis_request"
    ) as mock_delete, patch(
        "dre.service.repo.log_tiling_trigger_diagnostics"
    ):
        result = service.analyze_request("ar6", dry_run=False)

    mock_delete.assert_called_once_with("ar6")
    assert result["mode"] == "single_sheet"
    assert result["dry_run"] is False


def test_real_write_logs_tiling_trigger_diagnostics():
    """Passive data collection for docs/tiled_analysis_findings.md §3a —
    every real run logs whether that doc's tiling rule would fire, no
    commitment to build tiling implied. Must be attached to the real
    run_id from create_pipeline_run, not the dry-run placeholder string."""
    analysis_request = {
        "id": "ar8",
        "mode": "single_sheet",
        "old_drawing_id": "d-old",
        "new_drawing_id": None,
        "project_id": "p1",
        "sheet_number": "E-501",
    }
    drawings = {"d-old": {"id": "d-old", "file_path": "sheet.png"}}

    with patch("dre.service.repo.get_analysis_request", return_value=analysis_request), patch(
        "dre.service.repo.get_drawing", side_effect=lambda did: drawings[did]
    ), patch(
        "dre.service.repo.download_drawing_image",
        side_effect=lambda d, dest_dir, basename: dest_dir / f"{basename}.png",
    ), patch(
        "dre.service.build_pipeline", return_value=SimpleNamespace(run=_fake_pipeline_run)
    ), patch(
        "dre.service.repo.create_pipeline_run", return_value="run-1"
    ), patch("dre.service.repo.set_run_status"), patch(
        "dre.service.repo.set_analysis_status"
    ), patch(
        "dre.service.repo.delete_flagged_changes_for_analysis_request"
    ), patch(
        "dre.service.repo.log_tiling_trigger_diagnostics"
    ) as mock_log:
        service.analyze_request("ar8", dry_run=False)

    mock_log.assert_called_once()
    run_id_arg, diagnostics_arg = mock_log.call_args.args
    assert run_id_arg == "run-1"
    assert diagnostics_arg["detection_count"] == 0


def test_tiling_trigger_diagnostics_failure_never_breaks_the_real_write():
    """This is auxiliary data collection, not part of the guarantee callers
    depend on — e.g. the migration adding pipeline_runs.tiling_trigger_
    diagnostics not yet being applied must not take down real
    flagged_changes writes. A real production risk this session already
    hit once with an unrelated column (the null-drawing-id uuid bug), so
    this failure mode is worth guarding against explicitly, not assumed
    away."""
    analysis_request = {
        "id": "ar10",
        "mode": "single_sheet",
        "old_drawing_id": "d-old",
        "new_drawing_id": None,
        "project_id": "p1",
        "sheet_number": "E-501",
    }
    drawings = {"d-old": {"id": "d-old", "file_path": "sheet.png"}}

    with patch("dre.service.repo.get_analysis_request", return_value=analysis_request), patch(
        "dre.service.repo.get_drawing", side_effect=lambda did: drawings[did]
    ), patch(
        "dre.service.repo.download_drawing_image",
        side_effect=lambda d, dest_dir, basename: dest_dir / f"{basename}.png",
    ), patch(
        "dre.service.build_pipeline", return_value=SimpleNamespace(run=_fake_pipeline_run)
    ), patch(
        "dre.service.repo.create_pipeline_run", return_value="run-1"
    ), patch("dre.service.repo.set_run_status"), patch(
        "dre.service.repo.set_analysis_status"
    ), patch(
        "dre.service.repo.delete_flagged_changes_for_analysis_request"
    ), patch(
        "dre.service.repo.log_tiling_trigger_diagnostics",
        side_effect=Exception("column does not exist"),
    ):
        result = service.analyze_request("ar10", dry_run=False)

    assert result["dry_run"] is False
    assert result["mode"] == "single_sheet"


def test_dry_run_never_logs_tiling_trigger_diagnostics():
    analysis_request = {
        "id": "ar9",
        "mode": "single_sheet",
        "old_drawing_id": "d-old",
        "new_drawing_id": None,
        "project_id": "p1",
        "sheet_number": "E-501",
    }
    drawings = {"d-old": {"id": "d-old", "file_path": "sheet.png"}}

    with patch("dre.service.repo.get_analysis_request", return_value=analysis_request), patch(
        "dre.service.repo.get_drawing", side_effect=lambda did: drawings[did]
    ), patch(
        "dre.service.repo.download_drawing_image",
        side_effect=lambda d, dest_dir, basename: dest_dir / f"{basename}.png",
    ), patch(
        "dre.service.build_pipeline", return_value=SimpleNamespace(run=_fake_pipeline_run)
    ), patch(
        "dre.service.repo.log_tiling_trigger_diagnostics"
    ) as mock_log:
        service.analyze_request("ar9", dry_run=True)

    mock_log.assert_not_called()


def test_dry_run_never_deletes_prior_flagged_changes():
    analysis_request = {
        "id": "ar7",
        "mode": "single_sheet",
        "old_drawing_id": "d-old",
        "new_drawing_id": None,
        "project_id": "p1",
        "sheet_number": "E-501",
    }
    drawings = {"d-old": {"id": "d-old", "file_path": "sheet.png"}}

    with patch("dre.service.repo.get_analysis_request", return_value=analysis_request), patch(
        "dre.service.repo.get_drawing", side_effect=lambda did: drawings[did]
    ), patch(
        "dre.service.repo.download_drawing_image",
        side_effect=lambda d, dest_dir, basename: dest_dir / f"{basename}.png",
    ), patch(
        "dre.service.build_pipeline", return_value=SimpleNamespace(run=_fake_pipeline_run)
    ), patch(
        "dre.service.repo.delete_flagged_changes_for_analysis_request"
    ) as mock_delete:
        result = service.analyze_request("ar7", dry_run=True)

    mock_delete.assert_not_called()
    assert result["dry_run"] is True
