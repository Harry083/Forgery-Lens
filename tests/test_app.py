"""The desktop app's API (backend/api.py), called the way the window's JavaScript calls it."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from conftest import jpeg_bytes, scene
from backend.api import Api
from backend.report import find_browser


class FakeWindow:
    """Stands in for the pywebview window: file dialogs answer with a preset path."""

    def __init__(self):
        self.answer = ""

    def create_file_dialog(self, *args, **kwargs):
        return (self.answer,) if self.answer else None


@pytest.fixture
def api(tmp_path):
    a = Api(tmp_path / "views")
    a._attach(FakeWindow())
    yield a
    a._close()


def ok(res):
    assert res["ok"], res.get("error")
    return res["data"]


def wait(api, jid):
    for _ in range(480):
        job = ok(api.job(jid))
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.25)
    raise AssertionError("analysis did not finish")


def file_of(url: str) -> Path:
    p = unquote(urlparse(url).path)
    return Path(p[1:] if len(p) > 2 and p[2] == ":" else p)  # file:///C:/... on Windows


def test_config(api):
    cfg = ok(api.config())
    assert any(t["key"] == "ela" for t in cfg["techniques"]) and cfg["recent"] == []


def test_analyse_view_rerun_and_save(api, tmp_path):
    img = scene(384, 384, seed=11)
    img[200:296, 220:316] = img[40:136, 30:126]
    data = base64.b64encode(jpeg_bytes(img, 92)).decode()
    jid = ok(api.open_bytes("cm.jpg", data, {"skip": ["watermark", "ghost", "spectrum"]}))["id"]
    job = wait(api, jid)
    assert job["status"] == "done", job.get("error")
    assert any(f["title"].startswith("Clone detection: 1") for f in job["findings"])
    assert not any(t["key"] == "watermark" for t in job["techniques"])

    url = ok(api.view(jid, "ela", 0))["url"]
    assert url.startswith("file:") and file_of(url).read_bytes()[:4] == b"\x89PNG"
    assert file_of(ok(api.original(jid))["url"]).is_file()

    job2 = ok(api.rerun(jid, "ela", {"ela_quality": 75}))
    assert job2["settings"]["ela_quality"] == 75
    assert not file_of(url).exists()  # the stale view was replaced
    assert ok(api.view(jid, "ela", 0))["url"] != url

    api._window.answer = str(tmp_path / "report.html")
    assert ok(api.save_report(jid, "html"))["path"] == api._window.answer
    assert job["exhibit"]["sha256"] in Path(api._window.answer).read_text(encoding="utf-8")
    api._window.answer = str(tmp_path / "report.json")
    ok(api.save_report(jid, "json"))
    assert json.loads(Path(api._window.answer).read_text(encoding="utf-8"))["exhibit"]["sha256"] == job["exhibit"]["sha256"]
    if find_browser():
        api._window.answer = str(tmp_path / "report.pdf")
        ok(api.save_report(jid, "pdf"))
        assert Path(api._window.answer).read_bytes()[:5] == b"%PDF-"
    api._window.answer = str(tmp_path / "view.png")
    ok(api.save_view(jid, "ela", 0))
    assert Path(api._window.answer).read_bytes()[:4] == b"\x89PNG"
    api._window.answer = ""
    assert ok(api.save_report(jid, "html"))["path"] == ""  # cancelled


def test_open_by_path(api, tmp_path):
    p = tmp_path / "scene.jpg"
    p.write_bytes(jpeg_bytes(scene(256, 256, seed=3), 90))
    jid = ok(api.open_path(str(p), {"skip": ["watermark", "ghost", "spectrum", "clone"]}))["id"]
    job = wait(api, jid)
    assert job["status"] == "done", job.get("error")
    assert job["exhibit"]["file"] == "scene.jpg"
    res = api.open_path(str(tmp_path / "nope.jpg"))
    assert not res["ok"] and "not found" in res["error"]


def test_bad_input(api):
    res = api.open_bytes("x.jpg", base64.b64encode(b"not an image").decode())
    assert not res["ok"] and "not an image" in res["error"]
    res = api.open_bytes("x.jpg", "", {})
    assert not res["ok"]
    res = api.open_path(__file__, {"ela_quality": 5})
    assert not res["ok"] and "ELA quality" in res["error"]
    assert not api.job("missing")["ok"]


def test_close_removes_views(tmp_path):
    a = Api(tmp_path / "views")
    (tmp_path / "views").mkdir()
    a._close()
    assert not (tmp_path / "views").exists()
