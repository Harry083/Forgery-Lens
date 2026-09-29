"""Colour filter array (demosaicing) consistency
(Popescu & Farid 2005; Ferrara et al. 2012).

A camera sensor records one colour per pixel through a Bayer filter and
interpolates the rest. In the green channel, half the pixels are measured
and half are interpolated on a checkerboard, so the interpolated half is
much more predictable from its neighbours. That leaves a fixed 2×2 pattern
across the whole photo. Pasted material, retouching, resizing and
AI-generated content don't carry it, or carry it out of step.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..result import AI, INFO, MANIPULATION, NOTABLE, WEAK, Result, View
from ..util import block_view, components, dim, outline_blocks, to_image, turbo, upscale_grid

KEY = "cfa"
TITLE = "Camera demosaicing pattern (CFA)"
GUIDE = ("Colours show, for each 32×32 block, how strongly the green channel carries the camera's checkerboard "
         "interpolation pattern and whether it is in step with the rest of the photo (red = strong and in step, "
         "blue = absent or out of step; dim = too flat to measure). Outlined blocks are out of step with the image. "
         "The pattern only survives in high-quality, full-resolution images: resizing and heavier JPEG compression "
         "erase it everywhere.")
BLOCK = 32
KERNEL = np.array([[0, 0.25, 0], [0.25, 0, 0.25], [0, 0.25, 0]])


def run(ex, settings) -> Result:
    res = Result(KEY, TITLE, MANIPULATION, GUIDE, params={"block": BLOCK})
    g = ex.rgb[..., 1].astype(np.float64)
    e = g - ndimage.convolve(g, KERNEL, mode="reflect")
    e2 = e ** 2
    yy, xx = np.indices(g.shape)
    lat_a = ((yy + xx) % 2 == 0).astype(np.float64)
    va = block_view(e2 * lat_a, BLOCK).sum(axis=(2, 3)) / (block_view(lat_a, BLOCK).sum(axis=(2, 3)))
    vb = block_view(e2 * (1 - lat_a), BLOCK).sum(axis=(2, 3)) / (block_view(1 - lat_a, BLOCK).sum(axis=(2, 3)))
    lum = block_view(ex.luma, BLOCK).mean(axis=(2, 3))
    mask = ((va + vb) > 2.0) & (lum > 10) & (lum < 245)
    ratio = np.log2((va + 0.1) / (vb + 0.1))
    res.metrics = {"measurable_blocks": int(mask.sum())}
    if mask.sum() < 20:
        res.skipped = "not enough textured area to measure"
        res.add(INFO, "CFA: too little texture to measure", "The image is too flat or small for this check.")
        return res
    g_med = float(np.median(ratio[mask]))
    sign = 1.0 if g_med >= 0 else -1.0
    aligned = ratio * sign
    strength = abs(g_med)
    consistency = float((aligned[mask] > 0.15).mean())
    present = strength > 0.35 and consistency > 0.75
    res.metrics.update({"pattern_strength": round(strength, 3), "consistency": round(consistency, 3),
                        "pattern_present": present})

    colours = turbo(np.clip((aligned + 1) / 3, 0, 1))
    canvas = np.where(upscale_grid(mask, BLOCK, g.shape)[..., None], upscale_grid(colours, BLOCK, g.shape), dim(ex.rgb, 0.3))
    flag = np.zeros_like(mask)
    if present:
        flag = mask & (aligned < min(0.1, g_med * sign * 0.2))
        _, _, largest = components(flag)
        res.metrics.update({"out_of_step_blocks": int(flag.sum()), "largest_group": largest})
        view = to_image(canvas)
        if flag.any():
            view = outline_blocks(view, flag, BLOCK, (255, 255, 255))
        res.views.append(View("Demosaicing pattern by block", view))
        coherent = flag.any() and largest >= 0.4 * flag.sum()
        if largest >= 6 and flag.sum() >= max(6, mask.sum() * 0.01) and coherent:
            res.add(NOTABLE, "CFA: a region lacks the camera's demosaicing pattern",
                    f"The image carries a clear camera interpolation pattern, but {int(flag.sum())} blocks (largest "
                    f"group {largest}) don't. Material pasted from another image, painted, cloned or generated there "
                    "would look like this.")
        elif flag.any():
            res.add(WEAK, "CFA: a few blocks are out of step",
                    f"{int(flag.sum())} scattered blocks lack the pattern. Saturated or very smooth areas can do this.")
        else:
            res.add(INFO, "CFA: camera demosaicing pattern present throughout",
                    f"Strength {strength:.2f}, consistent in {consistency:.0%} of measurable blocks. That's what a "
                    "full-resolution camera image looks like.")
            res.add(INFO, "Camera demosaicing pattern found",
                    "A consistent camera interpolation pattern is a sign of a real camera capture. Generated images "
                    "don't normally have one, though it can be imitated.", AI)
    else:
        res.views.append(View("Demosaicing pattern by block", to_image(canvas)))
        has_camera = bool(ex.meta.exif and ex.meta.exif["ifd0"].get("Model"))
        jq = ex.meta.jpeg_quality
        full_quality = (not ex.meta.lossy) or (jq is not None and jq >= 95)
        res.add(INFO, "CFA: no camera demosaicing pattern",
                "There's no consistent interpolation pattern. That's normal for resized or compressed images, "
                "which is most images shared online, and it's also true of AI-generated images.")
        if has_camera and full_quality:
            res.add(WEAK, "No demosaicing pattern despite camera metadata and high quality",
                    "The file claims a camera origin and is saved at high quality, where the camera's interpolation "
                    "pattern usually survives, yet none is present. It may have been resized, heavily processed, "
                    "or generated.", AI)
    return res
