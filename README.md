# songscan

Read songs out of PDFs. A chord chart becomes [ChordPro](https://www.chordpro.org/), with the
song's details (title, artists, key, tempo, time, capo, copyright) alongside; sheet music will
become MusicXML.

A PDF's own text is read first: charts typed up in a word processor or chart app keep their words
and where each sits on the page, so their chords can be put back over the syllables they are
played on, without asking anyone. Anything else (scans, photos, sheet music, or text that isn't
plainly a chord chart) is read by Claude, looking at its pages.

## Installation

```bash
uv add "songscan @ git+https://github.com/jnhmcknight/songscan.git"
```

Reading scans needs an [Anthropic API key](https://platform.claude.com/), in `ANTHROPIC_API_KEY`
or a client you pass in. PDFs with their own text don't use it.

## Usage

```python
from songscan import ScanError, scan_pdf

try:
    result = scan_pdf(pdf_bytes)
except ScanError as error:
    ...  # error.retryable: whether trying again later may work

result.kind       # "chord_chart", "score" or "other"
result.chordpro   # a chord chart's ChordPro, details and all; else None
result.meta       # ScanMeta: title, artists, key, tempo, time, capo, copyright
result.source     # "text" (the PDF's own) or "vision" (Claude's reading)
result.warnings   # anything to check, or that couldn't be done
```

To choose the client or model:

```python
import anthropic

result = scan_pdf(pdf_bytes, client=anthropic.Anthropic(api_key=...), model="claude-opus-5-5")
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check && uv run black --check . && uv run pyright
```
