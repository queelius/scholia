// scholia v0.5.0 viewer
//
// The browser is read-only by design.  The human is a reviewer; the
// agent is the author.  The viewer's job:
//
//   1. Render the PDF via PDF.js with a selectable text layer.
//   2. Subscribe to compile events over WebSocket; auto-reload on success.
//   3. Display the comments queue with inline reply/resolve/dismiss forms.
//   4. Convert text selections in the PDF into pdf_region anchors with
//      bbox coords in PDF points (the unit SyncTeX speaks).
//   5. Surface compile errors as a small banner above the comments list.
//
// All DOM construction goes through the `h()` helper, which builds DOM
// nodes via createElement/textContent — no innerHTML on dynamic data.

import * as pdfjsLib from "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.min.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.worker.min.mjs";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ---------------------------------------------------------------------------
// DOM helper: hyperscript-style element factory (safe — no innerHTML)
// ---------------------------------------------------------------------------

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class" || k === "className") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "data") {
        for (const [dk, dv] of Object.entries(v)) el.dataset[dk] = dv;
      } else if (k === "style") {
        for (const [sk, sv] of Object.entries(v)) el.style[sk] = sv;
      } else if (k.startsWith("on")) {
        el.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (k === "title" || k === "type" || k === "value" || k === "placeholder") {
        el[k] = v;
      } else {
        el.setAttribute(k, v);
      }
    }
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    if (typeof c === "string" || typeof c === "number") {
      el.appendChild(document.createTextNode(String(c)));
    } else {
      el.appendChild(c);
    }
  }
  return el;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function placeholder(text) {
  return h("p", { class: "placeholder", text });
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  pdfDoc: null,
  renderScale: 1.4,
  pages: [],
  comments: [],
  paper: null,
  errors: [],
  warnings: [],
  ws: null,
  pendingAnchor: null,
  expanded: new Set(),
  // {cid, mode} when an inline reply/resolve/dismiss form is open.
  activeForm: null,
};

// ---------------------------------------------------------------------------
// PDF rendering
// ---------------------------------------------------------------------------

async function loadPdf() {
  try {
    const loadingTask = pdfjsLib.getDocument({
      url: `/pdf?t=${Date.now()}`,
      withCredentials: false,
    });
    const pdf = await loadingTask.promise;
    state.pdfDoc = pdf;
    await renderAllPages();
    refreshAnnotationOverlays();
  } catch (err) {
    showPdfError(err);
  }
}

async function renderAllPages() {
  const host = $("#pdf-canvas-host");
  clear(host);
  state.pages = [];
  for (let n = 1; n <= state.pdfDoc.numPages; n++) {
    const wrap = h("div", { class: "pdf-page", data: { page: String(n) } });
    host.appendChild(wrap);

    const page = await state.pdfDoc.getPage(n);
    const viewport = page.getViewport({ scale: state.renderScale });
    const canvas = h("canvas", { class: "pdf-canvas" });
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    wrap.style.width = `${viewport.width}px`;
    wrap.style.height = `${viewport.height}px`;
    wrap.appendChild(canvas);

    const textLayer = h("div", { class: "text-layer" });
    wrap.appendChild(textLayer);

    const overlay = h("div", { class: "annotation-overlay", data: { page: String(n) } });
    wrap.appendChild(overlay);

    const ctx = canvas.getContext("2d");
    await page.render({ canvasContext: ctx, viewport }).promise;

    const textContent = await page.getTextContent();
    if (pdfjsLib.renderTextLayer) {
      pdfjsLib.renderTextLayer({
        textContentSource: textContent,
        container: textLayer,
        viewport,
      });
    } else {
      const layer = new pdfjsLib.TextLayer({
        textContentSource: textContent,
        container: textLayer,
        viewport,
      });
      await layer.render();
    }

    state.pages.push({ pageNum: n, viewport, canvas, textLayer, overlay, wrap });
  }
}

function showPdfError(err) {
  const host = $("#pdf-canvas-host");
  clear(host);
  host.appendChild(placeholder(`PDF unavailable. ${err && err.message ? err.message : err}`));
}

// ---------------------------------------------------------------------------
// Selection -> pdf_region anchor
// ---------------------------------------------------------------------------

function attachSelectionListener() {
  document.addEventListener("mouseup", () => {
    setTimeout(updateSelectionToolbar, 0);
  });
  document.addEventListener("selectionchange", updateSelectionToolbar);
}

// ---------------------------------------------------------------------------
// Rectangular region selection (shift-click-drag)
//
// Lets the human box-select figures, equations, or any whitespace region
// where text-layer selection doesn't reach.  Produces the same
// pdf_region anchor shape as text selection, so comment + image-capture
// plumbing is identical.
// ---------------------------------------------------------------------------

function attachRegionDragListener() {
  // We store a *reference* to the page element (not a cached rect) so
  // page-local coordinates stay correct even if the user scrolls
  // mid-drag.  getBoundingClientRect() is re-queried on every event.
  let drag = null;  // {pageNum, pageEl, startX, startY, overlay}

  const pageLocal = (ev, pageEl) => {
    const r = pageEl.getBoundingClientRect();
    return [ev.clientX - r.left, ev.clientY - r.top];
  };

  document.addEventListener("mousedown", (ev) => {
    if (!ev.shiftKey || ev.button !== 0) return;
    const pageEl = ev.target.closest(".pdf-page");
    if (!pageEl) return;
    ev.preventDefault();
    // Suppress text selection while we drag.
    window.getSelection()?.removeAllRanges();
    const overlay = h("div", { class: "region-drag" });
    pageEl.appendChild(overlay);
    const [startX, startY] = pageLocal(ev, pageEl);
    drag = {
      pageNum: parseInt(pageEl.dataset.page, 10),
      pageEl,
      startX,
      startY,
      overlay,
    };
  }, true);

  document.addEventListener("mousemove", (ev) => {
    if (!drag) return;
    const { pageEl, startX, startY, overlay } = drag;
    const [x, y] = pageLocal(ev, pageEl);
    overlay.style.left = `${Math.min(startX, x)}px`;
    overlay.style.top = `${Math.min(startY, y)}px`;
    overlay.style.width = `${Math.abs(x - startX)}px`;
    overlay.style.height = `${Math.abs(y - startY)}px`;
  }, true);

  document.addEventListener("mouseup", (ev) => {
    if (!drag) return;
    const { pageNum, pageEl, startX, startY, overlay } = drag;
    drag = null;
    overlay.remove();
    const [endX, endY] = pageLocal(ev, pageEl);
    // Convert page-local CSS px to PDF points.
    const x1 = Math.min(startX, endX) / state.renderScale;
    const y1 = Math.min(startY, endY) / state.renderScale;
    const x2 = Math.max(startX, endX) / state.renderScale;
    const y2 = Math.max(startY, endY) / state.renderScale;
    // Ignore tiny drags (accidental clicks); threshold is in PDF points.
    if (x2 - x1 < 4 || y2 - y1 < 4) return;
    openCompose(
      { kind: "pdf_region", page: pageNum, bbox: [x1, y1, x2, y2] },
      `PDF p${pageNum}: rectangular region (${Math.round(x2 - x1)}×${Math.round(y2 - y1)} pt)`,
    );
  }, true);
}

function getSelectionPdfRegion() {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  const rects = range.getClientRects();
  if (rects.length === 0) return null;

  let node = range.startContainer;
  while (node && node.nodeType !== 1) node = node.parentNode;
  let pageEl = node;
  while (pageEl && !(pageEl.classList && pageEl.classList.contains("pdf-page"))) {
    pageEl = pageEl.parentElement;
  }
  if (!pageEl) return null;

  const pageNum = parseInt(pageEl.dataset.page, 10);
  const pageInfo = state.pages.find((p) => p.pageNum === pageNum);
  if (!pageInfo) return null;

  const pageRect = pageEl.getBoundingClientRect();
  let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
  for (const r of rects) {
    x1 = Math.min(x1, r.left);
    y1 = Math.min(y1, r.top);
    x2 = Math.max(x2, r.right);
    y2 = Math.max(y2, r.bottom);
  }
  const cx1 = (x1 - pageRect.left) / state.renderScale;
  const cy1 = (y1 - pageRect.top) / state.renderScale;
  const cx2 = (x2 - pageRect.left) / state.renderScale;
  const cy2 = (y2 - pageRect.top) / state.renderScale;

  return {
    page: pageNum,
    bbox: [cx1, cy1, cx2, cy2],
    text: sel.toString().trim(),
  };
}

function updateSelectionToolbar() {
  const region = getSelectionPdfRegion();
  const tb = $("#selection-toolbar");
  if (!region) {
    tb.classList.add("hidden");
    return;
  }
  tb.classList.remove("hidden");
  const sel = window.getSelection();
  const range = sel.getRangeAt(0);
  const rect = range.getBoundingClientRect();
  tb.style.left = `${window.scrollX + rect.left}px`;
  tb.style.top = `${window.scrollY + rect.top - 36}px`;
  tb.dataset.region = JSON.stringify(region);
}

// ---------------------------------------------------------------------------
// Compose dialog
// ---------------------------------------------------------------------------

function openCompose(anchor, label) {
  state.pendingAnchor = anchor;
  $("#compose-anchor").textContent = label;
  $("#compose-text").value = "";
  $("#compose-dialog").showModal();
  setTimeout(() => $("#compose-text").focus(), 50);
}

async function submitCompose(ev) {
  ev.preventDefault();
  const text = $("#compose-text").value.trim();
  if (!text || !state.pendingAnchor) return;
  const body = { anchor: state.pendingAnchor, text, author: "human" };
  $("#compose-dialog").close();
  state.pendingAnchor = null;
  try {
    const resp = await fetch("/comments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert(`Could not save comment: ${err.error || resp.status}`);
      return;
    }
    await refreshComments();
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// Sidebar — comments
// ---------------------------------------------------------------------------

async function refreshComments() {
  const status = $("#comment-filter").value;
  const url = status === "all" ? "/comments" : `/comments?status=${status}`;
  try {
    const resp = await fetch(url);
    const data = await resp.json();
    state.comments = data.comments || [];
    renderComments();
    refreshAnnotationOverlays();
  } catch (err) {
    console.warn("refreshComments failed:", err);
  }
}

function renderComments() {
  const list = $("#comments-list");
  clear(list);
  if (state.comments.length === 0) {
    list.appendChild(placeholder("No comments at this filter."));
    updateCommentCount();
    return;
  }
  for (const c of state.comments) list.appendChild(renderCommentItem(c));
  updateCommentCount();
}

function renderCommentItem(c) {
  const expanded = state.expanded.has(c.id);

  const headChildren = [
    h("button", {
      class: "cmt-toggle",
      type: "button",
      title: expanded ? "collapse" : "expand",
      text: expanded ? "▾" : "▸",
      onclick: () => {
        if (state.expanded.has(c.id)) state.expanded.delete(c.id);
        else state.expanded.add(c.id);
        renderComments();
      },
    }),
    h("span", { class: "cmt-id", text: c.id }),
    h("span", { class: "cmt-status", text: `[${c.status}]` }),
    c.stale ? h("span", { class: "stale", text: "STALE" }) : null,
    h("button", {
      class: "cmt-anchor",
      type: "button",
      title: "Jump to anchor",
      text: anchorLabel(c.anchor),
      onclick: () => jumpToComment(c.id),
    }),
  ];

  const head = h("div", { class: "cmt-head" }, ...headChildren);
  const preview = h("div", { class: "cmt-preview", text: c.thread[0]?.text || "" });

  const children = [head, preview];

  if (expanded) {
    const thread = h("div", { class: "cmt-thread" },
      ...c.thread.map(renderThreadEntry));
    const actions = h("div", { class: "cmt-actions" }, ...actionButtons(c));
    children.push(thread, actions);
    const inlineForm = renderActiveForm(c);
    if (inlineForm) children.push(inlineForm);
  }

  return h("div", {
    class: `cmt status-${c.status}`,
    data: { commentId: c.id },
  }, ...children);
}

function renderThreadEntry(entry) {
  const meta = h("div", {
    class: "thread-meta",
    text: `${entry.author} · ${entry.at}`,
  });
  const txt = h("div", { class: "thread-text", text: entry.text });
  const children = [meta, txt];
  if (entry.edits && entry.edits.length > 0) {
    children.push(
      h("div", { class: "thread-edits" },
        ...entry.edits.map((e) => h("span", { class: "edit", text: e })),
      ),
    );
  }
  return h("div", { class: `thread-entry author-${entry.author}` }, ...children);
}

function actionBtn(cls, label, onclick) {
  return h("button", { class: cls, type: "button", text: label, onclick });
}

function confirmDelete(cid) {
  if (confirm(`Permanently delete ${cid}?`)) doMutation(cid, "delete", {});
}

function actionButtons(c) {
  const deleteBtn = actionBtn("cmt-delete", "Delete", () => confirmDelete(c.id));
  if (c.status !== "open") return [deleteBtn];
  return [
    actionBtn("cmt-reply", "Reply", () => setActiveForm(c.id, "reply")),
    actionBtn("cmt-resolve", "Resolve", () => setActiveForm(c.id, "resolve")),
    actionBtn("cmt-dismiss", "Dismiss", () => setActiveForm(c.id, "dismiss")),
    deleteBtn,
  ];
}

function setActiveForm(cid, mode) {
  state.activeForm = { cid, mode };
  state.expanded.add(cid);
  renderComments();
}

const FORM_PLACEHOLDERS = {
  reply: "Reply…",
  resolve: "Summary of what was changed",
  dismiss: "Why dismiss?",
};
const FORM_BODY_KEY = { reply: "text", resolve: "summary", dismiss: "reason" };
const FORM_SUBMIT_LABEL = { reply: "Post reply", resolve: "Resolve", dismiss: "Dismiss" };

function renderActiveForm(c) {
  if (!state.activeForm || state.activeForm.cid !== c.id) return null;
  const mode = state.activeForm.mode;
  const ta = h("textarea", {
    class: "cmt-form-input",
    rows: 3,
    placeholder: FORM_PLACEHOLDERS[mode],
  });
  // Focus after the element is in the DOM.
  setTimeout(() => ta.focus(), 0);

  const submit = () => {
    const text = ta.value.trim();
    if (!text) return;
    const body = { author: "human" };
    body[FORM_BODY_KEY[mode]] = text;
    state.activeForm = null;
    doMutation(c.id, mode, body);
  };
  ta.addEventListener("keydown", (ev) => {
    // Cmd/Ctrl+Enter submits; Esc cancels.
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") {
      ev.preventDefault();
      submit();
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      state.activeForm = null;
      renderComments();
    }
  });

  return h("div", { class: `cmt-form mode-${mode}` },
    ta,
    h("div", { class: "cmt-form-actions" },
      h("button", {
        class: "cmt-form-cancel",
        type: "button",
        text: "Cancel",
        onclick: () => { state.activeForm = null; renderComments(); },
      }),
      h("button", {
        class: "cmt-form-submit",
        type: "button",
        text: FORM_SUBMIT_LABEL[mode],
        onclick: submit,
      }),
    ),
  );
}

function anchorLabel(anchor) {
  if (!anchor) return "[?]";
  switch (anchor.kind) {
    case "paper": return "[paper]";
    case "section": return `[section: ${anchor.title}]`;
    case "source_range": return `[${anchor.file}:${anchor.line_start}-${anchor.line_end}]`;
    case "pdf_region": return `[pdf p${anchor.page}]`;
    default: return "[?]";
  }
}

function updateCommentCount() {
  const open = state.comments.filter((c) => c.status === "open").length;
  $("#comment-count").textContent = `${open} open`;
}

// ---------------------------------------------------------------------------
// Comment mutations
// ---------------------------------------------------------------------------

async function doMutation(cid, action, body) {
  let url, method;
  if (action === "delete") {
    url = `/comments/${cid}`;
    method = "DELETE";
    body = null;
  } else {
    url = `/comments/${cid}/${action}`;
    method = "POST";
  }
  const opts = { method };
  if (body) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  try {
    const resp = await fetch(url, opts);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert(`Action failed: ${err.error || resp.status}`);
      return;
    }
    await refreshComments();
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

// (Inline forms via renderActiveForm replace the old prompt() based flow.)

// ---------------------------------------------------------------------------
// Annotation overlays
// ---------------------------------------------------------------------------

function refreshAnnotationOverlays() {
  for (const p of state.pages) clear(p.overlay);
  for (const c of state.comments) {
    if (c.anchor.kind !== "pdf_region") continue;
    if (c.status !== "open") continue;
    const pageInfo = state.pages.find((p) => p.pageNum === c.anchor.page);
    if (!pageInfo) continue;
    const [x1, y1, x2, y2] = c.anchor.bbox;
    const left = x1 * state.renderScale;
    const top = y1 * state.renderScale;
    const width = Math.max((x2 - x1) * state.renderScale, 6);
    const height = Math.max((y2 - y1) * state.renderScale, 14);
    const mark = h("a", {
      class: "annotation-mark",
      title: c.thread[0]?.text || "",
      data: { cid: c.id },
      style: {
        left: `${left}px`,
        top: `${top}px`,
        width: `${width}px`,
        height: `${height}px`,
      },
      onclick: (ev) => {
        ev.preventDefault();
        state.expanded.add(c.id);
        switchTab("comments");
        renderComments();
        const node = document.querySelector(`[data-comment-id="${c.id}"]`);
        node?.scrollIntoView({ behavior: "smooth", block: "center" });
      },
    });
    pageInfo.overlay.appendChild(mark);
  }
}

// ---------------------------------------------------------------------------
// Compile error banner — shows above the comment list when the build
// fails or warns; one entry per error/warning, expandable for context.
// ---------------------------------------------------------------------------

function renderErrorBanner() {
  const banner = $("#error-banner");
  clear(banner);
  if (state.errors.length === 0 && state.warnings.length === 0) {
    banner.classList.add("hidden");
    return;
  }
  banner.classList.remove("hidden");
  const items = [
    ...state.errors.map((e) => ({ ...e, level: "error" })),
    ...state.warnings.map((w) => ({ ...w, level: "warning" })),
  ];
  for (const e of items) {
    const children = [
      h("div", { class: "err-loc",
        text: `${e.file || ""}${e.line ? ":" + e.line : ""}` }),
      h("div", { class: "err-msg", text: e.message || "" }),
    ];
    if (e.context && e.context.length > 0) {
      children.push(h("pre", { class: "err-context", text: e.context.join("\n") }));
    }
    banner.appendChild(h("div", { class: `err-item err-${e.level}` }, ...children));
  }
}

async function refreshPaper() {
  try {
    const resp = await fetch("/paper");
    const data = await resp.json();
    state.paper = data;
    renderPaper();
  } catch (err) {
    console.warn("refreshPaper failed:", err);
  }
}

function renderPaper() {
  const info = $("#paper-info");
  clear(info);
  if (!state.paper) {
    info.appendChild(placeholder("Loading…"));
    return;
  }
  const p = state.paper;
  const compile = p.last_compile || {};
  const compileText =
    compile.success === true
      ? "✓ success"
      : compile.success === false
        ? "✗ failed"
        : "— (none yet)";
  const duration =
    typeof compile.duration_seconds === "number"
      ? ` · ${compile.duration_seconds.toFixed(2)}s`
      : "";

  const summary = h("div", { class: "paper-summary" },
    h("div", null,
      h("strong", { text: "Main: " }),
      p.main_file || "?"),
    h("div", null,
      h("strong", { text: "Last compile: " }),
      compileText + duration),
    h("div", null,
      h("strong", { text: "Sections: " }),
      String(p.sections?.length || 0)),
  );
  info.appendChild(summary);
  info.appendChild(h("h4", { text: "Sections" }));

  const list = h("ul", { class: "paper-sections" });
  for (const s of p.sections || []) {
    list.appendChild(
      h("li", { class: `paper-section level-${s.level}` },
        h("span", { class: "paper-section-title", text: s.title }),
        h("span", { class: "paper-section-loc", text: `${s.file}:${s.line}` }),
        h("button", {
          class: "comment-section-btn",
          type: "button",
          text: "+ comment",
          onclick: () =>
            openCompose({ kind: "section", title: s.title }, `Section: ${s.title}`),
        }),
      ),
    );
  }
  info.appendChild(list);
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

function switchTab(name) {
  for (const btn of $$(".tab-btn")) {
    btn.classList.toggle("active", btn.dataset.tab === name);
  }
  for (const pane of $$(".tab-pane")) {
    pane.classList.toggle("active", pane.id === `tab-${name}`);
  }
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  state.ws = ws;
  ws.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    handleWSMessage(msg);
  };
  ws.onclose = () => setTimeout(connectWS, 2000);
}

function applyCompileResult(r) {
  state.errors = r.errors || [];
  state.warnings = r.warnings || [];
  $("#compile-status").textContent = r.success ? "✓ ok" : "✗ failed";
  $("#error-count").textContent = `${state.errors.length} errors`;
  renderErrorBanner();
}

function handleWSMessage(msg) {
  switch (msg.type) {
    case "compiling":
      $("#compile-status").textContent = msg.status ? "compiling…" : "idle";
      break;
    case "compiled": {
      const r = msg.result || {};
      applyCompileResult(r);
      if (r.success) loadPdf();
      refreshPaper();
      break;
    }
    case "comment_added":
    case "comment_updated":
    case "comment_deleted":
      refreshComments();
      break;
    case "state":
      if (msg.result) applyCompileResult(msg.result);
      break;
    case "goto":
      if (msg.page) jumpToPage(msg.page);
      break;
  }
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

function jumpToPage(pageNum) {
  const p = state.pages.find((x) => x.pageNum === pageNum);
  if (p) p.wrap.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function jumpToComment(cid) {
  const c = state.comments.find((x) => x.id === cid);
  if (!c) return;
  if (c.anchor.kind === "pdf_region") {
    jumpToPage(c.anchor.page);
  } else if (c.resolved_source) {
    try {
      const params = new URLSearchParams({
        file: c.resolved_source.file,
        line: String(c.resolved_source.line_start),
      });
      const resp = await fetch(`/synctex/source-to-pdf?${params}`);
      if (resp.ok) {
        const data = await resp.json();
        if (data.page) jumpToPage(data.page);
      }
    } catch {
      /* ignore */
    }
  }
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

function init() {
  attachSelectionListener();
  attachRegionDragListener();

  $("#recompile-btn").addEventListener("click", () => fetch("/compile", { method: "POST" }));
  $("#paper-comment-btn").addEventListener("click", () =>
    openCompose({ kind: "paper" }, "Paper-level comment"),
  );
  $("#comment-selection-btn").addEventListener("click", () => {
    const tb = $("#selection-toolbar");
    const region = tb.dataset.region ? JSON.parse(tb.dataset.region) : null;
    if (!region) return;
    tb.classList.add("hidden");
    openCompose(
      { kind: "pdf_region", page: region.page, bbox: region.bbox },
      `PDF p${region.page}: "${region.text.slice(0, 80)}${region.text.length > 80 ? "…" : ""}"`,
    );
  });

  $("#compose-form").addEventListener("submit", submitCompose);
  $("#compose-cancel").addEventListener("click", (ev) => {
    ev.preventDefault();
    $("#compose-dialog").close();
  });
  $("#comment-filter").addEventListener("change", refreshComments);

  for (const btn of $$(".tab-btn")) {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  }

  connectWS();
  loadPdf();
  refreshComments();
  refreshPaper();
}

document.addEventListener("DOMContentLoaded", init);
