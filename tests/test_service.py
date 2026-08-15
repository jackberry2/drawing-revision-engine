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


def _fake_pipeline_run(ctx):
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
