"""Tuning harness for docs/tiled_analysis_findings.md §3b/§5 — wires
`compute_tile_grid` + `rasterize_pdf_tile_to_png` into a real `detect_single`
call against one tile, so candidate DPI values can be tested against real
sheets without going through the full Supabase-backed `analyze_request`
path. Runs the actual production `DetectSingleStep`, not a
reimplementation, so this tests exactly what a real tiled analysis would
produce for that tile — not an approximation of it.

See `dre tile-detect --help` (single tile) and `dre tile-detect-grid
--help` (full grid + §3c merge diagnostics) for the CLI entrypoints.
"""

from __future__ import annotations

import functools
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from dataclasses import dataclass, field

from dre.imaging import rasterize_pdf_tile_to_png
from dre.models.schemas import ExtractedTable, SingleSheetDetectResult
from dre.pipeline.base import PipelineContext
from dre.pipeline.detect_single import DetectSingleStep
from dre.pipeline.tile_merge import TiledDetection
from dre.tiling import DEFAULT_OVERLAP_FRACTION, DEFAULT_TILE_EDGE_PX, TileSpec, compute_tile_grid

# docs/tiled_analysis_findings.md §3f: bounded, not all 20-70 tiles at once —
# both to be a reasonable API citizen (even though the real rate limits
# checked there aren't a binding constraint at this scale) and because
# concurrent-connection behavior on a small Render instance hasn't been
# tested, not confirmed safe by assumption.
DEFAULT_MAX_PARALLEL_TILE_WORKERS = 6


@dataclass(frozen=True)
class GridDetectResult:
    """Every tile's detections (tagged with tile coords, see `TiledDetection`)
    plus every tile's `extracted_tables`, concatenated with no cross-tile
    dedup — that reconciliation is the caller's job (§3c for detections is
    `tile_merge`; table dedup has no design yet, see
    docs/tiled_analysis_findings.md §3c)."""

    tiled_detections: list[TiledDetection] = field(default_factory=list)
    extracted_tables: list[ExtractedTable] = field(default_factory=list)


def run_detect_single_on_tile(
    pdf_bytes: bytes,
    tile: TileSpec,
    *,
    dpi: float,
    usage_sink: Optional[list[dict]] = None,
) -> SingleSheetDetectResult:
    png_bytes = rasterize_pdf_tile_to_png(pdf_bytes, tile, dpi=dpi)
    with tempfile.TemporaryDirectory(prefix="dre_tile_tune_") as tmp:
        tile_path = Path(tmp) / f"tile_r{tile.row}c{tile.col}.png"
        tile_path.write_bytes(png_bytes)
        ctx = PipelineContext(run_id="tile-tune", old_image_path=tile_path, mode="single_sheet")
        if usage_sink is not None:
            ctx.token_usage = usage_sink
        return DetectSingleStep().execute(ctx)


def run_detect_single_on_grid(
    pdf_bytes: bytes,
    *,
    sheet_width_in: float,
    sheet_height_in: float,
    dpi: float,
    tile_edge_px: int = DEFAULT_TILE_EDGE_PX,
    overlap_fraction: float = DEFAULT_OVERLAP_FRACTION,
    max_workers: int = DEFAULT_MAX_PARALLEL_TILE_WORKERS,
    usage_sink: Optional[list[dict]] = None,
) -> GridDetectResult:
    """Runs `run_detect_single_on_tile` against every tile in the grid, in
    parallel via a bounded `ThreadPoolExecutor` (docs/tiled_analysis_
    findings.md §3f) — real API cost scales directly with tile count either
    way (§4; parallel changes wall-clock latency only, not cost or call
    count), so this is still for deliberate harness/calibration use, not
    something to call casually. Real accumulation path for §5's merge-
    threshold calibration data (§3c): there's no production tiled flow to
    passively log from yet, but this makes it possible to gather more real
    known-good/known-miss data points today, ahead of that integration
    existing. Also the function the real production tiled branch
    (`dre.pipeline.tiled_detect`) calls — this is no longer harness-only.

    Threads, not `asyncio`: `anthropic.Anthropic`'s sync client (used by
    every stage via `call_structured`) releases the GIL during its network
    I/O wait like any blocking call, so a plain `ThreadPoolExecutor` gets
    real concurrency here without an async rewrite of the whole call chain.
    `executor.map` preserves input order, so results line up with `tiles`
    positionally without extra bookkeeping — and propagates the first
    exception raised by any tile on iteration, same fail-the-whole-attempt
    semantics the previous sequential loop already had (a tile failure
    still surfaces to `decide_and_apply_tiling`'s existing fallback, not a
    new partial-failure mode to design around).

    `tile_edge_px`/`overlap_fraction` are passed straight through to
    `compute_tile_grid` — kept overridable here for the same reason
    `compute_tile_grid` never defaults `target_dpi`: this harness exists to
    sweep grid parameters against real sheets, not lock them in."""
    tiles = compute_tile_grid(
        sheet_width_in=sheet_width_in,
        sheet_height_in=sheet_height_in,
        target_dpi=dpi,
        tile_edge_px=tile_edge_px,
        overlap_fraction=overlap_fraction,
    )
    worker = functools.partial(run_detect_single_on_tile, pdf_bytes, dpi=dpi, usage_sink=usage_sink)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        tile_results = list(executor.map(worker, tiles))

    tiled_detections: list[TiledDetection] = []
    extracted_tables: list[ExtractedTable] = []
    for tile, result in zip(tiles, tile_results):
        for detection in result.detections:
            tiled_detections.append(
                TiledDetection(tile_row=tile.row, tile_col=tile.col, detection=detection)
            )
        extracted_tables.extend(result.extracted_tables)
    return GridDetectResult(tiled_detections=tiled_detections, extracted_tables=extracted_tables)
