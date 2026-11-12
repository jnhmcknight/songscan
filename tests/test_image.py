"""Images made ready for Claude: as they are when they can be, else upright,
scaled down, and in a type it takes."""

import io
import random

import pytest
from PIL import Image
from PIL.ExifTags import Base

from songscan import ScanError
from songscan.image import MAX_BYTES, MAX_SIDE, MAX_PAGES, is_image, prepare_image


def _image(size=(400, 300), mode="RGB", fmt="PNG", color="white", **options) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, fmt, **options)
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


@pytest.mark.parametrize(
    "fmt, media_type",
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp"), ("GIF", "image/gif")],
)
def test_sent_as_it_is(fmt, media_type):
    data = _image(fmt=fmt)
    (prepared,) = prepare_image(data)
    assert (prepared.data, prepared.media_type) == (data, media_type)


def test_other_types_are_converted():
    (prepared,) = prepare_image(_image(fmt="BMP", mode="L"))
    assert prepared.media_type == "image/png"
    assert _open(prepared.data).format == "PNG"


def test_photos_are_converted_to_jpeg():
    (prepared,) = prepare_image(_image(fmt="TIFF", color=(200, 150, 100)))
    assert prepared.media_type == "image/jpeg"


def test_cmyk_is_converted():
    (prepared,) = prepare_image(_image(mode="CMYK", fmt="JPEG"))
    assert prepared.media_type == "image/jpeg"
    assert _open(prepared.data).mode == "RGB"


def test_large_images_are_scaled_down():
    (prepared,) = prepare_image(_image(size=(MAX_SIDE * 2, MAX_SIDE), fmt="JPEG"))
    assert _open(prepared.data).size == (MAX_SIDE, MAX_SIDE // 2)


def test_photos_are_turned_upright():
    """A phone keeps a photo as the sensor saw it, saying in its EXIF how to turn it."""
    exif = Image.Exif()
    exif[Base.Orientation] = 6  # turned 90° clockwise to show
    (prepared,) = prepare_image(_image(size=(400, 300), fmt="JPEG", exif=exif))
    image = _open(prepared.data)
    assert image.size == (300, 400)
    assert image.getexif().get(Base.Orientation, 1) == 1


def test_large_files_are_made_smaller():
    """A see-through image too large as PNG: flattened onto white, as JPEG."""
    noise = Image.frombytes("RGBA", (1500, 1500), random.Random(0).randbytes(1500 * 1500 * 4))
    buf = io.BytesIO()
    noise.save(buf, "PNG")
    assert len(buf.getvalue()) > MAX_BYTES
    (prepared,) = prepare_image(buf.getvalue())
    assert len(prepared.data) <= MAX_BYTES
    assert prepared.media_type == "image/jpeg"


def test_tiff_pages():
    buf = io.BytesIO()
    first, *rest = [Image.new("L", (200, 300), shade) for shade in (0, 128, 255)]
    first.save(buf, "TIFF", save_all=True, append_images=rest)
    pages = prepare_image(buf.getvalue())
    assert len(pages) == 3
    assert [_open(page.data).getpixel((0, 0)) for page in pages] == [0, 128, 255]


def test_tiff_pages_are_limited():
    buf = io.BytesIO()
    first, *rest = [Image.new("L", (20, 20)) for _ in range(MAX_PAGES + 5)]
    first.save(buf, "TIFF", save_all=True, append_images=rest)
    assert len(prepare_image(buf.getvalue())) == MAX_PAGES


@pytest.mark.parametrize("data", [b"", b"not an image", _image()[:100]])
def test_not_an_image(data):
    with pytest.raises(ScanError):
        prepare_image(data)


def test_is_image():
    assert is_image(_image(fmt="JPEG"))
    assert not is_image(b"%PDF-1.7\n")
