"""Covers the real production bug: a genuinely PDF drawing sheet upload
(E-101.3) was passed straight through to Claude's vision API as an `image`
content block with media_type='application/pdf', which the API rejects
(only image/jpeg, image/png, image/gif, image/webp are accepted). Root
cause was trusting drawings.file_path's extension / Storage's reported
mimetype instead of the actual downloaded bytes."""

import pytest

from dre.imaging import (
    extension_for_media_type,
    is_pdf,
    normalize_drawing_bytes,
    rasterize_pdf_first_page_to_png,
    sniff_image_media_type,
)

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"rest of a fake but real-signature png"
_JPEG_BYTES = b"\xff\xd8\xff" + b"rest of a fake jpeg"
_GIF_BYTES = b"GIF89a" + b"rest of a fake gif"
_WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"rest"
_PDF_BYTES = b"%PDF-1.4\n%%EOF"


def _real_pdf_bytes() -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page(width=200, height=100)
    data = doc.tobytes()
    doc.close()
    return data


def test_sniff_recognizes_png():
    assert sniff_image_media_type(_PNG_BYTES) == "image/png"


def test_sniff_recognizes_jpeg():
    assert sniff_image_media_type(_JPEG_BYTES) == "image/jpeg"


def test_sniff_recognizes_gif():
    assert sniff_image_media_type(_GIF_BYTES) == "image/gif"


def test_sniff_recognizes_webp():
    assert sniff_image_media_type(_WEBP_BYTES) == "image/webp"


def test_sniff_raises_on_pdf_bytes_rather_than_guessing():
    """The exact bug: application/pdf must never silently become a fallback
    'image/png' guess — that would still crash at Claude's API, just with a
    more confusing error since the bytes and claimed type would mismatch."""
    with pytest.raises(ValueError, match="not a recognized image format"):
        sniff_image_media_type(_PDF_BYTES)


def test_sniff_raises_on_unrecognized_content():
    with pytest.raises(ValueError, match="not a recognized image format"):
        sniff_image_media_type(b"not an image at all")


def test_is_pdf_true_for_real_pdf_magic_bytes():
    assert is_pdf(_PDF_BYTES) is True


def test_is_pdf_false_for_png():
    assert is_pdf(_PNG_BYTES) is False


def test_extension_for_media_type_covers_all_sniffable_types():
    assert extension_for_media_type("image/png") == ".png"
    assert extension_for_media_type("image/jpeg") == ".jpg"
    assert extension_for_media_type("image/gif") == ".gif"
    assert extension_for_media_type("image/webp") == ".webp"


def test_rasterize_pdf_first_page_to_png_produces_real_png():
    png_bytes = rasterize_pdf_first_page_to_png(_real_pdf_bytes())
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_rasterize_large_sheet_stays_within_bounded_pixel_dimensions():
    """The real production incident: a fixed 200 DPI on a real 50"x36" (ARCH
    E) sheet produced a 10000x7200px raster (~275MB RGBA pixmap) that
    OOM-killed the Render worker. Large-format sheets are the norm for this
    application, so output must be bounded regardless of the page's
    physical size, not scaled up proportionally to it."""
    import fitz

    doc = fitz.open()
    # 50in x 36in at 72 points/inch, matching the real sheet that crashed
    doc.new_page(width=50 * 72, height=36 * 72)
    large_pdf_bytes = doc.tobytes()
    doc.close()

    png_bytes = rasterize_pdf_first_page_to_png(large_pdf_bytes, max_dimension=2000)

    import io

    from PIL import Image

    with Image.open(io.BytesIO(png_bytes)) as img:
        assert max(img.width, img.height) <= 2000


def test_default_max_dimension_matches_claude_high_resolution_tier_ceiling():
    """2576px is Claude 4.7+'s (claude-sonnet-5, this pipeline's default
    model) actual native long-edge ceiling per Anthropic's vision docs — not
    an arbitrary safety margin. An earlier version of this default (2000)
    was below that ceiling, throwing away resolution the model could
    already use for free. Sending anything past 2576 buys nothing since
    Claude resizes down server-side regardless, so the default should sit
    exactly at that number, not above or below it."""
    import fitz

    doc = fitz.open()
    doc.new_page(width=50 * 72, height=36 * 72)
    large_pdf_bytes = doc.tobytes()
    doc.close()

    png_bytes = rasterize_pdf_first_page_to_png(large_pdf_bytes)

    import io

    from PIL import Image

    with Image.open(io.BytesIO(png_bytes)) as img:
        assert max(img.width, img.height) == 2576


def test_normalize_drawing_bytes_rasterizes_pdf():
    image_bytes, media_type = normalize_drawing_bytes(_real_pdf_bytes())
    assert media_type == "image/png"
    assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_normalize_drawing_bytes_passes_through_real_image_unchanged():
    image_bytes, media_type = normalize_drawing_bytes(_PNG_BYTES)
    assert media_type == "image/png"
    assert image_bytes == _PNG_BYTES
