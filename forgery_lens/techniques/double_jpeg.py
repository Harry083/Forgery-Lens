"""Double JPEG compression from DCT coefficient histograms
(Popescu & Farid 2004; Lin et al. 2009).

A JPEG stores each 8×8 block's DCT coefficients divided by a quantisation
step. For an image compressed once, the histogram of a coefficient (in
units of its step) falls off smoothly from zero. If the image was first
saved with different steps and then saved again, which is what happens
when a JPEG is opened, edited and resaved, the double rounding leaves a
periodic pattern of peaks and gaps in the histogram.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image
from scipy import ndimage

from ..metadata import ZIGZAG
from ..result import INFO, MANIPULATION, NOTABLE, WEAK, Result, View
from ..util import line_chart, montage

KEY = "double_jpeg"
TITLE = "Double JPEG compression"
GUIDE = ("Histograms of low-frequency DCT coefficients, in units of the file's quantisation step. A single JPEG "
         "compression gives smooth, bell-shaped histograms. A regular comb of peaks and gaps means the image was "
         "compressed at least twice with different settings, i.e. opened and resaved, which is what editing a "
         "JPEG does. It doesn't show where; use ELA, JPEG ghosts and the block grid view for that.")
FREQS = list(range(1, 10))  # zig-zag positions of the first nine AC coefficients


def _dct_matrix() -> np.ndarray:
    k = np.arange(8)
    m = np.sqrt(2 / 8) * np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / 16)
    m[0] /= np.sqrt(2)
    return m


def _decoder_luma(data: bytes) -> np.ndarray | None:
    """The Y channel as the JPEG decoder produced it, before any colour
    conversion (which would blur the coefficients)."""
    im = Image.open(io.BytesIO(data))
    if im.mode not in ("L", "RGB", "YCbCr"):
        return None
    if im.mode != "L":
        im.draft("YCbCr", im.size)
        if im.mode != "YCbCr":
            return None
    a = np.asarray(im)
    return (a if a.ndim == 2 else a[..., 0]).astype(np.float64)


def periodicity(h: np.ndarray) -> float:
    """How strongly a folded coefficient histogram departs from a smooth
    fall-off in a periodic way: the largest non-trivial Fourier component
    of its ratio to a smoothed version."""
    if h.sum() < 200 or (h > 0).sum() < 6:
        return 0.0
    smooth = ndimage.uniform_filter1d(h.astype(float), 5, mode="nearest") + 1
    r = h / smooth - 1
    spec = np.abs(np.fft.rfft(r - r.mean()))
    if spec.size < 4:
        return 0.0
    return float(spec[2:].max() / np.sqrt(len(r)))


def run(ex, settings) -> Result:
    res = Result(KEY, TITLE, MANIPULATION, GUIDE)
    q = ex.meta.quant_table(0) if ex.meta.format == "JPEG" else None
    y = _decoder_luma(ex.data) if q is not None else None
    if q is None or y is None:
        res.skipped = "only applies to JPEG files"
        res.add(INFO, "Double JPEG: not a JPEG file", "This check reads JPEG quantisation data, which this file doesn't have.")
        return res
    h8, w8 = y.shape[0] // 8 * 8, y.shape[1] // 8 * 8
    blocks = (y[:h8, :w8] - 128).reshape(h8 // 8, 8, w8 // 8, 8).swapaxes(1, 2).reshape(-1, 8, 8)
    if len(blocks) > 400_000:
        blocks = blocks[:: len(blocks) // 400_000]
    m = _dct_matrix()
    coef = np.einsum("ij,njk,lk->nil", m, blocks, m)
    scores, charts = [], []
    for zz in FREQS:
        idx = ZIGZAG[zz]
        u, v = divmod(int(idx), 8)
        step = q[u, v]
        k = np.round(coef[:, u, v] / step).astype(int)
        span = int(min(60, max(8, np.percentile(np.abs(k), 99.5))))
        hist = np.bincount(np.abs(k[(k != 0) & (np.abs(k) <= span)]), minlength=span + 1)[1:]
        score = periodicity(hist)
        scores.append(score)
        if len(charts) < 6:
            full = np.bincount(k[np.abs(k) <= span] + span, minlength=2 * span + 1)
            charts.append(line_chart([("", np.arange(-span, span + 1), full, (232, 121, 59))],
                                     f"DCT ({u},{v}), step {int(step)}: periodicity {score:.2f}",
                                     "coefficient ÷ step", "blocks", size=(420, 220), bars=True))
    scores = np.array(scores)
    strong = int((scores > 1.5).sum())
    res.metrics = {"periodicity_by_frequency": [round(float(s), 3) for s in scores],
                   "strong_frequencies": strong, "blocks_used": int(len(blocks))}
    res.views.append(View("Coefficient histograms", montage(charts, 2)))
    if strong >= 3:
        res.add(NOTABLE, "Double JPEG: compressed more than once",
                f"{strong} of {len(FREQS)} low-frequency DCT histograms show a regular comb of peaks and gaps. The "
                "image was saved as a JPEG, then opened and saved again with different settings: typical of editing "
                "(or of re-uploading).")
    elif strong >= 1:
        res.add(WEAK, "Double JPEG: some sign of a second compression",
                f"{strong} of {len(FREQS)} DCT histograms show a periodic pattern. Could be a second compression at "
                "similar settings, or chance.")
    else:
        res.add(INFO, "Double JPEG: consistent with a single compression",
                "The DCT histograms fall off smoothly. A resave at identical settings, or after resizing or "
                "cropping, can't be detected this way.")
    return res
