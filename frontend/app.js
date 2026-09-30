// Forgery Lens: the desktop window's interface. The analysis runs in Python;
// the page calls it directly through pywebview's bridge, with no server or port.

(function () {
  "use strict";

  var LEVEL_LABEL = { notable: "Worth a closer look", weak: "Minor", info: "Note" };
  var CATEGORY_LABEL = { manipulation: "Editing and manipulation", ai: "AI-generated imagery", provenance: "Metadata and provenance" };

  var S = { config: null, job: null, tech: null, viewIdx: 0, poll: 0, holding: false, pending: null };

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

  // ---------- Backend calls ----------
  // The page talks to Python directly through pywebview's bridge; there is no HTTP server or port.
  var bridgeReady = new Promise(function (resolve) {
    if (window.pywebview && window.pywebview.api) resolve();
    else window.addEventListener("pywebviewready", resolve, { once: true });
  });

  function api(method) {
    var args = Array.prototype.slice.call(arguments, 1);
    return bridgeReady.then(function () {
      return window.pywebview.api[method].apply(null, args);
    }).then(function (res) {
      if (!res || !res.ok) throw new Error((res && res.error) || method + " failed");
      return res.data;
    });
  }

  /* ---------------- start-up ---------------- */

  function init() {
    api("config").then(function (cfg) {
      S.config = cfg;
      $("opt-ela-quality").value = cfg.defaults.ela_quality;
      $("opt-techniques").innerHTML = cfg.techniques.map(function (t) {
        return '<label class="check"><input type="checkbox" value="' + esc(t.key) + '" checked><span>' + esc(t.title) + "</span></label>";
      }).join("");
      renderRecent(cfg.recent);
      say("fl-status", "Nothing is uploaded anywhere. The image is analysed on this computer.");
    }).catch(function (e) { say("fl-status", e.message, "error"); });

    var path = $("fl-path"), drop = $("fl-drop");
    $("fl-run").addEventListener("click", analyse);
    path.addEventListener("keydown", function (e) { if (e.key === "Enter") analyse(); });
    // Typing over a dropped file's name means the typed path is wanted instead.
    path.addEventListener("input", function () { S.pending = null; });
    // Browse opens the operating system's own file dialog, attached to the app window.
    document.querySelectorAll(".browse-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var input = btn.closest(".path-input-row").querySelector(".path-input");
        btn.disabled = true;
        api("pick", input.value)
          .then(function (r) { if (r.path) { input.value = r.path; S.pending = null; ready(r.path); } })
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
    drop.addEventListener("drop", function (e) { if (e.dataTransfer.files[0]) hold(e.dataTransfer.files[0]); });
    document.addEventListener("paste", function (e) {
      var f = e.clipboardData && e.clipboardData.files && e.clipboardData.files[0];
      if (f && /^image\//.test(f.type)) hold(f);
    });

    wireViewer();
    $("fl-copy").addEventListener("click", copyReport);
    $("fl-report-html").addEventListener("click", function () { saveReport("html"); });
    $("fl-report-pdf").addEventListener("click", function () { saveReport("pdf"); });
    $("fl-report-json").addEventListener("click", function () { saveReport("json"); });
    $("fl-save-view").addEventListener("click", saveView);
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

  // Adding an image (Browse, drop, paste) only loads it; nothing runs until Analyse is pressed.
  function ready(name) {
    say("fl-status", name.split(/[\\/]/).pop() + " is ready. Check the options, then press Analyse.");
  }
  function hold(file) {
    S.pending = file;
    $("fl-path").value = file.name;
    ready(file.name);
  }
  function analyse() {
    var f = S.pending;
    if (f && $("fl-path").value === f.name) start(f); else startPath($("fl-path").value);
  }

  // A dropped or pasted file is handed to Python as base64 (the page can't see its path).
  function start(file) {
    var reader = new FileReader();
    reader.onload = function () {
      var b64 = String(reader.result).replace(/^data:[^,]*,/, "");
      submit(api("open_bytes", file.name, b64, options()), file.name);
    };
    reader.onerror = function () { say("fl-status", "Couldn't read " + file.name + ".", "error"); };
    say("fl-status", "Opening " + file.name + "…");
    reader.readAsDataURL(file);
  }

  // A path is read by Python straight from disk.
  function startPath(path) {
    path = (path || "").trim().replace(/^"(.*)"$/, "$1");
    if (!path) { say("fl-status", "Choose an image first: Browse…, type its path, or drop it here.", "error"); return; }
    submit(api("open_path", path, options()), path.split(/[\\/]/).pop());
  }

  function submit(call, name) {
    say("fl-status", "Opening " + name + "…");
    $("fl-progress").hidden = false;
    $("fl-progress-bar").style.width = "0%";
    call.then(function (r) { follow(r.id); })
      .catch(function (e) { $("fl-progress").hidden = true; say("fl-status", e.message, "error"); });
  }

  function follow(id) {
    clearTimeout(S.poll);
    api("job", id).then(function (job) {
      if (job.status === "done") {
        $("fl-progress").hidden = true;
        say("fl-status", "Analysis complete in " + job.seconds + " s. Start with the findings below.", "success");
        show(job, true);
        api("config").then(function (c) { renderRecent(c.recent); });
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

  // Python writes each view to a temporary PNG and returns its file:// URL.
  function setOriginal() {
    var orig = $("fl-orig"), jobId = S.job.id;
    if (orig.getAttribute("data-job") === jobId) return;
    api("original", jobId).then(function (r) {
      if (S.job && S.job.id === jobId) { orig.src = r.url; orig.setAttribute("data-job", jobId); }
    }).catch(function (e) { say("fl-caption", e.message, "error"); });
  }

  var viewToken = 0;
  function showView() {
    var t = techByKey(S.tech);
    var v = t && t.views[S.viewIdx];
    var orig = $("fl-orig"), view = $("fl-view"), stack = $("fl-stack");
    var same = !!(v && v.same_size);
    var token = ++viewToken;
    ["fl-hold", "fl-blend"].forEach(function (id) { $(id).disabled = !same; });
    $("fl-save-view").disabled = !v;
    if (!v) {
      view.hidden = true; orig.hidden = false;
      stack.removeAttribute("data-loading");
      setOriginal();
      $("fl-caption").innerHTML = t && t.skipped ? '<span class="skipped">Not run: ' + esc(t.skipped) + "</span>" : "This technique produces findings but no image.";
      return;
    }
    view.hidden = false;
    stack.setAttribute("data-loading", "true");
    view.onload = function () { stack.removeAttribute("data-loading"); };
    view.alt = v.label;
    api("view", S.job.id, S.tech, S.viewIdx).then(function (r) {
      if (token === viewToken) view.src = r.url;
    }).catch(function (e) { stack.removeAttribute("data-loading"); say("fl-caption", e.message, "error"); });
    orig.hidden = !same;
    if (same) setOriginal();
    applyBlend();
    $("fl-caption").textContent = v.label + (v.caption ? ". " + v.caption : "") + "  (" + v.width + " × " + v.height + ")";
  }

  function saveView() {
    var btn = $("fl-save-view");
    btn.disabled = true;
    api("save_view", S.job.id, S.tech, S.viewIdx)
      .then(function (r) { if (r.path) say("fl-caption", "Saved " + r.path, "success"); })
      .catch(function (e) { say("fl-caption", e.message, "error"); })
      .then(function () { btn.disabled = false; });
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
    api("rerun", S.job.id, key, opts).then(function (job) {
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

  function saveReport(kind) {
    var btns = ["fl-report-html", "fl-report-pdf", "fl-report-json"].map($);
    btns.forEach(function (b) { b.disabled = true; });
    if (kind === "pdf") say("fl-status", "Choose where to save the PDF. It takes a few seconds to make.");
    api("save_report", S.job.id, kind)
      .then(function (r) { say("fl-status", r.path ? "Report saved: " + r.path : "", r.path ? "success" : null); })
      .catch(function (e) { say("fl-status", e.message, "error"); })
      .then(function () { btns.forEach(function (b) { b.disabled = false; }); });
  }

  function copyReport() {
    var t = $("fl-report").value;
    var ok = function () { say("fl-report-status", "Copied.", "success"); };
    var fail = function () { $("fl-report").select(); say("fl-report-status", "Couldn't copy automatically. The text is selected; press Ctrl/Cmd+C.", "error"); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(ok, fail); else fail();
  }

  init();
})();
