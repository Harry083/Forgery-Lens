"""JPEG block-grid alignment (blocking artefact grid; Li, Yuan & Yu 2009).

JPEG compresses in 8×8 blocks, leaving faint steps along the block edges.
In an untouched image those steps line up on one grid starting at the top
left. A piece pasted from another JPEG brings its own grid, which rarely
lines up with the host's; an image cropped after compression has its whole
grid shifted.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from ..result import INFO, MANIPULATION, NOTABLE, WEAK, Result, View
from ..util import components, dim

KEY = "jpeg_grid"
TITLE = "JPEG block grid"
GUIDE = ("Each 64×64 tile is coloured by where its 8×8 JPEG block grid starts: green tiles match the grid of the "
         "image as a whole, orange tiles have a grid that is offset from it, and uncoloured tiles show no clear "
         "grid (smooth areas, or content never compressed as a JPEG). A cluster of orange tiles suggests a pasted "
         "piece of another JPEG.")
TILE = 64


def _boundary_strength(y: np.ndarray, axis: int) -> np.ndarray:
    """How much stronger the step between neighbouring pixels is than the
    steps either side of it. Large (true edge) values are clipped so real
    image edges don't swamp the faint JPEG block steps."""
    d = np.abs(np.diff(y, axis=axis))
    side = np.zeros_like(d)
    if axis == 1:
        side[:, 1:-1] = (d[:, :-2] + d[:, 2:]) / 2
    else:
        side[1:-1, :] = (d[:-2, :] + d[2:, :]) / 2
    b = np.clip(d - side, 0, 6)
    pad = [(0, 0), (0, 0)]
    pad[axis] = (0, 1)
    return np.pad(b, pad)  # value at index x = step between x and x+1


def _phase(v: np.ndarray):
    """Strongest of 8 phases, and how clearly it stands out."""
    med = np.median(v, axis=-1)
    return v.argmax(axis=-1), (v.max(axis=-1) - med) / (med + 1e-3)


def run(ex, settings) -> Result:
    res = Result(KEY, TITLE, MANIPULATION, GUIDE, params={"tile": TILE})
    y = ex.luma.astype(np.float32)
    h, w = y.shape
    gh, gw = h // TILE, w // TILE
    if gh < 2 or gw < 2:
        res.skipped = "image too small"
        return res
    bh = _boundary_strength(y, 1)[: gh * TILE, : gw * TILE]
    bv = _boundary_strength(y, 0)[: gh * TILE, : gw * TILE]
    k = TILE // 8
    col = bh.reshape(gh, TILE, gw, k, 8).sum(axis=(1, 3))          # (gh, gw, 8): column phase
    row = bv.reshape(gh, k, 8, gw, TILE).sum(axis=(1, 4)).transpose(0, 2, 1)  # (gh, gw, 8)
    gpx, gcx = _phase(col.sum(axis=(0, 1)))
    gpy, gcy = _phase(row.sum(axis=(0, 1)))
    px, cx = _phase(col)
    py, cy = _phase(row)
    confident = (cx > 0.6) & (cy > 0.6)
    grid_found = gcx > 0.25 and gcy > 0.25
    misaligned = confident & ((px != gpx) | (py != gpy)) if grid_found else np.zeros_like(confident)
    _, _, largest = components(misaligned)
    # Offsets are reported as where the grid starts (0 = aligned with the top-left corner).
    off_x, off_y = (int(gpx) + 1) % 8, (int(gpy) + 1) % 8
    res.metrics = {"grid_found": bool(grid_found), "grid_offset": [off_x, off_y],
                   "grid_strength": [round(float(gcx), 3), round(float(gcy), 3)],
                   "confident_tiles": int(confident.sum()), "misaligned_tiles": int(misaligned.sum()),
                   "largest_misaligned_group": largest}

    img = Image.fromarray(dim(ex.rgb, 0.6)).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for ty in range(gh):
        for tx in range(gw):
            if not confident[ty, tx]:
                continue
            colour = (232, 121, 59, 150) if misaligned[ty, tx] else (62, 207, 142, 70)
            d.rectangle([tx * TILE, ty * TILE, tx * TILE + TILE - 1, ty * TILE + TILE - 1], fill=colour)
    res.views.append(View("Block grid alignment by tile", Image.alpha_composite(img, layer).convert("RGB")))

    if not grid_found:
        res.add(INFO, "JPEG grid: no block grid found",
                "There's no consistent 8×8 blocking pattern, so the image was never JPEG-compressed, was saved at very "
                "high quality, or has been resized since.")
        return res
    if (off_x, off_y) != (0, 0):
        res.add(NOTABLE, f"JPEG grid is shifted by ({off_x}, {off_y}) pixels",
                "The image's JPEG block grid doesn't start at the top-left corner. It was probably cropped (or "
                "padded) after it was last compressed as a JPEG" + (", then saved in a lossless format." if not ex.meta.lossy else "."))
    if largest >= 4 and misaligned.sum() >= max(4, confident.sum() * 0.03):
        res.add(NOTABLE, "JPEG grid: a region's block grid doesn't line up",
                f"{int(misaligned.sum())} tiles (largest group {largest}) carry an 8×8 grid offset from the rest of "
                "the image. A piece of another JPEG pasted in usually does.")
    elif misaligned.any():
        res.add(WEAK, "JPEG grid: a few tiles are out of line",
                f"{int(misaligned.sum())} isolated tiles show an offset grid. Strong textures can do this by chance.")
    elif (off_x, off_y) == (0, 0):
        res.add(INFO, "JPEG grid: aligned throughout", "Every tile with a clear grid lines up with the image's grid.")
    return res
