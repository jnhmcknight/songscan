"""Sheet music, as MusicXML: tidied up, read for the song's details (and, for
a lead sheet, its chord chart), moved to another key, and drawn.

Needs the "score" extra (music21, verovio, svglib). music21 is large and
slow to import, so this is imported only when sheet music is read or drawn.
"""

import io
import re
from typing import Literal, overload

import verovio
from music21 import converter, expressions, harmony, interval, layout, pitch, stream, tempo
from music21 import key as m21key
from music21 import meter
from music21.musicxml.m21ToXml import GeneralObjectExporter
from reportlab.graphics import renderPDF
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from svglib.svglib import svg2rlg

from .models import ScanError, ScanMeta

# A lead sheet's chart has this many bars to a line, when it doesn't say
BARS_PER_LINE = 4

# Drawing: verovio's page, in its units (A4's shape: a narrower page, for a
# larger staff on the page drawn), and the SVG's zoom
_PAGE = {"pageWidth": 1500, "pageHeight": 2121, "scale": 100}

# Accidentals verovio draws in its music font in text (chord names), which
# neither the PDF nor every SVG viewer has: as letters, in the text's font
_TEXT_ACCIDENTALS = {
    "\uea64": "b",  # in chord symbols
    "\uea65": "",
    "\uea66": "#",
    "\ue260": "b",
    "\ue261": "",
    "\ue262": "#",
    "\ue263": "##",
    "\ue264": "bb",
}
_MUSIC_FONT_TEXT = re.compile(
    r'(<tspan font-size="\d+px">[^<]*)(</tspan>)\s*<tspan font-family="Leipzig" font-size="\d+px">([^<])</tspan>'
)

_KEY_RE = re.compile(r"^\s*([A-Ga-g])([#♯b♭]?)(m|min|minor)?\s*$")


def load(musicxml: str) -> stream.Score:
    """The score *musicxml* writes; ScanError if it isn't one."""
    try:
        score = converter.parseData(musicxml, format="musicxml")
    except Exception as error:  # music21 raises many kinds
        raise ScanError(f"This isn't MusicXML that can be read: {error}") from error
    if not isinstance(score, stream.Score) or not score.parts or not score.flatten().notes:
        raise ScanError("This MusicXML has no music in it.")
    return score


def to_musicxml(score: stream.Score) -> str:
    return GeneralObjectExporter(score).parse().decode("utf-8")


def clean(musicxml: str) -> str:
    """*musicxml* as music21 writes it: well-formed and consistent, whatever
    wrote it. ScanError if it can't be read."""
    return to_musicxml(load(musicxml))


# --- What the score says ---


def key_name(key: m21key.Key | m21key.KeySignature) -> str:
    """A key as ChordPro writes it: "G", "Bb", "F#m"."""
    if not isinstance(key, m21key.Key):
        key = key.asKey("major")
    name = _ascii(key.tonic.name)
    return f"{name}m" if key.mode == "minor" else name


def written_key(score: stream.Score) -> str | None:
    """The key the score is written in: as it says, or else as it sounds."""
    first = score.flatten().getElementsByClass(m21key.KeySignature).first()
    if isinstance(first, m21key.Key):
        return key_name(first)
    try:
        heard = score.analyze("key")
    except Exception:  # nothing to analyze
        heard = None
    if first is None:
        return key_name(heard) if isinstance(heard, m21key.Key) else None
    # A key signature alone: major, unless it sounds like its relative minor
    return key_name(heard if isinstance(heard, m21key.Key) and heard.sharps == first.sharps else first)


def score_meta(score: stream.Score) -> ScanMeta:
    """The song's details, as the score gives them."""
    md = score.metadata
    flat = score.flatten()
    times = flat.getElementsByClass(meter.TimeSignature)
    marks = flat.getElementsByClass(tempo.MetronomeMark)
    bpm = marks[0].number if marks else None
    return ScanMeta(
        title=(md.bestTitle if md else None) or None,
        artists=[str(c) for c in (md.composers if md else ())],
        key=written_key(score),
        tempo=str(round(bpm)) if bpm else None,
        time=times[0].ratioString if times else None,
        copyright=[str(md.copyright)] if md and md.copyright else [],
    )


def score_chordpro(score: stream.Score) -> str | None:
    """A lead sheet's chord chart, in ChordPro: its lyrics, verse by verse,
    with each chord before the syllable it falls on. None when the score has
    no lyrics, or no chords."""
    part = next((p for p in score.parts if any(n.lyrics for n in p.flatten().notes)), None)
    if part is None:
        return None
    # Each chord with where it falls in the piece (an element's own offset
    # follows whichever stream last held it)
    chords = _chords_at(part.flatten()) or _chords_at(score.flatten())
    if not chords:
        return None

    measures = list(part.getElementsByClass(stream.Measure))
    # Lines end where the score starts a new system, or else every few bars
    starts = {
        i for i, m in enumerate(measures) if any(s.isNew for s in m.getElementsByClass(layout.SystemLayout))
    }
    if len(starts) < 2:
        starts = set(range(0, len(measures), BARS_PER_LINE))

    verses = sorted({lyric.number for n in part.flatten().notes for lyric in n.lyrics})
    sections = []
    for number in verses:
        lines, line = [], ""
        pending = list(chords)
        for index, measure in enumerate(measures):
            if index in starts and line.strip():
                lines.append(line.strip())
                line = ""
            for n in measure.notes:
                if isinstance(n, harmony.ChordSymbol):
                    continue
                at = measure.offset + n.offset
                lyric = next((lyr for lyr in n.lyrics if lyr.number == number), None)
                if lyric is None:
                    continue
                while pending and pending[0][0] <= at:
                    line += f"[{chord_name(pending.pop(0)[1])}]"
                line += lyric.text or ""
                if lyric.syllabic in (None, "single", "end"):
                    line += " "
        line += "".join(f"[{chord_name(c)}]" for _, c in pending)
        if line.strip():
            lines.append(line.strip())
        label = f"Verse {number}" if len(verses) > 1 else "Verse"
        sections.append("\n".join([f"{{start_of_verse: {label}}}", *lines, "{end_of_verse}"]))
    return "\n\n".join(sections)


def _chords_at(flat: stream.Stream) -> list[tuple[float, harmony.ChordSymbol]]:
    found = [(float(flat.elementOffset(c)), c) for c in flat.getElementsByClass(harmony.ChordSymbol)]
    return sorted(found, key=lambda pair: pair[0])


def chord_name(symbol: harmony.ChordSymbol) -> str:
    """A chord symbol as charts write it: "Bb", not music21's "B-"."""
    return re.sub(r"(^|/)([A-G])-", r"\1\2b", symbol.figure)


# --- Another key ---


def transpose(musicxml: str, key: str) -> str:
    """*musicxml* moved to *key* ("Bb", "B♭", "F#m"…): its notes, key
    signatures and chord symbols, by the nearer way up or down, spelled as
    *key* is."""
    score = load(musicxml)
    moved = _transposed(score, key)
    return musicxml if moved is score else to_musicxml(moved)


def _transposed(score: stream.Score, key: str) -> stream.Score:
    target = _tonic(key)
    source = written_key(score)
    if source is None or target is None:
        return score
    start = pitch.Pitch(_m21(_tonic(source) or "C"), octave=4)
    end = pitch.Pitch(_m21(target), octave=4)
    if end.ps - start.ps > 6:
        end.octave = 3
    elif start.ps - end.ps > 6:
        end.octave = 5
    if start.nameWithOctave == end.nameWithOctave:
        return score
    moved = score.transpose(interval.Interval(start, end))
    return moved if isinstance(moved, stream.Score) else score


def _tonic(key: str) -> str | None:
    """The tonic of a key as given ("B♭m" → "Bb"); None if it isn't one."""
    m = _KEY_RE.match(key.replace("♭", "b").replace("♯", "#"))
    return f"{m.group(1).upper()}{m.group(2)}" if m else None


def _m21(name: str) -> str:
    """A note's name as music21 writes it: "B-", "F#"."""
    return name[0] + name[1:].replace("b", "-")


def _ascii(name: str) -> str:
    return name[0] + name[1:].replace("-", "b")


# --- Drawing ---


@overload
def render(musicxml: str, key: str | None = None, *, format: Literal["svg"] = "svg") -> list[str]: ...
@overload
def render(musicxml: str, key: str | None = None, *, format: Literal["pdf"]) -> bytes: ...


def render(
    musicxml: str, key: str | None = None, *, format: Literal["svg", "pdf"] = "svg"
) -> list[str] | bytes:
    """The score drawn, in *key* if given: a page of SVG each, or a PDF."""
    score = load(musicxml)
    if key:
        score = _transposed(score, key)
    _tempo_as_text(score)

    toolkit = verovio.toolkit()
    toolkit.setOptions(
        _PAGE | {"header": "none", "footer": "none", "breaks": "auto", "adjustPageHeight": format == "svg"}
    )
    if not toolkit.loadData(to_musicxml(score)):
        raise ScanError("This sheet music couldn't be drawn.")
    pages = [_plain_accidentals(toolkit.renderToSVG(page)) for page in range(1, toolkit.getPageCount() + 1)]
    return pages if format == "svg" else _pdf(pages)


def _plain_accidentals(svg: str) -> str:
    """Chord names' sharps and flats as "#" and "b", in with the letter
    before them (svglib draws each piece of text where its text starts)."""

    def plain(m: re.Match) -> str:
        text = _TEXT_ACCIDENTALS.get(m.group(3))
        return m.group(0) if text is None else f"{m.group(1)}{text}{m.group(2)}"

    return _MUSIC_FONT_TEXT.sub(plain, svg)


def _tempo_as_text(score: stream.Score) -> None:
    """Tempo marks as words ("72 bpm"): verovio draws a mark's note in its
    music font, which neither the PDF nor every SVG viewer has."""
    for mark in list(score.recurse().getElementsByClass(tempo.MetronomeMark)):
        site = mark.activeSite
        if site is None:
            continue
        words = expressions.TextExpression(f"{round(mark.number)} bpm" if mark.number else (mark.text or ""))
        words.placement = "above"  # pyright: ignore[reportAttributeAccessIssue]
        offset = mark.getOffsetBySite(site)
        site.remove(mark)
        if words.content:
            site.insert(offset, words)


def _pdf(pages: list[str]) -> bytes:
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    for svg in pages:
        drawing = svg2rlg(io.StringIO(svg))
        if drawing is None:
            continue
        zoom = width / drawing.width
        drawing.scale(zoom, zoom)
        renderPDF.draw(drawing, pdf, 0, height - drawing.height * zoom)
        pdf.showPage()
    pdf.save()
    return buf.getvalue()
