"""Synthetic test images, generated on the fly so the tests need no data files."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage


def scene(h: int = 512, w: int = 512, seed: int = 0) -> np.ndarray:
    """A photo-like image: smooth shapes, mid-scale texture and sensor noise."""
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w, 3))
    for sigma, amp in ((40, 70), (8, 35), (2, 20)):
        n = ndimage.gaussian_filter(rng.normal(0, 1, (h, w, 3)), (sigma, sigma, 0))
        out += n / (n.std() + 1e-9) * amp
    out += 128 + rng.normal(0, 3, (h, w, 3))
    return np.clip(out, 0, 255).astype(np.uint8)


def jpeg_bytes(rgb: np.ndarray, quality: int, **kw) -> bytes:
    b = io.BytesIO()
    Image.fromarray(rgb).save(b, "JPEG", quality=quality, **kw)
    return b.getvalue()


def png_bytes(rgb: np.ndarray, **kw) -> bytes:
    b = io.BytesIO()
    Image.fromarray(rgb).save(b, "PNG", **kw)
    return b.getvalue()


def decode(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def demosaic(scene_rgb: np.ndarray) -> np.ndarray:
    """Simulate a camera: RGGB Bayer sampling then bilinear demosaicing."""
    img = scene_rgb.astype(float)
    m = np.zeros_like(img)
    m[0::2, 0::2, 0] = img[0::2, 0::2, 0]
    m[0::2, 1::2, 1] = img[0::2, 1::2, 1]
    m[1::2, 0::2, 1] = img[1::2, 0::2, 1]
    m[1::2, 1::2, 2] = img[1::2, 1::2, 2]
    kg = np.array([[0, 1, 0], [1, 4, 1], [0, 1, 0]]) / 4
    krb = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]]) / 4
    out = np.stack([ndimage.convolve(m[..., 0], krb, mode="mirror"), ndimage.convolve(m[..., 1], kg, mode="mirror"),
                    ndimage.convolve(m[..., 2], krb, mode="mirror")], -1)
    return np.clip(out, 0, 255).astype(np.uint8)


@pytest.fixture
def base():
    return scene()


@pytest.fixture
def settings():
    from forgery_lens.settings import Settings
    return Settings()
