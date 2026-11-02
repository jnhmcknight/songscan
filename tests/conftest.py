"""PDFs to scan, made as the tests run, and a stand-in for Claude."""

import io
import re
from types import SimpleNamespace

import pytest
from reportlab.lib.pagesizes import letter
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


@pytest.fixture
def chart_pdf() -> bytes:
    return make_pdf(CHART)


@pytest.fixture
def image_pdf() -> bytes:
    return make_image_pdf()


class FakeMessages:
    """Claude's messages, answering with *song* (a vision._Song)."""

    def __init__(self, song=None, stop_reason="end_turn", error: Exception | None = None):
        self.song, self.stop_reason, self.error = song, stop_reason, error
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.song)


def fake_client(song=None, **kwargs):
    messages = FakeMessages(song, **kwargs)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages
