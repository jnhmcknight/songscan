"""Sheet music as MusicXML: tidying, reading, moving to another key, drawing."""

import re

import pytest
from music21 import harmony, key, note

from songscan import ScanError
from songscan import score as sc
from tests.conftest import LEAD_SHEET


def _chords(musicxml: str) -> list[str]:
    return [sc.chord_name(c) for c in sc.load(musicxml).flatten().getElementsByClass(harmony.ChordSymbol)]


def _notes(musicxml: str) -> list[str]:
    return [n.nameWithOctave for n in sc.load(musicxml).flatten().getElementsByClass(note.Note)]


def _sharps(musicxml: str) -> int:
    return sc.load(musicxml).flatten().getElementsByClass(key.KeySignature).first().sharps


class TestReading:
    def test_meta(self):
        meta = sc.score_meta(sc.load(LEAD_SHEET))
        assert (meta.title, meta.artists, meta.key, meta.tempo, meta.time) == (
            "Amazing Grace",
            ["John Newton"],
            "G",
            "72",
            "3/4",
        )

    def test_lead_sheet_chart(self):
        assert sc.score_chordpro(sc.load(LEAD_SHEET)) == (
            "{start_of_verse: Verse}\nA[G]mazing grace [C]how sweet [G]the [Em]sound\n{end_of_verse}"
        )

    def test_verses(self):
        score = sc.load(LEAD_SHEET)
        for n in score.flatten().getElementsByClass(note.Note):
            n.lyrics.append(note.Lyric(text="la", number=2))
        chart = sc.score_chordpro(sc.load(sc.to_musicxml(score)))
        assert "{start_of_verse: Verse 1}\nA[G]mazing" in chart
        assert "{start_of_verse: Verse 2}\nla [G]la la la [C]la la [G]la [Em]la\n{end_of_verse}" in chart

    def test_no_chords_no_chart(self):
        score = sc.load(LEAD_SHEET)
        for symbol in list(score.recurse().getElementsByClass(harmony.ChordSymbol)):
            symbol.activeSite.remove(symbol)
        assert sc.score_chordpro(sc.load(sc.to_musicxml(score))) is None

    def test_clean(self):
        cleaned = sc.clean(LEAD_SHEET)
        assert cleaned.startswith("<?xml")
        assert sc.clean(cleaned) == cleaned

    @pytest.mark.parametrize(
        "musicxml", ["", "<score-partwise", "<html></html>", '<?xml version="1.0"?><score-partwise/>']
    )
    def test_not_musicxml(self, musicxml):
        with pytest.raises(ScanError):
            sc.clean(musicxml)


class TestTranspose:
    def test_to_a_flat_key(self):
        moved = sc.transpose(LEAD_SHEET, "B♭")
        assert _chords(moved) == ["Bb", "Eb", "Bb", "Gm"]
        assert _sharps(moved) == -2
        assert _notes(moved)[:3] == ["F4", "B-4", "D5"]
        assert sc.score_meta(sc.load(moved)).key == "Bb"

    def test_to_a_sharp_key(self):
        moved = sc.transpose(LEAD_SHEET, "F#")
        assert _chords(moved) == ["F#", "B", "F#", "D#m"]
        assert _sharps(moved) == 6

    def test_the_nearer_way(self):
        assert _notes(sc.transpose(LEAD_SHEET, "D"))[0] == "A3"  # down a fourth, not up a fifth
        assert _notes(sc.transpose(LEAD_SHEET, "A"))[0] == "E4"  # up a tone

    def test_same_key(self):
        assert sc.transpose(LEAD_SHEET, "G") == LEAD_SHEET

    def test_not_a_key(self):
        assert sc.transpose(LEAD_SHEET, "nope") == LEAD_SHEET


class TestRender:
    def test_svg(self):
        (page,) = sc.render(LEAD_SHEET, "Bb")
        assert page.startswith("<svg")
        # Chord names and tempo in plain text, not verovio's music font
        assert re.search(r">Bb<", page)
        assert ">72 bpm<" in page
        assert not re.search(r'font-family="Leipzig"[^>]*>[--]<', page)

    def test_pdf(self):
        pdf = sc.render(LEAD_SHEET, format="pdf")
        assert pdf.startswith(b"%PDF")

    def test_on_a_thread_of_its_own(self):
        """verovio only knows where its fonts are on the thread that imported
        it: render says, wherever it is drawn (a web server's request)."""
        import threading

        pages = []
        thread = threading.Thread(target=lambda: pages.extend(sc.render(LEAD_SHEET, "Bb")))
        thread.start()
        thread.join(timeout=10)
        (page,) = pages
        assert page.startswith("<svg") and ">Bb<" in page

    def test_resources_given(self, tmp_path):
        with pytest.raises(ScanError, match="fonts couldn't be found"):
            sc.render(LEAD_SHEET, resources=str(tmp_path / "nowhere"))
        assert sc.render(LEAD_SHEET, resources=sc.resource_path())[0].startswith("<svg")

    def test_resources_from_the_environment(self, monkeypatch, tmp_path):
        monkeypatch.setenv(sc.RESOURCE_PATH_ENV, str(tmp_path / "nowhere"))
        assert sc.resource_path() == str(tmp_path / "nowhere")
        with pytest.raises(ScanError, match="fonts couldn't be found"):
            sc.render(LEAD_SHEET)
