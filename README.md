# songscan

Read songs out of PDFs and images (photos and scans). A chord chart becomes [ChordPro](https://www.chordpro.org/), with the
song's details (title, artists, key, tempo, time, capo, copyright) alongside; sheet music becomes
MusicXML, which songscan can move to another key and draw (and a lead sheet, with chords and
lyrics, a ChordPro chart too).

A PDF's own text is read first: charts typed up in a word processor or chart app keep their words
and where each sits on the page, so their chords can be put back over the syllables they are
played on, without asking anyone. Anything else (scanned PDFs, sheet music, or text that isn't
plainly a chord chart) is read by Claude, looking at its pages, as images always are.

Images may be any type [Pillow](https://python-pillow.org/) opens. Before Claude sees them they
are turned upright (as a phone's photo says to), scaled down to what Claude reads, and sent as
JPEG, PNG, GIF or WebP; a multi-page TIFF, as scanners make, as an image a page.

## Installation

```bash
uv add "songscan @ git+https://github.com/jnhmcknight/songscan.git"
```

Reading sheet music needs the `score` extra ([music21](https://www.music21.org/),
[verovio](https://www.verovio.org/) and [svglib](https://github.com/deeplook/svglib)):

```bash
uv add "songscan[score] @ git+https://github.com/jnhmcknight/songscan.git"
```

Reading scans and images needs an [Anthropic API key](https://platform.claude.com/), in
`ANTHROPIC_API_KEY` or a client you pass in. PDFs with their own text don't use it.

## Usage

```python
from songscan import ScanError, scan

try:
    result = scan(file_bytes)  # a PDF or an image, told apart by what is in it
except ScanError as error:
    ...  # error.retryable: whether trying again later may work

result.kind       # "chord_chart", "score" or "other"
result.chordpro   # a chord chart's ChordPro, details and all (a lead sheet's too); else None
result.musicxml   # sheet music's MusicXML; else None
result.meta       # ScanMeta: title, artists, key, tempo, time, capo, copyright
result.source     # "text" (the PDF's own) or "vision" (Claude's reading)
result.warnings   # anything to check, or that couldn't be done
```

To say which it is, `scan_pdf(pdf_bytes)` or `scan_image(image_bytes)`.

Writing sheet music out can take many minutes (Claude may write it twice, when
what it wrote first can't be read). Where a process may only run so long, read it
in steps, each of which can run in a process of its own:

```python
from songscan import complete_score, scan, transcribe_score

result = scan(file_bytes, transcribe=False)  # sheet music comes back transcribe_pending
if result.transcribe_pending:
    attempt = transcribe_score(file_bytes)  # one attempt
    if attempt.musicxml is None:
        # attempt.retry is JSON, to keep until the next step
        attempt = transcribe_score(file_bytes, retry=attempt.retry)  # MusicXML, or ScanError
    complete_score(result, attempt.musicxml)  # the details and chart it gives
```

Sheet music, in another key (by the nearer way up or down, spelled as the key is):

```python
from songscan import score

score.transpose(result.musicxml, "Bb")              # MusicXML
score.render(result.musicxml, "Bb")                 # a page of SVG each
score.render(result.musicxml, "Bb", format="pdf")   # a PDF
```

Drawing uses the fonts verovio comes with. To use others (or to find them
elsewhere), set `SONGSCAN_VEROVIO_RESOURCES` to their directory, or say where
they are each time:

```python
score.render(result.musicxml, "Bb", resources="/path/to/verovio/data")
```

They are given to each drawing, so it works on any thread: verovio itself
only knows where its fonts are on the thread that imported it.

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

Tests that read PDFs and images with vision use a stand-in for Claude. To run them against the real API
instead (which costs a little), set `SONGSCAN_LIVE_API=1` with a key in `ANTHROPIC_API_KEY`:

```bash
SONGSCAN_LIVE_API=1 uv run pytest
```

Tests of how songscan handles the API's answers and errors use the stand-in either way.
