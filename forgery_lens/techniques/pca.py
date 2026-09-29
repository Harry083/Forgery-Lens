"""Principal component analysis of colour.

Every pixel's RGB value is a point in 3-D. PCA finds the axes along which
the image's colours vary most. PC1 is essentially the picture; PC3 holds
the least variance, which is where compression artefacts end up. Spliced
material often shows a different artefact pattern there, or forms its own
cluster in the PC1 against PC2 plot.
"""

from __future__ import annotations

import numpy as np

from ..result import MANIPULATION, Result, View
from ..util import stretch, to_image, turbo

KEY = "pca"
TITLE = "Principal component analysis"
GUIDE = ("PC3 carries compression artefacts: a pasted region often shows a different artefact texture or an edge "
         "halo there. The density plot shows PC1 against PC2; two separate clusters can point to material from two "
         "sources. PC1 and PC2 are included for completeness.")


def pca_components(rgb: np.ndarray, sample_limit: int = 2_000_000):
    """Principal colour axes, sorted by variance. Each vector's
    largest-magnitude element is made positive so the signs are stable."""
    x = rgb.reshape(-1, 3)
    step = max(1, x.shape[0] // sample_limit)
    s = x[::step].astype(np.float64)
    cov = np.cov(s, rowvar=False)
    vals, vecs = np.linalg.eigh(cov)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    for k in range(3):
        if vecs[np.argmax(np.abs(vecs[:, k])), k] < 0:
            vecs[:, k] = -vecs[:, k]
    return np.clip(vals, 0, None), vecs


def project(rgb: np.ndarray, vec: np.ndarray) -> np.ndarray:
    """One component of every pixel, as an H×W float32 image."""
    return rgb.astype(np.float32) @ vec.astype(np.float32)


def scatter(rgb: np.ndarray, v1: np.ndarray, v2: np.ndarray, size: int = 256) -> np.ndarray:
    x = rgb.reshape(-1, 3)
    step = max(1, x.shape[0] // 1_500_000)
    s = x[::step].astype(np.float32)
    p1, p2 = s @ v1.astype(np.float32), s @ v2.astype(np.float32)
    r1 = np.percentile(p1, [0.1, 99.9])
    r2 = np.percentile(p2, [0.1, 99.9])
    h, _, _ = np.histogram2d(p2, p1, bins=size, range=[r2, r1])
    h = h[::-1]  # PC2 up
    img = turbo(0.15 + 0.85 * np.log1p(h) / (np.log1p(h.max()) or 1))
    img[h == 0] = (20, 24, 27)
    return np.repeat(np.repeat(img, 2, axis=0), 2, axis=1)


def run(ex, settings) -> Result:
    res = Result(KEY, TITLE, MANIPULATION, GUIDE)
    vals, vecs = pca_components(ex.rgb)
    explained = vals / (vals.sum() or 1)
    res.metrics = {"explained_variance": [round(float(v), 6) for v in explained],
                   "components": [[round(float(c), 6) for c in vecs[:, k]] for k in range(3)]}
    for k in (2, 0, 1):
        comp = project(ex.rgb, vecs[:, k])
        res.views.append(View(f"PC{k + 1} ({explained[k] * 100:.2f}% of colour variance)",
                              to_image(stretch(comp)), "Contrast stretched between the 0.5th and 99.5th percentiles."))
    res.views.append(View("PC1 against PC2 density", to_image(scatter(ex.rgb, vecs[:, 0], vecs[:, 1])),
                          "PC1 left to right, PC2 bottom to top, log density."))
    return res
