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

import tempfile
from pathlib import Path

from dataclasses import dataclass, field

from dre.imaging import rasterize_pdf_tile_to_png
from dre.models.schemas import ExtractedTable, SingleSheetDetectResult
from dre.pipeline.base import PipelineContext
from dre.pipeline.detect_single import DetectSingleStep
from dre.pipeline.tile_merge import TiledDetection
from dre.tiling import DEFAULT_OVERLAP_FRACTION, DEFAULT_TILE_EDGE_PX, TileSpec, compute_tile_grid


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
    pdf_bytes: bytes, tile: TileSpec, *, dpi: float
) -> SingleSheetDetectResult:
    png_bytes = rasterize_pdf_tile_to_png(pdf_bytes, tile, dpi=dpi)
    with tempfile.TemporaryDirectory(prefix="dre_tile_tune_") as tmp:
        tile_path = Path(tmp) / f"tile_r{tile.row}c{tile.col}.png"
        tile_path.write_bytes(png_bytes)
        ctx = PipelineContext(run_id="tile-tune", old_image_path=tile_path, mode="single_sheet")
        return DetectSingleStep().execute(ctx)


def run_detect_single_on_grid(
    pdf_bytes: bytes,
    *,
    sheet_width_in: float,
    sheet_height_in: float,
    dpi: float,
    tile_edge_px: int = DEFAULT_TILE_EDGE_PX,
    overlap_fraction: float = DEFAULT_OVERLAP_FRACTION,
) -> GridDetectResult:
    """Runs `run_detect_single_on_tile` against every tile in the grid,
    sequentially — real API cost scales directly with tile count (§4), so
    this is for deliberate harness/calibration use, not something to call
    casually or automatically. Real accumulation path for §5's merge-
    threshold calibration data (§3c): there's no production tiled flow to
    passively log from yet, but this makes it possible to gather more real
    known-good/known-miss data points today, ahead of that integration
    existing. Also the function the real production tiled branch
    (`dre.pipeline.tiled_detect`) calls — this is no longer harness-only.

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
    tiled_detections: list[TiledDetection] = []
    extracted_tables: list[ExtractedTable] = []
    for tile in tiles:
        result = run_detect_single_on_tile(pdf_bytes, tile, dpi=dpi)
        for detection in result.detections:
            tiled_detections.append(
                TiledDetection(tile_row=tile.row, tile_col=tile.col, detection=detection)
            )
        extracted_tables.extend(result.extracted_tables)
    return GridDetectResult(tiled_detections=tiled_detections, extracted_tables=extracted_tables)
