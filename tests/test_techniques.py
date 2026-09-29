"""Each technique finds what it should on a planted forgery, and stays quiet
on the untouched original."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from conftest import decode, demosaic, jpeg_bytes, png_bytes, scene
from forgery_lens.exhibit import load_bytes
from forgery_lens.result import NOTABLE
from forgery_lens.techniques import cfa, clone, double_jpeg, ela, ghost, jpeg_grid, resampling, wavelet


def notable(res) -> list[str]:
    return [f.title for f in res.findings if f.level == NOTABLE]


def test_ela_flags_a_patch_saved_fewer_times(base, settings):
    # The textbook case: a pristine patch pasted into a photo that was
    # already a JPEG, then saved again.
    resaved = decode(jpeg_bytes(base, 75))
    img = resaved.copy()
    img[160:320, 160:320] = scene(seed=5)[160:320, 160:320]
    forged = load_bytes(jpeg_bytes(img, 90), "f.jpg")
    clean = load_bytes(jpeg_bytes(resaved, 90), "c.jpg")
    assert any("ELA" in t for t in notable(ela.run(forged, settings)))
    assert not notable(ela.run(clean, settings))


def test_clone_finds_copy_move_with_its_shift(base, settings):
    img = base.copy()
    img[300:396, 330:426] = base[60:156, 40:136]
    res = clone.run(load_bytes(png_bytes(img), "cm.png"), settings)
    shifts = [c["shift"] for c in res.metrics["clusters"]]
    assert [290, 240] in shifts
    assert not clone.run(load_bytes(png_bytes(base), "b.png"), settings).metrics["clusters"]


def test_ghost_finds_a_patch_saved_at_lower_quality(settings):
    img = scene(768, 768, seed=2)
    patch = decode(jpeg_bytes(img, 60))
    forged = img.copy()
    forged[256:512, 256:512] = patch[256:512, 256:512]
    res = ghost.run(load_bytes(jpeg_bytes(forged, 90), "g.jpg"), settings)
    assert res.metrics.get("ghost_quality") in (55, 60, 65)
    assert "ghost_quality" not in ghost.run(load_bytes(jpeg_bytes(img, 90), "c.jpg"), settings).metrics


def test_double_jpeg(settings):
    img = scene(768, 768, seed=3)
    single = load_bytes(jpeg_bytes(img, 90), "s.jpg")
    double = load_bytes(jpeg_bytes(decode(jpeg_bytes(img, 60)), 90), "d.jpg")
    assert double_jpeg.run(double, settings).metrics["strong_frequencies"] >= 3
    assert double_jpeg.run(single, settings).metrics["strong_frequencies"] == 0
    assert double_jpeg.run(load_bytes(png_bytes(img), "p.png"), settings).skipped


def test_jpeg_grid_detects_crop_offset(settings):
    img = scene(768, 768, seed=4)
    cropped = decode(jpeg_bytes(img, 70))[5:, 3:]
    res = jpeg_grid.run(load_bytes(png_bytes(cropped), "c.png"), settings)
    assert res.metrics["grid_found"] and res.metrics["grid_offset"] == [5, 3]
    aligned = jpeg_grid.run(load_bytes(jpeg_bytes(img, 70), "a.jpg"), settings)
    assert aligned.metrics["grid_offset"] == [0, 0]


def test_cfa_pattern_and_pasted_region(settings):
    cam = demosaic(scene(768, 768, seed=6))
    res = cfa.run(load_bytes(png_bytes(cam), "cam.png"), settings)
    assert res.metrics["pattern_present"]
    pasted = cam.copy()
    pasted[200:500, 200:500] = scene(768, 768, seed=7)[200:500, 200:500]
    assert any("CFA" in t for t in notable(cfa.run(load_bytes(png_bytes(pasted), "p.png"), settings)))
    assert not cfa.run(load_bytes(png_bytes(scene(768, 768, seed=8)), "n.png"), settings).metrics["pattern_present"]


def test_resampling_detects_resized_image(settings):
    img = demosaic(scene(512, 512, seed=9))
    up = np.asarray(Image.fromarray(img).resize((640, 640), Image.BICUBIC))
    res = resampling.run(load_bytes(png_bytes(up), "up.png"), settings)
    assert res.metrics["global_peak_score"] > 8
    assert resampling.run(load_bytes(png_bytes(img), "o.png"), settings).metrics["global_peak_score"] < 8


def test_wavelet_runs_and_reports(base, settings):
    res = wavelet.run(load_bytes(png_bytes(base), "b.png"), settings)
    assert res.metrics["measurable_blocks"] > 0 and len(res.views) >= 3


def test_watermark_round_trip():
    pytest.importorskip("imwatermark")
    from imwatermark import WatermarkEncoder
    from forgery_lens.techniques import watermark

    img = demosaic(scene(512, 512, seed=10))
    enc = WatermarkEncoder()
    enc.set_watermark("bits", watermark.SDXL_BITS)
    marked = np.ascontiguousarray(enc.encode(np.ascontiguousarray(img), "dwtDct"))
    hits = [c for c in watermark.detect(marked) if c["match"]]
    assert [h["model"] for h in hits] == ["Stable Diffusion XL"]
    assert not any(c["match"] for c in watermark.detect(img))


def test_watermark_check_needs_no_optional_packages(settings):
    from forgery_lens.techniques import watermark

    res = watermark.run(load_bytes(png_bytes(scene(512, 512, seed=12)), "p.png"), settings)
    assert not res.skipped and len(res.metrics["candidates"]) == 3
    assert not any(c["match"] for c in res.metrics["candidates"])
