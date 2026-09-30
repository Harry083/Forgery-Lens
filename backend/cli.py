"""Command line: python app.py [analyse | techniques]. With no command, app.py opens the desktop window."""

from __future__ import annotations

import argparse
import csv
import sys
import webbrowser
from pathlib import Path

from . import __version__

IMAGE_EXT = {".jpg", ".jpeg", ".jpe", ".png", ".webp", ".tif", ".tiff", ".bmp"}


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _collect(paths: list[str], recursive: bool) -> list[Path]:
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            it = p.rglob("*") if recursive else p.iterdir()
            out += sorted(f for f in it if f.is_file() and f.suffix.lower() in IMAGE_EXT)
        elif p.is_file():
            out.append(p)
        else:
            _err(f"not found: {p}")
    return out


def _settings(a):
    from .settings import Settings
    from .techniques import TECHNIQUES

    skip = set()
    if a.skip:
        skip |= {s.strip() for s in a.skip.split(",") if s.strip()}
    if a.only:
        only = {s.strip() for s in a.only.split(",") if s.strip()}
        skip |= set(TECHNIQUES) - only
    unknown = skip - set(TECHNIQUES)
    if unknown:
        raise SystemExit(f"unknown technique(s): {', '.join(sorted(unknown))}. See: python app.py techniques")
    return Settings(ela_quality=a.ela_quality, clone_size=a.clone_size, clone_sensitivity=a.clone_sensitivity,
                    skip=skip)


def cmd_analyse(a) -> int:
    from .analyse import analyse
    from .exhibit import ExhibitError, load
    from .report import write
    from .result import AI

    files = _collect(a.paths, a.recursive)
    if not files:
        _err("no images to analyse")
        return 2
    settings = _settings(a)
    out_root = Path(a.output)
    rows, failures = [], 0
    for i, f in enumerate(files, 1):
        prefix = f"[{i}/{len(files)}] " if len(files) > 1 else ""
        try:
            ex = load(f)
        except ExhibitError as e:
            _err(f"{prefix}skipped: {e}")
            failures += 1
            continue
        if not a.quiet:
            print(f"{prefix}{f.name}  {ex.width}×{ex.height} {ex.meta.format}  sha256 {ex.sha256[:16]}…")
        an = analyse(ex, settings, progress=None if a.quiet else (lambda t: print(f"    {t}…", flush=True)))
        dest = out_root / (f.stem if len(files) == 1 else f"{f.stem}-{ex.sha256[:8]}")
        paths = write(an, dest, html_report="html" in a.format, json_report="json" in a.format, maps=not a.no_maps)
        c, ai = an.counts(), an.counts(AI)
        if not a.quiet:
            print(f"    {c['notable']} worth a closer look, {c['weak']} minor ({ai['notable']} + {ai['weak']} about AI generation)")
            for fnd in [x for x in an.findings if x.level == "notable"][:8]:
                print(f"      • {fnd.title}")
            print(f"    report: {paths.get('html') or paths.get('json')}")
        rows.append({"file": str(f), "sha256": ex.sha256, "format": ex.meta.format, "width": ex.width,
                     "height": ex.height, "notable": c["notable"], "minor": c["weak"],
                     "ai_notable": ai["notable"], "ai_minor": ai["weak"],
                     "notable_findings": " | ".join(x.title for x in an.findings if x.level == "notable"),
                     "report": str(paths.get("html") or paths.get("json") or dest)})
        if a.open and len(files) == 1 and paths.get("html"):
            webbrowser.open(paths["html"].resolve().as_uri())
    if len(files) > 1 and rows:
        out_root.mkdir(parents=True, exist_ok=True)
        with open(out_root / "summary.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"summary: {out_root / 'summary.csv'} ({len(rows)} images)")
    return 1 if failures and not rows else 0


def cmd_techniques(a) -> int:
    from .techniques import TECHNIQUES

    for key, mod in TECHNIQUES.items():
        doc = (mod.__doc__ or "").strip().splitlines()[0]
        print(f"{key:<12} {mod.TITLE}\n{'':<12} {doc}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="app.py", description="Image forgery and AI-generated imagery detection. Run with no command to open the desktop app.")
    p.add_argument("--version", action="version", version=f"forgery-lens {__version__}")
    sub = p.add_subparsers(dest="cmd")

    an = sub.add_parser("analyse", aliases=["analyze"], help="analyse images and write reports")
    an.add_argument("paths", nargs="+", help="image files or folders")
    an.add_argument("-o", "--output", default="forgery-lens-reports", help="output folder (default: %(default)s)")
    an.add_argument("-r", "--recursive", action="store_true", help="look inside sub-folders")
    an.add_argument("--format", default="html,json", help="html, json or both (default: %(default)s)")
    an.add_argument("--no-maps", action="store_true", help="don't save full-resolution PNG maps")
    an.add_argument("--open", action="store_true", help="open the report in a browser (single image)")
    an.add_argument("-q", "--quiet", action="store_true")
    g = an.add_argument_group("techniques")
    g.add_argument("--ela-quality", type=int, default=90, help="JPEG quality ELA resaves at (default: %(default)s)")
    g.add_argument("--clone-size", type=int, default=1536, help="long edge for clone search (default: %(default)s)")
    g.add_argument("--clone-sensitivity", choices=["strict", "normal", "sensitive"], default="normal")
    g.add_argument("--skip", help="comma-separated techniques to leave out")
    g.add_argument("--only", help="comma-separated techniques to run (all others skipped)")
    an.set_defaults(func=cmd_analyse)

    te = sub.add_parser("techniques", help="list the techniques")
    te.set_defaults(func=cmd_techniques)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = build_parser()
    a = p.parse_args(argv)
    if not getattr(a, "func", None):
        p.print_help()
        return 2
    try:
        return a.func(a)
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
