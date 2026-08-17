"""Tuning harness for docs/tiled_analysis_findings.md §3b/§5 — wires
`compute_tile_grid` + `rasterize_pdf_tile_to_png` into a real `detect_single`
call against one tile, so candidate DPI values can be tested against real
sheets without going through the full Supabase-backed `analyze_request`
path. Runs the actual production `DetectSingleStep`, not a
reimplementation, so this tests exactly what a real tiled analysis would
produce for that tile — not an approximation of it.

See `dre tile-detect --help` for the CLI entrypoint.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from dre.imaging import rasterize_pdf_tile_to_png
from dre.models.schemas import SingleSheetDetectResult
from dre.pipeline.base import PipelineContext
from dre.pipeline.detect_single import DetectSingleStep
from dre.tiling import TileSpec


def run_detect_single_on_tile(
    pdf_bytes: bytes, tile: TileSpec, *, dpi: float
) -> SingleSheetDetectResult:
    png_bytes = rasterize_pdf_tile_to_png(pdf_bytes, tile, dpi=dpi)
    with tempfile.TemporaryDirectory(prefix="dre_tile_tune_") as tmp:
        tile_path = Path(tmp) / f"tile_r{tile.row}c{tile.col}.png"
        tile_path.write_bytes(png_bytes)
        ctx = PipelineContext(run_id="tile-tune", old_image_path=tile_path, mode="single_sheet")
        return DetectSingleStep().execute(ctx)
