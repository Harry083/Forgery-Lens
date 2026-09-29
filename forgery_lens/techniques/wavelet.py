"""Wavelet noise analysis (Mahdian & Saic 2009; Krawetz).

An orthonormal Haar wavelet transform splits the luminance into a coarse
image and detail bands. The finest diagonal band (HH) is mostly sensor and
compression noise, whose local level (σ = median|HH| / 0.6745, Donoho's
robust estimator) should be roughly even across one photograph. Material
pasted from another image, or areas that have been smoothed, painted or
cloned, often carry a different noise level.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..result import INFO, MANIPULATION, NOTABLE, WEAK, Result, View
from ..util import block_view, components, conditional_residual, outline_blocks, stretch, to_image, turbo, upscale_grid

KEY = "wavelet"
TITLE = "Wavelet noise analysis"
GUIDE = ("Noise map: each 16×16 block's noise level compared with blocks of similar texture elsewhere in the image "
         "(blue = quieter than expected, red = noisier; dim areas have nothing to measure, such as clipped highlights "
         "or flat JPEG blocks). White outlines mark blocks that stand out. Out-of-focus backgrounds and motion blur "
         "are naturally quieter, so compare against what is in focus. The residual view shows the raw noise left "
         "after a 3×3 median filter.")
BLOCK = 8          # coefficients per block side in the level-1 bands (= 16 px)
Z, STOPS = 4.0, 1.0


def haar(a: np.ndarray):
    h, w = a.shape[0] // 2 * 2, a.shape[1] // 2 * 2
    a = a[:h, :w]
    p, q, r, s = a[0::2, 0::2], a[0::2, 1::2], a[1::2, 0::2], a[1::2, 1::2]
    ll = (p + q + r + s) / 2
    lh = (p - q + r - s) / 2
    hl = (p + q - r - s) / 2
    hh = (p - q - r + s) / 2
    return ll, lh, hl, hh


def run(ex, settings) -> Result:
    res = Result(KEY, TITLE, MANIPULATION, GUIDE, params={"block_px": BLOCK * 2})
    y = ex.luma.astype(np.float32)
    bands, cur = [], y
    hh1 = ll1 = None
    for level in range(3):
        if min(cur.shape) < 32:
            break
        ll, lh, hl, hh = haar(cur)
        bands.append(np.sqrt(lh ** 2 + hl ** 2 + hh ** 2))
        if level == 0:
            hh1, ll1 = hh, ll
        cur = ll

    # Noise per block, from the level-1 diagonal band.
    sig = np.median(np.abs(block_view(hh1, BLOCK)).reshape(*block_view(hh1, BLOCK).shape[:2], -1), axis=2) / 0.6745
    lmean = block_view(ll1, BLOCK).mean(axis=(2, 3)) / 2
    mask = (lmean > 8) & (lmean < 247) & (sig >= 0.25)
    # Texture leaks into the finest band, so each block is judged against
    # blocks with similar structure (level-2 detail energy).
    b2 = bands[1] if len(bands) > 1 else bands[0]
    s2 = BLOCK // 2 if len(bands) > 1 else BLOCK
    tex = block_view(b2, s2).mean(axis=(2, 3))
    gh, gw = min(sig.shape[0], tex.shape[0]), min(sig.shape[1], tex.shape[1])
    sig, mask, tex = sig[:gh, :gw], mask[:gh, :gw], tex[:gh, :gw]
    cr = conditional_residual(np.log2(sig + 0.05), tex, mask, bins=16)
    flag = mask & (np.abs(cr.r) / max(cr.mad, 0.2) > Z) & (np.abs(cr.r) > STOPS)
    _, _, largest = components(flag)
    valid, n_flag = int(mask.sum()), int(flag.sum())
    res.metrics = {"median_noise_sigma": float(np.median(sig[mask])) if valid else None,
                   "flagged_blocks": n_flag, "largest_group": largest, "measurable_blocks": valid}

    colours = turbo((np.clip(cr.r, -2, 2) + 2) / 4)
    backdrop = np.repeat((y * 0.25)[..., None], 3, axis=2).astype(np.uint8)
    noise = np.where(upscale_grid(mask, BLOCK * 2, y.shape)[..., None],
                     upscale_grid(colours, BLOCK * 2, y.shape), backdrop)
    view = to_image(noise)
    if n_flag:
        view = outline_blocks(view, flag, BLOCK * 2, (255, 255, 255))
    res.views.append(View("Noise level map", view, "Turbo scale: ¼× · ½× · 1× · 2× · 4× the noise expected for that texture."))
    resid = y - ndimage.median_filter(y, size=3)
    res.views.append(View("Noise residual (median filter)", to_image(stretch(np.abs(resid), 0, 99.5)),
                          "What remains after removing image content; pasted areas can show a different grain."))
    for i, b in enumerate(bands):
        f = 2 ** (i + 1)
        up = np.repeat(np.repeat(b, f, axis=0), f, axis=1)
        up = np.pad(up, ((0, max(0, y.shape[0] - up.shape[0])), (0, max(0, y.shape[1] - up.shape[1]))), mode="edge")
        res.views.append(View(f"Detail bands, level {i + 1}", to_image(stretch(up[:y.shape[0], :y.shape[1]], 0, 99.5))))

    if largest >= 8 and n_flag >= max(8, valid * 0.005):
        res.add(NOTABLE, "Wavelet: a region has a different noise level",
                f"{n_flag} blocks (largest group {largest}) have a noise level well away from other areas of similar "
                "texture. Material from another photo, or areas that have been smoothed, cloned or painted, often do. "
                "So do out-of-focus backgrounds and motion blur, so compare against what's in focus.")
    elif n_flag:
        res.add(WEAK, "Wavelet: minor noise-level variation",
                f"{n_flag} scattered blocks differ in noise level. Some variation is normal in dark or very smooth areas.")
    else:
        res.add(INFO, "Wavelet: noise level is even across the image",
                "No region's noise level stands apart from areas of similar texture.")
    return res
