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
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageOps

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
class FaxState:
    """Mutable state the controller drives and the view mirrors."""

    source: Image.Image | None = None
    source_bytes: bytes | None = None
    resolution: int = DEFAULT_RESOLUTION
    result: ScanResult | None = None

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


def to_png_bytes(image: Image.Image) -> bytes:
    """Encode a PIL image as PNG bytes for ``ft.Image(src=...)``."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()