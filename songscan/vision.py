"""Reading a PDF by looking at its pages, with Claude: for scans and photos of
charts, which have no text of their own, and to tell what a PDF is when its
text isn't plainly a chord chart.
"""

import base64
from typing import Literal

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaRequestDocumentBlockParam
from pydantic import BaseModel, Field

from .models import ScanError, ScanMeta, ScanResult

DEFAULT_MODEL = "claude-opus-5-5"

# Falls back to another model, picked by why, should the model decline
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """\
You read PDFs of songs for a church worship team's song library, and turn each into data the library \
can store. Musicians will play from what you write, so accuracy matters more than completeness.

First, decide what the PDF is:
- chord_chart: lyrics with chord names (above the words, or inline), or a chart of chords alone.
- score: sheet music, with notes on staves: lead sheets, piano/vocal, choral or instrumental parts.
- other: anything else, such as lyrics without chords.

For a chord_chart, write the song in ChordPro as `chordpro`:
- Put each chord in square brackets just before the syllable it is played on: "[G]Amazing [C]grace". \
Use the chord names exactly as written.
- Wrap each section in a start and end directive with the chart's own label, such as \
"{start_of_verse: Verse 1}" ... "{end_of_verse}". The kinds are verse, chorus, prechorus, bridge, \
intro, outro, tag, interlude and instrumental.
- A line of chords with no lyrics stays a line of bracketed chords: "[G] [C] [D]".
- Instructions to the band, such as "Repeat x2" or "Build", become "{comment: Repeat x2}".
- Copy what the PDF shows, as it shows it: don't correct, complete, transpose or reorder anything, \
and write out a section again only where the PDF does.
- Leave the title, artists, key, tempo, time signature, capo and copyright out of `chordpro`: \
they have fields of their own.
For a score or other, `chordpro` is null.

Fill in the song's details only from what the PDF shows, and null (or an empty list) for anything \
it doesn't: the title, the artists or writers, the key (like "G", "B♭" or "F♯m"), the tempo in beats \
per minute (digits only), the time signature (like "4/4"), the capo fret (digits only), and the \
copyright lines, one per line, including any CCLI song number.
"""


class _Song(BaseModel):
    """What the model makes of a PDF."""

    kind: Literal["chord_chart", "score", "other"]
    title: str | None
    artists: list[str]
    key: str | None
    tempo: str | None
    time: str | None
    capo: str | None
    copyright: list[str]
    chordpro: str | None = Field(description="The chord chart in ChordPro, without its details.")


def read_with_vision(data: bytes, pages: int, client: anthropic.Anthropic, model: str) -> ScanResult:
    """What Claude makes of the PDF *data* (of *pages* pages)."""
    document: BetaRequestDocumentBlockParam = {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.b64encode(data).decode(),
        },
    }
    message: BetaMessageParam = {
        "role": "user",
        "content": [document, {"type": "text", "text": "Read this PDF."}],
    }
    try:
        response = client.beta.messages.parse(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[message],
            output_format=_Song,
            output_config={"effort": "medium"},
            fallbacks="default",
            betas=[_FALLBACK_BETA],
        )
    except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError) as error:
        raise ScanError(f"The PDF couldn't be read just now: {error}", retryable=True) from error
    except anthropic.APIStatusError as error:
        raise ScanError(f"The PDF couldn't be read: {error.message}") from error

    if response.stop_reason == "refusal":
        raise ScanError("The PDF couldn't be read: reading it was declined.")
    if response.stop_reason == "max_tokens":
        raise ScanError("The PDF couldn't be read: it is too long.")
    song = response.parsed_output
    if song is None:
        raise ScanError("The PDF couldn't be read: no song came back.")

    meta = ScanMeta(
        title=song.title,
        artists=song.artists,
        key=song.key,
        tempo=song.tempo,
        time=song.time,
        capo=song.capo,
        copyright=song.copyright,
    )
    result = ScanResult(kind=song.kind, meta=meta, pages=pages, source="vision")
    if song.kind == "chord_chart" and song.chordpro:
        result.chordpro = "\n".join([*meta.directives(), "", song.chordpro.strip()])
    elif song.kind == "chord_chart":
        result.warnings.append("No chords or lyrics could be read from this chart.")
    elif song.kind == "score":
        result.warnings.append("Sheet music isn't read into MusicXML yet.")
    return result
