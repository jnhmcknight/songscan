"""Images of songs (photos and scans of charts, sheet music) made ready for
Claude to look at: upright, no larger than it reads them, and in a type it
takes.

Claude takes JPEG, PNG, GIF and WebP. Anything else Pillow can open (TIFF,
BMP...) is sent as PNG or JPEG; each page of a multi-page TIFF (as scanners
make) as an image of its own.
"""

import io
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
from PIL.ExifTags import Base

from .models import ScanError

MediaType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]

# The types Claude takes, by Pillow's name for them
CLAUDE_TYPES: dict[str, MediaType] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}

# The longest side Claude reads an image at: larger ones are scaled down
# before sending, as it would scale them down itself
MAX_SIDE = 2576
# The most an image may be, as sent (the API's limit is 5 MB)
MAX_BYTES = 5 * 2**20
# The most pages of a multi-page image read (the API takes up to 100 images)
MAX_PAGES = 20


@dataclass
class PreparedImage:
    """An image, as sent to Claude."""

    data: bytes
    media_type: MediaType


def is_image(data: bytes) -> bool:
    """Whether *data* is an image Pillow can open."""
    try:
        with Image.open(io.BytesIO(data)):
            return True
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return False


def prepare_image(data: bytes) -> list[PreparedImage]:
    """The image *data* as Claude is sent it: a page each (one, unless it is
    a multi-page TIFF). Raises ScanError when it isn't an image that can be
    read."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            fmt = image.format
            frames = getattr(image, "n_frames", 1)
            if fmt in CLAUDE_TYPES and frames == 1 and _as_sent(image, data):
                return [PreparedImage(data, CLAUDE_TYPES[fmt])]
            # An animated GIF or WebP is one picture; a TIFF's frames are pages
            pages = ImageSequence.Iterator(image) if fmt == "TIFF" else [image]
            prepared = []
            for page in pages:
                if len(prepared) == MAX_PAGES:
                    break
                prepared.append(_encode(page))
            return prepared
    # Not an image, one too large to open safely, or one cut short or broken
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as error:
        raise ScanError(f"The image couldn't be read: {error}") from error


def _as_sent(image: Image.Image, data: bytes) -> bool:
    """Whether *image* (of *data*) can be sent as it is: upright, small enough."""
    orientation = image.getexif().get(Base.Orientation, 1)
    return (
        orientation == 1
        # Not CMYK (as some scanners make), say, which may not be read as it shows
        and image.mode in _SENT_MODES
        and max(image.size) <= MAX_SIDE
        and len(data) <= MAX_BYTES
    )


# The colours an image may have to be sent as it is
_SENT_MODES = {"1", "L", "LA", "P", "PA", "RGB", "RGBA"}


def _encode(image: Image.Image) -> PreparedImage:
    """*image* upright, scaled down to MAX_SIDE, as PNG (if it is see-through
    or has few colours: line art, as scans of charts often are) or JPEG."""
    upright = ImageOps.exif_transpose(image)
    upright.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    if upright.mode in ("1", "L", "P", "LA", "PA", "RGBA"):
        png = _save(upright if upright.mode in ("1", "L", "RGBA") else upright.convert("RGBA"), "PNG")
        if len(png) <= MAX_BYTES:
            return PreparedImage(png, "image/png")
        upright = _flatten(upright)
    jpeg = _save(upright.convert("RGB"), "JPEG", quality=90)
    return PreparedImage(jpeg, "image/jpeg")


def _flatten(image: Image.Image) -> Image.Image:
    """*image* on white, with nothing see-through."""
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, "white")
    return Image.alpha_composite(background, rgba)


def _save(image: Image.Image, fmt: str, **options) -> bytes:
    buf = io.BytesIO()
    image.save(buf, fmt, **options)
    return buf.getvalue()
