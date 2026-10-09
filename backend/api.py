"""The desktop app's backend: every method is callable from the page as window.pywebview.api.<name>(...).

The Authenticate workspace (forgery and AI-image checks) uses the methods defined here. The Enhance workspace's
methods live in backend/enhance/api.py and are added to this class with an `en_` prefix, because pywebview
exposes a single object to the page.

Nothing listens on a network port. pywebview passes calls straight from the window's JavaScript to this
object. Each method returns {"ok": True, "data": ...} or {"ok": False, "error": "..."}.

Views are written as PNGs to a temporary folder that is deleted when the app closes, and the page shows
them by file:// URL; that keeps multi-megapixel images off the JavaScript bridge.

pywebview exposes every public attribute to JavaScript, so internal state is kept in `_`-prefixed names.
"""

from __future__ import annotations

import base64
import functools
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

import numpy as np

from . import __version__
from .analyse import Analysis, analyse
from .enhance.api import EnhanceApi
from .exhibit import ExhibitError, load, load_bytes
from .report import PdfError, render_html, render_pdf, render_print_html
from .result import Result
from .settings import Settings
from .techniques import TECHNIQUES

MAX_FILE = 300 * 1024 * 1024
MAX_JOBS = 6
IMAGE_TYPES = ("Images (*.jpg;*.jpeg;*.png;*.webp;*.tif;*.tiff;*.bmp)", "All files (*.*)")
_analysis_slot = threading.Semaphore(1)  # one analysis at a time keeps memory predictable


class ApiError(Exception):
    """An error whose message is shown to the user as-is."""


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


def _result(fn):
    """Wrap an API method so it always returns {"ok", "data"|"error"} instead of raising."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            # round-trip through JSON so numpy values and paths arrive as plain JSON
            return {"ok": True, "data": json.loads(json.dumps(fn(self, *args, **kwargs), default=_json_default))}
        except ApiError as exc:
            return {"ok": False, "error": str(exc)}
        except ExhibitError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return wrapper


def _first_path(result) -> str:
    """create_file_dialog returns a tuple, a list or a plain string depending on platform and dialog."""
    if not result:
        return ""
    path = result if isinstance(result, str) else result[0]
    return os.path.normpath(path) if path else ""


def _dialogs():
    """pywebview renamed its dialog constants in 5.x; support both spellings."""
    import webview

    fd = getattr(webview, "FileDialog", None)
    if fd:
        return fd.OPEN, fd.FOLDER, fd.SAVE
    return webview.OPEN_DIALOG, webview.FOLDER_DIALOG, webview.SAVE_DIALOG


LETTER_REGIONS = {"US", "CA", "MX", "PH", "CL", "CO", "VE", "PR", "GT", "CR", "PA", "DO", "SV", "NI", "HN"}


def _paper_inches() -> tuple[float, float]:
    """US Letter where the Windows region uses it, A4 everywhere else (WebView2 defaults to Letter)."""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(85)
        ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85)
        region = buf.value.rsplit("-", 1)[-1].upper()
    except Exception:  # noqa: BLE001
        region = ""
    return (8.5, 11.0) if region in LETTER_REGIONS else (8.27, 11.69)


def _pdf_with_webview2(page_html: str, out: Path, folder: Path) -> bool:
    """Windows: print `page_html` to `out` with the app's own WebView2 (Edge's engine, already running).

    Loads the page in a hidden window and calls CoreWebView2.PrintToPdfAsync on it. Returns False when this
    route isn't available (not Windows, or not the WebView2 backend) so the caller can fall back."""
    if os.name != "nt":
        return False
    import webview

    if not webview.windows:  # the app's window isn't running (e.g. under tests)
        return False
    folder.mkdir(parents=True, exist_ok=True)
    src = folder / f"print-{uuid.uuid4().hex[:8]}.html"
    src.write_text(page_html, encoding="utf-8")
    win = webview.create_window("Clarity PDF", url=src.as_uri(), hidden=True, width=1100, height=900)
    try:
        if not win.events.loaded.wait(60):
            raise PdfError("The report didn't load for printing.")
        form = win.native
        view = getattr(getattr(form, "browser", None), "webview", None)
        if view is None or type(view).__name__ != "WebView2":
            return False  # MSHTML or another backend: no PrintToPdf here (CoreWebView2 is UI-thread only, so don't probe it)
        import clr  # noqa: F401  (pythonnet, loaded by pywebview)
        from System import Func, Object

        started = {}

        def start():  # must run on the window's UI thread
            core = view.CoreWebView2
            settings = core.Environment.CreatePrintSettings()
            settings.ShouldPrintHeaderAndFooter = False
            settings.ShouldPrintBackgrounds = True
            settings.PageWidth, settings.PageHeight = _paper_inches()
            started["task"] = core.PrintToPdfAsync(str(out), settings)
            return None

        form.Invoke(Func[Object](start))
        task = started["task"]
        if not task.Wait(180_000):
            raise PdfError("Making the PDF took too long. Save the HTML report and print it instead.")
        if not task.Result or not out.is_file() or out.stat().st_size == 0:
            raise PdfError("The PDF couldn't be made. Save the HTML report and print it to PDF instead.")
        return True
    finally:
        win.destroy()
        src.unlink(missing_ok=True)


def _settings(opts: dict, base: Settings | None = None) -> Settings:
    s = replace(base) if base else Settings()
    for field in ("ela_quality", "ela_scale", "clone_size"):
        if field in opts:
            setattr(s, field, int(opts[field]))
    if opts.get("clone_sensitivity") in ("strict", "normal", "sensitive"):
        s.clone_sensitivity = opts["clone_sensitivity"]
    if "skip" in opts:
        s.skip = {k for k in opts.get("skip") or [] if k in TECHNIQUES}
    if not 30 <= s.ela_quality <= 100 or not 1 <= s.ela_scale <= 100 or not 256 <= s.clone_size <= 8192:
        raise ApiError("ELA quality must be 30–100, brightness 1–100 and clone size 256–8192.")
    return s


# ---------------------------------------------------------------- jobs

class Job:
    def __init__(self, exhibit, settings: Settings, folder: Path):
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
        self.folder = folder / self.id  # this job's PNGs
        self.files: dict[str, Path] = {}  # "original" / "view/<key>/<n>" -> written PNG
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
            except Exception as e:  # report it on the page, don't crash the app
                self.status, self.error = "error", f"{e.__class__.__name__}: {e}"
            finally:
                if self.current:
                    self.done.append(self.current)
                self.current = ""

    def png(self, key: str, img) -> Path:
        """Write `img` once and return its path. A re-run gets a fresh file name, so the page never shows a stale one."""
        with self.lock:
            if key not in self.files:
                self.folder.mkdir(parents=True, exist_ok=True)
                path = self.folder / f"{key.replace('/', '-')}-{uuid.uuid4().hex[:6]}.png"
                img.save(path, "PNG", compress_level=1)
                self.files[key] = path
            return self.files[key]

    def replace_result(self, res: Result) -> None:
        an = self.analysis
        with self.lock:
            idx = next((i for i, r in enumerate(an.results) if r.key == res.key), None)
            if idx is None:
                an.results.append(res)
            else:
                an.results[idx] = res
            for k in [k for k in self.files if k.startswith(f"view/{res.key}/")]:
                self.files.pop(k).unlink(missing_ok=True)

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
                           "same_size": v.image.size == (ex.width, ex.height)} for v in r.views]
            techniques.append(t)
        d["techniques"] = techniques
        d["metadata"] = ex.meta.to_dict()
        return d


class Jobs:
    def __init__(self, folder: Path):
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self.folder = folder

    def new(self, exhibit, settings: Settings) -> Job:
        job = Job(exhibit, settings, self.folder)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS:
                old = self._jobs.popitem(last=False)[1]
                shutil.rmtree(old.folder, ignore_errors=True)
        threading.Thread(target=job.run, daemon=True).start()
        return job

    def get(self, jid: str) -> Job | None:
        return self._jobs.get(jid)

    def recent(self) -> list[dict]:
        return [{"id": j.id, "name": j.exhibit.name, "status": j.status} for j in reversed(self._jobs.values())]


# ---------------------------------------------------------------- the API

class Api:
    def __init__(self, folder: Path | None = None) -> None:
        self._folder = folder or Path(tempfile.mkdtemp(prefix="clarity-"))
        self._jobs = Jobs(self._folder)
        self._window = None
        self._enhance = EnhanceApi()

    def _attach(self, window) -> None:
        self._window = window
        self._enhance._attach(window)

    def _close(self) -> None:
        """Delete every PNG this session wrote."""
        shutil.rmtree(self._folder, ignore_errors=True)

    def _job(self, jid: str) -> Job:
        job = self._jobs.get(str(jid))
        if job is None:
            raise ApiError("That analysis has expired. Open the image again.")
        return job

    def _finished(self, jid: str) -> Job:
        job = self._job(jid)
        if job.analysis is None:
            raise ApiError("The analysis hasn't finished yet.")
        return job

    def _save_dialog(self, filename: str, kind: str, label: str, start: str = "") -> str:
        _, _, save = _dialogs()
        chosen = self._window.create_file_dialog(
            save, directory=start if os.path.isdir(start) else "", save_filename=filename,
            file_types=(f"{label} (*.{kind})", "All files (*.*)"))
        return _first_path(chosen)

    def _stem(self, job: Job) -> str:
        return re.sub(r"[^\w.\-]", "_", Path(job.exhibit.name).stem)

    def _start_dir(self, job: Job) -> str:
        """Save dialogs open next to the image when it came from disk."""
        return str(job.exhibit.path.parent) if job.exhibit.path else ""

    # ---------- start-up ----------
    @_result
    def config(self):
        return {"version": __version__,
                "techniques": [{"key": k, "title": m.TITLE} for k, m in TECHNIQUES.items()],
                "defaults": Settings().to_dict(), "recent": self._jobs.recent()}

    @_result
    def pick(self, start: str = ""):
        """Open the operating system's own file dialog; returns {"path": ""} if cancelled."""
        open_, _, _ = _dialogs()
        start_dir = start if os.path.isdir(start) else os.path.dirname(start)
        chosen = self._window.create_file_dialog(open_, directory=start_dir if os.path.isdir(start_dir) else "",
                                                 file_types=IMAGE_TYPES)
        return {"path": _first_path(chosen)}

    # ---------- analysis ----------
    @_result
    def open_path(self, path: str, options: dict | None = None):
        """Analyse an image read straight from disk."""
        p = Path(str(path or "").strip().strip('"'))
        if not str(p) or str(p) == ".":
            raise ApiError("Choose an image first.")
        if not p.is_file():
            raise ApiError(f"File not found: {p}")
        if p.stat().st_size > MAX_FILE:
            raise ApiError("That file is over 300 MB.")
        s = _settings(options or {})
        try:
            ex = load(p)
        except OSError as e:
            raise ApiError(f"Couldn't read {e.filename or 'the file'}: {e.strerror or e}") from e
        return {"id": self._jobs.new(ex, s).id}

    @_result
    def open_bytes(self, name: str, data_b64: str, options: dict | None = None):
        """Analyse a dropped or pasted image, sent from the page as base64."""
        data = base64.b64decode(data_b64 or "")
        if not data:
            raise ApiError("Choose an image first.")
        if len(data) > MAX_FILE:
            raise ApiError("That file is over 300 MB.")
        s = _settings(options or {})
        ex = load_bytes(data, os.path.basename(name or "image"))
        return {"id": self._jobs.new(ex, s).id}

    @_result
    def job(self, jid: str):
        job = self._job(jid)
        with job.lock:
            return job.to_dict()

    @_result
    def rerun(self, jid: str, key: str, options: dict | None = None):
        job = self._finished(jid)
        mod = TECHNIQUES.get(key)
        if mod is None:
            raise ApiError("Unknown technique.")
        s = _settings(options or {}, job.settings)
        t = time.perf_counter()
        res = mod.run(job.exhibit, s)
        res.seconds = time.perf_counter() - t
        job.settings = s
        job.analysis.settings = s
        job.replace_result(res)
        with job.lock:
            return job.to_dict()

    # ---------- images ----------
    @_result
    def original(self, jid: str):
        """file:// URL of the image as analysed."""
        job = self._job(jid)
        return {"url": job.png("original", job.exhibit.image).as_uri()}

    @_result
    def view(self, jid: str, key: str, index: int):
        """file:// URL of one technique's output view."""
        job = self._finished(jid)
        res = job.analysis.result(key)
        i = int(index)
        if res is None or not 0 <= i < len(res.views):
            raise ApiError("No such view.")
        return {"url": job.png(f"view/{key}/{i}", res.views[i].image).as_uri()}

    # ---------- saving ----------
    @_result
    def save_view(self, jid: str, key: str, index: int):
        """Ask where to save one view as a full-resolution PNG. Returns {"path": ""} if cancelled."""
        job = self._finished(jid)
        res = job.analysis.result(key)
        i = int(index)
        if res is None or not 0 <= i < len(res.views):
            raise ApiError("No such view.")
        path = self._save_dialog(f"{self._stem(job)}-{key}-{i + 1}.png", "png", "PNG image", self._start_dir(job))
        if path:
            try:
                shutil.copyfile(job.png(f"view/{key}/{i}", res.views[i].image), path)
            except OSError as exc:
                raise ApiError(f"Could not save the view: {exc}") from exc
        return {"path": path}

    @_result
    def save_report(self, jid: str, kind: str = "html"):
        """Ask where to save the HTML, PDF or JSON report, then write it. Returns {"path": ""} if cancelled."""
        job = self._finished(jid)
        if kind not in ("html", "pdf", "json"):
            kind = "html"
        path = self._save_dialog(f"{self._stem(job)}-authenticity-report.{kind}", kind, f"{kind.upper()} file",
                                 self._start_dir(job))
        if not path:
            return {"path": ""}
        try:
            if kind == "pdf":
                if not _pdf_with_webview2(render_print_html(job.analysis), Path(path), self._folder):
                    render_pdf(job.analysis, Path(path))
            elif kind == "json":
                Path(path).write_text(json.dumps(job.analysis.to_dict(), indent=2, default=_json_default),
                                      encoding="utf-8")
            else:
                Path(path).write_text(render_html(job.analysis), encoding="utf-8")
        except PdfError as exc:
            raise ApiError(str(exc)) from exc
        except OSError as exc:
            raise ApiError(f"Could not save the report: {exc}") from exc
        return {"path": path}


def _delegate(name: str):
    def method(self, *args):
        return getattr(self._enhance, name)(*args)

    method.__name__ = method.__qualname__ = f"en_{name}"
    method.__doc__ = getattr(EnhanceApi, name).__doc__
    return method


# Expose the Enhance workspace's methods as en_<name>; each already returns {"ok", "data"|"error"}.
for _name in [n for n in vars(EnhanceApi) if not n.startswith("_") and callable(getattr(EnhanceApi, n))]:
    setattr(Api, f"en_{_name}", _delegate(_name))
