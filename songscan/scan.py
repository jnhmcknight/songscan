"""Scanning a PDF (its own text first, which is free and exact, and Claude's
reading of its pages when that isn't plainly a chord chart), or an image of
a song (a photo or a scan: Claude's reading).

Sheet music is read in one go by default. To read it in steps, each of which
can be run on its own (as where a process may only run so long), scan with
transcribe=False, then call transcribe_score (once, or twice when the first
attempt gives back a `retry`) and complete_score:

    result = scan(data, transcribe=False)
    if result.transcribe_pending:
        attempt = transcribe_score(data)
        if attempt.musicxml is None:
            attempt = transcribe_score(data, retry=attempt.retry)
        complete_score(result, attempt.musicxml)
"""

import anthropic

from .image import is_image, prepare_image
from .models import ScanError, ScanResult, Transcription
from .text import chart_from_text, read_text
from .vision import DEFAULT_MODEL, Content, image_content, pdf_content, read_with_vision, transcribe_content


def scan(
    data: bytes,
    *,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    transcribe: bool = True,
) -> ScanResult:
    """What the file *data* holds, a PDF or an image, told apart by what is
    in it (as scan_pdf and scan_image read them). Raises ScanError when it is
    neither, or can't be read."""
    if _is_pdf(data):
        return scan_pdf(data, client=client, model=model, transcribe=transcribe)
    if is_image(data):
        return scan_image(data, client=client, model=model, transcribe=transcribe)
    raise _neither()


def transcribe_score(
    data: bytes,
    *,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    retry: dict | None = None,
) -> Transcription:
    """One attempt at the MusicXML of the sheet music in *data* (a PDF or an
    image, as `scan` reads it with transcribe=False): its `musicxml`, or, when
    what Claude wrote can't be read, a `retry` to pass to a second attempt,
    which gives MusicXML or raises. Raises ScanError when it can't be written."""
    return transcribe_content(_content(data), client or anthropic.Anthropic(), model, retry)


def _content(data: bytes) -> Content:
    """What Claude is sent of *data*, a PDF or an image."""
    if _is_pdf(data):
        return pdf_content(data)
    if is_image(data):
        return image_content(prepare_image(data))
    raise _neither()


def _is_pdf(data: bytes) -> bool:
    # Readers allow a little before the header, as some PDFs have
    return b"%PDF-" in data[:1024]


def _neither() -> ScanError:
    return ScanError("This isn't a PDF or an image that can be read.")


def scan_pdf(
    data: bytes,
    *,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    transcribe: bool = True,
) -> ScanResult:
    """What the PDF *data* holds: a chord chart (as ChordPro), sheet music, or
    something else. *client* reads scans and sheet music; by default, one
    with the environment's API key. Sheet music comes with its MusicXML,
    unless not *transcribe* (then it is `transcribe_pending`: see
    transcribe_score). Raises ScanError when it can't be read.
    """
    text = read_text(data)
    if text.has_text and not text.music_font and (chart := chart_from_text(text.lines)):
        chordpro, meta = chart
        return ScanResult(kind="chord_chart", meta=meta, pages=text.pages, source="text", chordpro=chordpro)
    return read_with_vision(pdf_content(data), text.pages, client or anthropic.Anthropic(), model, transcribe)


def scan_image(
    data: bytes,
    *,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    transcribe: bool = True,
) -> ScanResult:
    """What the image *data* holds (a photo or scan of a page, or of each
    page, as a multi-page TIFF): as scan_pdf, but always read by Claude.
    Raises ScanError when it isn't an image, or can't be read."""
    images = prepare_image(data)
    return read_with_vision(
        image_content(images), len(images), client or anthropic.Anthropic(), model, transcribe
    )
