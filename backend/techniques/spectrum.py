"""Noise-residual frequency spectrum (Zhang et al. 2019; Durall et al. 2020;
Frank et al. 2020; Corvi et al. 2023).

Image generators build pictures up through learned upsampling layers (in
GANs, and in the decoder that turns a latent diffusion model's 8×-smaller
latent into pixels). These leave faint periodic patterns that are invisible
in the picture but show as isolated peaks in the Fourier spectrum of its
noise residual. Real photos have a smooth residual spectrum, apart from
peaks explained by JPEG's 8-pixel grid or the camera's 2-pixel colour
filter.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from ..result import AI, INFO, WEAK, Result, View
from ..util import line_chart, stretch

KEY = "spectrum"
TITLE = "Noise spectrum (AI generation)"
GUIDE = ("The averaged Fourier spectrum of the image's noise residual; the centre is zero frequency. Natural photos "
         "give a smooth glow. Isolated bright dots are periodic patterns: circled orange if nothing ordinary explains "
         "them, which is typical of AI generators' upsampling (and of resizing, so check the resampling view), grey "
         "if they sit on JPEG's 8-pixel grid, blue on the 2-pixel colour-filter frequency. The chart shows the "
         "average power at each frequency.")
EXCESS = 0.5          # log10 units above the local background (≈3×)
STRONG = 0.8          # ≈6×


def residual_spectrum(y: np.ndarray, tile: int, max_tiles: int = 256) -> tuple[np.ndarray, int]:
    """Welch-averaged power spectrum (fftshifted) of the median-filter
    residual, over half-overlapping tiles. Returns it with the tile count."""
    r = y - ndimage.median_filter(y, size=3)
    h, w = r.shape
    step = tile // 2
    coords = [(a, b) for a in range(0, h - tile + 1, step) for b in range(0, w - tile + 1, step)]
    if len(coords) > max_tiles:
        coords = [coords[i] for i in np.linspace(0, len(coords) - 1, max_tiles).astype(int)]
    win = np.outer(np.hanning(tile), np.hanning(tile))
    acc = np.zeros((tile, tile))
    for a, b in coords:
        t = r[a:a + tile, b:b + tile]
        acc += np.abs(np.fft.fft2((t - t.mean()) * win)) ** 2
    return np.fft.fftshift(acc / max(1, len(coords))), len(coords)


def find_peaks(logp: np.ndarray, threshold: float):
    n = logp.shape[0]
    bg = ndimage.median_filter(logp, size=11, mode="wrap")
    excess = logp - bg
    f = np.fft.fftshift(np.fft.fftfreq(n))
    fx, fy = np.meshgrid(f, f)
    is_max = logp == ndimage.maximum_filter(logp, size=5, mode="wrap")
    # A periodic pattern gives an isolated dot. Edges in the scene give
    # streaks, whose points have bright neighbours along the streak, so a
    # peak must also stand clear of the brightest point in a ring round it.
    yy, xx = np.mgrid[-4:5, -4:5]
    ring = (np.hypot(yy, xx) >= 2.5) & (np.hypot(yy, xx) <= 4.5)
    isolated = logp - ndimage.maximum_filter(logp, footprint=ring, mode="wrap") > threshold * 0.5
    keep = is_max & isolated & (excess > threshold) & (np.hypot(fx, fy) > 0.04)
    keep &= (fy > 0) | ((fy == 0) & (fx > 0))           # one of each symmetric pair
    peaks = []
    for iy, ix in zip(*np.nonzero(keep)):
        peaks.append({"fx": float(fx[iy, ix]), "fy": float(fy[iy, ix]), "excess": float(excess[iy, ix]),
                      "row": int(iy), "col": int(ix)})
    return sorted(peaks, key=lambda p: -p["excess"]), excess


def classify(p: dict, n: int, jpeg: bool) -> str:
    tol = 1.5 / n
    near = lambda v, k: abs(abs(v) - k) < tol  # noqa: E731
    if near(p["fx"], 0.5) or near(p["fy"], 0.5):
        return "nyquist"
    # JPEG blocking puts energy along whole lines at multiples of 1/8.
    if any(min(abs(abs(v) - k / 8) for k in range(1, 5)) < tol for v in (p["fx"], p["fy"])):
        return "grid8_jpeg" if jpeg else "grid8"
    return "unexplained"


def radial_profile(logp: np.ndarray):
    n = logp.shape[0]
    yy, xx = np.indices(logp.shape)
    r = np.hypot(yy - n // 2, xx - n // 2) / n
    bins = np.linspace(0, 0.5, 51)
    idx = np.digitize(r.ravel(), bins)
    prof = np.array([logp.ravel()[idx == i].mean() if np.any(idx == i) else np.nan for i in range(1, len(bins))])
    return (bins[:-1] + bins[1:]) / 2, prof


def run(ex, settings) -> Result:
    res = Result(KEY, TITLE, AI, GUIDE)
    y = ex.luma.astype(np.float64)
    tile = 256 if min(y.shape) >= 1024 else 128
    if min(y.shape) < tile:
        res.skipped = "image too small"
        res.add(INFO, "Noise spectrum: image too small", "The image is too small for a reliable spectrum.")
        return res
    p, n_tiles = residual_spectrum(y, tile)
    if n_tiles < 9:
        res.skipped = "image too small"
        res.add(INFO, "Noise spectrum: image too small", "Too few tiles to average for a reliable spectrum.")
        return res
    # An averaged periodogram's log wobbles by about 0.43/sqrt(K) (more with
    # overlapping tiles), so peaks must clear several times that.
    sigma = 0.6 / np.sqrt(n_tiles)
    threshold, strong = max(EXCESS, 6 * sigma), max(STRONG, 9 * sigma)
    logp = np.log10(p + 1e-9)
    peaks, _ = find_peaks(logp, threshold)
    jpeg_history = ex.meta.lossy or ex.meta.jpeg_quality is not None
    for pk in peaks:
        pk["kind"] = classify(pk, tile, jpeg_history)
    unexplained = [pk for pk in peaks if pk["kind"] == "unexplained"]
    grid8 = [pk for pk in peaks if pk["kind"] == "grid8"]
    strong_un = [pk for pk in unexplained if pk["excess"] > strong]
    freqs, prof = radial_profile(logp)
    hi = np.nanmean(prof[(freqs > 0.35)]) - np.nanmean(prof[(freqs > 0.1) & (freqs < 0.2)])
    res.params = {"tile": tile, "tiles_averaged": n_tiles, "peak_threshold_log10": round(threshold, 3)}
    res.metrics = {
        "peaks": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in pk.items() if k not in ("row", "col")}
                  for pk in peaks[:20]],
        "unexplained_peaks": len(unexplained), "strong_unexplained_peaks": len(strong_un),
        "grid8_peaks_without_jpeg": len(grid8), "high_frequency_falloff": round(float(hi), 3),
    }

    img = Image.fromarray(stretch(logp, 1, 99.9)).convert("RGB").resize((tile * 2, tile * 2), Image.NEAREST)
    d = ImageDraw.Draw(img)
    colours = {"unexplained": (232, 121, 59), "grid8": (232, 121, 59), "grid8_jpeg": (150, 160, 166), "nyquist": (76, 195, 255)}
    for pk in peaks[:40]:
        for (r0, c0) in ((pk["row"], pk["col"]), (tile - pk["row"], tile - pk["col"])):
            cx, cy = c0 * 2 + 1, r0 * 2 + 1
            d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], outline=colours[pk["kind"]], width=2)
    res.views.append(View("Noise residual spectrum", img))
    res.views.append(View("Average power by frequency", line_chart(
        [("", freqs, prof, (232, 121, 59))], "Residual power by spatial frequency (log10)",
        "cycles per pixel (0 to 0.5)", "log power")))

    # Per-image spectral cues are weak evidence (they are clearest averaged
    # over many images), so this check never goes above "minor" on its own.
    if len(strong_un) >= 2 or len(unexplained) >= 3:
        res.add(WEAK, "Periodic artefacts in the noise",
                f"The noise residual's spectrum has {len(unexplained)} isolated peak pair(s) that neither JPEG "
                "compression nor a camera's colour filter explains. Generator upsampling layers can leave peaks like "
                "these, but so can resizing, rotation or a repeating texture, so check the resampling view and treat "
                "this as a hint only.")
    elif unexplained:
        res.add(INFO, "A faint periodic artefact in the noise",
                f"{len(unexplained)} weak unexplained spectral peak(s): most often resizing or a repeating texture.")
    if grid8:
        res.add(WEAK, "8-pixel periodic pattern in a lossless image",
                "The residual repeats every 8 pixels, but the file isn't a JPEG. Either it was a JPEG before being "
                "converted, or it came from a latent-diffusion model, whose decoder upsamples its latent 8×.")
    if not peaks:
        res.add(INFO, "Noise spectrum is smooth", "No periodic artefacts were found in the noise residual.")
    return res
