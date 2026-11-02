"""Reading a PDF by looking at its pages, with Claude: for scans and photos of
charts, which have no text of their own, and to tell what a PDF is when its
text isn't plainly a chord chart.
"""

import base64
import re
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


SCORE_SYSTEM = """\
You transcribe sheet music into MusicXML 4.0 (score-partwise), for a church worship team's song \
library. Musicians will play from what you write, and it will be moved into other keys, so write \
exactly what the page shows:

- Every part and staff, with its clef, key signature and time signature, and their changes.
- Every note and rest, with its pitch and accidentals as written, duration, ties, dots, tuplets \
and voices; measures numbered as the page numbers them, pickup measures included.
- Chord symbols as <harmony> elements, on the beat they are written over.
- Lyrics under their notes, with <syllabic> marking words split across notes, and each verse as \
its own lyric number.
- Repeats, endings, D.C./D.S./coda marks, tempo marks and dynamics as written.
- The title, composers and lyricists in <work>/<identification>, and any copyright in <rights>.

Write the MusicXML only, from <?xml to </score-partwise>, without comments or explanation.
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
        _read_score(result, document, client, model)
    return result


def _read_score(
    result: ScanResult, document: BetaRequestDocumentBlockParam, client: anthropic.Anthropic, model: str
) -> None:
    """Fill in *result*, sheet music, with its MusicXML (and, from that, the
    details the PDF left out, and a lead sheet's chord chart); or a warning
    saying why it couldn't be."""
    try:
        from . import score  # music21 and verovio: the "score" extra
    except ImportError:
        result.warnings.append('Reading sheet music needs songscan\'s "score" extra.')
        return
    try:
        musicxml = read_musicxml(document, client, model)
    except ScanError as error:
        if error.retryable:
            raise
        result.warnings.append(f"The sheet music couldn't be read: {error}")
        return

    parsed = score.load(musicxml)
    result.musicxml = musicxml
    found = score.score_meta(parsed)
    meta = result.meta
    for name in ("title", "key", "tempo", "time"):
        if not getattr(meta, name):
            setattr(meta, name, getattr(found, name))
    meta.artists = meta.artists or found.artists
    meta.copyright = meta.copyright or found.copyright
    if chart := score.score_chordpro(parsed):
        result.chordpro = "\n".join([*meta.directives(), "", chart])


def read_musicxml(document: BetaRequestDocumentBlockParam, client: anthropic.Anthropic, model: str) -> str:
    """The MusicXML of the sheet music in *document* (a PDF of it), tidied
    up. Claude writes it again, once, if what it wrote can't be read."""
    from . import score

    messages: list[BetaMessageParam] = [
        {"role": "user", "content": [document, {"type": "text", "text": "Transcribe this sheet music."}]}
    ]
    for attempt in range(2):
        response = _stream(messages, client, model)
        text = "".join(block.text for block in response.content if block.type == "text")
        found = _MUSICXML_RE.search(text)
        try:
            if found is None:
                raise ScanError("No MusicXML came back.")
            return score.clean(found.group(0))
        except ScanError as error:
            if attempt:
                raise
            messages += [
                {
                    "role": "assistant",
                    "content": [block.model_dump(exclude_none=True) for block in response.content],
                },
                {
                    "role": "user",
                    "content": f"That MusicXML couldn't be read ({error}). Write the whole score again, "
                    "as complete, valid MusicXML.",
                },
            ]
    raise AssertionError("unreachable")


_MUSICXML_RE = re.compile(
    r"(<\?xml[^>]*>\s*)?(<!DOCTYPE[^>]*>\s*)?<score-partwise\b.*?</score-partwise>", re.DOTALL
)


def _stream(messages: list[BetaMessageParam], client: anthropic.Anthropic, model: str):
    """Claude's answer, streamed: a score can take tens of thousands of
    tokens, more than a request may wait for."""
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=128000,
            system=SCORE_SYSTEM,
            messages=messages,
            output_config={"effort": "high"},
            fallbacks="default",
            betas=[_FALLBACK_BETA],
        ) as stream:
            response = stream.get_final_message()
    except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError) as error:
        raise ScanError(f"The sheet music couldn't be read just now: {error}", retryable=True) from error
    except anthropic.APIStatusError as error:
        raise ScanError(error.message) from error
    if response.stop_reason == "refusal":
        raise ScanError("reading it was declined.")
    if response.stop_reason == "max_tokens":
        raise ScanError("it is too long.")
    return response
