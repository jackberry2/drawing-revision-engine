"""Content-based image handling, shared by the Supabase download path
(`supa.repository.download_drawing_image`) and the Claude-encoding path
(`llm.client.encode_image`) — both two-image and single-sheet mode go
through both of these, so a fix here covers both modes at once.

Real uploads have shown `drawings.file_path`'s extension (and even
Supabase Storage's own reported `mimetype`) can't be trusted to mean the
file is something Claude's vision API will accept: a real E-101.3 upload
came through as an honestly-labeled PDF (Storage correctly reported
`application/pdf` — nothing was lying), but Claude's `image` content block
only accepts image/jpeg, image/png, image/gif, or image/webp, not PDF.
Rather than trust any label at all, every image handed to Claude is
type-sniffed from its actual bytes, and PDFs are rasterized to PNG first.
"""

from __future__ import annotations

_PDF_MAGIC = b"%PDF-"

_MEDIA_TYPE_BY_SIGNATURE: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


def is_pdf(data: bytes) -> bool:
    return data.startswith(_PDF_MAGIC)


def sniff_image_media_type(data: bytes) -> str:
    """Identifies the real image format from file content, never from a
    filename/extension or a storage-reported content-type — both have
    proven unreliable against real uploads. Raises rather than guessing a
    fallback, since silently mislabeling media_type is exactly the bug this
    exists to prevent."""
    for signature, media_type in _MEDIA_TYPE_BY_SIGNATURE:
        if data.startswith(signature):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError(
        "File content is not a recognized image format (png/jpeg/gif/webp) "
        f"— first 16 bytes: {data[:16]!r}"
    )


_EXTENSION_BY_MEDIA_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def extension_for_media_type(media_type: str) -> str:
    return _EXTENSION_BY_MEDIA_TYPE[media_type]


def rasterize_pdf_first_page_to_png(pdf_bytes: bytes, *, dpi: int = 200) -> bytes:
    """Drawing sheets are exported as single-page PDFs in practice; only the
    first page is used. Requires PyMuPDF (pure-pip, no system Poppler
    dependency — matters for a plain `pip install` deploy target)."""
    import fitz  # PyMuPDF — imported lazily so it's only required when a PDF actually shows up

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        zoom = dpi / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return pix.tobytes("png")


def normalize_drawing_bytes(data: bytes) -> tuple[bytes, str]:
    """Given raw downloaded drawing bytes, returns (image_bytes, media_type)
    ready for Claude — rasterizing a PDF's first page if needed. This is the
    single point both modes' image loading goes through."""
    if is_pdf(data):
        png_bytes = rasterize_pdf_first_page_to_png(data)
        return png_bytes, "image/png"
    return data, sniff_image_media_type(data)
