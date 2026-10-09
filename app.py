"""Launch Clarity as a desktop application (a native window, no local web server or port).

With a command it runs the batch command line instead: python app.py analyse photo.jpg
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# PyInstaller unpacks bundled data to sys._MEIPASS; from source it sits next to this file.
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
FRONTEND_DIR = BASE_DIR / "frontend"
# Title-bar/taskbar icon. Windows needs the .ico (pywebview loads it as a Windows icon); GTK/Qt take the PNG.
ICON_PATH = BASE_DIR / ("clarity.ico" if os.name == "nt" else "clarity.png")
CLI_COMMANDS = {"analyse", "analyze", "techniques", "-h", "--help", "--version"}


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--debug"]
    if args and args[0] in CLI_COMMANDS:
        from backend.cli import main as cli_main

        return cli_main(args)

    import webview

    from backend.api import Api

    api = Api()
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
