"""Tile grid math for docs/tiled_analysis_findings.md §3b — splitting a
large sheet into overlapping regions so each region can hit Claude's
per-image resolution ceiling against a much smaller physical area than the
full sheet, multiplying effective DPI.

Pure geometry only: computing where tiles go and how big to render them.
Does not rasterize anything or call Claude — see `imaging.py` for
rasterization and (once built) the tiling tuning harness for wiring this
into an actual detect pass.

`target_dpi` is deliberately always a parameter here, never a module
constant — per §3b, empirical testing against real sheets is the only
remaining route to a validated DPI number (see §2), so nothing in this
codebase should hardcode a guess.
"""

from __future__ import annotations

import math

from pydantic import BaseModel

from dre.models.schemas import BBox

# Safe per-tile pixel ceiling for a square-ish tile under Claude's
# high-resolution tier: jointly capped by 2576px max long edge AND 4784
# max visual tokens (28x28px patches, ~3,750,656 px^2 total). A square tile
# hitting 2576px on both sides would need ~8,464 tokens, 77% over budget;
# the real ceiling is sqrt(3,750,656) =~ 1936.7px. 1900px leaves margin
# below that theoretical boundary since the token formula ceils
# (width/28)*(height/28), so a render right at the boundary risks tipping
# over after rounding. See docs/tiled_analysis_findings.md §3b.
DEFAULT_TILE_EDGE_PX = 1900

# Proposed 15-20% overlap so a revision cloud sitting on a tile boundary
# isn't split in half or missed by both tiles (§3b). Not yet validated
# against a real overlapping-boundary case (§5) - a reasonable starting
# point, not a tuned value.
DEFAULT_OVERLAP_FRACTION = 0.15


class TileSpec(BaseModel):
    row: int
    col: int
    # Normalized (0-1) region of the full sheet this tile covers, in the
    # same BBox convention used elsewhere in the pipeline for detection
    # regions - so a tile-local detection's coordinates can be remapped to
    # full-sheet space with the same kind of linear transform.
    region: BBox
    render_width_px: int
    render_height_px: int


def _axis_tile_starts(total_px: float, tile_edge_px: int, core_px: float) -> list[float]:
    """Pixel start-positions for tiles along one axis. Consecutive tiles
    are spaced by `core_px` (the non-overlapping "core" advance per tile);
    the final tile is pulled back to end exactly at the sheet's far edge
    so it stays full-size rather than shrinking into a sliver, at the cost
    of slightly more overlap with its predecessor than `core_px` implies.
    A known simplification for a first pass, not a final algorithm - see
    docs/tiled_analysis_findings.md §5 on real overlapping-boundary edge
    cases still needing a spike."""
    if total_px <= tile_edge_px:
        return [0.0]
    starts = []
    pos = 0.0
    while pos + tile_edge_px < total_px:
        starts.append(pos)
        pos += core_px
    starts.append(total_px - tile_edge_px)
    return starts


def compute_tile_grid(
    *,
    sheet_width_in: float,
    sheet_height_in: float,
    target_dpi: float,
    tile_edge_px: int = DEFAULT_TILE_EDGE_PX,
    overlap_fraction: float = DEFAULT_OVERLAP_FRACTION,
) -> list[TileSpec]:
    """Computes the tile grid for a sheet at a given target DPI. Pure
    function of sheet size, DPI, and the two tuning constants above - no
    I/O, no model calls, fully deterministic and unit-testable.

    `target_dpi` must be supplied by the caller, not defaulted here (see
    module docstring) - the tuning harness this function exists to support
    is specifically about sweeping this value against real sheets, not
    locking one in.
    """
    if sheet_width_in <= 0 or sheet_height_in <= 0:
        raise ValueError("sheet dimensions must be positive")
    if target_dpi <= 0:
        raise ValueError("target_dpi must be positive")
    if not (0 <= overlap_fraction < 1):
        raise ValueError("overlap_fraction must be in [0, 1)")

    total_width_px = sheet_width_in * target_dpi
    total_height_px = sheet_height_in * target_dpi
    core_px = tile_edge_px * (1 - overlap_fraction)

    col_starts = _axis_tile_starts(total_width_px, tile_edge_px, core_px)
    row_starts = _axis_tile_starts(total_height_px, tile_edge_px, core_px)

    tiles: list[TileSpec] = []
    for row, y0_px in enumerate(row_starts):
        tile_height_px = min(tile_edge_px, total_height_px)
        for col, x0_px in enumerate(col_starts):
            tile_width_px = min(tile_edge_px, total_width_px)
            tiles.append(
                TileSpec(
                    row=row,
                    col=col,
                    region=BBox(
                        x=x0_px / total_width_px,
                        y=y0_px / total_height_px,
                        width=tile_width_px / total_width_px,
                        height=tile_height_px / total_height_px,
                    ),
                    render_width_px=math.ceil(tile_width_px),
                    render_height_px=math.ceil(tile_height_px),
                )
            )
    return tiles
