"""Fast unit tests for the pure model layer (no Flet, no Flutter host)."""

from io import BytesIO

import pytest
from PIL import Image

from models.fax import (
    DEFAULT_RESOLUTION,
    FAX_HEADER_HEIGHT,
    PAPER,
    RESOLUTIONS,
    WORK_SIZE,
    FaxState,
    render_transmit_page,
    scan_image,
    standardize,
)


def make_image(width: int, height: int, color=(200, 100, 50), mode="RGB") -> bytes:
    buffer = BytesIO()
    Image.new(mode, (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_standardize_produces_square_rgb_canvas():
    result = standardize(make_image(300, 120))
    assert result.size == (WORK_SIZE, WORK_SIZE)
    assert result.mode == "RGB"


def test_standardize_letterboxes_without_stretching():
    result = standardize(make_image(400, 100))
    # centre keeps the photo colour, a corner is paper padding
    assert result.getpixel((WORK_SIZE // 2, WORK_SIZE // 2)) == (200, 100, 50)
    assert result.getpixel((5, 5)) == PAPER


def test_standardize_flattens_transparency_onto_paper():
    result = standardize(make_image(50, 50, (0, 0, 0, 0), mode="RGBA"))
    assert result.getpixel((WORK_SIZE // 2, WORK_SIZE // 2)) == PAPER


def test_upscales_small_images_to_fill_the_canvas():
    result = standardize(make_image(40, 40))
    # a square 40x40 is scaled up, so there is no padding
    assert result.getpixel((5, 5)) == (200, 100, 50)


def test_scan_uses_plain_mean_of_rgb():
    # solid image -> every cell equals (30 + 60 + 90) / 3 = 60
    result = scan_image(standardize(make_image(WORK_SIZE, WORK_SIZE, (30, 60, 90))), 8)
    assert result.resolution == 8
    assert result.cell_count == 64
    assert [len(row) for row in result.cells] == [8] * 8
    assert {cell for row in result.cells for cell in row} == {60}
    assert result.image_bytes.startswith(b"\x89PNG")


@pytest.mark.parametrize("resolution", RESOLUTIONS)
def test_scan_grid_shape_matches_resolution(resolution):
    result = scan_image(standardize(make_image(100, 100)), resolution)
    assert len(result.cells) == resolution
    assert all(len(row) == resolution for row in result.cells)


def test_state_load_scan_and_autoclear():
    state = FaxState()
    assert state.resolution == DEFAULT_RESOLUTION
    assert not state.has_source

    state.load(make_image(80, 40))
    assert state.has_source
    assert state.source_bytes is not None and state.source_bytes.startswith(b"\x89PNG")
    assert state.result is None

    state.scan()
    assert state.result is not None

    # loading a new image clears the previous scan automatically
    state.load(make_image(20, 20))
    assert state.result is None

    state.clear()
    assert not state.has_source
    assert state.source_bytes is None


def test_scan_without_source_raises():
    with pytest.raises(ValueError):
        FaxState().scan()


def test_render_transmit_page_adds_a_grey_header_band():
    state = FaxState()
    state.load(make_image(64, 64))
    result = state.scan()

    page = render_transmit_page(result, "555-0100", "2026-10-04 12:00")

    assert page.mode == "L"  # one grey byte per pixel, ready to stream
    assert page.size == (WORK_SIZE, WORK_SIZE + FAX_HEADER_HEIGHT)


def test_state_starts_with_an_empty_inbox():
    state = FaxState()
    assert state.received is None
    # Clearing the whiteboard must not throw away the inbox.
    state.clear()
    assert state.received is None