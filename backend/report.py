"""Reports: a single self-contained HTML file, a PDF printed from it, JSON, and full-resolution maps."""

from __future__ import annotations

import base64
import html
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from .analyse import Analysis
from .result import AI, CATEGORY_LABEL, LEVEL_LABEL, MANIPULATION, PROVENANCE
from .util import thumbnail

REPORT_EDGE = 1600


def _data_uri(img: Image.Image, max_edge: int = REPORT_EDGE) -> str:
    img = thumbnail(img.convert("RGB"), max_edge)
    buf = io.BytesIO()
    if img.width * img.height <= 700 * 500:
        img.save(buf, "PNG", optimize=True)
        mime = "image/png"
    else:
        img.save(buf, "JPEG", quality=90)
        mime = "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "view"


def _table(rows: dict, mono: bool = True) -> str:
    if not rows:
        return ""
    out = []
    for k, v in rows.items():
        if isinstance(v, float):
            v = f"{v:.6g}"
        elif isinstance(v, (list, tuple)):
            v = ", ".join(f"{x:.4g}" if isinstance(x, float) else str(x) for x in v)
        elif isinstance(v, dict):
            v = json.dumps(v, default=str)
        out.append(f"<tr><th>{_e(k)}</th><td{' class=mono' if mono else ''}>{_e(v)}</td></tr>")
    return "<table>" + "".join(out) + "</table>"


CSS = """
:root{--ink:#1c2023;--dim:#51606a;--faint:#7f8b91;--line:#e1e5e8;--bg:#f6f7f8;--panel:#fff;--accent:#e8793b;
--notable:#c2410c;--weak:#a16207;--info:#475569;--mono:Consolas,"Cascadia Mono",Menlo,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 60px}header h1{font-size:1.7rem;margin:0 0 4px;font-weight:650}
.sub{color:var(--dim);margin:0 0 24px}h2{font-size:1.25rem;margin:36px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--line)}
h3{font-size:1.05rem;margin:0 0 6px}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin:0 0 16px}
table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{text-align:left;vertical-align:top;padding:6px 8px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}
th{width:30%;color:var(--dim);font-weight:600}td.mono,.mono{font-family:var(--mono);font-size:.85rem}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}.summary .card{margin:0}
.big{font-size:1.9rem;font-weight:700;line-height:1.1}.pill{display:inline-block;font:600 .72rem/1 var(--mono);text-transform:uppercase;letter-spacing:.05em;padding:5px 8px;border-radius:99px;margin-right:6px;white-space:nowrap}
.pill.notable{background:#fde8dc;color:var(--notable)}.pill.weak{background:#fdf1d3;color:var(--weak)}.pill.info{background:#eef1f3;color:var(--info)}
ul.findings{list-style:none;padding:0;margin:0}ul.findings li{padding:10px 0 10px 14px;border-left:3px solid var(--line);margin:0 0 8px;background:var(--panel)}
ul.findings li.notable{border-left-color:var(--notable)}ul.findings li.weak{border-left-color:var(--weak)}
ul.findings li strong{display:block;margin:2px 0}ul.findings li span.d{color:var(--dim);font-size:.93rem}
ul.findings a{font-size:.85rem;color:var(--accent);margin-left:6px}
.guide{color:var(--dim);margin:0 0 12px}.views{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:14px}
figure{margin:0}figure img{display:block;width:100%;height:auto;border-radius:8px;border:1px solid var(--line);background:#111;cursor:zoom-in}
figcaption{font-size:.85rem;color:var(--dim);margin-top:6px}figcaption b{color:var(--ink)}
button.cmp{font:600 .78rem system-ui;border:1px solid var(--line);background:#fff;border-radius:99px;padding:4px 10px;margin-left:8px;cursor:pointer;user-select:none}
details{margin-top:10px}summary{cursor:pointer;color:var(--dim);font-size:.88rem}.skipped{color:var(--faint);font-style:italic}
.note{background:#fff7f1;border-left:3px solid var(--accent);padding:12px 16px;border-radius:6px}
.tlabel{font:600 .72rem var(--mono);color:var(--faint);text-transform:uppercase;letter-spacing:.06em}
#zoom{position:fixed;inset:0;background:rgba(0,0,0,.88);display:none;align-items:center;justify-content:center;z-index:9;cursor:zoom-out}
#zoom img{max-width:96vw;max-height:94vh}
@media print{body{background:#fff}.card{break-inside:avoid;border-color:#ccc}section.card{break-inside:auto}
h2,h3,.guide{break-after:avoid}button.cmp{display:none}.views{grid-template-columns:1fr 1fr}
figure{break-inside:avoid}figure img{max-height:120mm;object-fit:contain;cursor:auto}}
@media (max-width:560px){.views{grid-template-columns:1fr}th{width:40%}}
"""

JS = """
document.querySelectorAll('button.cmp').forEach(function(b){var img=b.closest('figure').querySelector('img'),src=img.src;
function on(e){e.preventDefault();img.src=document.getElementById('orig').value;b.textContent='Showing original'}
function off(){img.src=src;b.textContent='Hold to compare with original'}
b.addEventListener('pointerdown',on);['pointerup','pointerleave','pointercancel'].forEach(function(t){b.addEventListener(t,off)})});
var z=document.getElementById('zoom');document.querySelectorAll('figure img').forEach(function(i){i.addEventListener('click',function(){z.firstChild.src=i.src;z.style.display='flex'})});
z.addEventListener('click',function(){z.style.display='none'});
"""


def render_html(an: Analysis, extra_head: str = "", banner: str = "") -> str:
    ex = an.exhibit
    d = ex.describe()
    orig_uri = _data_uri(ex.image)
    parts = [f"<!DOCTYPE html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<title>Forgery Lens report — {_e(ex.name)}</title><style>{CSS}</style>{extra_head}</head><body>{banner}<div class=wrap>"]
    parts.append(f"<header><h1>Image forensics report</h1><p class=sub>Forgery Lens v{_e(an.version)} · "
                 f"analysed {_e(an.started.strftime('%Y-%m-%d %H:%M:%S UTC'))} · {an.seconds:.1f} s</p></header>")

    # Exhibit
    rows = {"File name": d["file"], "SHA-256": d["sha256"], "MD5": d["md5"], "Size": f"{d['bytes']:,} bytes",
            "Format": d["format"] + (f", {d['subsampling']}" if d.get("subsampling") else "") +
                      (f", JPEG quality ≈ {d['jpeg_quality_estimate']}" if d.get("jpeg_quality_estimate") else ""),
            "Dimensions": f"{d['width']} × {d['height']} px ({d['width'] * d['height'] / 1e6:.1f} MP)"}
    if d.get("path"):
        rows["Path"] = d["path"]
    if d.get("file_modified"):
        rows["File modified (file system)"] = d["file_modified"]
    parts.append("<h2>Exhibit</h2><div class=card style='display:grid;grid-template-columns:minmax(0,1fr) 240px;gap:18px'>"
                 f"<div>{_table(rows)}</div><figure><img src='{orig_uri}' alt='The image examined'>"
                 "<figcaption>As stored (EXIF rotation not applied).</figcaption></figure></div>")

    # Summary
    cards = []
    for cat in (MANIPULATION, AI, PROVENANCE):
        c = an.counts(cat)
        top = [f for f in an.findings if f.category == cat and f.level == "notable"][:3]
        cards.append(f"<div class=card><div class=tlabel>{_e(CATEGORY_LABEL[cat])}</div>"
                     f"<div class=big>{c['notable']}</div><div>worth a closer look · {c['weak']} minor · {c['info']} note{'' if c['info'] == 1 else 's'}</div>"
                     + ("<ul style='margin:8px 0 0;padding-left:18px'>" + "".join(f"<li>{_e(f.title)}</li>" for f in top) + "</ul>" if top else "")
                     + "</div>")
    parts.append("<h2>Summary</h2><div class=summary>" + "".join(cards) + "</div>")
    parts.append("<p class=note style='margin-top:14px'>These are indicators to guide an examination, not proof of "
                 "authenticity, manipulation or AI generation. Every technique produces false positives and can miss "
                 "careful work. Confirm each finding by examining the image, and keep this report with your notes.</p>")

    # Findings by category
    parts.append("<h2>Findings</h2>")
    for cat in (MANIPULATION, AI, PROVENANCE):
        fs = [f for f in an.findings if f.category == cat]
        if not fs:
            continue
        parts.append(f"<h3 style='margin-top:18px'>{_e(CATEGORY_LABEL[cat])}</h3><ul class=findings>")
        for f in fs:
            link = f"<a href='#t-{_e(f.technique)}'>view</a>" if f.technique else ""
            parts.append(f"<li class={f.level}><span class='pill {f.level}'>{_e(LEVEL_LABEL[f.level])}</span>"
                         f"<strong>{_e(f.title)}</strong><span class=d>{_e(f.detail)}</span>{link}</li>")
        parts.append("</ul>")

    # Techniques
    parts.append("<h2>Techniques</h2>")
    for r in an.results:
        parts.append(f"<section class=card id='t-{_e(r.key)}'><h3>{_e(r.title)}</h3>")
        if r.skipped:
            parts.append(f"<p class=skipped>Not run: {_e(r.skipped)}</p>")
        if r.guide:
            parts.append(f"<p class=guide>{_e(r.guide)}</p>")
        if r.views:
            parts.append("<div class=views>")
            for v in r.views:
                same_shape = v.image.size == ex.image.size
                btn = "<button class=cmp type=button>Hold to compare with original</button>" if same_shape else ""
                parts.append(f"<figure><img src='{_data_uri(v.image)}' alt='{_e(v.label)}' loading=lazy>"
                             f"<figcaption><b>{_e(v.label)}</b>{btn}{('<br>' + _e(v.caption)) if v.caption else ''}</figcaption></figure>")
            parts.append("</div>")
        detail = {**{f"param: {k}": v for k, v in r.params.items()},
                  **{k: v for k, v in r.metrics.items() if k != "traceback"}, "time": f"{r.seconds:.2f} s"}
        parts.append(f"<details><summary>Measurements and settings</summary>{_table(detail)}</details></section>")

    # Metadata
    m = ex.meta
    parts.append("<h2>Metadata</h2>")
    st = dict(m.structure)
    for k in ("segments", "chunks", "comments"):
        if isinstance(st.get(k), list):
            st[k] = " | ".join(st[k]) or "none"
    for t in m.dqt:
        est = t["estimate"]
        st[f"Quantisation table {t['id']}"] = ("standard libjpeg, quality " if est["exact"] else "custom, closest to quality ") + str(est["quality"])
    if m.trailing:
        st["Bytes after end of image"] = m.trailing
    blocks = [("File structure", st)]
    if m.exif:
        blocks += [("EXIF: main image", m.exif["ifd0"]), ("EXIF: capture", m.exif["exif"]), ("EXIF: GPS", m.exif["gps"]),
                   ("EXIF: thumbnail directory", m.exif["ifd1"])]
    if m.xmp:
        blocks.append(("XMP", {k: v for k, v in m.xmp.items() if v}))
    if m.text:
        blocks.append(("Text fields", {k: (v[:1500] + "…" if len(v) > 1500 else v) for k, v in m.text.items()}))
    if m.c2pa:
        blocks.append(("C2PA Content Credentials", m.c2pa))
    for title, rows in blocks:
        if rows:
            parts.append(f"<div class=card><h3>{_e(title)}</h3>{_table(rows)}</div>")
    if not m.exif:
        parts.append("<p class=sub>No EXIF metadata in this file.</p>")

    parts.append(f"<h2>Settings</h2><div class=card>{_table(an.settings.to_dict())}</div>")
    parts.append(f"<p class=sub>Forgery Lens v{_e(an.version)}. The original file was only read, never modified.</p>")
    parts.append(f"<input type=hidden id=orig value='{orig_uri}'><div id=zoom><img alt=''></div></div><script>{JS}</script></body></html>")
    return "".join(parts)


class PdfError(RuntimeError):
    """The PDF couldn't be made; the message is shown to the user as-is."""


def find_browser() -> str | None:
    """A Chromium-based browser that can print to PDF headless: Edge (always on Windows 10/11) or Chrome."""
    names = ["msedge", "microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable",
             "chromium", "chromium-browser", "chrome"]
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    for root in filter(None, (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"),
                              os.environ.get("LOCALAPPDATA"))):
        candidates += [Path(root, "Microsoft", "Edge", "Application", "msedge.exe"),
                       Path(root, "Google", "Chrome", "Application", "chrome.exe")]
    candidates += [Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                   Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                   Path("/Applications/Chromium.app/Contents/MacOS/Chromium")]
    return next((str(p) for p in candidates if p.is_file()), None)


def render_print_html(an: Analysis) -> str:
    """The HTML report as it should print: every "Measurements and settings" section open, every image loaded up front."""
    return render_html(an).replace("<details>", "<details open>").replace(" loading=lazy>", ">")


def render_pdf(an: Analysis, out: Path) -> None:
    """Print the HTML report to a PDF with a headless Edge/Chrome, the same as Print → Save as PDF.

    The desktop app on Windows prints with its own WebView2 instead (backend/api.py); this is the fallback."""
    browser = find_browser()
    if not browser:
        raise PdfError("Saving as PDF needs Microsoft Edge or Google Chrome installed. "
                       "Save the HTML report instead and print it to PDF from your browser.")
    page = render_print_html(an)
    with tempfile.TemporaryDirectory(prefix="forgery-lens-pdf-", ignore_cleanup_errors=True) as tmp:
        src, pdf = Path(tmp, "report.html"), Path(tmp, "report.pdf")
        src.write_text(page, encoding="utf-8")
        detail = ""
        # The new headless mode first; older browsers only know the old one.
        for mode in ("--headless=new", "--headless"):
            cmd = [browser, mode, "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                   "--disable-extensions", f"--user-data-dir={Path(tmp, 'profile-' + mode[-3:])}",
                   "--no-pdf-header-footer", "--print-to-pdf-no-header", f"--print-to-pdf={pdf}", src.as_uri()]
            try:
                # stdin too: a windowed app has no console handles to pass on
                r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=180,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired as e:
                raise PdfError("Making the PDF took too long. Save the HTML report and print it instead.") from e
            except OSError as e:
                raise PdfError(f"Couldn't start {Path(browser).name} to make the PDF ({e}).") from e
            if pdf.is_file() and pdf.stat().st_size:
                break
            errors = [ln for ln in r.stderr.decode(errors="replace").splitlines() if "ERROR" in ln or "FATAL" in ln]
            detail = f"{Path(browser).name} exited with code {r.returncode}" + (f": {errors[-1][-200:]}" if errors else "")
        else:
            raise PdfError(f"The PDF couldn't be made ({detail}). Save the HTML report and print it to PDF instead.")
        try:
            shutil.copyfile(pdf, out)
        except OSError as e:
            raise PdfError(f"The PDF was made but couldn't be saved to {out}: {e.strerror or e}") from e


def write(an: Analysis, out_dir: Path, html_report: bool = True, json_report: bool = True, maps: bool = True) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    if html_report:
        p = out_dir / "report.html"
        p.write_text(render_html(an), encoding="utf-8")
        paths["html"] = p
    if json_report:
        p = out_dir / "report.json"
        p.write_text(json.dumps(an.to_dict(), indent=2, default=str), encoding="utf-8")
        paths["json"] = p
    if maps:
        md = out_dir / "maps"
        md.mkdir(exist_ok=True)
        for r in an.results:
            for i, v in enumerate(r.views):
                v.image.save(md / f"{r.key}-{i + 1}-{slug(v.label)}.png")
        paths["maps"] = md
    return paths
