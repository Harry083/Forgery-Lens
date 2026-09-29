// Forgery Lens: browser interface for the local Python engine.
// Everything here talks only to the server that served this page
// (127.0.0.1 by default). The analysis itself runs in Python.

(function () {
  "use strict";

  var LEVEL_LABEL = { notable: "Worth a closer look", weak: "Minor", info: "Note" };
  var CATEGORY_LABEL = { manipulation: "Editing and manipulation", ai: "AI-generated imagery", provenance: "Metadata and provenance" };

  var S = { config: null, job: null, tech: null, viewIdx: 0, poll: 0, holding: false };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function say(el, text, state) {
    el = typeof el === "string" ? $(el) : el;
    el.textContent = text || "";
    if (state) el.setAttribute("data-state", state); else el.removeAttribute("data-state");
  }

  // Every request carries the app header; the server refuses writes without it.
  function api(method, url, body) {
    var opts = { method: method, headers: { "X-Forgery-Lens": "1" } };
    if (body instanceof FormData) opts.body = body;
    else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || ("The server returned " + r.status + "."));
        return data;
      });
    }, function () { throw new Error("Can't reach Forgery Lens. Is it still running?"); });
  }

  /* ---------------- start-up ---------------- */

  function init() {
    api("GET", "/api/config").then(function (cfg) {
      S.config = cfg;
      $("opt-ela-quality").value = cfg.defaults.ela_quality;
      $("opt-techniques").innerHTML = cfg.techniques.map(function (t) {
        return '<label class="check"><input type="checkbox" value="' + esc(t.key) + '" checked><span>' + esc(t.title) + "</span></label>";
      }).join("");
      renderRecent(cfg.recent);
      say("fl-status", "Nothing is uploaded anywhere. The image goes only to the Forgery Lens engine on this computer.");
    }).catch(function (e) { say("fl-status", e.message, "error"); });

    var path = $("fl-path"), drop = $("fl-drop");
    $("fl-run").addEventListener("click", function () { startPath(path.value); });
    path.addEventListener("keydown", function (e) { if (e.key === "Enter") startPath(path.value); });
    // Browse opens the operating system's own dialog, via the engine on this computer.
    document.querySelectorAll(".browse-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var input = btn.closest(".path-input-row").querySelector(".path-input");
        btn.disabled = true;
        api("POST", "/api/browse", { mode: btn.getAttribute("data-browse") || "file", start: input.value })
          .then(function (r) { if (r.path) { input.value = r.path; startPath(r.path); } })
          .catch(function (e) { say("fl-status", e.message, "error"); })
          .then(function () { btn.disabled = false; });
      });
    });
    ["dragenter", "dragover"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.setAttribute("data-over", "true"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.removeAttribute("data-over"); });
    });
    drop.addEventListener("drop", function (e) { if (e.dataTransfer.files[0]) start(e.dataTransfer.files[0]); });
    document.addEventListener("paste", function (e) {
      var f = e.clipboardData && e.clipboardData.files && e.clipboardData.files[0];
      if (f && /^image\//.test(f.type)) start(f);
    });

    wireViewer();
    $("fl-copy").addEventListener("click", copyReport);
  }

  function renderRecent(list) {
    var box = $("fl-recent");
    list = (list || []).filter(function (j) { return j.status === "done" && (!S.job || j.id !== S.job.id); });
    box.hidden = !list.length;
    box.innerHTML = list.length ? "Recent:" + list.map(function (j) {
      return '<button type="button" data-job="' + esc(j.id) + '">' + esc(j.name) + "</button>";
    }).join("") : "";
  }
  $("fl-recent").addEventListener("click", function (e) {
    var b = e.target.closest("[data-job]");
    if (b) follow(b.getAttribute("data-job"));
  });

  /* ---------------- analysis ---------------- */

  function options() {
    var skip = [];
    document.querySelectorAll("#opt-techniques input").forEach(function (c) { if (!c.checked) skip.push(c.value); });
    return {
      ela_quality: +$("opt-ela-quality").value, clone_size: +$("opt-clone-size").value,
      clone_sensitivity: $("opt-clone-sens").value, skip: skip
    };
  }

  // A dropped or pasted file is uploaded to the engine.
  function start(file) {
    var fd = new FormData();
    fd.append("image", file, file.name);
    fd.append("options", JSON.stringify(options()));
    submit(fd, file.name);
  }

  // A path is read by the engine straight from disk.
  function startPath(path) {
    path = (path || "").trim().replace(/^"(.*)"$/, "$1");
    if (!path) { say("fl-status", "Choose an image first: Browse…, type its path, or drop it here.", "error"); return; }
    submit({ path: path, options: options() }, path.split(/[\\/]/).pop());
  }

  function submit(body, name) {
    say("fl-status", "Opening " + name + "…");
    $("fl-progress").hidden = false;
    $("fl-progress-bar").style.width = "0%";
    api("POST", "/api/jobs", body).then(function (r) { follow(r.id); })
      .catch(function (e) { $("fl-progress").hidden = true; say("fl-status", e.message, "error"); });
  }

  function follow(id) {
    clearTimeout(S.poll);
    api("GET", "/api/jobs/" + id).then(function (job) {
      if (job.status === "done") {
        $("fl-progress").hidden = true;
        say("fl-status", "Analysis complete in " + job.seconds + " s. Start with the findings below.", "success");
        show(job, true);
        api("GET", "/api/config").then(function (c) { renderRecent(c.recent); });
        return;
      }
      if (job.status === "error") {
        $("fl-progress").hidden = true;
        say("fl-status", "The analysis failed: " + job.error, "error");
        return;
      }
      $("fl-progress").hidden = false;
      $("fl-progress-bar").style.width = Math.round(100 * job.done / Math.max(1, job.total)) + "%";
      say("fl-progress-text", (job.status === "queued" ? "Waiting for the previous analysis to finish…" :
        (job.current || "Starting") + "… (" + job.done + " of " + job.total + ")") + "  " + job.exhibit.file);
      S.poll = setTimeout(function () { follow(id); }, 450);
    }).catch(function (e) { $("fl-progress").hidden = true; say("fl-status", e.message, "error"); });
  }

  function show(job, fresh) {
    S.job = job;
    $("fl-workspace").hidden = false;
    renderFileInfo(job);
    renderSummary(job);
    renderFindings(job);
    renderTabs(job);
    if (fresh || !S.tech || !techByKey(S.tech)) selectTech(firstInteresting(job), 0);
    else selectTech(S.tech, S.viewIdx);
    renderMeta(job);
    buildReport(job);
    $("fl-report-html").href = "/api/jobs/" + job.id + "/report.html";
    $("fl-report-json").href = "/api/jobs/" + job.id + "/report.json";
    if (fresh) $("fl-results-anchor").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function techByKey(key) {
    return S.job && S.job.techniques.filter(function (t) { return t.key === key; })[0];
  }
  function firstInteresting(job) {
    var f = job.findings.filter(function (x) { return x.level === "notable" && techByKey(x.technique) && techByKey(x.technique).views.length; })[0];
    return f ? f.technique : (job.techniques[0] || {}).key;
  }

  /* ---------------- rendering ---------------- */

  function fmtBytes(n) { return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(2) + " MB"; }

  function renderFileInfo(job) {
    var e = job.exhibit;
    var fmt = e.format + (e.subsampling ? " · " + e.subsampling : "") + (e.jpeg_quality_estimate ? " · JPEG quality ≈ " + e.jpeg_quality_estimate : "");
    $("fl-fileinfo").innerHTML = block("File", table({
      "File": e.file, "Format": fmt,
      "Dimensions": e.width + " × " + e.height + " px (" + (e.width * e.height / 1e6).toFixed(1) + " MP)",
      "File size": fmtBytes(e.bytes) + " (" + e.bytes.toLocaleString() + " bytes)",
      "SHA-256": e.sha256, "MD5": e.md5
    }));
  }

  function counts(job, cat) {
    var c = { notable: 0, weak: 0, info: 0 };
    job.findings.forEach(function (f) { if (!cat || f.category === cat) c[f.level]++; });
    return c;
  }

  function renderSummary(job) {
    $("fl-summary").innerHTML = ["manipulation", "ai", "provenance"].map(function (cat) {
      var c = counts(job, cat);
      var top = job.findings.filter(function (f) { return f.category === cat && f.level === "notable"; }).slice(0, 3);
      return '<div class="score-card"><div class="metric-name">' + esc(CATEGORY_LABEL[cat]) + "</div>" +
        '<div class="metric-value" data-zero="' + (c.notable === 0) + '">' + c.notable + "</div>" +
        '<div class="metric-sub"><span>worth a closer look</span><span>' + c.weak + " minor</span><span>" +
        c.info + " note" + (c.info === 1 ? "" : "s") + "</span></div>" +
        (top.length ? "<ul>" + top.map(function (f) { return "<li>" + esc(f.title) + "</li>"; }).join("") + "</ul>" : "") + "</div>";
    }).join("");
  }

  function renderFindings(job) {
    var html = "";
    ["manipulation", "ai", "provenance"].forEach(function (cat) {
      var fs = job.findings.filter(function (f) { return f.category === cat; });
      if (!fs.length) return;
      var item = function (f) {
        var t = techByKey(f.technique);
        var link = t && t.views.length ? ' <button class="link" type="button" data-goto="' + esc(f.technique) + '">Show me</button>' : "";
        return '<li data-level="' + f.level + '"><span class="level">' + LEVEL_LABEL[f.level] + "</span><strong>" +
          esc(f.title) + "</strong><span>" + esc(f.detail) + link + "</span></li>";
      };
      var main = fs.filter(function (f) { return f.level !== "info"; });
      var notes = fs.filter(function (f) { return f.level === "info"; });
      html += '<div class="cat-title">' + esc(CATEGORY_LABEL[cat]) + "</div>";
      html += main.length ? '<ul class="findings">' + main.map(item).join("") + "</ul>"
        : '<p class="placeholder">Nothing worth a closer look.</p>';
      // Routine notes ("no clone found", quality estimates…) fold away.
      if (notes.length) html += '<details class="notes"><summary>' + notes.length + " note" + (notes.length === 1 ? "" : "s") +
        '</summary><ul class="findings">' + notes.map(item).join("") + "</ul></details>";
    });
    $("fl-findings").innerHTML = html || '<p class="placeholder">No findings.</p>';
  }
  $("fl-findings").addEventListener("click", function (e) {
    var b = e.target.closest("[data-goto]");
    if (!b) return;
    selectTech(b.getAttribute("data-goto"), 0);
    $("fl-viewer").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  function renderTabs(job) {
    $("fl-tabs").innerHTML = job.techniques.map(function (t) {
      var hot = job.findings.some(function (f) { return f.technique === t.key && f.level === "notable"; });
      return '<button type="button" role="tab" data-tech="' + esc(t.key) + '" data-empty="' + (!t.views.length) + '">' +
        esc(shortTitle(t)) + (hot ? '<span class="dot" title="Worth a closer look"></span>' : "") + "</button>";
    }).join("");
  }
  function shortTitle(t) {
    return ({ ela: "Error level", pca: "PCA", gradient: "Luminance gradient", wavelet: "Wavelet noise", clone: "Clone detection",
      ghost: "JPEG ghosts", jpeg_grid: "Block grid", double_jpeg: "Double JPEG", resampling: "Resampling", cfa: "Camera pattern",
      spectrum: "Noise spectrum", watermark: "Watermarks", provenance: "Metadata" })[t.key] || t.title;
  }

  /* ---------------- viewer ---------------- */

  var CONTROLS = {
    ela: function (job) {
      var p = job.settings;
      return '<label class="model-select-label"><span>Resave quality <output id="c-q-val">' + p.ela_quality + '</output></span><input type="range" id="c-q" min="50" max="100" value="' + p.ela_quality + '"></label>' +
        '<label class="model-select-label"><span>Brightness ×<output id="c-s-val">' + p.ela_scale + '</output></span><input type="range" id="c-s" min="1" max="60" value="' + p.ela_scale + '"></label>' +
        '<span class="inline-note">Changes re-run ELA in the engine.</span>';
    },
    clone: function (job) {
      var p = job.settings;
      var sizes = [1024, 1536, 2048, 4096].map(function (v) { return '<option value="' + v + '"' + (v === p.clone_size ? " selected" : "") + ">" + v + " px</option>"; }).join("");
      var sens = ["strict", "normal", "sensitive"].map(function (v) { return '<option value="' + v + '"' + (v === p.clone_sensitivity ? " selected" : "") + ">" + v[0].toUpperCase() + v.slice(1) + "</option>"; }).join("");
      return '<label class="model-select-label">Analysis size<select id="c-size">' + sizes + '</select></label><label class="model-select-label">Sensitivity<select id="c-sens">' + sens +
        '</select></label><button class="btn" type="button" id="c-clone-run">Search again</button>';
    }
  };

  function selectTech(key, idx) {
    var t = techByKey(key);
    if (!t) return;
    S.tech = key;
    S.viewIdx = Math.min(idx || 0, Math.max(0, t.views.length - 1));
    document.querySelectorAll("#fl-tabs [data-tech]").forEach(function (b) {
      var on = b.getAttribute("data-tech") === key;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    });
    $("fl-guide").textContent = t.guide || "";
    $("fl-tech-controls").innerHTML = CONTROLS[key] ? CONTROLS[key](S.job) : "";
    $("fl-tech-controls").hidden = !CONTROLS[key];
    wireControls(key);
    $("fl-chips").innerHTML = t.views.length > 1 ? t.views.map(function (v, i) {
      return '<button type="button" data-view="' + i + '" aria-pressed="' + (i === S.viewIdx) + '">' + esc(v.label) + "</button>";
    }).join("") : "";
    renderMetrics(t);
    showView();
  }

  function showView() {
    var t = techByKey(S.tech);
    var v = t && t.views[S.viewIdx];
    var orig = $("fl-orig"), view = $("fl-view"), stack = $("fl-stack");
    var same = !!(v && v.same_size);
    ["fl-hold", "fl-blend"].forEach(function (id) { $(id).disabled = !same; });
    if (!v) {
      view.hidden = true; orig.hidden = false;
      orig.src = "/api/jobs/" + S.job.id + "/original";
      $("fl-caption").innerHTML = t && t.skipped ? '<span class="skipped">Not run: ' + esc(t.skipped) + "</span>" : "This technique produces findings but no image.";
      $("fl-save-view").removeAttribute("href");
      return;
    }
    view.hidden = false;
    stack.setAttribute("data-loading", "true");
    view.onload = function () { stack.removeAttribute("data-loading"); };
    view.src = v.url;
    view.alt = v.label;
    orig.hidden = !same;
    if (same && orig.getAttribute("src") !== "/api/jobs/" + S.job.id + "/original") orig.src = "/api/jobs/" + S.job.id + "/original";
    applyBlend();
    $("fl-caption").textContent = v.label + (v.caption ? ". " + v.caption : "") + "  (" + v.width + " × " + v.height + ")";
    var a = $("fl-save-view");
    a.href = v.url;
    a.download = (S.job.exhibit.file.replace(/\.[^.]+$/, "") + "-" + S.tech + "-" + (S.viewIdx + 1) + ".png");
  }

  function applyBlend() {
    var t = techByKey(S.tech), v = t && t.views[S.viewIdx];
    var same = !!(v && v.same_size);
    var blend = +$("fl-blend").value / 100;
    $("fl-view").style.opacity = S.holding && same ? "0" : String(same ? 1 - blend : 1);
  }

  function renderMetrics(t) {
    var rows = {};
    Object.keys(t.params || {}).forEach(function (k) { rows["setting: " + k] = t.params[k]; });
    Object.keys(t.metrics || {}).forEach(function (k) { if (k !== "traceback") rows[k] = t.metrics[k]; });
    rows.time = t.seconds + " s";
    $("fl-metrics").innerHTML = block(t.title, table(rows));
  }

  function wireViewer() {
    $("fl-tabs").addEventListener("click", function (e) {
      var b = e.target.closest("[data-tech]");
      if (b) selectTech(b.getAttribute("data-tech"), 0);
    });
    $("fl-tabs").addEventListener("keydown", function (e) {
      var d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!d) return;
      var tabs = Array.prototype.slice.call(document.querySelectorAll("#fl-tabs [data-tech]"));
      var i = tabs.indexOf(document.activeElement);
      var n = tabs[(i + d + tabs.length) % tabs.length];
      if (n) { n.focus(); n.click(); }
    });
    $("fl-chips").addEventListener("click", function (e) {
      var b = e.target.closest("[data-view]");
      if (!b) return;
      S.viewIdx = +b.getAttribute("data-view");
      document.querySelectorAll("#fl-chips [data-view]").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
      showView();
    });
    $("fl-blend").addEventListener("input", function () { $("fl-blend-val").textContent = $("fl-blend").value; applyBlend(); });
    var hold = $("fl-hold");
    var on = function (e) { e.preventDefault(); S.holding = true; applyBlend(); };
    var off = function () { if (S.holding) { S.holding = false; applyBlend(); } };
    hold.addEventListener("pointerdown", on);
    ["pointerup", "pointerleave", "pointercancel"].forEach(function (ev) { hold.addEventListener(ev, off); });
    hold.addEventListener("keydown", function (e) { if (e.key === " " || e.key === "Enter") on(e); });
    hold.addEventListener("keyup", off);
    $("fl-zoom").addEventListener("click", function () {
      var stage = $("fl-stage"), actual = stage.getAttribute("data-zoom") !== "actual";
      stage.setAttribute("data-zoom", actual ? "actual" : "fit");
      $("fl-zoom").textContent = actual ? "Fit to screen" : "Actual pixels";
    });
  }

  var rerunTimer = 0;
  function wireControls(key) {
    if (key === "ela") {
      ["c-q", "c-s"].forEach(function (id) {
        $(id).addEventListener("input", function () {
          $(id + "-val").textContent = $(id).value;
          clearTimeout(rerunTimer);
          rerunTimer = setTimeout(function () { rerun("ela", { ela_quality: +$("c-q").value, ela_scale: +$("c-s").value }); }, 450);
        });
      });
    } else if (key === "clone") {
      $("c-clone-run").addEventListener("click", function () {
        rerun("clone", { clone_size: +$("c-size").value, clone_sensitivity: $("c-sens").value }, $("c-clone-run"));
      });
    }
  }

  function rerun(key, opts, btn) {
    $("fl-stack").setAttribute("data-loading", "true");
    if (btn) { btn.disabled = true; btn.textContent = "Searching…"; }
    var keepIdx = S.viewIdx;
    api("POST", "/api/jobs/" + S.job.id + "/rerun/" + key, opts).then(function (job) {
      S.tech = key;
      S.viewIdx = keepIdx;
      show(job, false);
    }).catch(function (e) {
      $("fl-stack").removeAttribute("data-loading");
      say("fl-caption", e.message, "error");
      if (btn) { btn.disabled = false; btn.textContent = "Search again"; }
    });
  }

  /* ---------------- metadata ---------------- */

  function fmtVal(v) {
    if (v === null || v === undefined) return "";
    if (Array.isArray(v)) return v.map(function (x) { return typeof x === "number" ? +x.toFixed(6) : (typeof x === "object" ? JSON.stringify(x) : x); }).join(", ");
    if (typeof v === "number") return String(+v.toFixed(6));
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }
  function table(obj) {
    var keys = Object.keys(obj || {}).filter(function (k) { return obj[k] !== null && obj[k] !== "" && !(Array.isArray(obj[k]) && !obj[k].length); });
    if (!keys.length) return "";
    return '<table class="meta-table">' + keys.map(function (k) {
      return "<tr><td>" + esc(k) + "</td><td>" + esc(fmtVal(obj[k])) + "</td></tr>";
    }).join("") + "</table>";
  }
  // A titled group of rows, as in Frame Guard's metadata panels.
  function block(title, body) {
    return body ? '<div class="meta-block"><div class="meta-block-title">' + esc(title) + "</div>" + body + "</div>" : "";
  }

  function renderMeta(job) {
    var m = job.metadata, html = "", st = Object.assign({}, m.structure);
    ["segments", "chunks", "comments"].forEach(function (k) { if (Array.isArray(st[k])) st[k] = st[k].join(" | ") || "none"; });
    (m.quantisation_tables || []).forEach(function (t) {
      st["Quantisation table " + t.id] = (t.estimate.exact ? "standard libjpeg, quality " : "custom, closest to quality ") + t.estimate.quality;
    });
    if (m.trailing_bytes) st["Bytes after end of image"] = m.trailing_bytes;
    html += block("File structure", table(st));
    if (m.exif) {
      [["EXIF: main image", m.exif.ifd0], ["EXIF: capture", m.exif.exif], ["EXIF: GPS", m.exif.gps], ["EXIF: thumbnail directory", m.exif.ifd1]]
        .forEach(function (p) { html += block(p[0], table(p[1])); });
    } else html += '<div class="meta-block"><p class="placeholder">No EXIF metadata in this file.</p></div>';
    if (m.xmp) html += block("XMP", table(m.xmp));
    if (m.text && Object.keys(m.text).length) html += block("Text fields", table(m.text));
    if (m.c2pa) html += block("C2PA Content Credentials", table(m.c2pa));
    $("fl-meta").innerHTML = html;
  }

  /* ---------------- report ---------------- */

  function buildReport(job) {
    var e = job.exhibit, L = [];
    L.push("IMAGE FORENSICS: FORGERY LENS v" + (S.config ? S.config.version : ""));
    L.push("Analysed " + new Date().toLocaleString());
    L.push("");
    L.push("File:        " + e.file);
    L.push("SHA-256:     " + e.sha256);
    L.push("MD5:         " + e.md5);
    L.push("Size:        " + e.bytes.toLocaleString() + " bytes");
    L.push("Format:      " + e.format + (e.subsampling ? ", " + e.subsampling : "") + (e.jpeg_quality_estimate ? ", quality ≈ " + e.jpeg_quality_estimate : ""));
    L.push("Dimensions:  " + e.width + " x " + e.height + " px");
    ["manipulation", "ai", "provenance"].forEach(function (cat) {
      var fs = job.findings.filter(function (f) { return f.category === cat && f.level !== "info"; });
      L.push("");
      L.push(CATEGORY_LABEL[cat].toUpperCase() + (fs.length ? ":" : ": nothing worth a closer look"));
      fs.forEach(function (f) { L.push("  [" + LEVEL_LABEL[f.level] + "] " + f.title + ". " + f.detail); });
    });
    L.push("");
    L.push("These are indicators to guide examination, not proof of authenticity, manipulation or AI generation. Confirm each finding by examination and keep the full report with the case notes.");
    $("fl-report").value = L.join("\n");
  }

  function copyReport() {
    var t = $("fl-report").value;
    var ok = function () { say("fl-report-status", "Copied.", "success"); };
    var fail = function () { $("fl-report").select(); say("fl-report-status", "Couldn't copy automatically. The text is selected; press Ctrl/Cmd+C.", "error"); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(ok, fail); else fail();
  }

  init();
})();
