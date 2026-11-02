"""Reading a PDF's own text, and chord charts in it."""

import re

import pytest

from chordpro import parse, render
from songscan import ScanError
from songscan.text import MUSIC_FONTS, chart_from_text, is_chord_chart, read_text, section
from tests.conftest import CHART, make_pdf

EXPECTED = """\
{title: Amazing Grace}
{artist: John Newton}
{key: G}
{tempo: 72}
{time: 3/4}
{copyright: Public Domain. CCLI Song # 22025}

{start_of_verse: Verse 1}
[G]Amazing grace [C]how sweet the [G]sound
[G]That saved a wretch like [D]me
{end_of_verse}

{start_of_chorus: Chorus}
[C]My chains [G]are gone
{comment: Repeat}
{end_of_chorus}"""


class TestReadText:
    def test_lines_as_on_the_page(self, chart_pdf):
        text = read_text(chart_pdf)
        assert text.pages == 1
        assert text.has_text
        assert not text.music_font
        assert text.lines == CHART

    def test_proportional_font_keeps_chords_over_their_syllables(self):
        text = read_text(make_pdf(CHART, font="Helvetica", chord_size=9))
        index = text.lines.index("Amazing grace how sweet the sound")
        assert [m.start() for m in re.finditer(r"\S+", text.lines[index - 1])] == [0, 14, 28]
        index = text.lines.index("That saved a wretch like me")
        assert [m.start() for m in re.finditer(r"\S+", text.lines[index - 1])] == [0, 25]

    def test_proportional_font_chart(self):
        chordpro, _ = chart_from_text(read_text(make_pdf(CHART, font="Times-Roman", chord_size=9)).lines)
        assert chordpro == EXPECTED

    def test_picture_has_no_text(self, image_pdf):
        text = read_text(image_pdf)
        assert text.pages == 1
        assert not text.has_text
        assert text.lines == []

    def test_not_a_pdf(self):
        with pytest.raises(ScanError):
            read_text(b"not a pdf")


class TestSection:
    @pytest.mark.parametrize(
        "line, found",
        [
            ("Verse 1", ("verse", "Verse 1")),
            ("CHORUS", ("chorus", "Chorus")),
            ("Pre-Chorus:", ("prechorus", "Pre-Chorus")),
            ("[Bridge]", ("bridge", "Bridge")),
            ("Chorus (x2)", ("chorus", "Chorus")),
            ("  Tag  ", ("tag", "Tag")),
            ("Chorus x 2", ("chorus", "Chorus")),
            ("[Verse 2]:", ("verse", "Verse 2")),
        ],
    )
    def test_labels(self, line, found):
        assert section(line) == found

    @pytest.mark.parametrize(
        "line",
        [
            "Bridge over troubled water",
            "Amazing grace",
            "Verse one of many lines here",
            "",
            # Long runs of spaces, as across a page's columns: quickly, not at all
            "Amazing" + " " * 200 + "Grace" + " " * 200 + "KEY",
        ],
    )
    def test_not_labels(self, line):
        assert section(line) is None


class TestChartFromText:
    def test_chart(self):
        assert chart_from_text(CHART)[0] == EXPECTED

    def test_details(self):
        _, meta = chart_from_text(CHART)
        assert meta.title == "Amazing Grace"
        assert meta.artists == ["John Newton"]
        assert (meta.key, meta.tempo, meta.time, meta.capo) == ("G", "72", "3/4", None)
        assert meta.copyright == ["Public Domain. CCLI Song # 22025"]

    def test_chordpro_parses(self):
        song = parse(chart_from_text(CHART)[0])
        assert song.meta.title == "Amazing Grace"
        assert song.meta.key == ["G"]

    def test_details_beside_the_title(self):
        lines = ["Cornerstone          Capo 2 | 4/4 | 70 bpm", "By Hillsong", "", *CHART[4:9]]
        _, meta = chart_from_text(lines)
        assert meta.title == "Cornerstone"
        assert meta.artists == ["Hillsong"]
        assert (meta.capo, meta.time, meta.tempo) == ("2", "4/4", "70")

    def test_details_only_line(self):
        _, meta = chart_from_text(["Song", "Key of Bb · 4/4", "Capo: 3", "", *CHART[4:9]])
        assert (meta.title, meta.key, meta.time, meta.capo) == ("Song", "Bb", "4/4", "3")
        assert meta.artists == []

    def test_no_details(self):
        chordpro, meta = chart_from_text(CHART[4:9])
        assert chordpro.startswith("\n{start_of_verse: Verse 1}")
        assert meta.title is None

    def test_lyrics_alone_are_not_a_chart(self):
        assert chart_from_text(["Amazing Grace", "", "Amazing grace how sweet the sound"]) is None

    def test_chart_printed_by_chordpro(self):
        """The title in large print, the details in a column across from it,
        and the sections and lyrics indented."""
        pdf = render(parse(EXPECTED), format="pdf")
        chordpro, meta = chart_from_text(read_text(pdf).lines)
        assert (meta.title, meta.artists) == ("Amazing Grace", ["John Newton"])
        assert (meta.key, meta.tempo, meta.time) == ("G", "72", "3/4")
        assert meta.copyright == ["Public Domain. CCLI Song # 22025"]
        assert "{start_of_verse: Verse 1}\n[G]Amazing grace [C]how sweet the [G]sound\n" in chordpro
        assert "[G]That saved a wretch like [D]me\n{end_of_verse}" in chordpro
        assert "{start_of_chorus: Chorus}\n[C]My chains [G]are gone\n" in chordpro


class TestIsChordChart:
    def test_one_line_of_chords_is_not_enough(self):
        assert not is_chord_chart(["G   C", "Amazing grace", "how sweet", "the sound"])

    def test_chords_alone(self):
        assert is_chord_chart(["Intro", "| G | C | D | G |", "| Em | C | D | D |"])


def test_music_fonts():
    assert MUSIC_FONTS.search("ABCDEF+Leland")
    assert MUSIC_FONTS.search("Bravura-Text")
    assert not MUSIC_FONTS.search("Helvetica-Bold")
