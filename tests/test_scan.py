"""Scanning: the PDF's own text when it is a chord chart, Claude otherwise."""

import httpx2
import pytest

import anthropic
from songscan import DEFAULT_MODEL, ScanError, scan_pdf
from songscan.vision import _Song
from tests.conftest import fake_client, make_pdf


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
