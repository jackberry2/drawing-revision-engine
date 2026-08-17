"""Covers dre.pipeline.tile_tuning.run_detect_single_on_tile - wiring only
(rasterization -> real DetectSingleStep), with the actual Claude call
mocked so this doesn't cost real API credits on every test run."""

from unittest.mock import patch

from dre.models.schemas import SingleSheetDetectResult
from dre.pipeline.tile_tuning import run_detect_single_on_tile
from dre.tiling import compute_tile_grid


def test_run_detect_single_on_tile_wires_rasterized_png_into_detect_single():
    import fitz

    doc = fitz.open()
    doc.new_page(width=400, height=300)
    pdf_bytes = doc.tobytes()
    doc.close()

    tiles = compute_tile_grid(sheet_width_in=400 / 72, sheet_height_in=300 / 72, target_dpi=72)
    tile = tiles[0]

    fake_result = SingleSheetDetectResult(detections=[], extracted_tables=[])
    captured = {}

    def fake_execute(ctx):
        # Captured while the temp file still exists - the enclosing
        # TemporaryDirectory is cleaned up as soon as this function returns,
        # before the test can inspect it afterward.
        captured["mode"] = ctx.mode
        captured["exists"] = ctx.old_image_path.exists()
        captured["is_png"] = ctx.old_image_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        return fake_result

    with patch("dre.pipeline.tile_tuning.DetectSingleStep.execute", side_effect=fake_execute):
        result = run_detect_single_on_tile(pdf_bytes, tile, dpi=72)

    assert result is fake_result
    assert captured["mode"] == "single_sheet"
    assert captured["exists"] is True
    assert captured["is_png"] is True
