"""Covers dre.imaging.rasterize_pdf_tile_to_png against a synthetic PDF
with known marker content at known coordinates, so crop-boundary
correctness (not just "it doesn't crash") is actually verified: a wrong
region or off-by-one boundary would show up as the marker being absent
from the tile that should contain it, or present in one that shouldn't."""

import io

from PIL import Image

from dre.imaging import rasterize_pdf_tile_to_png
from dre.tiling import compute_tile_grid


def _pdf_with_corner_markers():
    """An 800x600pt page with a solid black square in each quadrant's
    corner, so any given tile's content can be checked against exactly
    which marker(s) it should and shouldn't contain."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=800, height=600)
    markers = {
        "top_left": fitz.Rect(20, 20, 60, 60),
        "top_right": fitz.Rect(740, 20, 780, 60),
        "bottom_left": fitz.Rect(20, 540, 60, 580),
        "bottom_right": fitz.Rect(740, 540, 780, 580),
    }
    for rect in markers.values():
        page.draw_rect(rect, color=(0, 0, 0), fill=(0, 0, 0))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes, markers


def _has_dark_pixels(png_bytes: bytes) -> bool:
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    return img.getextrema()[0] < 50  # a genuinely dark (near-black) pixel is present


def test_tile_containing_a_marker_actually_contains_it():
    pdf_bytes, _ = _pdf_with_corner_markers()
    # 800x600pt = 800/72 x 600/72 in. A single small tile covering roughly
    # the top-left quarter should include the top_left marker.
    tiles = compute_tile_grid(
        sheet_width_in=800 / 72,
        sheet_height_in=600 / 72,
        target_dpi=72,
        tile_edge_px=300,
        overlap_fraction=0.0,
    )
    top_left_tile = next(t for t in tiles if t.row == 0 and t.col == 0)
    png = rasterize_pdf_tile_to_png(pdf_bytes, top_left_tile, dpi=72)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert _has_dark_pixels(png), "top-left tile should contain the top-left marker"


def test_tile_not_containing_a_marker_is_actually_blank():
    pdf_bytes, _ = _pdf_with_corner_markers()
    tiles = compute_tile_grid(
        sheet_width_in=800 / 72,
        sheet_height_in=600 / 72,
        target_dpi=72,
        tile_edge_px=300,
        overlap_fraction=0.0,
    )
    # A tile in the middle of the page shouldn't contain any corner marker.
    rows = sorted({t.row for t in tiles})
    cols = sorted({t.col for t in tiles})
    middle_tile = next(
        t for t in tiles if t.row == rows[len(rows) // 2] and t.col == cols[len(cols) // 2]
    )
    png = rasterize_pdf_tile_to_png(pdf_bytes, middle_tile, dpi=72)
    assert not _has_dark_pixels(png), "a middle tile should not contain any corner marker"


def test_rendered_pixel_dimensions_are_close_to_the_tile_spec():
    pdf_bytes, _ = _pdf_with_corner_markers()
    tiles = compute_tile_grid(
        sheet_width_in=800 / 72, sheet_height_in=600 / 72, target_dpi=150, tile_edge_px=400
    )
    tile = tiles[0]
    png = rasterize_pdf_tile_to_png(pdf_bytes, tile, dpi=150)
    img = Image.open(io.BytesIO(png))
    assert abs(img.width - tile.render_width_px) <= 2
    assert abs(img.height - tile.render_height_px) <= 2


def test_higher_dpi_tile_has_proportionally_more_pixels():
    pdf_bytes, _ = _pdf_with_corner_markers()
    tiles_low = compute_tile_grid(sheet_width_in=800 / 72, sheet_height_in=600 / 72, target_dpi=72)
    tiles_high = compute_tile_grid(sheet_width_in=800 / 72, sheet_height_in=600 / 72, target_dpi=144)
    png_low = rasterize_pdf_tile_to_png(pdf_bytes, tiles_low[0], dpi=72)
    png_high = rasterize_pdf_tile_to_png(pdf_bytes, tiles_high[0], dpi=144)
    img_low = Image.open(io.BytesIO(png_low))
    img_high = Image.open(io.BytesIO(png_high))
    assert img_high.width > img_low.width
    assert img_high.height > img_low.height
