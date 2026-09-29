"""Metadata parsing, provenance findings, report, CLI and server."""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image, PngImagePlugin

from conftest import jpeg_bytes, png_bytes
from forgery_lens import metadata
from forgery_lens.analyse import analyse
from forgery_lens.exhibit import ExhibitError, load_bytes
from forgery_lens.result import AI, NOTABLE
from forgery_lens.techniques import pca, provenance


def titles(res, category=None):
    return [f.title for f in res.findings if f.level == NOTABLE and (category is None or f.category == category)]


def test_quality_estimate_matches_libjpeg(base):
    for q in (50, 75, 90):
        md = metadata.parse(jpeg_bytes(base, q))
        assert md.jpeg_quality == q and md.dqt[0]["estimate"]["exact"]
        assert md.subsampling == "4:2:0"


def test_exif_editor_and_dimension_mismatch(base, settings):
    ex = Image.Exif()
    ex[0x010F], ex[0x0110], ex[0x0131] = "Canon", "EOS 5D", "Adobe Photoshop 25.0"
    ex.get_ifd(0x8769)[0xA002] = 6000
    ex.get_ifd(0x8769)[0xA003] = 4000
    data = jpeg_bytes(base, 90, exif=ex.tobytes())
    res = provenance.run(load_bytes(data, "e.jpg"), settings)
    t = titles(res)
    assert "Editing software recorded" in t and "EXIF dimensions don't match the image" in t


def test_trailing_data_after_jpeg(base, settings):
    data = jpeg_bytes(base, 90) + b"hidden payload" * 10
    assert "Data after the end of the image" in titles(provenance.run(load_bytes(data, "t.jpg"), settings))


def test_ai_provenance_png_parameters_and_iptc(base, settings):
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "a cat\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, Model: sd_xl_base_1.0")
    xmp = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           '<rdf:Description xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" '
           'Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"/>'
           '</rdf:RDF></x:xmpmeta>')
    info.add_itxt("XML:com.adobe.xmp", xmp)
    res = provenance.run(load_bytes(png_bytes(base, pnginfo=info), "ai.png"), settings)
    t = titles(res, AI)
    assert "Provenance declares AI-generated content" in t
    assert "Generator settings embedded in the file" in t


def test_generator_names_need_whole_words():
    assert metadata.AI_GENERATORS.search("Made with Midjourney v6")
    assert not metadata.AI_GENERATORS.search("imagen de la playa")  # Spanish for "image"
    assert not metadata.AI_GENERATORS.search("Soraya's camera")


def test_c2pa_claim_generator_is_read():
    blob = b"jumb....c2pa....claim_generator" + bytes([0x60 + 11]) + b"Firefly 3.0" + b"...digitalsourcetype/trainedAlgorithmicMedia"
    md = metadata.Metadata()
    metadata._find_c2pa(blob, md)
    assert md.c2pa["claim_generator"] == "Firefly 3.0"
    assert md.c2pa["digital_source_types"] == ["trainedAlgorithmicMedia"]


def test_pca_components_have_stable_signs(base):
    vals, vecs = pca.pca_components(base)
    assert list(vals) == sorted(vals, reverse=True)
    for k in range(3):  # largest-magnitude element is positive
        assert vecs[np.argmax(np.abs(vecs[:, k])), k] > 0


def test_rejects_non_images():
    with pytest.raises(ExhibitError):
        load_bytes(b"not an image", "x.jpg")


def test_full_analysis_report_and_cli(tmp_path, base):
    from forgery_lens.cli import main
    from forgery_lens.report import render_html, write
    from forgery_lens.settings import Settings

    img = base.copy()
    img[300:396, 330:426] = base[60:156, 40:136]
    data = jpeg_bytes(img, 92)
    ex = load_bytes(data, "cm.jpg")
    an = analyse(ex, Settings(skip={"watermark"}))
    assert any(f.title.startswith("Clone detection: 1") for f in an.findings)
    page = render_html(an)
    assert ex.sha256 in page and "<script>" in page
    paths = write(an, tmp_path / "r")
    assert json.loads(paths["json"].read_text())["exhibit"]["sha256"] == ex.sha256
    assert any(paths["maps"].iterdir())

    src = tmp_path / "in.jpg"
    src.write_bytes(data)
    assert main(["analyse", str(src), "-o", str(tmp_path / "cli"), "-q", "--skip", "watermark", "--no-maps"]) == 0
    assert (tmp_path / "cli" / "in" / "report.html").exists()


def test_server_multipart_parsing():
    from forgery_lens.app import parse_multipart as _parse_multipart

    body = (b"--XX\r\nContent-Disposition: form-data; name=\"image\"; filename=\"a.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n"
            b"\xff\xd8abc\r\n--XX\r\nContent-Disposition: form-data; name=\"options\"\r\n\r\n{}\r\n--XX--\r\n")
    f = _parse_multipart("multipart/form-data; boundary=XX", body)
    assert f["image"][0]["filename"] == "a.jpg" and f["image"][0]["data"] == b"\xff\xd8abc"
    assert f["options"][0]["data"] == b"{}"
