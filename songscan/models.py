"""What a scan finds in a PDF or image."""

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["chord_chart", "score", "other"]


@dataclass
class ScanMeta:
    """The song's details, as far as its pages show them."""

    title: str | None = None
    artists: list[str] = field(default_factory=list)
    key: str | None = None
    tempo: str | None = None
    time: str | None = None
    capo: str | None = None
    copyright: list[str] = field(default_factory=list)

    def directives(self) -> list[str]:
        """These details as ChordPro directives, for the top of a chart."""
        lines = [f"{{title: {self.title}}}"] if self.title else []
        lines += [f"{{artist: {artist}}}" for artist in self.artists]
        for name in ("key", "tempo", "time", "capo"):
            if value := getattr(self, name):
                lines.append(f"{{{name}: {value}}}")
        lines += [f"{{copyright: {line}}}" for line in self.copyright]
        return lines


@dataclass
class ScanResult:
    """A PDF or image, read.

    A chord chart comes with its ChordPro (details and all), and sheet music
    with its MusicXML; anything else, with neither. `source` says how it was
    read: from a PDF's own text, or by looking at its pages.
    """

    kind: Kind
    meta: ScanMeta
    pages: int
    source: Literal["text", "vision"]
    chordpro: str | None = None
    musicxml: str | None = None
    # Things the reader should check, or that couldn't be done
    warnings: list[str] = field(default_factory=list)
    # Sheet music read without transcribing it (transcribe=False): its MusicXML
    # is still to be written, with transcribe_score and complete_score
    transcribe_pending: bool = False


@dataclass
class Transcription:
    """One attempt at writing sheet music out (transcribe_score): its MusicXML,
    or, when what Claude wrote couldn't be read, `retry`, to pass to the next
    attempt (JSON, to keep between processes)."""

    musicxml: str | None = None
    retry: dict | None = None


class ScanError(Exception):
    """The PDF or image couldn't be read. `retryable` when trying again later may work
    (the vision service was busy or unreachable)."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable
