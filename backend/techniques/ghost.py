"""JPEG ghosts (Farid 2009).

If part of an image was once saved as a JPEG at quality q1 and then pasted
into an image saved at a different quality, resaving the whole picture at
q1 changes that part very little: it "remembers" q1. Resaving at a range
of qualities and looking for regions whose difference dips at one quality
while the rest of the image doesn't reveals those ghosts.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from ..result import INFO, MANIPULATION, NOTABLE, WEAK, Result, View
from ..util import block_mean, components, label_tile, line_chart, montage, outline_blocks, resave_jpeg, to_image

KEY = "ghost"
TITLE = "JPEG ghosts"
GUIDE = ("Each tile shows how much the image changes when resaved at one quality, normalised per block across all "
         "qualities: dark means the block barely changed. Most of the image goes dark near the quality it was last "
         "saved at. A region that goes dark at a different, lower quality than its surroundings was probably saved "
         "at that quality before being pasted in.")
BLOCK = 16


def run(ex, settings) -> Result:
    qs = [q for q in settings.ghost_qualities if 10 <= q <= 100]
    res = Result(KEY, TITLE, MANIPULATION, GUIDE, params={"qualities": qs, "block": BLOCK})
    rgb = ex.rgb.astype(np.float32)
    d = np.stack([block_mean(((rgb - resave_jpeg(ex.rgb, q)) ** 2).mean(axis=2), BLOCK) for q in qs])
    lo, hi = d.min(axis=0), d.max(axis=0)
    mask = (hi - lo) > 1.0
    dn = (d - lo) / np.maximum(hi - lo, 1e-6)
    floor50 = float(np.median(d[0][mask])) if mask.any() else 0.0
    qi = ex.meta.jpeg_quality
    limit = (qi - 10) if qi else max(qs)

    best, whole = None, None
    for i, q in enumerate(qs):
        if q > limit or not mask.any():
            continue
        # The whole image dipping at one quality means it was saved at that
        # quality before its last save: an estimate of the earlier quality.
        med_here = float(np.median(dn[i][mask]))
        med_above = float(np.median(dn[i + 1:i + 3].max(axis=0)[mask])) if i + 1 < len(qs) else 0.0
        if med_here < 0.2 and med_above > med_here + 0.25 and whole is None:
            whole = q
    for i, q in enumerate(qs):
        if q > limit or not mask.any():
            continue
        bulk = float(np.median(dn[i][mask]))
        # A ghost block changes far less at q than the rest of the image does
        # at the same q. Flat blocks also change little at every quality, so
        # a block must have changed substantially at the lowest quality tried.
        ghost = mask & (dn[i] < 0.2) & (bulk - dn[i] > 0.4) & (d[0] >= 0.25 * floor50)
        _, _, largest = components(ghost)
        share = ghost.sum() / mask.sum()
        coherent = ghost.any() and largest >= 0.5 * ghost.sum()
        if bulk > 0.45 and largest >= 6 and share < 0.6 and coherent and (best is None or largest > best[2]):
            best = (q, ghost, largest, bulk)
    res.metrics = {"image_quality_estimate": qi, "measurable_blocks": int(mask.sum()),
                   "median_difference_by_quality": {q: float(np.median(d[i][mask])) if mask.any() else None
                                                    for i, q in enumerate(qs)}}

    tile_edge = 360 if len(qs) > 6 else 480
    tiles = []
    for i, q in enumerate(qs):
        g = np.where(mask, dn[i] * 255, 40).astype(np.uint8)
        s = tile_edge / max(g.shape)
        img = to_image(g).resize((max(1, round(g.shape[1] * s)), max(1, round(g.shape[0] * s))), Image.NEAREST)
        tiles.append(label_tile(img, f"quality {q}"))
    res.views.append(View("Difference at each resave quality", montage(tiles, 4)))

    if best:
        q, ghost, largest, bulk = best
        view = outline_blocks(ex.image, ghost, BLOCK, (76, 195, 255))
        res.views.append(View(f"Ghost at quality {q}", view, "Blue outlines: blocks that barely change at this quality."))
        region = [float(np.mean(dn[i][ghost])) for i in range(len(qs))]
        rest = [float(np.median(dn[i][mask & ~ghost])) for i in range(len(qs))]
        res.views.append(View("Normalised difference against quality", line_chart(
            [("ghost region", np.array(qs), np.array(region), (76, 195, 255)),
             ("rest of image", np.array(qs), np.array(rest), (232, 121, 59))],
            "Normalised difference (lower = remembers this quality)", "resave quality", "diff")))
        res.metrics.update({"ghost_quality": q, "ghost_blocks": int(ghost.sum()), "largest_group": largest})
        res.add(NOTABLE, f"JPEG ghost at quality {q}",
                f"A region of {int(ghost.sum())} blocks (largest group {largest}) barely changes when resaved at "
                f"quality {q}, while the rest of the image does" + (f" (the image itself is about quality {qi})" if qi else "") +
                ". That region was probably saved at around that quality before it was combined with the rest.")
    if whole:
        res.metrics["previous_quality_estimate"] = whole
        res.add(WEAK, f"JPEG ghosts: the whole image was previously saved at about quality {whole}",
                f"The entire image barely changes when resaved at quality {whole}, below its current quality"
                + (f" of about {qi}" if qi else "") + ". It was saved at that quality at least once before its last "
                "save: a resave, as happens after editing, cropping or re-uploading.")
    if not best:
        res.add(INFO, "JPEG ghosts: no region remembers a different quality",
                "No area dips at a lower resave quality than the rest of the image.")
    return res
