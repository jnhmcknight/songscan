"""Scanning a PDF (its own text first, which is free and exact, and Claude's
reading of its pages when that isn't plainly a chord chart), or an image of
a song (a photo or a scan: Claude's reading)."""

import anthropic

from .image import is_image, prepare_image
from .models import ScanError, ScanResult
from .text import chart_from_text, read_text
from .vision import DEFAULT_MODEL, image_content, pdf_content, read_with_vision


def scan(data: bytes, *, client: anthropic.Anthropic | None = None, model: str = DEFAULT_MODEL) -> ScanResult:
    """What the file *data* holds, a PDF or an image, told apart by what is
    in it (as scan_pdf and scan_image read them). Raises ScanError when it is
    neither, or can't be read."""
    # Readers allow a little before the header, as some PDFs have
    if b"%PDF-" in data[:1024]:
        return scan_pdf(data, client=client, model=model)
    if is_image(data):
        return scan_image(data, client=client, model=model)
    raise ScanError("This isn't a PDF or an image that can be read.")


def scan_pdf(
    data: bytes, *, client: anthropic.Anthropic | None = None, model: str = DEFAULT_MODEL
) -> ScanResult:
    """What the PDF *data* holds: a chord chart (as ChordPro), sheet music, or
    something else. *client* reads scans and sheet music; by default, one
    with the environment's API key. Raises ScanError when it can't be read.
    """
    text = read_text(data)
    if text.has_text and not text.music_font and (chart := chart_from_text(text.lines)):
        chordpro, meta = chart
        return ScanResult(kind="chord_chart", meta=meta, pages=text.pages, source="text", chordpro=chordpro)
    return read_with_vision(pdf_content(data), text.pages, client or anthropic.Anthropic(), model)


def scan_image(
    data: bytes, *, client: anthropic.Anthropic | None = None, model: str = DEFAULT_MODEL
) -> ScanResult:
    """What the image *data* holds (a photo or scan of a page, or of each
    page, as a multi-page TIFF): as scan_pdf, but always read by Claude.
    Raises ScanError when it isn't an image, or can't be read."""
    images = prepare_image(data)
    return read_with_vision(image_content(images), len(images), client or anthropic.Anthropic(), model)
