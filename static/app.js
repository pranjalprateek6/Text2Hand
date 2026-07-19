"use strict";

/* Text2Hand studio.
 *
 * Views: Write (editor + preview) and Library (past renders, kept in
 * localStorage so the tool remembers work without needing accounts).
 * The server does the two slow things as polled jobs: reading a PDF into
 * Markdown, and rendering text to pages.
 */

const $ = (id) => document.getElementById(id);

const MAX_CHARS = window.MAX_CHARS || 60000;
const PAGE_SIZE = window.PAGE_SIZE || 1200;

const state = {
  id: null, pages: 0, page: 1,
  file: null,
  rendered: null,     // JSON of what is on screen, to skip pointless re-renders
  timer: null,        // auto-render debounce
  busy: false,
  queued: false,      // a change arrived while a render was running
};

/* -------------------------------------------------------------------- theme */

$("themeBtn").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("t2h.theme", next);
});

/* -------------------------------------------------------------------- views */

function showView(which) {
  const write = which === "write";
  $("viewWrite").hidden = !write;
  $("viewLibrary").hidden = write;
  $("navWrite").classList.toggle("is-on", write);
  $("navLibrary").classList.toggle("is-on", !write);
  if (!write) drawLibrary();
}
$("navWrite").addEventListener("click", () => showView("write"));
$("navLibrary").addEventListener("click", () => showView("library"));

/* ------------------------------------------------------------------ options */

const OPTIONS = { markdown: false, ruled: true, texture: true, skew: true };
let INK = "blue-black";

Object.keys(OPTIONS).forEach((key) => {
  const el = $("o" + key[0].toUpperCase() + key.slice(1));
  el.addEventListener("click", () => {
    OPTIONS[key] = !OPTIONS[key];
    el.classList.toggle("is-on", OPTIONS[key]);
    el.setAttribute("aria-pressed", String(OPTIONS[key]));
    touched();
  });
});

document.querySelectorAll(".ink").forEach((dot) => {
  dot.addEventListener("click", () => {
    INK = dot.dataset.ink;
    document.querySelectorAll(".ink").forEach((d) => {
      d.classList.toggle("is-on", d === dot);
      d.setAttribute("aria-checked", String(d === dot));
    });
    touched();
  });
});

function setOptions(opts, ink) {
  Object.keys(OPTIONS).forEach((key) => {
    OPTIONS[key] = !!opts[key];
    const el = $("o" + key[0].toUpperCase() + key.slice(1));
    el.classList.toggle("is-on", OPTIONS[key]);
    el.setAttribute("aria-pressed", String(OPTIONS[key]));
  });
  const want = document.querySelector(`.ink[data-ink="${ink}"]`);
  if (want) want.click();
}

/* ---------------------------------------------------------- title and count */

function title() {
  const first = ($("text").value.split("\n").find((l) => l.trim()) || "").trim()
    .replace(/^#+\s*/, "");
  return first ? (first.length > 40 ? first.slice(0, 40) + "…" : first) : "Untitled";
}

function count() {
  const n = $("text").value.length;
  const pages = Math.max(1, Math.ceil(n / PAGE_SIZE));
  $("count").textContent = n ? `${n.toLocaleString()} · ~${pages}p` : "0";
  $("count").classList.toggle("is-over", n > MAX_CHARS);
  $("tries").hidden = n > 0;
  $("docTitle").textContent = title();
}

$("text").addEventListener("input", () => { count(); touched(); });

/* ------------------------------------------------------------------ samples */

const SAMPLES = {
  pangram:
    "The quick brown fox jumps over the lazy dog.\n" +
    "Pack my box with five dozen liquor jugs.",
  punctuation:
    "Punctuation works now: colons, semicolons; and \"quotation marks\" all render.\n\n" +
    "It's got apostrophes too, so contractions like don't and can't look right.",
  symbols:
    "Technical symbols work too: a/b, x = y + 1, 50% off, c & d, and user@host.\n\n" +
    "Brackets and braces: (a) [b] {c}, a pipe x|y, and under_score.",
  markdown:
    "# Deep Learning Notes\n\n" +
    "An introduction to the topic, written as ordinary prose so the wrapping\n" +
    "can be checked.\n\n" +
    "## Key Ideas\n\n" +
    "- Neural networks learn from data\n" +
    "- Layers stack to form depth\n" +
    "    - Each layer extracts features\n" +
    "- Training adjusts the weights\n\n" +
    "1. Collect the data\n" +
    "2. Train the model\n\n" +
    "> Deep learning is a subset of machine learning.\n\n" +
    "---\n\n" +
    "![Figure 1 accuracy over time](chart.png)\n",
};

document.querySelectorAll(".try").forEach((btn) => {
  btn.addEventListener("click", () => {
    const which = btn.dataset.sample;
    $("text").value = SAMPLES[which] || "";
    if (OPTIONS.markdown !== (which === "markdown")) $("oMarkdown").click();
    count();
    touched();
    $("text").focus();
  });
});

/* -------------------------------------------------------------------- notes */

function note(message, bad) {
  const el = $("note");
  el.textContent = message;
  el.classList.toggle("is-bad", !!bad);
  el.hidden = false;
}
function hush() { $("note").hidden = true; }

/* --------------------------------------------------------------------- jobs */

async function poll(jobId, onStage) {
  while (true) {
    await new Promise((r) => setTimeout(r, 400));
    const res = await fetch(`/api/job/${jobId}`);
    if (!res.ok) return { error: "Lost track of that job." };
    const job = await res.json();
    if (job.state === "running") { if (onStage) onStage(job.message); continue; }
    if (job.state === "error") return { error: job.error || "That did not work." };
    return job;
  }
}

/* ------------------------------------------------------------------- render */

function payload() {
  return {
    text: $("text").value,
    ruled: OPTIONS.ruled, texture: OPTIONS.texture, skew: OPTIONS.skew,
    markdown: OPTIONS.markdown, ink: INK,
  };
}

function setBusy(on, stage) {
  state.busy = on;
  $("render").classList.toggle("is-busy", on);
  $("renderLabel").textContent = on ? (stage || "Rendering") : "Render";
  $("status").textContent = on ? (stage || "Rendering…") : $("status").textContent;
  $("status").classList.toggle("is-live", on);
  $("loading").hidden = !on;
  if (on) {
    $("empty").hidden = true;
    $("page").hidden = true;
    $("pager").hidden = true;
    $("loadingText").textContent = stage || "Laying out the text";
  }
}

async function render() {
  const body = payload();
  if (!body.text.trim()) { note("Write something first."); $("text").focus(); return; }

  hush();
  setBusy(true);

  try {
    const res = await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const started = await res.json();
    if (!res.ok) { setBusy(false); after(); note(started.error || "That did not work.", true); return; }

    const done = await poll(started.job, (m) => {
      $("loadingText").textContent = m;
      $("renderLabel").textContent = m;
      $("status").textContent = m + "…";
    });
    setBusy(false);
    if (done.error) { after(); note(done.error, true); return; }

    state.id = done.id;
    state.pages = done.pages;
    state.rendered = JSON.stringify(body);
    show(1);
    $("exportBtn").disabled = false;
    $("status").textContent = `${done.pages} page${done.pages === 1 ? "" : "s"} · ${body.text.length.toLocaleString()} chars`;
    remember(done, body);

    if (done.missing && done.missing.length) {
      note("No glyph for: " + done.missing.join(" "));
    }
  } catch (err) {
    setBusy(false);
    after();
    note("Could not reach the server. Is it still running?", true);
  } finally {
    // a change that arrived mid-render still deserves its render
    if (state.queued) { state.queued = false; touched(); }
  }
}

/* what the preview shows when a render fails or is missing */
function after() {
  if (state.id) { $("page").hidden = false; $("pager").hidden = state.pages < 2; }
  else $("empty").hidden = false;
  $("status").textContent = "Ready";
  $("status").classList.remove("is-live");
}

function show(n) {
  state.page = Math.min(Math.max(n, 1), state.pages);
  $("page").src = `/preview/${state.id}/${state.page}.jpg`;
  $("page").alt = `Page ${state.page}`;
  $("page").hidden = false;
  $("empty").hidden = true;
  $("n").textContent = `${state.page} / ${state.pages}`;
  $("prev").disabled = state.page === 1;
  $("next").disabled = state.page === state.pages;
  $("pager").hidden = state.pages < 2;
  // keep the numbered form the pane labels use: "02 / 3 PAGES"
  $("previewLabel").textContent =
    "02 / " + (state.pages === 1 ? "1 page" : `${state.pages} pages`);
  $("dlPdf").href = `/download/${state.id}/handwriting.pdf`;
  $("dlZip").href = `/download/${state.id}/pages.zip`;
  $("dlPagePng").href = `/download/${state.id}/page_${state.page}.png`;
  $("dlPagePdf").href = `/download/${state.id}/page_${state.page}.pdf`;
}

$("render").addEventListener("click", render);
$("prev").addEventListener("click", () => show(state.page - 1));
$("next").addEventListener("click", () => show(state.page + 1));

/* ------------------------------------------------------------- auto render */

// Overleaf-style: when Auto is on, a pause after a change re-renders. Off by
// default because every render is real work on the server, and remembered
// because a preference that resets is a nag.
$("auto").checked = localStorage.getItem("t2h.auto") === "1";
$("auto").addEventListener("change", () => {
  localStorage.setItem("t2h.auto", $("auto").checked ? "1" : "0");
  touched();
});

function touched() {
  if (!$("auto").checked) return;
  if (!$("text").value.trim()) return;
  if (JSON.stringify(payload()) === state.rendered) return;
  if (state.busy) { state.queued = true; return; }
  clearTimeout(state.timer);
  state.timer = setTimeout(render, 1400);
}

/* ------------------------------------------------------------------ library */

// The library is this browser's memory of what it rendered: source, options
// and a small thumbnail stored as a data URL, because the server prunes old
// renders and a library of dead links teaches people not to open it.
const LIB_KEY = "t2h.library";
const LIB_MAX = 24;

function lib() {
  try { return JSON.parse(localStorage.getItem(LIB_KEY) || "[]"); }
  catch { return []; }
}

async function remember(done, body) {
  let thumb = "";
  try {
    const blob = await (await fetch(`/thumb/${done.id}/1.jpg`)).blob();
    thumb = await new Promise((res) => {
      const r = new FileReader();
      r.onload = () => res(r.result);
      r.readAsDataURL(blob);
    });
  } catch { /* a library entry without a picture still works */ }

  const entries = lib().filter((e) => e.text !== body.text || JSON.stringify(e.opts) !== JSON.stringify({
    markdown: body.markdown, ruled: body.ruled, texture: body.texture, skew: body.skew,
  }) || e.ink !== body.ink);
  entries.unshift({
    id: done.id, title: title(), pages: done.pages, chars: body.text.length,
    ts: Date.now(), text: body.text, ink: body.ink,
    opts: { markdown: body.markdown, ruled: body.ruled, texture: body.texture, skew: body.skew },
    thumb,
  });
  try { localStorage.setItem(LIB_KEY, JSON.stringify(entries.slice(0, LIB_MAX))); }
  catch { /* quota: drop the oldest and try once more */
    try { localStorage.setItem(LIB_KEY, JSON.stringify(entries.slice(0, 8))); } catch {}
  }
}

function drawLibrary() {
  const entries = lib();
  $("libEmpty").hidden = entries.length > 0;
  const grid = $("grid");
  grid.innerHTML = "";
  const ago = (ts) => {
    const m = Math.round((Date.now() - ts) / 60000);
    if (m < 1) return "now";
    if (m < 60) return m + "m ago";
    const h = Math.round(m / 60);
    if (h < 24) return h + "h ago";
    return Math.round(h / 24) + "d ago";
  };
  entries.forEach((e, i) => {
    const card = document.createElement("button");
    card.className = "cardlet";
    card.style.animationDelay = Math.min(i * 40, 320) + "ms";
    card.innerHTML =
      `<span class="cardlet__thumb">${e.thumb ? `<img src="${e.thumb}" alt="" />` : "no preview"}</span>` +
      `<span class="cardlet__meta">` +
      `<span class="cardlet__title"></span>` +
      `<span class="cardlet__sub"><span>${e.pages}p · ${ago(e.ts)}</span>` +
      `<span class="cardlet__del" title="Remove" role="button" tabindex="0">` +
      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>` +
      `</span></span></span>`;
    card.querySelector(".cardlet__title").textContent = e.title;   // never innerHTML user text
    card.addEventListener("click", (ev) => {
      if (ev.target.closest(".cardlet__del")) {
        const rest = lib().filter((x) => x.ts !== e.ts);
        localStorage.setItem(LIB_KEY, JSON.stringify(rest));
        drawLibrary();
        return;
      }
      openEntry(e);
    });
    grid.appendChild(card);
  });
}

function openEntry(e) {
  $("text").value = e.text;
  setOptions(e.opts, e.ink);
  count();
  showView("write");
  // The server may have pruned this render, so probe before trusting it.
  const img = new Image();
  img.onload = () => {
    state.id = e.id; state.pages = e.pages; state.rendered = JSON.stringify(payload());
    show(1);
    $("exportBtn").disabled = false;
    $("status").textContent = `${e.pages} page${e.pages === 1 ? "" : "s"} · ${e.chars.toLocaleString()} chars`;
  };
  img.onerror = () => {
    state.id = null; state.pages = 0; state.rendered = null;
    $("page").hidden = true; $("pager").hidden = true;
    $("empty").hidden = false;
    $("exportBtn").disabled = true;
    $("status").textContent = "Ready";
    note("This render has been cleaned up on the server. Press Render to make it again.");
  };
  img.src = `/preview/${e.id}/1.jpg`;
  $("text").focus();
}

/* -------------------------------------------------------------------- menus */

$("exportBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const open = $("exportPop").hidden;
  $("exportPop").hidden = !open;
  $("exportBtn").setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".menu")) {
    $("exportPop").hidden = true;
    $("exportBtn").setAttribute("aria-expanded", "false");
  }
});

/* ---------------------------------------------------------------------- PDF */

function takeFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) { note("That is not a PDF.", true); return; }
  state.file = file;
  $("pdfName").textContent = file.name;
  $("pdf").hidden = false;
  showView("write");
  hush();
}

$("pdfBtn").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", () => takeFile($("file").files[0]));
$("pdfClear").addEventListener("click", () => {
  state.file = null;
  $("file").value = "";
  $("pdf").hidden = true;
});

$("read").addEventListener("click", async () => {
  if (!state.file) return;

  const body = new FormData();
  body.append("file", state.file);
  body.append("converter", $("converter").value);
  body.append("pages", $("pageRange").value);

  hush();
  $("read").disabled = true;
  $("read").textContent = "Reading";
  $("status").textContent = "Opening the PDF…";
  $("status").classList.add("is-live");

  try {
    const res = await fetch("/api/convert", { method: "POST", body });
    const started = await res.json();
    if (!res.ok) { note(started.error || "Could not read that PDF.", true); return; }

    const done = await poll(started.job, (m) => { $("status").textContent = m + "…"; });
    if (done.error) { note(done.error, true); return; }

    // Extraction is never perfect, so it lands in the editor for review.
    $("text").value = done.markdown;
    if (!OPTIONS.markdown) $("oMarkdown").click();
    count();
    $("pdf").hidden = true;
    state.file = null;
    $("file").value = "";
    $("text").focus();
    note(`Read ${done.pages_converted} of ${done.total_pages} pages. `
         + "Check it over, then render. " + (done.notes || []).join(" "));
    touched();
  } catch (err) {
    note("Could not reach the server. Is it still running?", true);
  } finally {
    $("read").disabled = false;
    $("read").textContent = "Read it";
    $("status").textContent = "Ready";
    $("status").classList.remove("is-live");
  }
});

/* a PDF can be dropped anywhere in the window */
let depth = 0;
window.addEventListener("dragenter", (e) => {
  if (!e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
  e.preventDefault();
  if (++depth === 1) $("veil").hidden = false;
});
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("dragleave", () => {
  if (--depth <= 0) { depth = 0; $("veil").hidden = true; }
});
window.addEventListener("drop", (e) => {
  e.preventDefault();
  depth = 0;
  $("veil").hidden = true;
  takeFile(e.dataTransfer && e.dataTransfer.files[0]);
});

/* ----------------------------------------------------------------- keyboard */

document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); render(); return; }
  if (/^(TEXTAREA|INPUT|SELECT)$/.test(e.target.tagName)) return;
  if (e.key === "Escape") { $("exportPop").hidden = true; return; }
  if ($("viewWrite").hidden || state.pages < 2) return;
  if (e.key === "ArrowLeft") { e.preventDefault(); show(state.page - 1); }
  if (e.key === "ArrowRight") { e.preventDefault(); show(state.page + 1); }
});

/* ------------------------------------------------------------------ session */

// The editor survives a reload: losing a page of typing to F5 is the kind of
// thing people do not forgive a tool for.
const DRAFT_KEY = "t2h.draft";
window.addEventListener("beforeunload", () => {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({
      text: $("text").value, opts: OPTIONS, ink: INK,
    }));
  } catch {}
});
try {
  const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
  if (draft && draft.text) {
    $("text").value = draft.text;
    setOptions(draft.opts || OPTIONS, draft.ink || "blue-black");
  }
} catch {}

count();
