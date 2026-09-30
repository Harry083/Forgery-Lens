"""Metadata, file structure and provenance checks.

Metadata can show that a file went through an editor, was resized after
capture, carries a thumbnail that no longer matches, or declares itself
AI-generated (C2PA Content Credentials, IPTC digital source type, or the
prompt and settings some generators embed). It can't prove what was
changed, and it is easy to strip or fake, so it is treated as context.
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime

import numpy as np
from PIL import Image

from ..metadata import AI_GENERATORS, DIGITAL_SOURCE_TYPES, EDITORS
from ..result import AI, INFO, NOTABLE, PROVENANCE, WEAK, Result, View
from ..util import label_tile, montage

KEY = "provenance"
TITLE = "Metadata and provenance"
GUIDE = ("Read from the file's own bytes: container structure, JPEG quantisation tables, EXIF, XMP, text chunks, "
         "C2PA Content Credentials and any generator settings. Metadata is easy to strip or fake, so use it as "
         "context alongside the image analysis.")

# Output sizes common to popular generators (DALL·E, Midjourney, SDXL
# buckets, Flux, GPT image), in addition to "both sides a multiple of 64".
GENERATOR_SIZES = {
    (512, 512), (768, 768), (1024, 1024), (2048, 2048), (1024, 1792), (1792, 1024), (1152, 896), (896, 1152),
    (1216, 832), (832, 1216), (1344, 768), (768, 1344), (1536, 640), (640, 1536), (1024, 1536), (1536, 1024),
    (1456, 816), (816, 1456), (1232, 928), (928, 1232), (1344, 896), (896, 1344), (1664, 928), (928, 1664),
}


def _exif_date(s) -> datetime | None:
    try:
        return datetime.strptime(str(s)[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _generator_params(text: dict[str, str]) -> list[str]:
    """Recognise the settings blocks that generator front-ends embed."""
    hits = []
    for k, v in text.items():
        kl = k.lower()
        if kl == "parameters" and re.search(r"\bSteps:\s*\d+", v) and re.search(r"Sampler:|CFG scale:|Seed:", v):
            model = re.search(r"Model:\s*([^,\n]+)", v)
            hits.append("Stable Diffusion web UI (AUTOMATIC1111/Forge) generation parameters"
                        + (f", model “{model.group(1).strip()}”" if model else ""))
        elif kl in ("prompt", "workflow") and v.lstrip().startswith("{"):
            try:
                j = json.loads(v)
                if any(isinstance(n, dict) and "class_type" in n for n in (j.values() if isinstance(j, dict) else [])) \
                        or (isinstance(j, dict) and "nodes" in j):
                    hits.append(f"ComfyUI {'workflow' if kl == 'workflow' else 'prompt graph'}")
            except ValueError:
                pass
        elif kl in ("invokeai_metadata", "invokeai_graph", "sd-metadata", "dream"):
            hits.append(f"InvokeAI metadata ({k})")
        elif kl == "comment" and re.search(r'"(steps|sampler|n_samples|uc|scale)"\s*:', v):
            hits.append("Generator settings in a Comment field (NovelAI-style)")
        elif kl in ("fooocus_scheme", "fooocus") or "fooocus" in v.lower()[:200]:
            hits.append("Fooocus metadata")
    return list(dict.fromkeys(hits))


def _thumbnail_view(ex, res: Result) -> None:
    try:
        th = Image.open(io.BytesIO(ex.meta.thumbnail))
        th.load()
        th = th.convert("RGB")
    except Exception:
        return
    tw, tht = th.size
    iw, ih = ex.width, ex.height
    res.metrics["thumbnail_size"] = [tw, tht]
    small = ex.image.resize((tw, tht), Image.BILINEAR)
    ta, ia = tw / tht, iw / ih
    tiles = [label_tile(th.resize((th.width * 3, th.height * 3), Image.NEAREST), f"EXIF thumbnail {tw}×{tht}"),
             label_tile(small.resize((tw * 3, tht * 3), Image.NEAREST), "The image, reduced to match")]
    res.views.append(View("Embedded thumbnail against the image", montage(tiles, 2),
                          "Editors often leave the camera's original thumbnail behind."))
    if abs(ta - ia) > 0.03 and abs(ta - 1 / ia) > 0.03:
        res.add(NOTABLE, "Embedded thumbnail has a different shape",
                f"The EXIF thumbnail is {tw}×{tht} but the image is {iw}×{ih}, so it was probably cropped after the "
                "thumbnail was made. Compare the two in the thumbnail view.")
        return
    if abs(ta - ia) <= 0.03:
        a = np.asarray(th.convert("L"), dtype=np.float64).ravel()
        b = np.asarray(small.convert("L"), dtype=np.float64).ravel()
        if a.std() > 1 and b.std() > 1:
            corr = float(np.corrcoef(a, b)[0, 1])
            res.metrics["thumbnail_correlation"] = round(corr, 4)
            if corr < 0.85:
                res.add(NOTABLE, "Embedded thumbnail doesn't match the image",
                        f"The EXIF thumbnail and the image agree poorly (correlation {corr:.2f}). The image may "
                        "have been edited after the thumbnail was made; compare them in the thumbnail view.")


def run(ex, settings) -> Result:
    res = Result(KEY, TITLE, PROVENANCE, GUIDE)
    m = ex.meta
    exif = m.exif or {"ifd0": {}, "exif": {}, "gps": {}, "ifd1": {}}
    i0, ie = exif["ifd0"], exif["exif"]
    xmp = m.xmp or {}
    texts = m.text_fields()
    res.metrics = {"format": m.format, "jpeg_quality": m.jpeg_quality, "subsampling": m.subsampling or None,
                   "has_exif": m.exif is not None, "has_xmp": m.xmp is not None, "c2pa": m.c2pa,
                   "trailing_bytes": m.trailing}

    # ---- AI provenance: the strongest signals come first.
    declared = set()
    src_type = xmp.get("DigitalSourceType", "")
    for t in (m.c2pa or {}).get("digital_source_types", []):
        declared.add(t)
    if src_type:
        declared.add(src_type.rsplit("/", 1)[-1])
    ai_declared = [t for t in declared if t in ("trainedAlgorithmicMedia", "compositeWithTrainedAlgorithmicMedia",
                                                "algorithmicMedia", "compositeSynthetic")]
    if ai_declared:
        res.add(NOTABLE, "Provenance declares AI-generated content",
                "; ".join(f"{t}: {DIGITAL_SOURCE_TYPES.get(t, t)}" for t in sorted(ai_declared)) +
                ". This is the IPTC digital-source-type label written by generators and editors that follow the "
                "C2PA / IPTC standards.", AI)
    if m.c2pa:
        gen = m.c2pa.get("claim_generator", "")
        res.add(INFO, "C2PA Content Credentials present",
                "The file carries a C2PA manifest" + (f" written by “{gen}”" if gen else "") +
                ". It records how the image was made and edited. This tool reads it but doesn't verify its "
                "signature; check it with c2patool or contentcredentials.org/verify.", PROVENANCE)

    params = _generator_params(m.text)
    for p in params:
        res.add(NOTABLE, "Generator settings embedded in the file", p + ". Image generators write these settings "
                "when they save an image.", AI)
    named = {k: AI_GENERATORS.search(v).group(0) for k, v in texts.items() if AI_GENERATORS.search(v)}
    if named:
        res.add(NOTABLE, "Metadata names an AI image generator",
                "; ".join(f"{k}: “{v}”" for k, v in list(named.items())[:5]) + ".", AI)

    has_camera = bool(i0.get("Make") or i0.get("Model")) and bool(ie.get("ExposureTime") or ie.get("FNumber") or ie.get("ISO"))
    size = (ex.width, ex.height)
    gen_size = size in GENERATOR_SIZES or (ex.width % 64 == 0 and ex.height % 64 == 0 and 512 <= min(size) and max(size) <= 2048)
    res.metrics["generator_like_size"] = gen_size
    if has_camera:
        res.add(INFO, "Camera details recorded",
                f"{i0.get('Make', '')} {i0.get('Model', '')}".strip() + " with exposure settings. Real cameras write "
                "these, but they can be copied into any file, so they don't prove a photo is genuine.", AI)
    elif gen_size and not ai_declared and not params and not named:
        res.add(WEAK, "Image size typical of AI generators, and no camera details",
                f"{ex.width}×{ex.height} matches the output sizes image generators use, and the file has no camera "
                "metadata. Plenty of genuine images share these properties, so treat this as a small hint only.", AI)

    # ---- Editing history.
    software = [i0.get("Software"), xmp.get("CreatorTool")] + list(xmp.get("HistorySoftware") or [])
    software += [v for k, v in m.text.items() if re.search(r"software|comment|description", k, re.I)]
    software = [str(s) for s in software if s]
    editors = [s for s in software if EDITORS.search(s)]
    if editors:
        res.add(NOTABLE, "Editing software recorded",
                "The file names " + "; ".join(list(dict.fromkeys(editors))[:4]) +
                ". That shows it passed through an editor, not what (if anything) was changed.")
    elif software:
        res.add(INFO, "Software recorded", "; ".join(list(dict.fromkeys(software))[:4])[:400])
    if xmp.get("HistoryActions"):
        res.add(WEAK, "XMP edit history present",
                "Recorded actions: " + ", ".join(list(dict.fromkeys(xmp["HistoryActions"]))[:6]) + ".")
    if xmp.get("OriginalDocumentID") and xmp.get("DocumentID") and xmp["OriginalDocumentID"] != xmp["DocumentID"]:
        res.add(WEAK, "File derived from another document",
                "XMP DocumentID differs from OriginalDocumentID, so this file was saved from an earlier one.")
    d_orig, d_mod = _exif_date(ie.get("DateTimeOriginal")), _exif_date(i0.get("DateTime"))
    if d_orig and d_mod and abs((d_mod - d_orig).total_seconds()) > 2:
        res.add(WEAK, "Modified after capture",
                f"DateTime ({i0['DateTime']}) differs from DateTimeOriginal ({ie['DateTimeOriginal']}).")
    ew, eh = ie.get("PixelXDimension"), ie.get("PixelYDimension")
    if isinstance(ew, int) and isinstance(eh, int) and ew and eh and (ew, eh) not in (size, size[::-1]):
        res.add(NOTABLE, "EXIF dimensions don't match the image",
                f"EXIF records {ew}×{eh} but the image is {ex.width}×{ex.height}. It has been resized or cropped "
                "since the EXIF was written.")
    if m.thumbnail:
        _thumbnail_view(ex, res)

    # ---- File structure.
    if m.format == "JPEG":
        if m.exif is None:
            res.add(WEAK, "No EXIF",
                    "Camera originals almost always carry EXIF. Its absence means it was stripped, which social "
                    "media sites, messaging apps, many editors and AI generators do on export.")
        if m.trailing > 16:
            if m.mpf:
                res.add(INFO, "Extra images after the main one",
                        f"{m.trailing:,} bytes after the end-of-image marker, declared by an MPF segment "
                        "(multi-picture or depth data, common on phones).")
            else:
                res.add(NOTABLE, "Data after the end of the image",
                        f"{m.trailing:,} bytes follow the end-of-image marker with nothing declaring them. That can "
                        "be appended content, or a sign the file was assembled by hand.")
        lum = [t for t in m.dqt if t["id"] == 0]
        if lum:
            est = lum[0]["estimate"]
            if est["exact"]:
                res.add(INFO, f"JPEG quality: standard tables at quality {est['quality']}",
                        "The quantisation tables are the standard libjpeg ones. Many editors, apps, browsers and "
                        "generators use them; cameras usually use their own.")
            else:
                res.add(INFO, f"JPEG quality: about {est['quality']} (custom tables)",
                        "The quantisation tables aren't the standard libjpeg ones. That's typical of camera firmware "
                        "and of some editors (Photoshop, for example).")
    elif m.format == "PNG" and m.trailing > 0:
        res.add(NOTABLE, "Data after the end of the PNG", f"{m.trailing:,} bytes follow the IEND chunk.")
    return res
