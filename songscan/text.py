"""Reading a PDF's own text: for charts typed up in a word processor or chart
app, which keep their words (and where on the page each one is). Their lines
are rebuilt from where the words sit, so that chords stay above the syllables
they are played on, and a chord chart is turned into ChordPro without asking
anyone.
"""

import io
import itertools
import re
from dataclasses import dataclass, field
from statistics import median

import pdfplumber
from pdfminer.pdfparser import PDFSyntaxError
from pdfplumber.page import Page
from pdfplumber.utils.exceptions import PdfminerException

from chordpro.chordsheet import chord_line, chord_sheet_to_chordpro, section_kind

from .models import ScanError, ScanMeta

# Below this many characters, a PDF's text is too little to be the song: it
# is a scan or a picture, perhaps with a page number or a footer
MIN_TEXT = 40

# Fonts that notation software draws notes with: a PDF using one is sheet music
MUSIC_FONTS = re.compile(
    r"bravura|leland|petaluma|emmentaler|feta|gonville|maestro|petrucci|engraver|"
    r"sebastian|opus|mscore|musisync|november|finale|lilyjazz|musejazz",
    re.IGNORECASE,
)

# A chart needs at least this many lines of chords, making up at least this
# share of its lines
MIN_CHORD_LINES = 2
MIN_CHORD_SHARE = 0.15

_KEY_RE = re.compile(r"\bkey\s*(?:of|[:=-])?\s*([A-G][#b♯♭]?m?)\b", re.IGNORECASE)
_TEMPO_RE = re.compile(r"\b(?:tempo\s*[:=-]?\s*(\d{2,3})|(\d{2,3})\s*bpm)\b", re.IGNORECASE)
_TIME_RE = re.compile(r"\b(?:time(?:\s*signature)?\s*[:=-]?\s*)?(\d{1,2}/\d{1,2})\b", re.IGNORECASE)
_CAPO_RE = re.compile(r"\bcapo\s*[:=-]?\s*(\d{1,2})\b", re.IGNORECASE)
_COPYRIGHT_RE = re.compile(r"©|\(c\)|\bcopyright\b|\bccli\b|\ball rights reserved\b", re.IGNORECASE)
# Between a header's columns
_COLUMN_GAP = re.compile(r"\s{4,}")
_BY_RE = re.compile(r"^\s*(?:words\s+and\s+music\s+)?by\s+", re.IGNORECASE)
# A section label, once tidied: "Verse 1", "Pre-Chorus 2", "Bridge"
_SECTION_RE = re.compile(r"[A-Za-z][A-Za-z-]*(?: [A-Za-z][A-Za-z-]*)*(?: ?\d+)?")
# What may follow a label: "Chorus (x2)", "Chorus x2"
_REPEAT_RE = re.compile(r"\(? ?x ?\d+ ?\)?$", re.IGNORECASE)
# Most a label can be: "Pre-Chorus 2", not a lyric that starts with "Bridge"
_SECTION_WORDS = 3


@dataclass
class PdfText:
    """A PDF's text, as lines laid out the way they are on its pages."""

    pages: int
    lines: list[str] = field(default_factory=list)
    # Characters of text it has, and whether it draws notes in a music font
    characters: int = 0
    music_font: bool = False

    @property
    def has_text(self) -> bool:
        return self.characters >= MIN_TEXT


def read_text(data: bytes) -> PdfText:
    """The text of the PDF *data*."""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = PdfText(pages=len(pdf.pages))
            for page in pdf.pages:
                if text.lines:
                    text.lines.append("")
                text.lines.extend(page_lines(page))
                text.characters += sum(1 for char in page.chars if not char["text"].isspace())
                text.music_font = text.music_font or any(
                    MUSIC_FONTS.search(c["fontname"]) for c in page.chars
                )
    except (PdfminerException, PDFSyntaxError, ValueError) as error:
        raise ScanError(f"This isn't a PDF that can be read: {error}") from error
    return text


def page_lines(page: Page) -> list[str]:
    """The page's lines of text, each word at the column it starts at, and a
    blank line where there is a gap between lines (as between sections)."""
    words = page.extract_words(use_text_flow=False)
    if not words:
        return []
    char_width = median((w["x1"] - w["x0"]) / len(w["text"]) for w in words)
    height = median(w["bottom"] - w["top"] for w in words)
    left = min(w["x0"] for w in words)

    # Words on the same line (their tops within half a line of each other)
    rows: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and word["top"] - rows[-1][0]["top"] <= height / 2:
            rows[-1].append(word)
        else:
            rows.append([word])

    rows = [sorted(row, key=lambda w: w["x0"]) for row in rows]
    tops = [row[0]["top"] for row in rows]
    gaps = [b - a for a, b in itertools.pairwise(tops)]
    # Lines further apart than this have a blank line between them
    blank_gap = median(gaps) * 1.6 if gaps else 0

    placed = [_columns(row, left, char_width) for row in rows]
    texts = [_line(columns) for columns in placed]
    # Chords go over the syllables they sit above, wherever the lyric's
    # (proportional) letters put those in the line
    for index in range(len(rows) - 1):
        if (
            gaps[index] <= blank_gap
            and chord_line(texts[index]) is not None
            and chord_line(texts[index + 1]) is None
        ):
            placed[index] = _over(rows[index], placed[index + 1], left, char_width)
            texts[index] = _line(placed[index])

    lines = []
    for index, text in enumerate(texts):
        if index and gaps[index - 1] > blank_gap:
            lines.append("")
        lines.append(text)
    return lines


def _columns(row: list[dict], left: float, char_width: float) -> list[tuple[int, dict]]:
    """Each of a line's words, with the column it starts at."""
    columns: list[tuple[int, dict]] = []
    end = -1  # where the word before ends, and a space after it
    before = None
    for word in row:
        own_width = (word["x1"] - word["x0"]) / len(word["text"])
        if before is not None and word["x0"] - before["x1"] < own_width * 1.5:
            column = end + 1  # an ordinary space, however large the font
        else:
            column = max(round((word["x0"] - left) / char_width), end + 1)
        columns.append((column, word))
        end = column + len(word["text"])
        before = word
    return columns


def _over(chords: list[dict], lyric: list[tuple[int, dict]], left: float, char_width: float):
    """The columns of a line of *chords* over a *lyric*: each over the letter
    of the lyric's word that it starts above."""
    columns: list[tuple[int, dict]] = []
    end = -1
    for chord in chords:
        column = round((chord["x0"] - left) / char_width)
        for start, word in lyric:
            width = (word["x1"] - word["x0"]) / len(word["text"])
            if word["x0"] - width / 2 <= chord["x0"] < word["x1"]:
                column = start + min(round((chord["x0"] - word["x0"]) / width), len(word["text"]) - 1)
                break
            if chord["x0"] >= word["x1"]:
                # Past this word: as far past its end as the chord is
                column = start + len(word["text"]) + max(round((chord["x0"] - word["x1"]) / char_width), 1)
        column = max(column, end + 1)
        columns.append((column, chord))
        end = column + len(chord["text"])
    return columns


def _line(columns: list[tuple[int, dict]]) -> str:
    line = ""
    for column, word in columns:
        line = line.ljust(column) + word["text"]
    return line


def section(line: str) -> tuple[str, str] | None:
    """The ChordPro section and label a chart's line opens, if it is a label."""
    label = " ".join(line.split())  # one space between words
    label = _REPEAT_RE.sub("", label).strip()
    label = label.removeprefix("[").removesuffix(":").removesuffix("]").strip()
    if len(label.split()) > _SECTION_WORDS or not _SECTION_RE.fullmatch(label):
        return None
    kind = section_kind(label)
    if kind is None:
        return None
    # Labels in capitals read as shouting in ChordPro's: "VERSE 1" → "Verse 1"
    return kind, label.title() if label.isupper() else label


def is_chord_chart(lines: list[str]) -> bool:
    """Whether *lines* are a chord chart: lines of chords, over lyrics or not."""
    text = [line for line in lines if line.strip()]
    chords = sum(1 for line in text if chord_line(line) is not None)
    return chords >= MIN_CHORD_LINES and chords / len(text) >= MIN_CHORD_SHARE


def chart_from_text(lines: list[str]) -> tuple[str, ScanMeta] | None:
    """The ChordPro, and the details, of a chord chart's lines; None when they
    aren't a chord chart."""
    if not is_chord_chart(lines):
        return None
    meta = ScanMeta()

    # Copyright lines may be anywhere, often at the foot of the page
    body = []
    for line in lines:
        if _COPYRIGHT_RE.search(line):
            meta.copyright.append(line.strip())
        else:
            body.append(line)

    # The details are at the top, before the first chords or section label
    start = next(
        (i for i, line in enumerate(body) if chord_line(line) is not None or section(line)),
        len(body),
    )
    # Often in two columns, the title on the left and the details on the right
    aside = []
    for line in body[:start]:
        left, *right = _COLUMN_GAP.split(line.strip())
        aside.extend(right)
        if not left or _details(left, meta):
            continue
        if meta.title is None:
            meta.title = left
        elif not meta.artists:
            meta.artists = [_BY_RE.sub("", left)]
    _details(" ".join(aside), meta, whole_line=False)
    # The details may also share a line with the first chords' label
    for line in body[start : start + 3]:
        _details(line, meta, whole_line=False)

    content = _unindent(chord_sheet_to_chordpro(body[start:], section))
    return "\n".join([*meta.directives(), "", *content]), meta


def _unindent(chordpro: list[str]) -> list[str]:
    """*chordpro* without the margins of the page it came from. Its chords
    are in its lyrics, so this can't move them; tabs keep their spacing."""
    lines, tab = [], False
    for line in chordpro:
        if line.startswith("{start_of_tab"):
            tab = True
        elif line.startswith("{end_of_tab"):
            tab = False
        lines.append(line if tab else line.strip())
    return lines


def _details(line: str, meta: ScanMeta, whole_line: bool = True) -> bool:
    """Fill in *meta* with the key, tempo, time or capo *line* gives; whether
    it gave any (and, with *whole_line*, was nothing but those)."""
    found = False
    for pattern, name in ((_KEY_RE, "key"), (_TEMPO_RE, "tempo"), (_CAPO_RE, "capo")):
        if (m := pattern.search(line)) and getattr(meta, name) is None:
            setattr(meta, name, next(g for g in m.groups() if g))
            found = True
    if (m := _TIME_RE.search(line)) and meta.time is None and (found or "time" in line.lower() or whole_line):
        meta.time = m.group(1)
        found = True
    if not whole_line:
        return found
    rest = line
    for pattern in (_KEY_RE, _TEMPO_RE, _TIME_RE, _CAPO_RE):
        rest = pattern.sub("", rest)
    return found and not re.sub(r"[\s|,;·•/-]+", "", rest)
