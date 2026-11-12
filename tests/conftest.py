"""PDFs and images to scan, made as the tests run, and a stand-in for Claude.

Tests that read PDFs or images with vision ask Claude itself when SONGSCAN_LIVE_API=1
(with an API key in ANTHROPIC_API_KEY), and a stand-in otherwise: see the
`vision` fixture. Tests of how songscan handles the API's answers and errors
always use the stand-in.
"""

import io
import os
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from chordpro.chordsheet import chord_line

# A chart as a chart app would print it: details at the top, labelled
# sections, chords over the syllables they are played on, and a footer.
CHART = [
    "Amazing Grace",
    "John Newton",
    "Key: G   Tempo: 72   Time: 3/4",
    "",
    "Verse 1",
    "G             C             G",
    "Amazing grace how sweet the sound",
    "G                        D",
    "That saved a wretch like me",
    "",
    "CHORUS",
    "C" + " " * 9 + "G",
    "My chains are gone",
    "(Repeat)",
    "",
    "Public Domain. CCLI Song # 22025",
]


def make_pdf(lines: list[str], font: str = "Courier", size: int = 11, chord_size: int | None = None) -> bytes:
    """A one-page PDF of *lines*, laid out as a chart app prints them: each
    line as one string, and each chord over the letter of the lyric below it
    that it is over in *lines* (wherever the font puts that letter). Chords
    are in *chord_size*, if given."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=letter)
    y = 720
    for index, line in enumerate(lines):
        below = lines[index + 1] if index + 1 < len(lines) else ""
        if chord_line(line) is not None:
            pdf.setFont(font, chord_size or size)
            for start, word in _words(line):
                if below.strip() and chord_line(below) is None:
                    x = pdf.stringWidth(below[:start], font, size)
                else:
                    x = start * pdf.stringWidth("n", font, size)
                pdf.drawString(72 + x, y, word)
            y -= (chord_size or size) + 3
        else:
            pdf.setFont(font, size)
            pdf.drawString(72, y, line)
            y -= size + 4
    pdf.save()
    return buf.getvalue()


def _words(line: str):
    return [(m.start(), m.group(0)) for m in re.finditer(r"\S+", line)]


def make_image_pdf() -> bytes:
    """A PDF of a picture, with no text: as a scanner makes."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=letter)
    for row in range(40):
        pdf.rect(72, 700 - row * 15, 400 - (row % 7) * 30, 8, fill=1, stroke=0)
    pdf.save()
    return buf.getvalue()


def make_scanned_pdf(lines: list[str]) -> bytes:
    """A PDF of a picture of *lines*, laid out as `make_pdf` lays them out
    but with no text of its own: as a scanner makes of a printed chart."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=letter)
    pdf.drawImage(ImageReader(chart_image(lines)), 0, 0, *letter)
    pdf.save()
    return buf.getvalue()


def make_image(lines: list[str], fmt: str = "PNG", **options) -> bytes:
    """A picture of *lines* (as `chart_image`), as a file of *fmt*: as a
    phone or scanner makes of a printed chart."""
    buf = io.BytesIO()
    chart_image(lines).save(buf, fmt, **options)
    return buf.getvalue()


def chart_image(lines: list[str]) -> Image.Image:
    """A page with *lines* on it, laid out as `make_pdf` lays them out."""
    image = Image.new("L", (1700, 2200), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=36)
    y = 150
    for index, line in enumerate(lines):
        below = lines[index + 1] if index + 1 < len(lines) else ""
        if chord_line(line) is not None:
            for start, word in _words(line):
                if below.strip() and chord_line(below) is None:
                    x = draw.textlength(below[:start], font=font)
                else:
                    x = start * draw.textlength("n", font=font)
                draw.text((150 + x, y), word, font=font, fill=0)
        else:
            draw.text((150, y), line, font=font, fill=0)
        y += 50
    return image


@pytest.fixture
def chart_pdf() -> bytes:
    return make_pdf(CHART)


@pytest.fixture
def image_pdf() -> bytes:
    return make_image_pdf()


@pytest.fixture
def scanned_chart_pdf() -> bytes:
    return make_scanned_pdf(CHART)


@pytest.fixture
def scanned_chart_image() -> bytes:
    return make_image(CHART)


LIVE = os.environ.get("SONGSCAN_LIVE_API") == "1"
# How long a test may take with Claude itself: reading sheet music, with two
# calls and a long answer, can take minutes (the stand-in's, under a second)
LIVE_TIMEOUT = 600


def pytest_collection_modifyitems(items):
    """With SONGSCAN_LIVE_API=1, more time for the tests that ask Claude."""
    if LIVE:
        for item in items:
            if "vision" in getattr(item, "fixturenames", ()):
                item.add_marker(pytest.mark.timeout(LIVE_TIMEOUT))


@pytest.fixture
def vision():
    """A client to read PDFs with vision: `vision(song, scores)` gives Claude
    itself with SONGSCAN_LIVE_API=1, else a stand-in answering with *song*
    (what Claude would make of the test's PDF) and, for sheet music, *scores*
    (the MusicXML it would write). Tests using it check only what either
    would give."""

    def client(song, scores=()):
        return anthropic.Anthropic() if LIVE else fake_client(song, scores=scores)[0]

    return client


class FakeMessages:
    """Claude's messages, answering with *song* (a vision._Song), and then,
    asked to write sheet music out, with each of *scores* in turn (MusicXML,
    or anything else it might write)."""

    def __init__(
        self,
        song=None,
        stop_reason="end_turn",
        error: Exception | None = None,
        scores: list[str] = (),
        score_stop_reason="end_turn",
        score_error: Exception | None = None,
    ):
        self.song, self.stop_reason, self.error = song, stop_reason, error
        self.scores, self.score_stop_reason, self.score_error = list(scores), score_stop_reason, score_error
        self.calls: list[dict] = []
        self.streams: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.song)

    @contextmanager
    def stream(self, **kwargs):
        self.streams.append(kwargs)
        if self.score_error:
            raise self.score_error
        text = self.scores.pop(0) if self.scores else ""
        block = SimpleNamespace(type="text", text=text, model_dump=lambda **_: {"type": "text", "text": text})
        message = SimpleNamespace(stop_reason=self.score_stop_reason, content=[block])
        yield SimpleNamespace(get_final_message=lambda: message)


def fake_client(song=None, **kwargs):
    messages = FakeMessages(song, **kwargs)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


# A lead sheet: "Amazing Grace", in G, 3/4, with chords and one verse's lyrics
LEAD_SHEET = (Path(__file__).parent / "fixtures" / "lead-sheet.musicxml").read_text()
