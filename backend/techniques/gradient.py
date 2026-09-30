"""Luminance gradient analysis.

A scene is usually lit by a small number of light sources, so brightness
across each surface falls off in a way that follows them. The Sobel
operator measures which way and how steeply brightness changes at every
pixel; an object lit from a different direction to its surroundings can be
out of place.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..result import MANIPULATION, Result, View
from ..util import hsv_to_rgb, to_image

KEY = "gradient"
TITLE = "Luminance gradient"
GUIDE = ("Direction view: hue shows the direction brightness falls off in, brightness shows how steep the change "
         "is. Surfaces lit by the same light should share a consistent pattern; an object whose shading runs the "
         "other way can be out of place. The magnitude view colours how steep the change is on its own.")


def sobel_magnitude(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sobel gradients with replicated borders, and their magnitude."""
    gx = ndimage.sobel(gray, axis=1, mode="nearest")
    gy = ndimage.sobel(gray, axis=0, mode="nearest")
    return gx, gy, np.hypot(gx, gy)


def run(ex, settings) -> Result:
    res = Result(KEY, TITLE, MANIPULATION, GUIDE)
    gx, gy, mag = sobel_magnitude(ex.luma.astype(np.float64))
    hue = (np.arctan2(gy, gx) + np.pi) / (2 * np.pi)
    res.views.append(View("Direction and strength", to_image(hsv_to_rgb(hue, 1.0, np.clip(mag / 255.0, 0, 1)))))
    res.views.append(View("Magnitude, HSV colour map", to_image(hsv_to_rgb(np.clip(np.round(mag), 0, 255) / 256.0, 1.0, 1.0)),
                          "Gradient magnitude mapped through an HSV colour wheel."))
    res.metrics = {"mean_gradient": float(mag.mean()), "p99_gradient": float(np.percentile(mag, 99))}
    return res
