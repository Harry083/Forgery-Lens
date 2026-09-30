"""Error level analysis (Krawetz 2007).

JPEG discards detail in 8×8 blocks every time an image is saved, and each
resave loses less than the one before. Resaving at a known quality and
measuring how much each pixel changes shows areas with a different
compression history: pasted or retouched material has been saved fewer
times than the rest.
"""

from __future__ import annotations

import numpy as np

from ..result import INFO, MANIPULATION, NOTABLE, WEAK, Result, View
from ..util import block_mean, components, conditional_residual, outline_blocks, resave_jpeg, to_image

KEY = "ela"
TITLE = "Error level analysis"
GUIDE = ("Bright areas lost more detail when the image was resaved. In an untouched JPEG, similar surfaces "
         "(edges with edges, texture with texture, flat with flat) should look similarly bright. A region that is "
         "clearly brighter or darker than comparable surfaces around it may have a different compression history. "
         "Orange outlines mark blocks that are much brighter or darker than blocks of similar texture elsewhere in "
         "the image.")
BLOCK = 16


def _pass(ex, q: int, t: np.ndarray) -> dict:
    """One ELA pass at quality q, with blocks compared against blocks of
    similar texture (ELA error is naturally higher on edges and detail)."""
    diff = np.abs(ex.rgb.astype(np.int16) - resave_jpeg(ex.rgb, q).astype(np.int16)).astype(np.uint8)
    e = block_mean(diff.mean(axis=2), BLOCK)
    cr = conditional_residual(np.log(e + 0.5), t, bins=16)
    z = cr.r / cr.mad
    # Too bright: saved fewer times than the rest (a pasted or retouched area).
    high = (z > 3.5) & (cr.r > np.log(1.6))
    # Too dark: already reduced to coarse steps by an earlier low-quality
    # save, so it barely changes now. Held to a stricter bar because flat
    # areas are naturally dark.
    low = (z < -4.0) & (cr.r < -np.log(2.5)) & (t > np.median(t))
    flag = high | low
    _, _, largest = components(flag)
    return {"quality": q, "diff": diff, "flag": flag, "high": int(high.sum()), "low": int(low.sum()),
            "flagged": int(flag.sum()), "largest": largest}


def run(ex, settings) -> Result:
    q = settings.ela_quality
    y = ex.luma
    tex = np.zeros_like(y)
    tex[:, :-1] += np.abs(np.diff(y, axis=1))
    tex[:-1, :] += np.abs(np.diff(y, axis=0))
    t = block_mean(tex, BLOCK) / 2
    passes = [_pass(ex, q, t)]
    # A single blanket quality doesn't suit every image. Material with a different history stands out most when resaving near
    # the quality the rest of the image was saved at before, so JPEGs also
    # get a pass 10-15 below their own quality.
    jq = ex.meta.jpeg_quality
    adaptive = int(np.clip(round(((jq or 90) - 12) / 5) * 5, 60, 85)) if ex.meta.lossy else None
    if adaptive and abs(adaptive - q) >= 5:
        passes.append(_pass(ex, adaptive, t))
    best = max(passes, key=lambda p: (p["largest"], p["flagged"]))
    flag, n_flag, largest, total = best["flag"], best["flagged"], best["largest"], best["flag"].size
    res = Result(KEY, TITLE, MANIPULATION, GUIDE,
                 params={"qualities": [p["quality"] for p in passes], "scale": settings.ela_scale, "block": BLOCK})
    res.metrics = {"passes": [{k: v for k, v in p.items() if k not in ("diff", "flag")} for p in passes],
                   "reported_quality": best["quality"], "mean_error": float(passes[0]["diff"].mean()),
                   "flagged_blocks": n_flag, "brighter_blocks": best["high"], "darker_blocks": best["low"],
                   "largest_group": largest, "blocks": total}
    for p in passes:
        view = to_image(np.clip(p["diff"].astype(np.float32) * settings.ela_scale, 0, 255))
        if p["flagged"]:
            view = outline_blocks(view, p["flag"], BLOCK)
        note = "Outlined blocks recompress differently from blocks of similar texture."
        if p is not passes[0]:
            note += f" Quality chosen from the file's own quality (about {jq})."
        res.views.append(View(f"ELA at quality {p['quality']}, ×{settings.ela_scale}", view, note))
    q = best["quality"]
    high, low = best["high"], best["low"]

    # Without a JPEG history ELA is less reliable, so a lossless file needs a larger connected region to count as notable.
    need = 4 if ex.meta.lossy else 8
    if largest >= need and n_flag >= max(need, total * (0.002 if ex.meta.lossy else 0.005)):
        res.add(NOTABLE, "ELA: a connected region recompresses differently from the rest",
                f"{n_flag} of {total} blocks (largest group {largest}) recompress differently from blocks of similar "
                f"texture at quality {q} ({high} brighter, {low} darker). Edits and pasted "
                "material often carry a different compression history: brighter if saved fewer times, darker if "
                "taken from a lower-quality JPEG.")
    elif n_flag:
        res.add(WEAK, "ELA: a few scattered blocks stand out",
                f"{n_flag} isolated blocks recompress differently from blocks of similar texture. Scattered blocks "
                "are usually fine detail or noise rather than an edit.")
    else:
        res.add(INFO, "ELA: error level is consistent with texture",
                "No block stands out once edges and texture are allowed for.")
    if not ex.meta.lossy:
        res.add(INFO, "Lossless source: ELA and PCA are weaker here",
                f"This is a {ex.meta.format} image. ELA and PCA work best on JPEGs, where the image has its own "
                "compression history to compare against. Lean on the other techniques too.")
    return res
