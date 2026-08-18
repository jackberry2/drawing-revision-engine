"""Covers dre.pipeline.tile_tuning - wiring only (rasterization -> real
DetectSingleStep), with the actual Claude call mocked so this doesn't cost
real API credits on every test run."""

from unittest.mock import patch

from dre.models.schemas import SingleSheetDetection, SingleSheetDetectResult
from dre.pipeline.tile_tuning import run_detect_single_on_grid, run_detect_single_on_tile
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


def test_run_detect_single_on_grid_tags_each_tiles_detections_with_its_own_row_col():
    import fitz

    doc = fitz.open()
    doc.new_page(width=800, height=600)
    pdf_bytes = doc.tobytes()
    doc.close()

    # Small tile_edge_px so this produces multiple tiles at a tiny DPI,
    # keeping the test fast while still exercising more than one tile.
    tiles = compute_tile_grid(
        sheet_width_in=800 / 72, sheet_height_in=600 / 72, target_dpi=72, tile_edge_px=300
    )
    assert len(tiles) > 1  # sanity check this test actually exercises multiple tiles

    call_count = {"n": 0}

    def fake_execute(ctx):
        call_count["n"] += 1
        return SingleSheetDetectResult(
            detections=[
                SingleSheetDetection(
                    id=f"det-{call_count['n']}",
                    flagged_by="revision_cloud",
                    geometry_description="fake",
                )
            ],
            extracted_tables=[],
        )

    with patch("dre.pipeline.tile_tuning.DetectSingleStep.execute", side_effect=fake_execute):
        grid_result = run_detect_single_on_grid(
            pdf_bytes,
            sheet_width_in=800 / 72,
            sheet_height_in=600 / 72,
            dpi=72,
            tile_edge_px=300,
        )

    assert len(grid_result.tiled_detections) == len(tiles)
    assert call_count["n"] == len(tiles)
    seen_tile_coords = {(td.tile_row, td.tile_col) for td in grid_result.tiled_detections}
    expected_tile_coords = {(t.row, t.col) for t in tiles}
    assert seen_tile_coords == expected_tile_coords
    assert grid_result.extracted_tables == []
