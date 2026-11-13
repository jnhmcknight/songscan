"""Scanning: a PDF's own text when it is a chord chart, Claude otherwise (and
for images, always)."""

import json

import httpx2
import pytest

import anthropic
from songscan import DEFAULT_MODEL, ScanError, complete_score, scan, scan_image, scan_pdf, transcribe_score
from songscan.vision import _Song
from songscan import score
from tests.conftest import CHART, LEAD_SHEET, fake_client, make_image, make_pdf


def _song(**fields) -> _Song:
    data = dict(
        kind="chord_chart",
        title="Amazing Grace",
        artists=["John Newton"],
        key="G",
        tempo=None,
        time="3/4",
        capo=None,
        copyright=["Public Domain"],
        chordpro="{start_of_verse: Verse 1}\n[G]Amazing [C]grace\n{end_of_verse}\n",
    )
    return _Song(**(data | fields))


def _status_error(cls, status: int):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("failed", response=httpx2.Response(status, request=request), body=None)


class TestFromText:
    def test_chart_is_read_without_claude(self, chart_pdf):
        client, messages = fake_client()
        result = scan_pdf(chart_pdf, client=client)
        assert messages.calls == []
        assert (result.kind, result.source, result.pages) == ("chord_chart", "text", 1)
        assert "[G]Amazing grace [C]how sweet the [G]sound" in result.chordpro
        assert result.meta.key == "G"

    def test_lyrics_alone_go_to_claude(self, vision):
        lyrics = ["Amazing Grace", "", "Amazing grace how sweet the sound", "That saved a wretch like me"]
        client = vision(_song(kind="other", chordpro=None))
        result = scan_pdf(make_pdf(lyrics), client=client)
        assert (result.kind, result.source, result.chordpro) == ("other", "vision", None)


class TestWithVision:
    def test_scan(self, vision, scanned_chart_pdf):
        result = scan_pdf(scanned_chart_pdf, client=vision(_song()))
        assert (result.kind, result.source, result.pages) == ("chord_chart", "vision", 1)
        assert (result.meta.title, result.meta.key) == ("Amazing Grace", "G")
        assert result.meta.artists == ["John Newton"]
        assert result.meta.time == "3/4"
        assert any("Public Domain" in line for line in result.meta.copyright)
        assert result.chordpro.startswith("{title: Amazing Grace}\n{artist: John Newton}\n{key: G}\n")
        assert "{start_of_verse: Verse 1}\n[G]Amazing" in result.chordpro
        assert "[C]grace" in result.chordpro or "[C]how" in result.chordpro

    def test_details_go_in_the_directives(self, image_pdf):
        client, _ = fake_client(_song())
        result = scan_pdf(image_pdf, client=client)
        assert result.chordpro == (
            "{title: Amazing Grace}\n{artist: John Newton}\n{key: G}\n{time: 3/4}\n"
            "{copyright: Public Domain}\n\n"
            "{start_of_verse: Verse 1}\n[G]Amazing [C]grace\n{end_of_verse}"
        )

    def test_request(self, image_pdf):
        client, messages = fake_client(_song())
        scan_pdf(image_pdf, client=client)
        call = messages.calls[0]
        assert call["model"] == DEFAULT_MODEL
        assert call["output_format"] is _Song
        assert call["fallbacks"] == "default"
        assert call["betas"] == ["server-side-fallback-2026-07-01"]
        document = call["messages"][0]["content"][0]
        assert document["type"] == "document"
        assert document["source"]["media_type"] == "application/pdf"

    def test_model(self, image_pdf):
        client, messages = fake_client(_song())
        scan_pdf(image_pdf, client=client, model="claude-sonnet-5-5")
        assert messages.calls[0]["model"] == "claude-sonnet-5-5"

    def test_score(self, image_pdf):
        client, _ = fake_client(_song(kind="score", chordpro=None))
        result = scan_pdf(image_pdf, client=client)
        assert (result.kind, result.chordpro, result.musicxml) == ("score", None, None)
        assert result.warnings

    def test_chart_without_chords(self, image_pdf):
        client, _ = fake_client(_song(chordpro=None))
        result = scan_pdf(image_pdf, client=client)
        assert result.chordpro is None
        assert result.warnings

    @pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
    def test_stopped(self, image_pdf, stop_reason):
        client, _ = fake_client(_song(), stop_reason=stop_reason)
        with pytest.raises(ScanError) as error:
            scan_pdf(image_pdf, client=client)
        assert not error.value.retryable

    def test_nothing_parsed(self, image_pdf):
        client, _ = fake_client(None)
        with pytest.raises(ScanError):
            scan_pdf(image_pdf, client=client)

    @pytest.mark.parametrize(
        "error, retryable",
        [
            (_status_error(anthropic.RateLimitError, 429), True),
            (_status_error(anthropic.InternalServerError, 500), True),
            (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")), True),
            (_status_error(anthropic.BadRequestError, 400), False),
        ],
    )
    def test_errors(self, image_pdf, error, retryable):
        client, _ = fake_client(error=error)
        with pytest.raises(ScanError) as raised:
            scan_pdf(image_pdf, client=client)
        assert raised.value.retryable is retryable


class TestSheetMusic:
    """Sheet music: written out as MusicXML, by Claude (or its stand-in)."""

    def test_sheet_music(self, vision):
        pdf = score.render(LEAD_SHEET, format="pdf")
        client = vision(_song(kind="score", key=None, time=None, chordpro=None), scores=[LEAD_SHEET])
        result = scan_pdf(pdf, client=client)
        assert (result.kind, result.source) == ("score", "vision")
        assert result.musicxml is not None and "<score-partwise" in result.musicxml
        assert result.meta.key == "G"
        assert result.meta.time == "3/4"
        # A lead sheet's chart, from the MusicXML
        assert result.chordpro is not None and "[G]" in result.chordpro
        assert "{start_of_verse" in result.chordpro

    def test_request(self, image_pdf):
        client, messages = fake_client(_song(kind="score", chordpro=None), scores=[LEAD_SHEET])
        scan_pdf(image_pdf, client=client)
        (call,) = messages.streams
        assert call["max_tokens"] == 128000
        assert call["fallbacks"] == "default"
        assert call["output_config"] == {"effort": "high"}
        assert call["messages"][0]["content"][0]["type"] == "document"

    def test_details_from_the_pdf_come_first(self, image_pdf):
        client, _ = fake_client(
            _song(kind="score", title="Grace (live)", key="A", chordpro=None), scores=[LEAD_SHEET]
        )
        result = scan_pdf(image_pdf, client=client)
        assert (result.meta.title, result.meta.key, result.meta.tempo) == ("Grace (live)", "A", "72")

    def test_written_again_when_unreadable(self, image_pdf):
        wrapped = f"Here it is:\n```xml\n{LEAD_SHEET}\n```"
        client, messages = fake_client(
            _song(kind="score", chordpro=None), scores=["<score-partwise>oops", wrapped]
        )
        result = scan_pdf(image_pdf, client=client)
        assert result.musicxml is not None
        assert len(messages.streams) == 2
        retry = messages.streams[1]["messages"]
        assert [m["role"] for m in retry] == ["user", "assistant", "user"]
        assert "couldn't be read" in retry[2]["content"]

    def test_unreadable_twice(self, image_pdf):
        client, _ = fake_client(_song(kind="score", chordpro=None), scores=["nope", "still nope"])
        result = scan_pdf(image_pdf, client=client)
        assert (result.kind, result.musicxml, result.chordpro) == ("score", None, None)
        assert "couldn't be read" in result.warnings[0]

    @pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
    def test_stopped(self, image_pdf, stop_reason):
        client, _ = fake_client(
            _song(kind="score", chordpro=None), scores=[LEAD_SHEET], score_stop_reason=stop_reason
        )
        result = scan_pdf(image_pdf, client=client)
        assert result.musicxml is None
        assert result.warnings

    def test_busy(self, image_pdf):
        error = _status_error(anthropic.RateLimitError, 429)
        client, _ = fake_client(_song(kind="score", chordpro=None), score_error=error)
        with pytest.raises(ScanError) as raised:
            scan_pdf(image_pdf, client=client)
        assert raised.value.retryable


class TestInSteps:
    """Sheet music read in steps (transcribe=False, transcribe_score, complete_score),
    each of which could be run in a process of its own."""

    def _steps(self, data, **kwargs):
        client, messages = fake_client(_song(kind="score", key=None, time=None, chordpro=None), **kwargs)
        result = scan(data, client=client, transcribe=False)
        return result, client, messages

    def test_stops_before_transcribing(self, image_pdf):
        result, _, messages = self._steps(image_pdf, scores=[LEAD_SHEET])
        assert (result.kind, result.musicxml, result.transcribe_pending) == ("score", None, True)
        assert messages.streams == []

    def test_same_as_in_one_go(self):
        pdf = score.render(LEAD_SHEET, format="pdf")
        result, client, _ = self._steps(pdf, scores=[LEAD_SHEET])
        attempt = transcribe_score(pdf, client=client)
        assert attempt.retry is None
        complete_score(result, attempt.musicxml)

        whole, _ = fake_client(_song(kind="score", key=None, time=None, chordpro=None), scores=[LEAD_SHEET])
        assert result == scan_pdf(pdf, client=whole)
        assert not result.transcribe_pending

    def test_image(self, scanned_chart_image):
        client, messages = fake_client(scores=[LEAD_SHEET])
        attempt = transcribe_score(scanned_chart_image, client=client)
        assert attempt.musicxml is not None
        assert messages.streams[0]["messages"][0]["content"][0]["type"] == "image"

    def test_retry(self, image_pdf):
        _, client, messages = self._steps(image_pdf, scores=["<score-partwise>oops", LEAD_SHEET])
        first = transcribe_score(image_pdf, client=client)
        assert first.musicxml is None
        # Kept between processes as JSON
        retry = json.loads(json.dumps(first.retry))
        second = transcribe_score(image_pdf, client=client, retry=retry)
        assert second.musicxml is not None
        sent = messages.streams[1]["messages"]
        assert [m["role"] for m in sent] == ["user", "assistant", "user"]
        assert sent[1]["content"] == [{"type": "text", "text": "<score-partwise>oops"}]
        assert "couldn't be read" in sent[2]["content"]

    def test_unreadable_twice(self, image_pdf):
        _, client, _ = self._steps(image_pdf, scores=["nope", "still nope"])
        first = transcribe_score(image_pdf, client=client)
        with pytest.raises(ScanError) as raised:
            transcribe_score(image_pdf, client=client, retry=first.retry)
        assert not raised.value.retryable

    def test_busy(self, image_pdf):
        client, _ = fake_client(score_error=_status_error(anthropic.RateLimitError, 429))
        with pytest.raises(ScanError) as raised:
            transcribe_score(image_pdf, client=client)
        assert raised.value.retryable

    def test_neither(self):
        with pytest.raises(ScanError):
            transcribe_score(b"not a song", client=fake_client()[0])


class TestImages:
    """Photos and scans of a chart: read by Claude, a page each."""

    def test_scan(self, vision, scanned_chart_image):
        result = scan_image(scanned_chart_image, client=vision(_song()))
        assert (result.kind, result.source, result.pages) == ("chord_chart", "vision", 1)
        assert (result.meta.title, result.meta.key) == ("Amazing Grace", "G")
        assert "{start_of_verse: Verse 1}\n[G]Amazing" in result.chordpro

    def test_request(self, scanned_chart_image):
        client, messages = fake_client(_song())
        scan_image(scanned_chart_image, client=client)
        image, ask = messages.calls[0]["messages"][0]["content"]
        assert image["type"] == "image"
        assert image["source"]["media_type"] == "image/png"
        assert ask["text"] == "Read the song in these images."

    def test_sheet_music(self):
        client, messages = fake_client(_song(kind="score", chordpro=None), scores=[LEAD_SHEET])
        result = scan_image(make_image(CHART, "JPEG"), client=client)
        assert result.musicxml is not None
        assert messages.streams[0]["messages"][0]["content"][0]["type"] == "image"

    def test_errors_say_image(self, scanned_chart_image):
        client, _ = fake_client(_song(), stop_reason="refusal")
        with pytest.raises(ScanError, match="The image couldn't be read"):
            scan_image(scanned_chart_image, client=client)

    def test_not_an_image(self):
        client, messages = fake_client(_song())
        with pytest.raises(ScanError):
            scan_image(b"not an image", client=client)
        assert messages.calls == []


class TestScan:
    """Either: told apart by what is in the file."""

    def test_pdf(self, chart_pdf):
        client, messages = fake_client()
        assert scan(chart_pdf, client=client).source == "text"
        assert messages.calls == []

    def test_image(self, scanned_chart_image):
        client, messages = fake_client(_song())
        assert scan(scanned_chart_image, client=client).source == "vision"
        assert messages.calls[0]["messages"][0]["content"][0]["type"] == "image"

    def test_neither(self):
        with pytest.raises(ScanError):
            scan(b"lyrics.txt", client=fake_client()[0])
