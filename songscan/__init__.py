"""
songscan
~~~~~~~~

Read songs out of PDFs and images (photos and scans): chord charts become
ChordPro, and sheet music MusicXML.

Usage::

    from songscan import scan

    result = scan(file_bytes)  # or scan_pdf / scan_image, to say which it is
    if result.kind == "chord_chart":
        save(result.chordpro, key=result.meta.key)
"""

from .models import ScanError, ScanMeta, ScanResult, Transcription
from .scan import scan, scan_image, scan_pdf, transcribe_score
from .vision import DEFAULT_MODEL, complete_score

__all__ = [
    "DEFAULT_MODEL",
    "ScanError",
    "ScanMeta",
    "ScanResult",
    "Transcription",
    "complete_score",
    "scan",
    "scan_image",
    "scan_pdf",
    "transcribe_score",
]
