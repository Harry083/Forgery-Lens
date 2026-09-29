"""The local web app's API, end to end over HTTP."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer

import pytest

from conftest import jpeg_bytes, scene
from forgery_lens.app import Jobs, make_handler


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Jobs(), {"127.0.0.1", "localhost"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def call(url, method="GET", data=None, headers=None, raw=False):
    req = urllib.request.Request(url, data=data, method=method, headers={"X-Forgery-Lens": "1", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
            return r.status, (body if raw else json.loads(body))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def multipart(fields: dict) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    out = b""
    for name, (filename, data) in fields.items():
        disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        out += f"--{b}\r\nContent-Disposition: {disp}\r\n\r\n".encode() + data + b"\r\n"
    return out + f"--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"


def test_page_and_config(server):
    status, page = call(server + "/", raw=True)
    assert status == 200 and b"/static/app.js" in page
    assert call(server + "/static/app.js", raw=True)[0] == 200
    status, cfg = call(server + "/api/config")
    assert status == 200 and any(t["key"] == "ela" for t in cfg["techniques"])


def test_rejects_foreign_host_and_missing_header(server):
    assert call(server + "/api/config", headers={"Host": "evil.example"})[0] == 403
    body, ctype = multipart({"image": ("a.jpg", b"x")})
    req = urllib.request.Request(server + "/api/jobs", data=body, method="POST", headers={"Content-Type": ctype})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403


def test_analyse_view_rerun_and_report(server):
    img = scene(384, 384, seed=11)
    img[200:296, 220:316] = img[40:136, 30:126]
    opts = json.dumps({"skip": ["watermark", "ghost", "spectrum"]}).encode()
    body, ctype = multipart({"image": ("cm.jpg", jpeg_bytes(img, 92)), "options": (None, opts)})
    status, r = call(server + "/api/jobs", "POST", body, {"Content-Type": ctype})
    assert status == 202
    jid = r["id"]
    for _ in range(240):
        status, job = call(f"{server}/api/jobs/{jid}")
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.25)
    assert job["status"] == "done", job.get("error")
    assert any(f["title"].startswith("Clone detection: 1") for f in job["findings"])
    assert not any(t["key"] == "watermark" for t in job["techniques"])
    view = next(t for t in job["techniques"] if t["key"] == "ela")["views"][0]
    status, png = call(server + view["url"], raw=True)
    assert status == 200 and png[:4] == b"\x89PNG"
    status, job2 = call(f"{server}/api/jobs/{jid}/rerun/ela", "POST", json.dumps({"ela_quality": 75}).encode(),
                        {"Content-Type": "application/json"})
    assert status == 200 and job2["settings"]["ela_quality"] == 75
    status, html = call(f"{server}/api/jobs/{jid}/report.html", raw=True)
    assert status == 200 and job["exhibit"]["sha256"].encode() in html


def test_open_by_path(server, tmp_path):
    p = tmp_path / "scene.jpg"
    p.write_bytes(jpeg_bytes(scene(256, 256, seed=3), 90))
    body = json.dumps({"path": str(p), "options": {"skip": ["watermark", "ghost", "spectrum", "clone"]}}).encode()
    status, r = call(server + "/api/jobs", "POST", body, {"Content-Type": "application/json"})
    assert status == 202
    for _ in range(240):
        status, job = call(f"{server}/api/jobs/{r['id']}")
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.25)
    assert job["status"] == "done", job.get("error")
    assert job["exhibit"]["file"] == "scene.jpg"
    missing = json.dumps({"path": str(tmp_path / "nope.jpg")}).encode()
    status, err = call(server + "/api/jobs", "POST", missing, {"Content-Type": "application/json"})
    assert status == 400 and "not found" in err["error"]


def test_bad_upload(server):
    body, ctype = multipart({"image": ("x.jpg", b"not an image")})
    status, err = call(server + "/api/jobs", "POST", body, {"Content-Type": ctype})
    assert status == 400 and "not an image" in err["error"]
