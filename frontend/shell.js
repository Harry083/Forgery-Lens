// Clarity's shell: the sidebar (workspaces and their sections), the open-evidence list, the Home screen,
// and handing a file from one workspace to the other. Enhance (enhance/*.js) and Authenticate
// (authenticate.js) keep their own state; this file only moves between them.
(function () {
  "use strict";

  var VIEWS = ["home", "enhance", "authenticate", "guide"];
  // the sidebar's own icons, reused for the evidence list
  var ICONS = {
    enhance: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" class="fill"/></svg>',
    auth: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5 5"/><path d="m8 10.6 1.8 1.8L13.2 9"/></svg>'
  };
  var current = "home";
  var q = function (sel, root) { return (root || document).querySelector(sel); };
  var qa = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function go(view, opts) {
    if (VIEWS.indexOf(view) < 0) view = "home";
    var changed = view !== current;
    current = view;
    VIEWS.forEach(function (v) { q("#view-" + v).hidden = v !== view; });
    qa(".nav-item[data-go]").forEach(function (a) { a.classList.toggle("active", a.getAttribute("data-go") === view); });
    qa(".nav-sub").forEach(function (s) { s.classList.toggle("open", s.getAttribute("data-for") === view); });
    try { localStorage.setItem("cl-view", view); } catch (e) { /* storage can be unavailable */ }
    if (changed && !(opts && opts.keepScroll)) window.scrollTo(0, 0);
    updateNav();
  }

  function scrollTo(id) {
    var el = document.getElementById(id);
    if (!el) return;
    if (el.tagName === "DETAILS") el.open = true;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------- sidebar navigation ----------
  document.addEventListener("click", function (e) {
    var g = e.target.closest("[data-go]");
    if (g) {
      e.preventDefault();
      go(g.getAttribute("data-go"));
      return;
    }
    var t = e.target.closest(".nav-sub [data-target]");
    if (t) {
      e.preventDefault();
      if (t.getAttribute("aria-disabled") === "true") return;
      scrollTo(t.getAttribute("data-target"));
    }
  });

  // Sections that aren't on screen yet (nothing opened, no analysis) are dimmed; the one in view is highlighted.
  function visible(el) { return !!el && el.offsetParent !== null; }
  function updateNav() {
    var sub = q('.nav-sub[data-for="' + current + '"]');
    if (!sub) return;
    var links = qa("[data-target]", sub);
    var active = null;
    links.forEach(function (a) {
      var el = document.getElementById(a.getAttribute("data-target"));
      var on = visible(el);
      a.setAttribute("aria-disabled", on ? "false" : "true");
      if (on && el.getBoundingClientRect().top < window.innerHeight * 0.35) active = a;
    });
    if (!active) active = links.filter(function (a) { return a.getAttribute("aria-disabled") === "false"; })[0];
    links.forEach(function (a) { a.classList.toggle("current", a === active); });
  }
  window.addEventListener("scroll", updateNav, { passive: true });
  new MutationObserver(function () { clearTimeout(updateNav.t); updateNav.t = setTimeout(updateNav, 60); })
    .observe(q("main"), { subtree: true, attributes: true, attributeFilter: ["hidden", "class", "open"] });

  // ---------- open evidence (one per workspace) ----------
  function shortHash(h) { return h ? h.slice(0, 12) + "…" : ""; }
  function evidence() {
    var items = [];
    // CL is a top-level const in enhance/core.js, so it's global but not a property of window
    var src = typeof CL !== "undefined" && CL.state.source;
    if (src) {
      var f = src.files && src.files[0] || {};
      items.push({
        view: "enhance", icon: ICONS.enhance, name: src.kind === "sequence" ? src.name + " +" + (src.count - 1) : src.name,
        sub: (src.kind === "video" ? "video · " + src.count + " frames" : src.kind === "sequence" ? "sequence" : "image") + " · " + src.width + "×" + src.height,
        hash: src.hashed ? (src.file_count > 1 ? "hashed (" + src.file_count + " files)" : "SHA-256 " + shortHash(f.sha256)) : "hashing…",
        title: src.path
      });
    }
    var job = window.FL && FL.job();
    if (job) {
      var ex = job.exhibit;
      items.push({
        view: "authenticate", icon: ICONS.auth, name: ex.file, sub: ex.format + " · " + ex.width + "×" + ex.height +
          (job.status === "done" ? "" : " · analysing…"),
        hash: ex.sha256 ? "SHA-256 " + shortHash(ex.sha256) : "", title: ex.path || ex.file
      });
    }
    return items;
  }
  var lastEvidence = "";
  function renderEvidence() {
    var items = evidence();
    var key = JSON.stringify(items) + current;
    if (key === lastEvidence) return;
    lastEvidence = key;
    q("#evidence-list").innerHTML = items.length ? items.map(function (it) {
      return '<a href="#" class="evidence-item' + (it.view === current ? " active" : "") + '" data-go="' + it.view + '" title="' + esc(it.title) + '">' +
        '<span class="ev-icon" aria-hidden="true">' + it.icon + '</span><span class="ev-main"><span class="ev-name">' + esc(it.name) + "</span>" +
        '<span class="ev-sub">' + esc(it.sub) + '</span><span class="ev-hash">' + esc(it.hash) + "</span></span></a>";
    }).join("") : '<p class="evidence-empty">Nothing open yet.</p>';
  }
  setInterval(renderEvidence, 700);

  // ---------- Home ----------
  function click(id) { var b = document.getElementById(id); if (b) b.click(); }
  q("#home-enhance").addEventListener("click", function () { go("enhance"); click("browse-media"); });
  q("#home-sequence").addEventListener("click", function () { go("enhance"); click("browse-sequence"); });
  q("#home-project").addEventListener("click", function () { go("enhance"); click("open-project"); });
  q("#home-auth").addEventListener("click", function () { go("authenticate"); var b = q("#fl-drop .browse-btn"); if (b) b.click(); });

  // An image dropped on the Authenticate card (or anywhere on Home) goes to Authenticate, ready to analyse.
  var card = q("#home-auth-card");
  ["dragenter", "dragover"].forEach(function (ev) {
    q("#view-home").addEventListener(ev, function (e) { e.preventDefault(); card.setAttribute("data-over", "true"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    q("#view-home").addEventListener(ev, function (e) { e.preventDefault(); card.removeAttribute("data-over"); });
  });
  q("#view-home").addEventListener("drop", function (e) {
    var f = e.dataTransfer.files[0];
    if (f && window.FL) { go("authenticate"); FL.hold(f); }
  });

  // ---------- hand a file between workspaces ----------
  window.Shell = {
    go: go,
    is: function (v) { return current === v; },
    current: function () { return current; },
    toAuthenticate: function (path) { go("authenticate"); FL.analysePath(path); },
    toEnhance: function (path) { go("enhance"); CL.openSource([path]); }
  };

  var saved = "home";
  try { saved = localStorage.getItem("cl-view") || "home"; } catch (e) { /* ignore */ }
  // a workspace with nothing open would just be an empty form after a restart, so start at Home then
  go(saved === "guide" ? "guide" : "home");
})();
