"""Covers dre.pipeline.tile_tuning - wiring only (rasterization -> real
DetectSingleStep), with the actual Claude call mocked so this doesn't cost
real API credits on every test run."""

import threading
import time
import uuid
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


def test_run_detect_single_on_tile_shares_usage_sink_with_the_grid_caller():
    """A per-tile ctx must accumulate into the *same* usage_sink list the
    grid caller passed in, not its own private default list — otherwise
    real per-tile token usage would be silently dropped rather than
    attributed to the run."""
    import fitz

    doc = fitz.open()
    doc.new_page(width=400, height=300)
    pdf_bytes = doc.tobytes()
    doc.close()

    tiles = compute_tile_grid(sheet_width_in=400 / 72, sheet_height_in=300 / 72, target_dpi=72)
    tile = tiles[0]
    fake_result = SingleSheetDetectResult(detections=[], extracted_tables=[])

    def fake_execute(ctx):
        ctx.token_usage.append({"model": "claude-sonnet-5", "input_tokens": 42, "output_tokens": 7})
        return fake_result

    shared_sink: list[dict] = []
    with patch("dre.pipeline.tile_tuning.DetectSingleStep.execute", side_effect=fake_execute):
        run_detect_single_on_tile(pdf_bytes, tile, dpi=72, usage_sink=shared_sink)

    assert shared_sink == [{"model": "claude-sonnet-5", "input_tokens": 42, "output_tokens": 7}]


def test_run_detect_single_on_grid_aggregates_usage_across_all_tiles():
    """Every tile's usage lands in the one shared sink passed to the grid
    call — the real, production-used path (dre.pipeline.tiled_detect calls
    this exact function with ctx.token_usage as usage_sink)."""
    import fitz

    doc = fitz.open()
    doc.new_page(width=800, height=600)
    pdf_bytes = doc.tobytes()
    doc.close()

    tiles = compute_tile_grid(
        sheet_width_in=800 / 72, sheet_height_in=600 / 72, target_dpi=72, tile_edge_px=300
    )
    assert len(tiles) > 1

    def fake_execute(ctx):
        ctx.token_usage.append({"model": "claude-sonnet-5", "input_tokens": 10, "output_tokens": 1})
        return SingleSheetDetectResult(detections=[], extracted_tables=[])

    shared_sink: list[dict] = []
    with patch("dre.pipeline.tile_tuning.DetectSingleStep.execute", side_effect=fake_execute):
        run_detect_single_on_grid(
            pdf_bytes,
            sheet_width_in=800 / 72,
            sheet_height_in=600 / 72,
            dpi=72,
            tile_edge_px=300,
            usage_sink=shared_sink,
        )

    assert len(shared_sink) == len(tiles)
    assert all(u == {"model": "claude-sonnet-5", "input_tokens": 10, "output_tokens": 1} for u in shared_sink)


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

    call_lock = threading.Lock()
    call_count = {"n": 0}

    def fake_execute(ctx):
        # This now runs concurrently across threads (§3f) - a plain
        # dict[key] += 1 is not guaranteed atomic under real thread
        # interleaving, and each call needs a genuinely unique id
        # (uuid4, not a shared counter) rather than relying on call
        # ordering that parallel execution no longer guarantees.
        with call_lock:
            call_count["n"] += 1
        return SingleSheetDetectResult(
            detections=[
                SingleSheetDetection(
                    id=f"det-{uuid.uuid4()}",
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


def test_run_detect_single_on_grid_actually_runs_tiles_concurrently():
    """§3f's whole point: real wall-clock reduction, not just correctness.
    Proven two ways: (1) more than one call is ever in-flight at the same
    time (direct evidence of concurrency, not inferred from timing alone),
    and (2) total wall time is far less than sequential would take."""
    import fitz

    doc = fitz.open()
    doc.new_page(width=800, height=600)
    pdf_bytes = doc.tobytes()
    doc.close()

    tiles = compute_tile_grid(
        sheet_width_in=800 / 72, sheet_height_in=600 / 72, target_dpi=72, tile_edge_px=300
    )
    assert len(tiles) >= 6  # enough tiles that max_workers actually gets exercised

    concurrency_lock = threading.Lock()
    state = {"in_flight": 0, "max_in_flight": 0}
    per_call_delay_s = 0.2

    def fake_execute(ctx):
        with concurrency_lock:
            state["in_flight"] += 1
            state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        time.sleep(per_call_delay_s)
        with concurrency_lock:
            state["in_flight"] -= 1
        return SingleSheetDetectResult(detections=[], extracted_tables=[])

    with patch("dre.pipeline.tile_tuning.DetectSingleStep.execute", side_effect=fake_execute):
        start = time.perf_counter()
        run_detect_single_on_grid(
            pdf_bytes,
            sheet_width_in=800 / 72,
            sheet_height_in=600 / 72,
            dpi=72,
            tile_edge_px=300,
            max_workers=6,
        )
        elapsed = time.perf_counter() - start

    assert state["max_in_flight"] > 1  # direct evidence: calls actually overlapped in time
    sequential_would_take = len(tiles) * per_call_delay_s
    assert elapsed < sequential_would_take * 0.6  # real wall-clock reduction, not marginal


def test_max_workers_bounds_concurrency():
    import fitz

    doc = fitz.open()
    doc.new_page(width=800, height=600)
    pdf_bytes = doc.tobytes()
    doc.close()

    tiles = compute_tile_grid(
        sheet_width_in=800 / 72, sheet_height_in=600 / 72, target_dpi=72, tile_edge_px=300
    )
    assert len(tiles) >= 6

    concurrency_lock = threading.Lock()
    state = {"in_flight": 0, "max_in_flight": 0}

    def fake_execute(ctx):
        with concurrency_lock:
            state["in_flight"] += 1
            state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        time.sleep(0.05)
        with concurrency_lock:
            state["in_flight"] -= 1
        return SingleSheetDetectResult(detections=[], extracted_tables=[])

    with patch("dre.pipeline.tile_tuning.DetectSingleStep.execute", side_effect=fake_execute):
        run_detect_single_on_grid(
            pdf_bytes,
            sheet_width_in=800 / 72,
            sheet_height_in=600 / 72,
            dpi=72,
            tile_edge_px=300,
            max_workers=2,
        )

    assert state["max_in_flight"] <= 2  # never exceeds the bound, even with more tiles available
