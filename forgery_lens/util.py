"""Array, statistics and drawing helpers shared by the techniques."""

from __future__ import annotations

import io
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

ACCENT = (232, 121, 59)


# ---------------------------------------------------------------- arrays

def luma(rgb: np.ndarray) -> np.ndarray:
    """ITU-R BT.601 luminance as float32."""
    rgb = rgb.astype(np.float32)
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def block_view(a: np.ndarray, b: int) -> np.ndarray:
    """Crop to a multiple of b and reshape to (rows, cols, b, b[, ...])."""
    h, w = a.shape[0] // b * b, a.shape[1] // b * b
    a = a[:h, :w]
    shape = (h // b, b, w // b, b) + a.shape[2:]
    return a.reshape(shape).swapaxes(1, 2)


def block_mean(a: np.ndarray, b: int) -> np.ndarray:
    return block_view(a, b).mean(axis=(2, 3))


def resave_jpeg(rgb: np.ndarray, quality: int) -> np.ndarray:
    """Encode as JPEG at the given quality and decode again."""
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=int(quality))
    buf.seek(0)
    return np.asarray(Image.open(buf).convert("RGB"))


def upscale_grid(grid: np.ndarray, block: int, shape: tuple[int, int]) -> np.ndarray:
    """Nearest-neighbour expand a per-block grid back to image size."""
    out = np.repeat(np.repeat(grid, block, axis=0), block, axis=1)
    h, w = shape
    pad_h, pad_w = max(0, h - out.shape[0]), max(0, w - out.shape[1])
    if pad_h or pad_w:
        pad = ((0, pad_h), (0, pad_w)) + ((0, 0),) * (out.ndim - 2)
        out = np.pad(out, pad, mode="edge")
    return out[:h, :w]


# ---------------------------------------------------------------- statistics

class Residual(NamedTuple):
    r: np.ndarray
    mad: float


def conditional_residual(values: np.ndarray, covariate: np.ndarray,
                         mask: np.ndarray | None = None, bins: int = 16) -> Residual:
    """How far each value sits from what is typical for similar blocks.

    Blocks are sorted by the covariate (usually texture), cut into
    equal-sized bins and compared with their bin's median. Returns the
    residuals and their robust spread (MAD scaled to a standard deviation).
    """
    v, c = values.ravel(), covariate.ravel()
    idx = np.flatnonzero(mask.ravel()) if mask is not None else np.arange(v.size)
    r = np.zeros(v.size, dtype=np.float64)
    if idx.size == 0:
        return Residual(r.reshape(values.shape), 1e-3)
    idx = idx[np.argsort(c[idx], kind="stable")]
    per = max(8, int(np.ceil(idx.size / bins)))
    for s in range(0, idx.size, per):
        grp = idx[s:s + per]
        r[grp] = v[grp] - np.median(v[grp])
    mad = float(np.median(np.abs(r[idx])) * 1.4826) or 1e-3
    return Residual(r.reshape(values.shape), mad)


def components(flag: np.ndarray) -> tuple[np.ndarray, int, int]:
    """4-connected components: (labels, count, size of the largest)."""
    labels, n = ndimage.label(flag)
    if n == 0:
        return labels, 0, 0
    sizes = np.bincount(labels.ravel())[1:]
    return labels, n, int(sizes.max())


# ---------------------------------------------------------------- colour

def stretch(a: np.ndarray, lo_pct: float = 0.5, hi_pct: float = 99.5,
            lo: float | None = None, hi: float | None = None) -> np.ndarray:
    """Linear contrast stretch to uint8."""
    if lo is None or hi is None:
        sample = a.ravel()
        if sample.size > 400_000:
            sample = sample[:: sample.size // 400_000]
        lo, hi = np.percentile(sample, [lo_pct, hi_pct])
    span = (hi - lo) or 1.0
    return np.clip((a - lo) / span * 255, 0, 255).astype(np.uint8)


def turbo(t: np.ndarray) -> np.ndarray:
    """Google's Turbo colour map (polynomial fit). t in [0, 1] -> uint8 RGB."""
    t = np.clip(t, 0, 1).astype(np.float32)
    r = 0.13572138 + t * (4.61539260 + t * (-42.66032258 + t * (132.13108234 + t * (-152.94239396 + t * 59.28637943))))
    g = 0.09140261 + t * (2.19418839 + t * (4.84296658 + t * (-14.18503333 + t * (4.27729857 + t * 2.82956604))))
    b = 0.10667330 + t * (12.64194608 + t * (-60.58204836 + t * (110.36276771 + t * (-89.90310912 + t * 27.34824973))))
    return (np.clip(np.stack([r, g, b], axis=-1), 0, 1) * 255).astype(np.uint8)


def hsv_to_rgb(h: np.ndarray, s: np.ndarray | float, v: np.ndarray | float) -> np.ndarray:
    """Vectorised HSV -> uint8 RGB. All inputs in [0, 1]."""
    h6 = (np.mod(h, 1.0) * 6).astype(np.float32)
    pure = np.stack([np.abs(h6 - 3) - 1, 2 - np.abs(h6 - 2), 2 - np.abs(h6 - 4)], axis=-1)
    pure = np.clip(pure, 0, 1)
    s = np.asarray(s, dtype=np.float32)[..., None] if np.ndim(s) else np.float32(s)
    v = np.asarray(v, dtype=np.float32)[..., None] if np.ndim(v) else np.float32(v)
    return (np.clip(v * (1 - s + s * pure), 0, 1) * 255 + 0.5).astype(np.uint8)


def dim(rgb: np.ndarray, factor: float = 0.45) -> np.ndarray:
    """Dimmed greyscale copy, used as a backdrop for overlays."""
    return np.repeat((luma(rgb) * factor)[..., None], 3, axis=2).astype(np.uint8)


# ---------------------------------------------------------------- drawing

def to_image(a: np.ndarray) -> Image.Image:
    if a.ndim == 2:
        return Image.fromarray(a.astype(np.uint8), "L").convert("RGB")
    return Image.fromarray(a.astype(np.uint8), "RGB")


def line_width(size: tuple[int, int]) -> int:
    return max(1, round(max(size) / 700))


def outline_blocks(img: Image.Image, flag: np.ndarray, block: int,
                   colour: tuple[int, int, int] = ACCENT) -> Image.Image:
    """Draw a rectangle round every flagged block."""
    img = img.copy()
    d = ImageDraw.Draw(img)
    lw = line_width(img.size)
    for by, bx in zip(*np.nonzero(flag)):
        x0, y0 = int(bx * block), int(by * block)
        d.rectangle([x0, y0, x0 + block - 1, y0 + block - 1], outline=colour, width=lw)
    return img


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def label_tile(img: Image.Image, text: str) -> Image.Image:
    """Add a caption strip under a small image (for montages)."""
    f = font(max(12, img.width // 14))
    strip = max(18, img.width // 9)
    out = Image.new("RGB", (img.width, img.height + strip), (28, 32, 35))
    out.paste(img, (0, 0))
    ImageDraw.Draw(out).text((6, img.height + 3), text, fill=(238, 241, 242), font=f)
    return out


def montage(tiles: list[Image.Image], cols: int, gap: int = 6) -> Image.Image:
    rows = (len(tiles) + cols - 1) // cols
    tw = max(t.width for t in tiles)
    th = max(t.height for t in tiles)
    out = Image.new("RGB", (cols * tw + (cols - 1) * gap, rows * th + (rows - 1) * gap), (20, 24, 27))
    for i, t in enumerate(tiles):
        out.paste(t, ((i % cols) * (tw + gap), (i // cols) * (th + gap)))
    return out


def thumbnail(img: Image.Image, max_edge: int) -> Image.Image:
    if max(img.size) <= max_edge:
        return img
    s = max_edge / max(img.size)
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)


def line_chart(series: list[tuple[str, np.ndarray, np.ndarray, tuple[int, int, int]]],
               title: str, xlabel: str, ylabel: str, size: tuple[int, int] = (640, 300),
               bars: bool = False) -> Image.Image:
    """Tiny dependency-free chart: one or more (label, x, y, colour) series."""
    w, h = size
    img = Image.new("RGB", size, (28, 32, 35))
    d = ImageDraw.Draw(img)
    f = font(12)
    left, right, top, bottom = 48, 12, 26, 34
    xs = np.concatenate([s[1] for s in series]).astype(float)
    ys = np.concatenate([s[2] for s in series]).astype(float)
    x0, x1 = float(xs.min()), float(xs.max()) or 1.0
    y0, y1 = float(min(0.0, ys.min())), float(ys.max()) or 1.0
    if x1 == x0:
        x1 = x0 + 1
    if y1 == y0:
        y1 = y0 + 1

    def px(x, y):
        return (left + (x - x0) / (x1 - x0) * (w - left - right),
                h - bottom - (y - y0) / (y1 - y0) * (h - top - bottom))

    d.rectangle([left, top, w - right, h - bottom], outline=(70, 78, 84))
    d.text((left, 6), title, fill=(238, 241, 242), font=f)
    d.text((left, h - 16), xlabel, fill=(169, 179, 184), font=f)
    d.text((4, top), ylabel, fill=(169, 179, 184), font=f)
    d.text((4, h - bottom - 12), f"{y0:g}", fill=(127, 139, 145), font=f)
    d.text((4, top + 12), f"{y1:.3g}", fill=(127, 139, 145), font=f)
    d.text((left, h - bottom + 2), f"{x0:g}", fill=(127, 139, 145), font=f)
    d.text((w - right - 30, h - bottom + 2), f"{x1:g}", fill=(127, 139, 145), font=f)
    ly = top + 4
    for label, x, y, colour in series:
        pts = [px(a, b) for a, b in zip(x, y)]
        if bars:
            bw = max(1, (w - left - right) / max(1, len(x)) * 0.8)
            for (cx, cy) in pts:
                d.rectangle([cx - bw / 2, cy, cx + bw / 2, h - bottom], fill=colour)
        elif len(pts) > 1:
            d.line(pts, fill=colour, width=2)
        if label:
            d.text((w - right - 150, ly), label, fill=colour, font=f)
            ly += 14
    return img
