"""Copy-move (clone) detection by block matching (Fridrich et al. 2003).

Overlapping 16×16 blocks of a greyscale copy are described by the means of
their 4×4 sub-blocks (read from integral images), sorted so near-identical
blocks sit together, and compared with their neighbours in that order. A
genuine clone shows up as many block pairs sharing one shift vector.
Periodic texture, straight edges and scattered chance matches are set
aside, because they match themselves at many offsets.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from ..result import INFO, MANIPULATION, NOTABLE, Result, View
from ..util import dim, font, luma

KEY = "clone"
TITLE = "Clone detection"
GUIDE = ("Regions that appear twice. Each duplicated pair is shaded in two colours (one per copy) and joined by a "
         "numbered arrow. There's no way to tell which copy is the original. Tiles, fences, text and other genuinely "
         "repeating patterns can also match, so confirm any copy by eye.")
SENSITIVITY = {
    "strict": {"tol": 2.5, "min_matches": 40, "min_std": 8.0},
    "normal": {"tol": 4.0, "min_matches": 24, "min_std": 6.0},
    "sensitive": {"tol": 6.0, "min_matches": 14, "min_std": 4.0},
}
B, SUB, STRIDE, WINDOW = 16, 4, 2, 24
COLOURS = [((62, 207, 142), (240, 90, 90)), ((76, 195, 255), (245, 185, 66)),
           ((180, 140, 255), (232, 121, 59)), ((94, 234, 212), (251, 113, 133))]


def detect(rgb: np.ndarray, size: int, sensitivity: str = "normal") -> dict:
    p = dict(SENSITIVITY[sensitivity])
    # Larger analysis sizes test more blocks, so chance matches pile up
    # faster: scale the required number of matching pairs with size.
    p["min_matches"] = round(p["min_matches"] * max(1.0, size / 768))
    h, w = rgb.shape[:2]
    scale = min(1.0, size / max(h, w))
    if scale < 1:
        small = np.asarray(Image.fromarray(rgb).resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS))
    else:
        small = rgb
    g = luma(small).astype(np.float64)
    sh, sw = g.shape
    integ = np.zeros((sh + 1, sw + 1))
    integ[1:, 1:] = g.cumsum(0).cumsum(1)
    integ2 = np.zeros((sh + 1, sw + 1))
    integ2[1:, 1:] = (g * g).cumsum(0).cumsum(1)

    ys, xs = np.meshgrid(np.arange(0, sh - B + 1, STRIDE), np.arange(0, sw - B + 1, STRIDE), indexing="ij")
    X, Y = xs.ravel(), ys.ravel()

    def rect(a, x0, y0, s):
        return a[y0 + s, x0 + s] - a[y0, x0 + s] - a[y0 + s, x0] + a[y0, x0]

    mean = rect(integ, X, Y, B) / (B * B)
    sd = np.sqrt(np.maximum(0, rect(integ2, X, Y, B) / (B * B) - mean ** 2))
    feats = np.stack([rect(integ, X + sx * SUB, Y + sy * SUB, SUB) / (SUB * SUB)
                      for sy in range(4) for sx in range(4)], axis=1).astype(np.float32)
    f4 = feats.reshape(-1, 4, 4)
    vh = ((f4 - f4.mean(axis=2, keepdims=True)) ** 2).sum(axis=(1, 2))  # variation along rows
    vv = ((f4 - f4.mean(axis=1, keepdims=True)) ** 2).sum(axis=(1, 2))  # variation along columns
    # Blocks that only vary in one direction (a straight edge) match
    # themselves at any shift along the edge, so leave them out.
    keep = (sd >= p["min_std"]) & (np.minimum(vh, vv) >= np.maximum(8, 0.06 * np.maximum(vh, vv)))
    feats, X, Y = feats[keep], X[keep], Y[keep]
    n = len(feats)
    result = {"scale": scale, "block": B / scale, "blocks": int(n), "clusters": [], "repetitive": 0, "params": p}
    if n < 2:
        return result

    q = 6.0
    m = feats.mean(axis=1)
    f4 = feats.reshape(-1, 4, 4)
    gxs = (f4[:, :, 2:].sum(axis=(1, 2)) - f4[:, :, :2].sum(axis=(1, 2))) / 8
    gys = (f4[:, 2:, :].sum(axis=(1, 2)) - f4[:, :2, :].sum(axis=(1, 2))) / 8
    keys = np.round(np.column_stack([m, gxs, gys, feats]) / q).astype(np.int16)
    order = np.lexsort(keys.T[::-1])

    src_l, dst_l, dx_l, dy_l = [], [], [], []
    min_d2 = (B * 1.5) ** 2
    for k in range(1, min(WINDOW, n)):
        a, b = order[:-k], order[k:]
        ok = np.abs(feats[a] - feats[b]).max(axis=1) <= p["tol"]
        a, b = a[ok], b[ok]
        dx, dy = X[b] - X[a], Y[b] - Y[a]
        far = dx.astype(np.int64) ** 2 + dy.astype(np.int64) ** 2 >= min_d2
        a, b, dx, dy = a[far], b[far], dx[far], dy[far]
        neg = (dx < 0) | ((dx == 0) & (dy < 0))
        src_l.append(np.where(neg, b, a))
        dst_l.append(np.where(neg, a, b))
        dx_l.append(np.where(neg, -dx, dx))
        dy_l.append(np.where(neg, -dy, dy))
    if not src_l:
        return result
    src, dst = np.concatenate(src_l), np.concatenate(dst_l)
    sdx, sdy = np.concatenate(dx_l), np.concatenate(dy_l)
    if src.size == 0:
        return result

    # Pairs grouped by shift vector.
    off = 4096
    code = (sdx.astype(np.int64) + off) * (2 * off) + (sdy.astype(np.int64) + off)
    order2 = np.argsort(code, kind="stable")
    code, src, dst = code[order2], src[order2], dst[order2]
    uniq, start, counts = np.unique(code, return_index=True, return_counts=True)
    count_of = dict(zip(uniq.tolist(), counts.tolist()))
    start_of = dict(zip(uniq.tolist(), start.tolist()))

    def unpack(c):
        return c // (2 * off) - off, c % (2 * off) - off

    def packed(dx, dy):
        return (dx + off) * (2 * off) + (dy + off)

    def support_near(dx, dy):
        total = 0
        for ddx in range(-3, 4):
            for ddy in range(-3, 4):
                for sx, sy in ((dx + ddx, dy + ddy), (-dx - ddx, -dy - ddy)):
                    total += count_of.get(packed(sx, sy), 0)
        return total

    # Merge shifts within ±2 px (resampling smears them), strongest first.
    used, cands = set(), []
    for c in uniq[np.argsort(-counts, kind="stable")].tolist():
        if count_of[c] * 25 < p["min_matches"] or len(cands) >= 40:
            break
        if c in used:
            continue
        dx0, dy0 = unpack(c)
        members = []
        for ddx in range(-2, 3):
            for ddy in range(-2, 3):
                cc = packed(dx0 + ddx, dy0 + ddy)
                if cc in count_of and cc not in used:
                    used.add(cc)
                    members.append(cc)
        s_idx = np.concatenate([np.arange(start_of[cc], start_of[cc] + count_of[cc]) for cc in members])
        if s_idx.size < p["min_matches"]:
            continue
        cands.append({"dx": dx0, "dy": dy0, "matches": int(s_idx.size),
                      "src": set(src[s_idx].tolist()), "dst": set(dst[s_idx].tolist())})

    def compact(blocks: set) -> bool:
        """A copied patch is one solid area; fragments on a patterned
        surface are not."""
        gx, gy = X[list(blocks)] // STRIDE, Y[list(blocks)] // STRIDE
        grid = np.zeros((gy.max() - gy.min() + 5, gx.max() - gx.min() + 5), bool)
        grid[gy - gy.min() + 2, gx - gx.min() + 2] = True
        # Bridge the small holes left by blocks the texture filters skipped.
        lab, cnt = ndimage.label(ndimage.binary_dilation(grid, iterations=2))
        if cnt == 0:
            return False
        per_label = np.bincount(lab[grid], minlength=cnt + 1)[1:]
        return per_label.max() >= len(blocks) * 0.5

    owners: dict[int, int] = {}
    for c in cands:
        for bi in c["src"] | c["dst"]:
            owners[bi] = owners.get(bi, 0) + 1
    clusters, repetitive = [], 0
    for c in cands:
        both = len(c["src"] & c["dst"])
        shared = sum(1 for bi in list(c["src"]) + list(c["dst"]) if owners[bi] > 1)
        total = len(c["src"]) + len(c["dst"])
        periodic = (both > min(len(c["src"]), len(c["dst"])) * 0.25 or shared > total * 0.3
                    or support_near(2 * c["dx"], 2 * c["dy"]) > c["matches"] * 0.25
                    or not compact(c["src"]) or not compact(c["dst"]))
        if periodic:
            repetitive += 1
            continue
        if min(len(c["src"]), len(c["dst"])) < p["min_matches"] / 2 or len(clusters) >= 6:
            continue
        clusters.append({
            "dx": c["dx"] / scale, "dy": c["dy"] / scale, "matches": c["matches"],
            "src": [(float(X[bi] / scale), float(Y[bi] / scale)) for bi in sorted(c["src"])],
            "dst": [(float(X[bi] / scale), float(Y[bi] / scale)) for bi in sorted(c["dst"])],
        })
    result["clusters"], result["repetitive"] = clusters, repetitive
    return result


def draw(rgb: np.ndarray, det: dict) -> Image.Image:
    img = Image.fromarray(dim(rgb, 0.55)).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bs = det["block"]
    for i, cl in enumerate(det["clusters"]):
        ca, cb = COLOURS[i % len(COLOURS)]
        for (x, y) in cl["src"]:
            d.rectangle([x, y, x + bs, y + bs], fill=ca + (115,))
        for (x, y) in cl["dst"]:
            d.rectangle([x, y, x + bs, y + bs], fill=cb + (115,))
    img = Image.alpha_composite(img, layer).convert("RGB")
    d = ImageDraw.Draw(img)
    lw = max(2, round(max(img.size) / 500))
    for i, cl in enumerate(det["clusters"]):
        cs = np.mean(cl["src"], axis=0) + bs / 2
        cd = np.mean(cl["dst"], axis=0) + bs / 2
        d.line([tuple(cs), tuple(cd)], fill=(255, 255, 255), width=lw)
        ang = np.arctan2(cd[1] - cs[1], cd[0] - cs[0])
        ah = lw * 6
        d.polygon([tuple(cd), (cd[0] - ah * np.cos(ang - 0.4), cd[1] - ah * np.sin(ang - 0.4)),
                   (cd[0] - ah * np.cos(ang + 0.4), cd[1] - ah * np.sin(ang + 0.4))], fill=(255, 255, 255))
        d.text((cs[0] + lw * 3, cs[1] - lw * 10), str(i + 1), fill=(255, 255, 255), font=font(lw * 9))
    return img


def run(ex, settings) -> Result:
    det = detect(ex.rgb, settings.clone_size, settings.clone_sensitivity)
    res = Result(KEY, TITLE, MANIPULATION, GUIDE,
                 params={"analysis_size": settings.clone_size, "sensitivity": settings.clone_sensitivity,
                         "scale": round(det["scale"], 4), **det["params"]})
    res.metrics = {"blocks_tested": det["blocks"], "set_aside_as_repeating": det["repetitive"],
                   "clusters": [{"shift": [round(c["dx"]), round(c["dy"])], "matches": c["matches"],
                                 "source_centre": [round(v) for v in np.mean(c["src"], axis=0) + det["block"] / 2],
                                 "copy_centre": [round(v) for v in np.mean(c["dst"], axis=0) + det["block"] / 2]}
                                for c in det["clusters"]]}
    res.views.append(View("Duplicated regions", draw(ex.rgb, det)))
    cl = det["clusters"]
    if cl:
        desc = "; ".join(f"#{i + 1}: {c['matches']} matching blocks, shifted {round(c['dx'])}, {round(c['dy'])} px"
                         for i, c in enumerate(cl))
        res.add(NOTABLE, f"Clone detection: {len(cl)} duplicated region{'s' if len(cl) > 1 else ''}",
                desc + ". Repeating patterns such as tiles, brickwork or text also match, so confirm the copy by eye.")
    else:
        extra = (f" {det['repetitive']} match group{'s were' if det['repetitive'] > 1 else ' was'} set aside as "
                 "repeating pattern rather than a copy.") if det["repetitive"] else ""
        res.add(INFO, "Clone detection: no duplicated regions found",
                f"Checked {det['blocks']:,} textured blocks at {round(det['scale'] * 100)}% scale.{extra}")
    return res
