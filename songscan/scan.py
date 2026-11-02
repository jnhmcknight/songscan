"""Scanning a PDF: its own text first, which is free and exact, and Claude's
reading of its pages when that isn't plainly a chord chart."""

import anthropic

from .models import ScanResult
from .text import chart_from_text, read_text
from .vision import DEFAULT_MODEL, read_with_vision


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
    return read_with_vision(data, text.pages, client or anthropic.Anthropic(), model)
