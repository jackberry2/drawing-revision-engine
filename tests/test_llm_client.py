import base64

import pytest

from dre.llm.client import encode_image


def test_encode_image_sniffs_media_type_from_content_not_extension(tmp_path):
    """A file's extension must never decide media_type — a real production
    bug had a genuinely-PDF file pass straight through with
    media_type='application/pdf', which Claude's API rejects. Here a file
    named with a misleading .txt extension but containing real PNG bytes
    must still be correctly identified as image/png."""
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"rest of a fake but real-signature png"
    path = tmp_path / "sheet.txt"
    path.write_bytes(png_bytes)

    result = encode_image(path)

    assert result["source"]["media_type"] == "image/png"
    assert result["source"]["data"] == base64.standard_b64encode(png_bytes).decode("utf-8")


def test_encode_image_raises_on_pdf_instead_of_sending_bad_media_type(tmp_path):
    path = tmp_path / "sheet.pdf"
    path.write_bytes(b"%PDF-1.4\n%%EOF")

    with pytest.raises(ValueError, match="not a recognized image format"):
        encode_image(path)
