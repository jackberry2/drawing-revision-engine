"""Covers dre.tiling.compute_tile_grid - pure geometry for
docs/tiled_analysis_findings.md §3b. No I/O, no model calls."""

import pytest

from dre.tiling import DEFAULT_TILE_EDGE_PX, compute_tile_grid


def test_small_sheet_fits_in_a_single_tile():
    """A letter/tabloid-size sheet at a modest DPI shouldn't be split at
    all - real §3a behavior is to not tile these in the first place, but
    the grid math itself should also degrade gracefully to one tile."""
    tiles = compute_tile_grid(sheet_width_in=8.5, sheet_height_in=11, target_dpi=150)
    assert len(tiles) == 1
    t = tiles[0]
    assert t.region.x == 0 and t.region.y == 0
    assert t.region.width == 1.0 and t.region.height == 1.0
    assert t.render_width_px == round(8.5 * 150)
    assert t.render_height_px == round(11 * 150)


def test_real_e101_sheet_at_interim_default_dpi_produces_expected_grid():
    """The real 50x36in sheet size (E-101.2/E-101.3) at 100 DPI - the low
    end of §2's interim 100-150 DPI tuning range - should produce a 3x3
    grid (9 tiles) under the default 1900px tile ceiling / 15% overlap."""
    tiles = compute_tile_grid(sheet_width_in=50, sheet_height_in=36, target_dpi=100)
    rows = {t.row for t in tiles}
    cols = {t.col for t in tiles}
    assert len(rows) == 3
    assert len(cols) == 3
    assert len(tiles) == 9


def test_higher_dpi_produces_more_tiles():
    """Tile count should scale up with DPI - directly checks the
    steep-cost-curve finding from §3b/§4 holds in the actual implementation,
    not just the hand math in the doc."""
    low = compute_tile_grid(sheet_width_in=50, sheet_height_in=36, target_dpi=100)
    high = compute_tile_grid(sheet_width_in=50, sheet_height_in=36, target_dpi=150)
    assert len(high) > len(low)


def test_grid_fully_covers_the_sheet_with_no_gaps():
    """Every point on the sheet must fall inside at least one tile's
    region - sampled on a fine grid across normalized (0-1) sheet space."""
    tiles = compute_tile_grid(sheet_width_in=50, sheet_height_in=36, target_dpi=133)

    def covered(x: float, y: float) -> bool:
        return any(
            t.region.x <= x <= t.region.x + t.region.width
            and t.region.y <= y <= t.region.y + t.region.height
            for t in tiles
        )

    steps = 25
    for i in range(steps + 1):
        for j in range(steps + 1):
            x, y = i / steps, j / steps
            assert covered(x, y), f"gap in tile coverage at ({x}, {y})"


def test_adjacent_tiles_overlap():
    """§3b's overlap requirement - column-adjacent tiles in the same row
    must share real (non-zero-width) horizontal overlap, so a revision
    cloud on a boundary isn't split or missed by both tiles."""
    tiles = compute_tile_grid(sheet_width_in=50, sheet_height_in=36, target_dpi=150)
    row0_cols = sorted((t for t in tiles if t.row == 0), key=lambda t: t.col)
    assert len(row0_cols) >= 2
    for a, b in zip(row0_cols, row0_cols[1:]):
        overlap = (a.region.x + a.region.width) - b.region.x
        assert overlap > 0, "adjacent tiles in the same row must overlap"


def test_no_overlap_when_overlap_fraction_is_zero():
    tiles = compute_tile_grid(
        sheet_width_in=50, sheet_height_in=36, target_dpi=100, overlap_fraction=0.0
    )
    row0_cols = sorted((t for t in tiles if t.row == 0), key=lambda t: t.col)
    for a, b in zip(row0_cols, row0_cols[1:-1]):  # exclude the pulled-back final tile
        overlap = (a.region.x + a.region.width) - b.region.x
        assert overlap == pytest.approx(0.0, abs=1e-9)


def test_target_dpi_has_no_default_and_must_be_supplied():
    """§2's whole conclusion is that no DPI number is validated yet - this
    module must never silently default to one."""
    import inspect

    sig = inspect.signature(compute_tile_grid)
    assert sig.parameters["target_dpi"].default is inspect.Parameter.empty


def test_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        compute_tile_grid(sheet_width_in=0, sheet_height_in=36, target_dpi=100)
    with pytest.raises(ValueError):
        compute_tile_grid(sheet_width_in=50, sheet_height_in=36, target_dpi=0)
    with pytest.raises(ValueError):
        compute_tile_grid(sheet_width_in=50, sheet_height_in=36, target_dpi=100, overlap_fraction=1.0)


def test_default_tile_edge_matches_documented_safe_ceiling():
    assert DEFAULT_TILE_EDGE_PX == 1900
