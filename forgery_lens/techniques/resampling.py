"""Resampling detection (Popescu & Farid 2005; Kirchner 2008).

Enlarging, shrinking or rotating an image interpolates new pixels from
their neighbours, which makes some pixels exactly predictable from others
in a periodic pattern. A fixed linear predictor's error, turned into a
"p-map", then carries periodic peaks in its Fourier spectrum. A pasted
object that was scaled or rotated to fit shows those peaks while the rest
of the image doesn't; a whole image that has been resized shows them
everywhere.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..result import INFO, MANIPULATION, NOTABLE, WEAK, Result, View
from ..util import dim, outline_blocks, stretch, to_image, turbo, upscale_grid

KEY = "resampling"
TITLE = "Resampling (resize and rotation) traces"
GUIDE = ("Left: how strongly each 128×128 tile shows the periodic pattern resizing or rotation leaves (blue = none, "
         "red = strong); outlined tiles stand out from the rest of the image. Right: the spectrum of the whole "
         "image's p-map; bright isolated dots away from the centre mean the image as a whole was resized or "
         "rotated. JPEG's own 8×8 grid and the camera's colour-filter pattern produce dots in fixed positions, and "
         "those are ignored.")
TILE, STEP = 128, 64
KERNEL = np.array([[-0.25, 0.5, -0.25], [0.5, 0.0, 0.5], [-0.25, 0.5, -0.25]])


def _ignore_mask(n: int, jpeg: bool) -> np.ndarray:
    """Spectrum bins to ignore: the low-frequency centre, the Nyquist edges
    (camera colour-filter pattern) and, for JPEGs, multiples of 1/8."""
    f = np.fft.fftshift(np.fft.fftfreq(n))
    fx, fy = np.meshgrid(f, f)
    ign = np.hypot(fx, fy) < 0.06
    ign |= (np.abs(np.abs(fx) - 0.5) < 1.5 / n) | (np.abs(np.abs(fy) - 0.5) < 1.5 / n)
    if jpeg:
        for k in range(1, 5):
            ign |= np.abs(np.abs(fx) - k / 8) < 1.5 / n
            ign |= np.abs(np.abs(fy) - k / 8) < 1.5 / n
    return ign


def _pmap(y: np.ndarray) -> np.ndarray:
    e = y - ndimage.convolve(y, KERNEL, mode="reflect")
    return np.exp(-(e ** 2))


def _peak_score(spec: np.ndarray, ign: np.ndarray) -> float:
    vals = spec[~ign]
    return float(vals.max() / (np.median(vals) + 1e-9))


def run(ex, settings) -> Result:
    res = Result(KEY, TITLE, MANIPULATION, GUIDE, params={"tile": TILE, "step": STEP})
    y = ex.luma.astype(np.float64)
    h, w = y.shape
    if min(h, w) < TILE * 2:
        res.skipped = "image too small"
        return res
    p = _pmap(y)
    win = np.outer(np.hanning(TILE), np.hanning(TILE))
    ign = _ignore_mask(TILE, ex.meta.format == "JPEG" or ex.meta.jpeg_quality is not None)
    ys = list(range(0, h - TILE + 1, STEP))
    xs = list(range(0, w - TILE + 1, STEP))
    scores = np.zeros((len(ys), len(xs)))
    acc = np.zeros((TILE, TILE))
    textured = np.zeros_like(scores, dtype=bool)
    for i, y0 in enumerate(ys):
        for j, x0 in enumerate(xs):
            t = p[y0:y0 + TILE, x0:x0 + TILE]
            spec = np.abs(np.fft.fftshift(np.fft.fft2((t - t.mean()) * win)))
            scores[i, j] = _peak_score(spec, ign)
            textured[i, j] = y[y0:y0 + TILE, x0:x0 + TILE].std() > 4
            acc += spec
    g_score = _peak_score(acc, ign)
    base = float(np.median(scores[textured])) if textured.any() else float(np.median(scores))
    mad = float(np.median(np.abs(scores[textured] - base)) * 1.4826) if textured.any() else 1.0
    flag = textured & (scores > base + 5 * max(mad, 0.5)) & (scores > 9)
    lab, n = ndimage.label(flag, structure=np.ones((3, 3)))  # overlapping tiles: count diagonal neighbours too
    largest = int(np.bincount(lab.ravel())[1:].max()) if n else 0
    res.metrics = {"global_peak_score": round(g_score, 2), "tile_median_score": round(base, 2),
                   "flagged_tiles": int(flag.sum()), "largest_group": largest}

    # Tile map at image scale (tiles overlap, so each STEP cell shows its tile's score).
    norm = np.clip((scores - base) / max(4 * max(mad, 0.5), 1e-6), 0, 1)
    colours = turbo(norm)
    grid = upscale_grid(colours, STEP, (len(ys) * STEP, len(xs) * STEP))
    canvas = dim(ex.rgb, 0.35)
    hh, ww = min(h, grid.shape[0]), min(w, grid.shape[1])
    canvas[:hh, :ww] = (0.55 * grid[:hh, :ww] + 0.45 * canvas[:hh, :ww]).astype(np.uint8)
    view = to_image(canvas)
    if flag.any():
        view = outline_blocks(view, flag, STEP, (255, 255, 255))
    res.views.append(View("Resampling traces by tile", view))
    spec_img = stretch(np.log1p(acc), 1, 99.9)
    res.views.append(View("p-map spectrum (whole image)", to_image(np.repeat(np.repeat(spec_img, 3, 0), 3, 1)),
                          "Isolated bright dots off-centre = periodic interpolation (resize/rotation)."))

    if g_score > 8:
        res.add(WEAK, "Resampling: the whole image has been resized or rotated",
                f"The p-map spectrum has strong periodic peaks (score {g_score:.1f}). The image was scaled or rotated "
                "at some point. That's common and harmless on its own (apps resize on upload), but it also removes "
                "traces other techniques rely on.")
    if largest >= 3 and flag.sum() >= 3:
        res.add(NOTABLE, "Resampling: a region was resized or rotated separately",
                f"{int(flag.sum())} tiles (largest group {largest}) show interpolation traces the rest of the image "
                "doesn't. An object scaled or rotated to fit before being pasted in leaves this signature.")
    elif flag.sum() >= 2:
        res.add(WEAK, "Resampling: a couple of tiles show interpolation traces",
                f"{int(flag.sum())} separate tiles stand out. Strong periodic texture can cause this by chance.")
    elif g_score <= 8:
        res.add(INFO, "Resampling: no resize or rotation traces", "Neither the image nor any region shows periodic interpolation.")
    return res
