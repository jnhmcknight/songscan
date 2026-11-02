"""
songscan
~~~~~~~~

Read songs out of PDFs: chord charts become ChordPro, and sheet music
MusicXML.

Usage::

    from songscan import scan_pdf

    result = scan_pdf(pdf_bytes)
    if result.kind == "chord_chart":
        save(result.chordpro, key=result.meta.key)
"""

from .models import ScanError, ScanMeta, ScanResult
from .scan import scan_pdf
from .vision import DEFAULT_MODEL

__all__ = ["DEFAULT_MODEL", "ScanError", "ScanMeta", "ScanResult", "scan_pdf"]
