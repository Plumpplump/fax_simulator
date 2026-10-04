"""Model layer: dataclasses + pure image logic for the fax simulator.

Deliberately free of any Flet import so the whole layer is unit-testable
without a running UI (see ``tests/test_model.py``).

Pipeline (``plan.txt``):
    load  -> decode bytes, fix EXIF orientation, flatten transparency, then
             letterbox onto a fixed ``WORK_SIZE`` square so the original and
             the scan always share one frame.
    scan  -> shrink the square to ``resolution x resolution`` (BOX = block
             average), turn each cell into grey = mean(R, G, B), then blow the
             grid back up for display with faint cell borders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

# Side of the standardized square canvas used for both display and scanning.
WORK_SIZE = 512

# Grid sizes offered in the resolution dropdown. 512 matches WORK_SIZE, so a
# 512 x 512 scan is one averaged cell per source pixel - the finest possible.
RESOLUTIONS: tuple[int, ...] = (8, 16, 32, 64, 128, 256, 512)

# Paper colour used for letterbox padding and for alpha flattening.
PAPER: tuple[int, int, int] = (255, 255, 255)

# Faint colour of the grid lines drawn on the scanned sheet.
GRID_LINE: tuple[int, int, int] = (200, 200, 200)

# Only outline cells when they are at least this many pixels wide on screen;
# drawing the border of a 1-2 px cell would just flood the sheet with grey.
MIN_GRID_STEP = 8

DEFAULT_RESOLUTION = 32

# Height (in pixels) of the small "from / date" band printed above the grid,
# mimicking the header line a real fax machine burns into every page.
FAX_HEADER_HEIGHT = 30


@dataclass
class ScanResult:
    """One scan of the source image at a single grid resolution."""

    resolution: int
    cells: list[list[int]] = field(default_factory=list)
    image_bytes: bytes = b""

    @property
    def cell_count(self) -> int:
        return self.resolution * self.resolution


@dataclass
class ReceivedFax:
    """A fax that arrived from the network and is now sitting in the inbox.

    The controller builds one of these when a transmission finishes, then the
    view shows ``image_bytes`` and offers to Save it to disk.
    """

    station: str
    resolution: int
    width: int
    height: int
    image_bytes: bytes
    received_at: datetime
    sender: str = ""


@dataclass
class FaxState:
    """Mutable state the controller drives and the view mirrors."""

    source: Image.Image | None = None
    source_bytes: bytes | None = None
    resolution: int = DEFAULT_RESOLUTION
    result: ScanResult | None = None
    # The last fax received from the network (the "inbox"). Unlike the scan,
    # ``clear()`` deliberately leaves this alone - an inbox is not a whiteboard.
    received: ReceivedFax | None = None

    @property
    def has_source(self) -> bool:
        return self.source is not None

    def clear(self) -> None:
        """Drop the loaded image and any scan."""
        self.source = None
        self.source_bytes = None
        self.result = None

    def load(self, data: bytes) -> None:
        """Standardize raw image bytes; a new image auto-clears the old scan."""
        self.source = standardize(data)
        self.source_bytes = to_png_bytes(self.source)
        self.result = None

    def scan(self) -> ScanResult:
        """Scan the loaded image at the currently selected resolution."""
        if self.source is None:
            raise ValueError("load an image before scanning")
        self.result = scan_image(self.source, self.resolution)
        return self.result


def _flatten(image: Image.Image) -> Image.Image:
    """Composite any transparency onto paper and force RGB."""
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGBA", image.size, PAPER + (255,))
        image = Image.alpha_composite(background, image)
    return image.convert("RGB")


def standardize(data: bytes) -> Image.Image:
    """Decode arbitrary image bytes onto the canonical ``WORK_SIZE`` square."""
    with Image.open(BytesIO(data)) as raw:
        image = _flatten(ImageOps.exif_transpose(raw))
        # Scale to fit the square: shrinks big photos and enlarges small ones.
        image = ImageOps.contain(
            image, (WORK_SIZE, WORK_SIZE), Image.Resampling.LANCZOS
        )

        canvas = Image.new("RGB", (WORK_SIZE, WORK_SIZE), PAPER)
        offset = (
            (WORK_SIZE - image.width) // 2,
            (WORK_SIZE - image.height) // 2,
        )
        canvas.paste(image, offset)
    return canvas


def scan_image(source: Image.Image, resolution: int) -> ScanResult:
    """Average each grid cell to one grey value and render the fax sheet."""
    cell_image = source.resize((resolution, resolution), Image.Resampling.BOX)
    rgb = np.asarray(cell_image, dtype=np.float64)

    # plan.txt: a pixel's grey is the plain mean of R, G, B - not Rec.601 luma.
    grey = rgb.mean(axis=2).round().astype(np.uint8)

    sheet = _render_sheet(Image.fromarray(grey))
    return ScanResult(
        resolution=resolution,
        cells=grey.tolist(),
        image_bytes=to_png_bytes(sheet),
    )


def _render_sheet(grid: Image.Image) -> Image.Image:
    """Upscale the ``n x n`` grid to ``WORK_SIZE`` and outline the cells."""
    sheet = grid.resize((WORK_SIZE, WORK_SIZE), Image.Resampling.NEAREST).convert("RGB")

    step = WORK_SIZE / grid.width
    if grid.width > 1 and step >= MIN_GRID_STEP:
        draw = ImageDraw.Draw(sheet)
        for i in range(1, grid.width):
            pos = round(i * step)
            draw.line([(pos, 0), (pos, WORK_SIZE)], fill=GRID_LINE, width=1)
            draw.line([(0, pos), (WORK_SIZE, pos)], fill=GRID_LINE, width=1)
    return sheet


def render_transmit_page(
    scan: ScanResult,
    station: str,
    sent_at: str | None = None,
    page_width: int = WORK_SIZE,
) -> Image.Image:
    """Build the greyscale page that actually gets transmitted.

    Real fax machines print a header line ("from" + date/time) above the image,
    so we bake one in. The page is returned in mode ``"L"`` - one grey byte per
    pixel - because the sender streams it row by row and that is the cheapest,
    most fax-like representation.
    """
    sent_at = sent_at or datetime.now().strftime("%Y-%m-%d %H:%M")

    # 1. The averaged grid, upscaled so every page has the same width.
    grid = Image.fromarray(np.asarray(scan.cells, dtype=np.uint8), mode="L")
    sheet = grid.resize((page_width, page_width), Image.Resampling.NEAREST)

    # 2. Faint cell borders, matching the on-screen sheet.
    step = page_width / grid.width
    if grid.width > 1 and step >= MIN_GRID_STEP:
        draw = ImageDraw.Draw(sheet)
        for i in range(1, grid.width):
            pos = round(i * step)
            draw.line([(pos, 0), (pos, page_width)], fill=GRID_LINE[0], width=1)
            draw.line([(0, pos), (page_width, pos)], fill=GRID_LINE[0], width=1)

    # 3. The header band: text on paper white, with a thin rule underneath.
    band = Image.new("L", (page_width, FAX_HEADER_HEIGHT), PAPER[0])
    band_draw = ImageDraw.Draw(band)
    band_draw.text(
        (8, 8),
        f"FROM {station}    {sent_at}",
        fill=0,
        font=ImageFont.load_default(size=14),
    )
    band_draw.line(
        [(0, FAX_HEADER_HEIGHT - 1), (page_width, FAX_HEADER_HEIGHT - 1)],
        fill=120,
        width=1,
    )

    # 4. Stack the band on top of the grid.
    page = Image.new("L", (page_width, page_width + FAX_HEADER_HEIGHT), PAPER[0])
    page.paste(band, (0, 0))
    page.paste(sheet, (0, FAX_HEADER_HEIGHT))
    return page


def to_png_bytes(image: Image.Image) -> bytes:
    """Encode a PIL image as PNG bytes for ``ft.Image(src=...)``."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()