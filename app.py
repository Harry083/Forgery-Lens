"""Launch Clarity as a desktop application (a native window, no local web server or port).

With a command it runs the batch command line instead: python app.py analyse photo.jpg
With file paths it opens them in the Enhance workspace (this is how "Open with Clarity" in Explorer works).
`--self-test <result.json>` checks a packaged build without opening a window (used by the installer build).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

# PyInstaller unpacks bundled data to sys._MEIPASS; from source it sits next to this file.
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
FRONTEND_DIR = BASE_DIR / "frontend"
# Title-bar/taskbar icon. Windows needs the .ico (pywebview loads it as a Windows icon); GTK/Qt take the PNG.
ICON_PATH = BASE_DIR / ("clarity.ico" if os.name == "nt" else "clarity.png")
CLI_COMMANDS = {"analyse", "analyze", "techniques", "-h", "--help", "--version"}


def self_test(out: str) -> int:
    """Exercise both engines and every bundled module, then write the result to `out` (a windowed build has no
    console to print to). Exit code 0 means the packaged app works."""
    result = {"ok": False}
    try:
        import cv2
        import numpy as np
        import webview  # noqa: F401  (the window library and its Windows back end must be bundled)
        from PIL import Image

        from backend import __version__
        from backend.api import Api

        with tempfile.TemporaryDirectory() as tmp:
            img = (np.random.default_rng(0).random((240, 320, 3)) * 255).astype(np.uint8)
            path = Path(tmp) / "self-test.png"
            Image.fromarray(img).save(path)
            api = Api(Path(tmp) / "views")
            checks = {
                "frontend": (FRONTEND_DIR / "index.html").is_file(),
                "enhance_catalogue": len(api.en_catalogue()["data"]["filters"]),
                "enhance_open": api.en_open_source(str(path))["ok"],
                "enhance_preview": api.en_preview({"chain": [{"id": "levels"}, {"id": "perspective"}]})["ok"],
            }
            job = api.open_path(str(path), {"skip": ["clone", "watermark"]})
            status = "error"
            for _ in range(600):  # the analysis runs on its own thread; give it up to a minute
                status = api.job(job["data"]["id"])["data"]["status"] if job["ok"] else "error"
                if status in ("done", "error"):
                    break
                time.sleep(0.1)
            checks["authenticate_analysis"] = status == "done"
            api._enhance._source.wait_hashes(30)
            api._close()
        result = {"ok": all(checks.values()), "version": __version__, "opencv": cv2.__version__, "checks": checks}
    except Exception:  # noqa: BLE001
        result["error"] = traceback.format_exc()
    Path(out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--debug"]
    if args[:1] == ["--self-test"]:
        return self_test(args[1] if len(args) > 1 else "clarity-self-test.json")
    if args and args[0] in CLI_COMMANDS:
        from backend.cli import main as cli_main

        return cli_main(args)

    import webview

    from backend.api import Api

    api = Api()
    api._startup = [os.path.abspath(a) for a in args if not a.startswith("-") and os.path.isfile(a)]
    window = webview.create_window(
        "Clarity",
        url=(FRONTEND_DIR / "index.html").as_uri(),  # file://, served by nothing
        js_api=api,
        width=1480,
        height=960,
        min_size=(1000, 680),
        background_color="#1c2023",  # matches --bg in styles.css, so there's no white flash on open
        text_select=True,
    )
    api._attach(window)
    try:
        webview.start(
            http_server=False,
            debug="--debug" in sys.argv,
            icon=str(ICON_PATH) if ICON_PATH.exists() else None,
        )
    finally:
        api._close()  # delete this session's temporary view PNGs
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
