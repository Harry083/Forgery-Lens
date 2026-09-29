"""The local web app: `python run.py` (or `python run.py serve`).

A small HTTP server on this computer serves the browser interface from
forgery_lens/web/ and a JSON API the page talks to. Analysis runs here in
Python; nothing leaves the machine. Standard library only.

API (all under /api, JSON unless noted):
  GET  /config                          version, techniques, defaults, recent jobs
  POST /browse                          {mode, start} -> {path}, from the native dialog
  POST /jobs                            multipart image + options, or {path, options} -> {id}
  GET  /jobs/<id>                       status, progress and (when done) results
  POST /jobs/<id>/rerun/<technique>     {options} -> job, with that technique re-run
  GET  /jobs/<id>/original              PNG, the image as analysed
  GET  /jobs/<id>/view/<technique>/<n>  PNG, one output view
  GET  /jobs/<id>/report.html|.json     downloadable reports
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import time
import uuid
import webbrowser
from collections import OrderedDict
from dataclasses import replace
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

from . import __version__
from .analyse import Analysis, analyse
from .exhibit import ExhibitError, load, load_bytes
from .report import render_html
from .result import Result
from .settings import Settings
from .techniques import TECHNIQUES

MAX_UPLOAD = 300 * 1024 * 1024
MAX_JOBS = 6
_analysis_slot = threading.Semaphore(1)  # one analysis at a time keeps memory predictable


# ---------------------------------------------------------------- helpers

def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    if isinstance(o, (Path, bytes)):
        return str(o) if isinstance(o, Path) else f"({len(o)} bytes)"
    return str(o)


def to_json(obj) -> bytes:
    return json.dumps(obj, default=_json_default).encode("utf-8")


def parse_multipart(ctype: str, body: bytes) -> dict[str, list[dict]]:
    msg = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    fields: dict[str, list[dict]] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            fields.setdefault(name, []).append({"filename": part.get_filename(),
                                                "data": part.get_payload(decode=True) or b""})
    return fields


_browse_lock = threading.Lock()  # one native dialog at a time


def browse(mode: str = "file", start: str = "") -> str:
    """Open the operating system's own file or folder dialog; '' if cancelled."""
    import tkinter as tk
    from tkinter import filedialog

    with _browse_lock:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)  # otherwise the dialog can open behind the browser
        start_dir = start if os.path.isdir(start) else os.path.dirname(start) or None
        try:
            if mode == "folder":
                path = filedialog.askdirectory(parent=root, initialdir=start_dir)
            else:
                path = filedialog.askopenfilename(parent=root, initialdir=start_dir, filetypes=[
                    ("Images", "*.jpg *.jpeg *.png *.webp *.tif *.tiff *.bmp"), ("All files", "*.*")])
        finally:
            root.destroy()
    return os.path.normpath(path) if path else ""


def _png(img, fast: bool = True) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=1 if fast else 6)
    return buf.getvalue()


# ---------------------------------------------------------------- jobs

class Job:
    def __init__(self, exhibit, settings: Settings):
        self.id = uuid.uuid4().hex[:12]
        self.exhibit = exhibit
        self.settings = settings
        self.analysis: Analysis | None = None
        self.status = "queued"
        self.current = ""
        self.done: list[str] = []
        self.total = sum(1 for k in TECHNIQUES if k not in settings.skip)
        self.error = ""
        self.lock = threading.Lock()
        self.cache: dict[str, bytes] = {}
        self.created = time.time()

    def progress(self, title: str) -> None:
        if self.current:
            self.done.append(self.current)
        self.current = title

    def run(self) -> None:
        with _analysis_slot:
            self.status = "running"
            try:
                self.analysis = analyse(self.exhibit, self.settings, progress=self.progress)
                self.status = "done"
            except Exception as e:  # report, don't crash the server
                self.status, self.error = "error", f"{e.__class__.__name__}: {e}"
            finally:
                if self.current:
                    self.done.append(self.current)
                self.current = ""

    def replace_result(self, res: Result) -> None:
        an = self.analysis
        with self.lock:
            idx = next((i for i, r in enumerate(an.results) if r.key == res.key), None)
            if idx is None:
                an.results.append(res)
            else:
                an.results[idx] = res
            for k in [k for k in self.cache if k.startswith(f"view/{res.key}/")]:
                del self.cache[k]

    def to_dict(self) -> dict:
        ex = self.exhibit
        d = {"id": self.id, "status": self.status, "error": self.error, "current": self.current,
             "done": len(self.done), "total": self.total, "exhibit": ex.describe()}
        an = self.analysis
        if an is None:
            return d
        d["seconds"] = round(an.seconds, 2)
        d["settings"] = self.settings.to_dict()
        d["counts"] = an.counts()
        d["findings"] = [f.to_dict() for f in an.findings]
        techniques = []
        for r in an.results:
            t = r.to_dict()
            t["guide"] = r.guide
            t["views"] = [{"label": v.label, "caption": v.caption, "width": v.image.width, "height": v.image.height,
                           "same_size": v.image.size == (ex.width, ex.height),
                           "url": f"/api/jobs/{self.id}/view/{r.key}/{i}?v={int(r.seconds * 1000)}"}
                          for i, v in enumerate(r.views)]
            techniques.append(t)
        d["techniques"] = techniques
        d["metadata"] = ex.meta.to_dict()
        return d


class Jobs:
    def __init__(self):
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS:
                self._jobs.popitem(last=False)
        threading.Thread(target=job.run, daemon=True).start()

    def get(self, jid: str) -> Job | None:
        return self._jobs.get(jid)

    def recent(self) -> list[dict]:
        return [{"id": j.id, "name": j.exhibit.name, "status": j.status} for j in reversed(self._jobs.values())]


# ---------------------------------------------------------------- HTTP

def _web_file(name: str) -> bytes | None:
    try:
        return resources.files("forgery_lens").joinpath("web", name).read_bytes()
    except (FileNotFoundError, OSError):
        return None


CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".woff2": "font/woff2"}


def make_handler(jobs: Jobs, allowed_hosts: set[str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"ForgeryLens/{__version__}"
        protocol_version = "HTTP/1.1"

        # -- plumbing
        def _send(self, body: bytes, status: int = 200, ctype: str = "application/json", headers: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(to_json(obj), status)

        def _fail(self, status: int, message: str):
            self._json({"error": message}, status)

        def _host_ok(self) -> bool:
            # Refuse requests addressed to another host name (DNS rebinding).
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in allowed_hosts

        def _body(self) -> bytes | None:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_UPLOAD:
                self._fail(413, "That upload is over 300 MB.")
                return None
            return self.rfile.read(n) if n else b""

        def _job(self, jid: str) -> Job | None:
            job = jobs.get(jid)
            if job is None:
                self._fail(404, "That analysis has expired. Open the image again.")
            return job

        # -- routes
        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return self._fail(403, "forbidden host")
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._send(_web_file("index.html") or b"missing web/index.html", ctype=CTYPES[".html"],
                                  headers={"Content-Security-Policy": "default-src 'self'; img-src 'self' blob: data:; "
                                           "style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"})
            if path.startswith("/static/"):
                # A flat file, or one in web/fonts/; nothing else is reachable.
                name = os.path.basename(path)
                if path.startswith("/static/fonts/"):
                    name = f"fonts/{name}"
                body = _web_file(name)
                if body is None:
                    return self._fail(404, "not found")
                return self._send(body, ctype=CTYPES.get(Path(name).suffix, "application/octet-stream"))
            if path == "/api/config":
                return self._json({"version": __version__,
                                   "techniques": [{"key": k, "title": m.TITLE} for k, m in TECHNIQUES.items()],
                                   "defaults": Settings().to_dict(), "recent": jobs.recent()})
            m = re.fullmatch(r"/api/jobs/(\w+)(?:/(.*))?", path)
            if not m:
                return self._fail(404, "not found")
            job = self._job(m.group(1))
            if job is None:
                return
            sub = m.group(2) or ""
            if sub == "":
                with job.lock:
                    return self._json(job.to_dict())
            if sub == "original":
                if "original" not in job.cache:
                    job.cache["original"] = _png(job.exhibit.image)
                return self._send(job.cache["original"], ctype="image/png")
            vm = re.fullmatch(r"view/(\w+)/(\d+)", sub)
            if vm and job.analysis:
                key = f"view/{vm.group(1)}/{vm.group(2)}"
                if key not in job.cache:
                    res = job.analysis.result(vm.group(1))
                    i = int(vm.group(2))
                    if res is None or i >= len(res.views):
                        return self._fail(404, "no such view")
                    job.cache[key] = _png(res.views[i].image)
                return self._send(job.cache[key], ctype="image/png")
            if sub in ("report.html", "report.json") and job.analysis:
                stem = re.sub(r"[^\w.\-]", "_", Path(job.exhibit.name).stem)
                if sub == "report.html":
                    body, ctype = render_html(job.analysis).encode("utf-8"), CTYPES[".html"]
                else:
                    body, ctype = json.dumps(job.analysis.to_dict(), indent=2, default=_json_default).encode(), "application/json"
                return self._send(body, ctype=ctype, headers={
                    "Content-Disposition": f'attachment; filename="{stem}-forgery-lens-{sub}"'})
            return self._fail(404, "not found")

        do_HEAD = do_GET

        def do_POST(self):  # noqa: N802
            if not self._host_ok():
                return self._fail(403, "forbidden host")
            # Browsers can't add this header to a cross-site request without a
            # CORS preflight (which this server never approves), so its
            # presence shows the request came from this app's own page.
            if self.headers.get("X-Forgery-Lens") != "1":
                return self._fail(403, "missing app header")
            path = urlparse(self.path).path
            body = self._body()
            if body is None:
                return
            ctype = self.headers.get("Content-Type", "")
            try:
                if path == "/api/browse":
                    return self._browse(json.loads(body or b"{}"))
                if path == "/api/jobs":
                    return self._new_job(ctype, body)
                m = re.fullmatch(r"/api/jobs/(\w+)/rerun/(\w+)", path)
                if not m:
                    return self._fail(404, "not found")
                job = self._job(m.group(1))
                if job is None:
                    return
                if job.analysis is None:
                    return self._fail(409, "The analysis hasn't finished yet.")
                return self._rerun(job, m.group(2), json.loads(body or b"{}"))
            except ValueError as e:
                return self._fail(400, str(e))

        # -- actions
        def _local_client(self) -> bool:
            # Opening dialogs and reading paths on this computer is only for
            # someone sitting at it, even when serving beyond it.
            return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

        def _browse(self, opts: dict):
            if not self._local_client():
                return self._fail(403, "Browsing works only on the computer running Forgery Lens.")
            mode = "folder" if opts.get("mode") == "folder" else "file"
            try:
                path = browse(mode, str(opts.get("start") or ""))
            except Exception as e:  # no display, or Tk missing from this Python
                return self._fail(500, f"Couldn't open the file dialog ({e}). Type the path instead.")
            return self._json({"path": path})

        def _new_job(self, ctype: str, body: bytes):
            if "application/json" in ctype:
                req = json.loads(body or b"{}")
                opts = req.get("options") or {}
                if not self._local_client():
                    return self._fail(403, "Opening by path works only on the computer running Forgery Lens.")
                p = Path(str(req.get("path") or "").strip().strip('"'))
                if not str(p) or str(p) == ".":
                    return self._fail(400, "Choose an image first.")
                if not p.is_file():
                    return self._fail(400, f"File not found: {p}")
                if p.stat().st_size > MAX_UPLOAD:
                    return self._fail(413, "That file is over 300 MB.")
                loader = lambda: load(p)  # noqa: E731
            elif "multipart/form-data" in ctype:
                fields = parse_multipart(ctype, body)
                img = (fields.get("image") or [None])[0]
                if not img or not img["data"]:
                    return self._fail(400, "Choose an image first.")
                opts = json.loads((fields.get("options") or [{"data": b"{}"}])[0]["data"] or b"{}")
                loader = lambda: load_bytes(img["data"], os.path.basename(img["filename"] or "image"))  # noqa: E731
            else:
                return self._fail(400, "expected a file upload or a path")
            s = Settings()
            s.ela_quality = int(opts.get("ela_quality", s.ela_quality))
            s.clone_size = int(opts.get("clone_size", s.clone_size))
            if opts.get("clone_sensitivity") in ("strict", "normal", "sensitive"):
                s.clone_sensitivity = opts["clone_sensitivity"]
            s.skip = {k for k in opts.get("skip", []) if k in TECHNIQUES}
            if not 30 <= s.ela_quality <= 100 or not 256 <= s.clone_size <= 8192:
                raise ValueError("ELA quality must be 30–100 and clone size 256–8192.")
            try:
                ex = loader()
            except ExhibitError as e:
                return self._fail(400, str(e))
            except OSError as e:
                return self._fail(400, f"Couldn't read {e.filename or 'the file'}: {e.strerror or e}")
            job = Job(ex, s)
            jobs.add(job)
            return self._json({"id": job.id}, 202)

        def _rerun(self, job: Job, key: str, opts: dict):
            mod = TECHNIQUES.get(key)
            if mod is None:
                return self._fail(404, "unknown technique")
            s = replace(job.settings)
            for field, cast in (("ela_quality", int), ("ela_scale", int), ("clone_size", int)):
                if field in opts:
                    setattr(s, field, cast(opts[field]))
            if opts.get("clone_sensitivity") in ("strict", "normal", "sensitive"):
                s.clone_sensitivity = opts["clone_sensitivity"]
            if not 30 <= s.ela_quality <= 100 or not 1 <= s.ela_scale <= 100 or not 256 <= s.clone_size <= 8192:
                raise ValueError("value out of range")
            t = time.perf_counter()
            res = mod.run(job.exhibit, s)
            res.seconds = time.perf_counter() - t
            job.settings = s
            job.analysis.settings = s
            job.replace_result(res)
            with job.lock:
                return self._json(job.to_dict())

        def log_message(self, fmt, *args):
            msg = fmt % args
            # Progress polling and image requests would flood the console.
            if re.search(r'"(GET|HEAD) /(api/jobs/\w+(/view/|/original| )|static/)', msg):
                return
            print(f"{self.address_string()} {msg}")

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    allowed = {"127.0.0.1", "localhost", "::1"}
    if host not in ("127.0.0.1", "localhost", "::1"):
        allowed |= {host} | ({"0.0.0.0"} if host == "0.0.0.0" else set())
        if host in ("0.0.0.0", ""):
            import socket
            allowed |= {socket.gethostname().lower(), socket.getfqdn().lower()}
            try:
                allowed.add(socket.gethostbyname(socket.gethostname()))
            except OSError:
                pass
    try:
        httpd = ThreadingHTTPServer((host, port), make_handler(Jobs(), allowed))
    except OSError as e:
        raise SystemExit(f"Couldn't start on port {port} ({e.strerror}). Is Forgery Lens already running? "
                         f"Try --port {port + 1}.") from e
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/"
    print(f"Forgery Lens {__version__} is running at {url}")
    print("Leave this window open while you use it. Press Ctrl+C to stop.")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("Warning: listening beyond this computer. Anyone who can reach it can upload images.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
