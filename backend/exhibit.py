"""Loading an image for examination."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path

import numpy as np
from PIL import Image

from . import metadata as md_mod
from .util import luma

# Allow large camera images without Pillow's decompression-bomb warning,
# but keep a hard ceiling so a single file can't exhaust memory.
MAX_PIXELS = 120_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ExhibitError(ValueError):
    pass


@dataclass
class Exhibit:
    name: str
    data: bytes
    rgb: np.ndarray                 # H×W×3 uint8, exactly as stored (no EXIF rotation, no colour management)
    meta: md_mod.Metadata
    path: Path | None = None
    mode: str = ""                  # Pillow's original mode, e.g. "RGB", "P", "I;16"
    loaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def width(self) -> int:
        return self.rgb.shape[1]

    @property
    def height(self) -> int:
        return self.rgb.shape[0]

    @cached_property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @cached_property
    def md5(self) -> str:
        return hashlib.md5(self.data).hexdigest()

    @cached_property
    def luma(self) -> np.ndarray:
        return luma(self.rgb)

    @cached_property
    def image(self) -> Image.Image:
        return Image.fromarray(self.rgb, "RGB")

    def describe(self) -> dict:
        d = {
            "file": self.name, "path": str(self.path) if self.path else None,
            "bytes": len(self.data), "sha256": self.sha256, "md5": self.md5,
            "format": self.meta.format, "mode": self.mode,
            "width": self.width, "height": self.height,
            "subsampling": self.meta.subsampling or None,
            "jpeg_quality_estimate": self.meta.jpeg_quality,
            "loaded_at": self.loaded_at.isoformat(),
        }
        if self.path and self.path.exists():
            d["file_modified"] = datetime.fromtimestamp(self.path.stat().st_mtime, timezone.utc).isoformat()
        return d


def _to_rgb(im: Image.Image) -> np.ndarray:
    if im.mode in ("I;16", "I;16B", "I;16L", "I"):
        a = np.asarray(im, dtype=np.float64)
        a = a / (65535.0 if a.max() > 255 else 255.0) * 255.0
        a = np.clip(a, 0, 255).astype(np.uint8)
        return np.repeat(a[..., None], 3, axis=2)
    if im.mode == "F":
        a = np.asarray(im, dtype=np.float64)
        return np.repeat(np.clip(a * (255 if a.max() <= 1 else 1), 0, 255).astype(np.uint8)[..., None], 3, axis=2)
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        # Composite onto mid-grey so transparent areas don't read as black edges.
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (128, 128, 128, 255))
        return np.asarray(Image.alpha_composite(bg, rgba).convert("RGB"))
    return np.asarray(im.convert("RGB"))


def load_bytes(data: bytes, name: str, path: Path | None = None) -> Exhibit:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:  # Pillow raises many different types for bad input
        raise ExhibitError(f"{name}: not an image Pillow can read ({e})") from e
    if im.width * im.height > MAX_PIXELS:
        raise ExhibitError(f"{name}: {im.width}×{im.height} is over the {MAX_PIXELS // 1_000_000} MP limit")
    if min(im.size) < 32:
        raise ExhibitError(f"{name}: {im.width}×{im.height} is too small to analyse")
    mode = im.mode
    rgb = np.ascontiguousarray(_to_rgb(im))
    return Exhibit(name=name, data=data, rgb=rgb, meta=md_mod.parse(data), path=path, mode=mode)


def load(path: str | Path) -> Exhibit:
    path = Path(path)
    return load_bytes(path.read_bytes(), path.name, path.resolve())
