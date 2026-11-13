"""Reading a PDF or images by looking at their pages, with Claude: for scans
and photos of charts, which have no text of their own, and to tell what a PDF
is when its text isn't plainly a chord chart.
"""

import base64
import re
from typing import Literal

import anthropic
from anthropic.types.beta import BetaImageBlockParam, BetaMessageParam, BetaRequestDocumentBlockParam
from pydantic import BaseModel, Field

from .image import PreparedImage
from .models import ScanError, ScanMeta, ScanResult, Transcription

DEFAULT_MODEL = "claude-opus-5-5"

# Falls back to another model, picked by why, should the model decline
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """\
You read songs for a church worship team's song library, from a PDF or from images of the pages \
(photos or scans), and turn each into data the library can store. Musicians will play from what you \
write, so accuracy matters more than completeness.

First, decide what the song's pages are:
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
- Copy what the pages show, as they show it: don't correct, complete, transpose or reorder anything, \
and write out a section again only where the pages do.
- Leave the title, artists, key, tempo, time signature, capo and copyright out of `chordpro`: \
they have fields of their own.
For a score or other, `chordpro` is null.

Fill in the song's details only from what the pages show, and null (or an empty list) for anything \
they don't: the title, the artists or writers, the key (like "G", "B♭" or "F♯m"), the tempo in beats \
per minute (digits only), the time signature (like "4/4"), the capo fret (digits only), and the \
copyright lines, one per line, including any CCLI song number.
"""


SCORE_SYSTEM = """\
You transcribe sheet music into MusicXML 4.0 (score-partwise), for a church worship team's song \
library. Musicians will play from what you write, and it will be moved into other keys, so write \
exactly what the page shows, reading past any skew, shadows or glare in a photo or scan of it:

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
    """What the model makes of a song's pages."""

    kind: Literal["chord_chart", "score", "other"]
    title: str | None
    artists: list[str]
    key: str | None
    tempo: str | None
    time: str | None
    capo: str | None
    copyright: list[str]
    chordpro: str | None = Field(description="The chord chart in ChordPro, without its details.")


# What Claude is sent of a song: a PDF, or an image of each page
Content = list[BetaRequestDocumentBlockParam | BetaImageBlockParam]


def pdf_content(data: bytes) -> Content:
    return [
        {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": _base64(data)},
        }
    ]


def image_content(images: list[PreparedImage]) -> Content:
    return [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": image.media_type, "data": _base64(image.data)},
        }
        for image in images
    ]


def _base64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def read_with_vision(
    content: Content, pages: int, client: anthropic.Anthropic, model: str, transcribe: bool = True
) -> ScanResult:
    """What Claude makes of the song in *content* (of *pages* pages): a PDF,
    or images of its pages. Sheet music is written out as MusicXML too, unless
    not *transcribe*: then it comes back `transcribe_pending`."""
    is_pdf = content[0]["type"] == "document"
    what = "PDF" if is_pdf else "image"
    ask = "Read this PDF." if is_pdf else "Read the song in these images."
    message: BetaMessageParam = {"role": "user", "content": [*content, {"type": "text", "text": ask}]}
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
        raise ScanError(f"The {what} couldn't be read just now: {error}", retryable=True) from error
    except anthropic.APIStatusError as error:
        raise ScanError(f"The {what} couldn't be read: {error.message}") from error

    if response.stop_reason == "refusal":
        raise ScanError(f"The {what} couldn't be read: reading it was declined.")
    if response.stop_reason == "max_tokens":
        raise ScanError(f"The {what} couldn't be read: it is too long.")
    song = response.parsed_output
    if song is None:
        raise ScanError(f"The {what} couldn't be read: no song came back.")

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
    elif song.kind == "score" and _has_score_extra(result):
        if transcribe:
            _read_score(result, content, client, model)
        else:
            result.transcribe_pending = True
    return result


def _has_score_extra(result: ScanResult) -> bool:
    """Whether sheet music can be read here (the "score" extra); if not, *result* says so."""
    try:
        from . import score  # noqa: F401  (music21 and verovio)
    except ImportError:
        result.warnings.append(_NO_SCORE_EXTRA)
        return False
    return True


_NO_SCORE_EXTRA = 'Reading sheet music needs songscan\'s "score" extra.'


def _read_score(result: ScanResult, content: Content, client: anthropic.Anthropic, model: str) -> None:
    """Fill in *result*, sheet music, with its MusicXML (and, from that, the
    details its pages left out, and a lead sheet's chord chart); or a warning
    saying why it couldn't be."""
    try:
        musicxml = read_musicxml(content, client, model)
    except ScanError as error:
        if error.retryable:
            raise
        result.warnings.append(f"The sheet music couldn't be read: {error}")
        return
    complete_score(result, musicxml)


def complete_score(result: ScanResult, musicxml: str) -> ScanResult:
    """*result*, sheet music, with *musicxml* (as transcribe_score wrote it)
    as its MusicXML, and, from that, the details its pages left out and a
    lead sheet's chord chart."""
    from . import score

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
    result.transcribe_pending = False
    return result


def read_musicxml(content: Content, client: anthropic.Anthropic, model: str) -> str:
    """The MusicXML of the sheet music in *content* (a PDF of it, or images
    of its pages), tidied up. Claude writes it again, once, if what it wrote can't be read."""
    attempt = transcribe_content(content, client, model)
    if attempt.musicxml is None:
        attempt = transcribe_content(content, client, model, retry=attempt.retry)
    assert attempt.musicxml is not None  # the second attempt raises instead
    return attempt.musicxml


def transcribe_content(
    content: Content, client: anthropic.Anthropic, model: str, retry: dict | None = None
) -> Transcription:
    """One attempt at the MusicXML of the sheet music in *content*: tidied up,
    or, if what Claude wrote can't be read, `retry`, for a second attempt
    (given *retry*, a second attempt: Claude is told what was wrong and writes
    it again, and what it writes then must be read). Raises ScanError when
    it couldn't be written, or a second attempt couldn't be read."""
    try:
        from . import score  # music21 and verovio: the "score" extra
    except ImportError:
        raise ScanError(_NO_SCORE_EXTRA) from None

    messages: list[BetaMessageParam] = [
        {"role": "user", "content": [*content, {"type": "text", "text": "Transcribe this sheet music."}]}
    ]
    if retry is not None:
        messages += [
            {"role": "assistant", "content": retry["content"]},
            {
                "role": "user",
                "content": f"That MusicXML couldn't be read ({retry['error']}). Write the whole score again, "
                "as complete, valid MusicXML.",
            },
        ]
    response = _stream(messages, client, model)
    text = "".join(block.text for block in response.content if block.type == "text")
    found = _MUSICXML_RE.search(text)
    try:
        if found is None:
            raise ScanError("No MusicXML came back.")
        return Transcription(musicxml=score.clean(found.group(0)))
    except ScanError as error:
        if retry is not None:
            raise
        written = [block.model_dump(mode="json", exclude_none=True) for block in response.content]
        return Transcription(retry={"content": written, "error": str(error)})


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
