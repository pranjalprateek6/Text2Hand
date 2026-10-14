"use strict";

/* Text2Hand studio.
 *
 * Views: Write (editor + preview) and Library (past renders, kept in
 * localStorage so the tool remembers work without needing accounts).
 * The server does the two slow things as polled jobs: reading a PDF into
 * Markdown, and rendering text to pages.
 */

const $ = (id) => document.getElementById(id);

// handed over on the script tag rather than in an inline script, which the
// content security policy does not allow
const CONFIG = document.currentScript ? document.currentScript.dataset : {};
const MAX_CHARS = Number(CONFIG.maxChars) || 60000;
const PAGE_SIZE = Number(CONFIG.pageSize) || 1200;

const state = {
  id: null, pages: 0, page: 1,
  title: null,        // a name typed into the bar; null follows the text
  job: null,          // the render job in flight, so Cancel can name it
  reading: null,      // the conversion job in flight
  file: null,
  statusLine: null,   // what the bar status returns to after a job ends
  rendered: null,     // JSON of what is on screen, to skip pointless re-renders
  timer: null,        // auto-render debounce
  busy: false,
  queued: false,      // a change arrived while a render was running
};

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

const OPTIONS = { markdown: false, texture: true, skew: true };
let INK = "blue-black";
let SIZE = "normal";
let PAPER = "ruled";      // ruled, grid or plain
let SHEET = "a4";         // a4 or letter

$("paper").addEventListener("change", () => { PAPER = $("paper").value; touched(); });
$("sheet").addEventListener("change", () => { SHEET = $("sheet").value; touched(); });

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
      d.tabIndex = d === dot ? 0 : -1;
    });
    $("inkName").textContent = dot.title;
    touched();
  });
});

document.querySelectorAll(".sizebtn").forEach((btn) => {
  btn.addEventListener("click", () => {
    SIZE = btn.dataset.size;
    document.querySelectorAll(".sizebtn").forEach((b) => {
      b.classList.toggle("is-on", b === btn);
      b.setAttribute("aria-checked", String(b === btn));
      b.tabIndex = b === btn ? 0 : -1;
    });
    touched();
  });
});

// A radio group is one stop on the Tab key, and the arrows move the choice
// within it, the way a native radio group behaves.
document.querySelectorAll('[role="radiogroup"]').forEach((group) => {
  const radios = [...group.querySelectorAll('[role="radio"]')];
  radios.forEach((r) => { r.tabIndex = r.getAttribute("aria-checked") === "true" ? 0 : -1; });
  group.addEventListener("keydown", (e) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    e.stopPropagation();                     // not a page turn
    const at = radios.indexOf(document.activeElement);
    const next = radios[(at + step + radios.length) % radios.length];
    next.click();
    next.focus();
  });
});

/* ------------------------------------------------------------------- looks */

// One choice that sets the paper, pen and size together, for the common
// cases. Any change by hand afterwards shows as Custom rather than leaving a
// preset named that no longer describes the page.
const LOOKS = {
  notebook: { paper: "ruled", texture: true, skew: true, ink: "blue-black", size: "normal" },
  plain: { paper: "plain", texture: true, skew: true, ink: "black", size: "normal" },
  letter: { paper: "plain", texture: true, skew: false, ink: "blue", size: "large" },
  clean: { paper: "ruled", texture: false, skew: false, ink: "blue-black", size: "normal" },
  lab: { paper: "grid", texture: true, skew: true, ink: "black", size: "small" },
};

function syncLook() {
  const match = Object.keys(LOOKS).find((k) => {
    const l = LOOKS[k];
    return l.paper === PAPER && l.texture === OPTIONS.texture &&
      l.skew === OPTIONS.skew && l.ink === INK && l.size === SIZE;
  });
  $("look").value = match || "custom";
}

$("look").addEventListener("change", () => {
  const l = LOOKS[$("look").value];
  if (!l) return;
  setOptions({ ...OPTIONS, ...l, sheet: SHEET }, l.ink);   // a look keeps the page size
  touched();
});

function setOptions(opts, ink) {
  // entries saved before paper styles existed carry only "ruled"
  PAPER = opts.paper || (opts.ruled === false ? "plain" : "ruled");
  SHEET = opts.sheet || "a4";
  $("paper").value = PAPER;
  $("sheet").value = SHEET;
  Object.keys(OPTIONS).forEach((key) => {
    OPTIONS[key] = !!opts[key];
    const el = $("o" + key[0].toUpperCase() + key.slice(1));
    el.classList.toggle("is-on", OPTIONS[key]);
    el.setAttribute("aria-pressed", String(OPTIONS[key]));
  });
  const want = document.querySelector(`.ink[data-ink="${ink}"]`);
  if (want) want.click();
  const size = document.querySelector(`.sizebtn[data-size="${opts.size || "normal"}"]`);
  if (size) size.click();
}

/* ---------------------------------------------------------- title and count */

function titleOf(text) {
  // First non-empty line, with Markdown markers stripped for display: the
  // title of "# **Notes**" is "Notes", and a converted PDF's bold header
  // must not show its asterisks in the bar or on a library card.
  const first = (text.split("\n").find((l) => l.trim()) || "").trim()
    .replace(/^[#>\s-]+/, "")
    // strip emphasis markers but keep single underscores: Pranjal_Prateek is
    // a name, not italics
    .replace(/(\*\*|__|[*`])/g, "")
    .trim();
  return first ? (first.length > 40 ? first.slice(0, 40) + "…" : first) : "Untitled";
}

// A name typed into the bar wins; until then the title follows the text.
function title() {
  return state.title || titleOf($("text").value);
}

function fileStem() {
  // The display title, ready to name a downloaded file. The server sanitizes
  // for the filesystem; this only decides whether there is a name worth
  // sending at all.
  const t = title();
  return t === "Untitled" ? "" : t.replace(/…$/, "").trim();
}

function count() {
  const n = $("text").value.length;
  const pages = Math.max(1, Math.ceil(n / PAGE_SIZE));
  $("count").textContent = n ? `${n.toLocaleString()} · ~${pages}p` : "0";
  $("count").classList.toggle("is-over", n > MAX_CHARS);
  $("tries").hidden = n > 0;
  // never rewrite the name mid-edit out from under the person typing it
  if (document.activeElement !== $("docTitle")) $("docTitle").textContent = title();
}

// The name in the bar is editable, and an edited name follows the document
// into its exports and its library card.
$("docTitle").addEventListener("input", () => {
  state.title = $("docTitle").textContent.trim() || null;
  draftSoon();
});
$("docTitle").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("docTitle").blur(); }
});
$("docTitle").addEventListener("blur", () => {
  count();                              // an emptied name falls back to the text's
  if (state.id) show(state.page);       // exports pick the new name up
});

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
  btn.addEventListener("click", async () => {
    const which = btn.dataset.sample;
    // The chips hide once there is text, so this only ever triggers on
    // whitespace; still, a keyboard path or a future layout should not be
    // able to wipe the editor without asking.
    if ($("text").value.trim() &&
        !(await ask("Replace what you've written with the sample?", "Replace"))) return;
    $("text").value = SAMPLES[which] || "";
    if (OPTIONS.markdown !== (which === "markdown")) $("oMarkdown").click();
    count();
    touched();
    $("text").focus();
  });
});

/* ----------------------------------------------------------------- question */

// The app's own confirm. window.confirm is the browser talking; this is the
// studio talking, in its own type and colours. Returns a promise, so every
// caller awaits the answer the way the old call blocked for it.
let asking = null;

function ask(message, okLabel = "Continue") {
  return new Promise((resolve) => {
    asking = { resolve, before: document.activeElement };
    $("askMsg").textContent = message;
    $("askOk").textContent = okLabel;
    $("ask").hidden = false;
    $("askOk").focus();
  });
}

function askDone(answer) {
  if (!asking) return;
  $("ask").hidden = true;
  const { resolve, before } = asking;
  asking = null;
  if (before && before.focus) before.focus();
  resolve(answer);
}

$("askOk").addEventListener("click", () => askDone(true));
$("askCancel").addEventListener("click", () => askDone(false));
$("askVeil").addEventListener("click", () => askDone(false));
// two buttons and a question: Tab just moves between the buttons
$("ask").addEventListener("keydown", (e) => {
  if (e.key === "Tab") {
    e.preventDefault();
    ($("askOk") === document.activeElement ? $("askCancel") : $("askOk")).focus();
  }
});

/* -------------------------------------------------------------------- notes */

function note(message, bad) {
  const el = $("note");
  // an error interrupts a screen reader; anything else waits its turn
  if (bad) el.setAttribute("role", "alert"); else el.removeAttribute("role");
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
    if (job.state === "running") { if (onStage) onStage(job.message, job); continue; }
    if (job.state === "cancelled") return { cancelled: true };
    if (job.state === "error") return { error: job.error || "That did not work." };
    return job;
  }
}

/* ------------------------------------------------------------------- render */

function payload() {
  return {
    text: $("text").value,
    paper: PAPER, sheet: SHEET, ruled: PAPER === "ruled",
    texture: OPTIONS.texture, skew: OPTIONS.skew,
    markdown: OPTIONS.markdown, ink: INK, size: SIZE,
  };
}

// Changing the text or a setting used to leave the old render on screen
// looking current, and Export went on handing out that old file. Now the page
// dims and says so until it is rendered again.
function markStale() {
  const stale = !!state.id && !state.busy && JSON.stringify(payload()) !== state.rendered;
  $("pages").classList.toggle("is-stale", stale);
  $("stale").hidden = !stale;
}
$("stale").addEventListener("click", () => render());

function cancelJob() {
  // a press before the server has named the job is remembered, not lost
  if (!state.job) { state.cancelPending = state.busy; return; }
  fetch(`/api/job/${state.job}/cancel`, { method: "POST" }).catch(() => {});
}

// While a render runs, a page already on screen stays there, dimmed, with
// the progress and a Cancel in a chip above it; replacing it with a skeleton
// threw away the page someone was reading. The skeleton is only for when
// there is nothing to show yet.
function setBusy(on, stage) {
  state.busy = on;
  state.live = null;
  state.cancelPending = false;
  $("render").classList.toggle("is-busy", on);
  $("renderLabel").textContent = on ? (stage || "Rendering") : "Render";
  $("status").textContent = on ? (stage || "Rendering…") : $("status").textContent;
  $("status").classList.toggle("is-live", on);
  const showing = on && !$("page").hidden;
  $("loading").hidden = !on || showing;
  $("live").hidden = !showing;
  $("pages").classList.toggle("is-working", showing);
  if (on) {
    $("empty").hidden = true;
    $("liveText").textContent = stage || "Laying out the text";
    $("loadingText").textContent = stage || "Laying out the text";
  }
  markStale();
}

// Pages of the render in flight, shown as soon as their previews exist. The
// job publishes how many are ready, counted from page 1 with no gaps.
function showLive(job) {
  if (!job || !job.id || !job.ready) return;
  const n = Math.min(Math.max(state.page, 1), job.ready);
  if (state.live === `${job.id}/${n}`) return;
  state.live = `${job.id}/${n}`;
  $("page").src = `/preview/${job.id}/${n}.jpg`;
  $("page").alt = `Page ${n}, still writing`;
  $("page").hidden = false;
  $("loading").hidden = true;
  $("live").hidden = false;
  $("pager").hidden = true;
  $("pages").classList.remove("is-working", "is-stale");
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
    state.job = started.job;
    if (state.cancelPending) cancelJob();

    const done = await poll(started.job, (m, job) => {
      $("loadingText").textContent = m;
      $("liveText").textContent = m;
      $("status").textContent = m + "…";
      showLive(job);
    });
    state.job = null;
    setBusy(false);
    if (done.cancelled) { after(); $("status").textContent = "Cancelled"; return; }
    if (done.error) { after(); note(done.error, true); return; }

    state.id = done.id;
    state.pages = done.pages;
    state.rendered = JSON.stringify(body);
    // Keep the reader's place: proofreading page 6, fixing a word and
    // re-rendering used to bounce back to page 1. show() clamps, so a render
    // that came back shorter lands on its last page instead.
    show(state.page);
    $("exportBtn").disabled = false;
    state.statusLine = `${done.pages} page${done.pages === 1 ? "" : "s"} · ${body.text.length.toLocaleString()} chars`;
    $("status").textContent = state.statusLine;
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
  // the page on screen may be one from the render that just stopped, so the
  // last finished render is put back rather than whatever is showing
  if (state.id) show(state.page);
  else { $("page").hidden = true; $("empty").hidden = false; }
  $("status").textContent = state.statusLine || "Ready";
  $("status").classList.remove("is-live");
  markStale();
}

function show(n) {
  state.page = Math.min(Math.max(n, 1), state.pages);
  $("page").src = `/preview/${state.id}/${state.page}.jpg`;
  $("page").alt = `Page ${state.page}`;
  $("page").hidden = false;
  $("empty").hidden = true;
  $("pageN").value = state.page;
  $("pageN").max = state.pages;
  $("pageTotal").textContent = `/ ${state.pages}`;
  $("prev").disabled = state.page === 1;
  $("next").disabled = state.page === state.pages;
  $("pager").hidden = state.pages < 2;
  // Downloads carry the document's title, so the file that lands in a full
  // Downloads folder says which document it is, not "handwriting (7).pdf".
  const name = fileStem() ? `?name=${encodeURIComponent(fileStem())}` : "";
  $("dlPdf").href = `/download/${state.id}/handwriting.pdf${name}`;
  $("dlZip").href = `/download/${state.id}/pages.zip${name}`;
  $("dlPagePng").href = `/download/${state.id}/page_${state.page}.png${name}`;
  $("dlPagePdf").href = `/download/${state.id}/page_${state.page}.pdf${name}`;

  // On a one-page render the whole document and this page are the same file,
  // and the zip is that one page in a wrapper. Offer the two artefacts that
  // actually differ rather than four entries where two are duplicates.
  const many = state.pages > 1;
  $("dlZip").hidden = !many;
  $("dlPagePdf").hidden = !many;
  document.querySelector("#exportPop .pop__rule").hidden = !many;
  markStale();
}

// While busy, the Render button is the way out: its label is the progress.
$("render").addEventListener("click", () => (state.busy ? cancelJob() : render()));
// stopping is safe at any moment: the server only ever stops between pages
$("cancelRender").addEventListener("click", cancelJob);
$("liveCancel").addEventListener("click", cancelJob);
$("prev").addEventListener("click", () => show(state.page - 1));
$("next").addEventListener("click", () => show(state.page + 1));

// The preview opens at 85%, which fits a whole page in the pane without a
// scrollbar; each press moves 15%. show() never touches this, so the level
// survives paging and re-renders. On a narrow screen 85% is wider than the
// pane, so until someone zooms by hand it fits the page to the width instead.
let zoom = 0.85;
let zoomedByHand = false;
function applyZoom() {
  $("pages").style.setProperty("--zoom", zoom);
  $("zoomOut").disabled = zoom < 0.41;
  $("zoomIn").disabled = zoom > 1.95;
}
function fitZoom() {
  if (zoomedByHand) return;
  const room = $("pages").clientWidth - 52;          // the pane's padding
  zoom = room > 0 ? Math.max(0.4, Math.min(0.85, room / 540)) : 0.85;
  applyZoom();
}
$("zoomIn").addEventListener("click", () => { zoomedByHand = true; zoom = Math.min(2.05, zoom + 0.15); applyZoom(); });
$("zoomOut").addEventListener("click", () => { zoomedByHand = true; zoom = Math.max(0.40, zoom - 0.15); applyZoom(); });
window.addEventListener("resize", fitZoom);
fitZoom();

// Type a page number to jump straight there: prev/next alone made page 40 of
// an 80-page render a 40-click trip. Enter commits via blur, and show()
// clamps whatever was typed.
$("pageN").addEventListener("change", () => {
  const n = parseInt($("pageN").value, 10);
  if (Number.isFinite(n) && state.id) show(n);
  else $("pageN").value = state.page;
});
$("pageN").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("pageN").blur(); }
});

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
  // every path that changes the document runs through here, which makes it
  // the one place the draft needs saving from
  draftSoon();
  syncLook();
  markStale();
  if (!$("auto").checked) return;
  if (!$("text").value.trim()) return;
  if (JSON.stringify(payload()) === state.rendered) return;
  if (state.busy) {
    // The render in flight is for text that has already changed, so finishing
    // it only delays the one that matters. Stop it; the change renders next.
    state.queued = true;
    cancelJob();
    return;
  }
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

  const opts = { markdown: body.markdown, paper: body.paper, sheet: body.sheet,
                 ruled: body.ruled, texture: body.texture,
                 skew: body.skew, size: body.size };
  const entries = lib().filter((e) =>
    e.text !== body.text || JSON.stringify(e.opts) !== JSON.stringify(opts) || e.ink !== body.ink);
  entries.unshift({
    id: done.id, title: title(), pages: done.pages, chars: body.text.length,
    ts: Date.now(), text: body.text, ink: body.ink, opts, thumb,
  });
  try { localStorage.setItem(LIB_KEY, JSON.stringify(entries.slice(0, LIB_MAX))); }
  catch { /* quota: drop the oldest and try once more */
    try { localStorage.setItem(LIB_KEY, JSON.stringify(entries.slice(0, 8))); } catch {}
  }
}

function drawLibrary() {
  const all = lib();
  const query = ($("libSearch").value || "").trim().toLowerCase();
  // searched over the full text, not only the title: the memorable phrase is
  // usually somewhere in the middle of the document
  const entries = query
    ? all.filter((e) => (e.title + "\n" + e.text).toLowerCase().includes(query))
    : all;
  $("libEmpty").hidden = all.length > 0;
  // at capacity the oldest entries roll off silently, so say so
  $("libCap").hidden = all.length < LIB_MAX;
  $("libClear").hidden = all.length === 0;
  $("libSearch").hidden = all.length < 2;
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
    const drop = async () => {
      // asked, because the X is small, next to the open action, and final
      if (!(await ask(`Remove "${e.title}" from the library?`, "Remove"))) return;
      localStorage.setItem(LIB_KEY, JSON.stringify(lib().filter((x) => x.ts !== e.ts)));
      drawLibrary();
    };
    card.addEventListener("click", (ev) => {
      if (ev.target.closest(".cardlet__del")) { drop(); return; }
      openEntry(e);
    });
    // the delete control sits inside the card button, so Enter and Space
    // would activate the card and open the entry instead of deleting it
    card.querySelector(".cardlet__del").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        ev.stopPropagation();
        drop();
      }
    });
    grid.appendChild(card);
  });
}

$("libSearch").addEventListener("input", drawLibrary);

$("libClear").addEventListener("click", async () => {
  const n = lib().length;
  if (!n) return;
  if (!(await ask(`Remove all ${n} entr${n === 1 ? "y" : "ies"} from the library? `
                  + "This cannot be undone.", "Remove all"))) return;
  localStorage.removeItem(LIB_KEY);
  drawLibrary();
});

async function openEntry(e) {
  // Anything rendered is already in the library; only typing that was never
  // rendered is unsaved, and that is the one thing opening an entry can lose.
  const current = $("text").value;
  if (current.trim() && current !== e.text &&
      !lib().some((x) => x.text === current) &&
      !(await ask("Replace the unsaved text in the editor with this entry?", "Replace"))) return;
  $("text").value = e.text;
  // a card's name is kept only if it was really a rename; a title the text
  // would derive anyway stays live, following future edits
  state.title = e.title === titleOf(e.text) ? null : e.title;
  setOptions(e.opts, e.ink);
  count();
  // restoring options fires the same handlers as clicking them, and each of
  // those calls touched(); with Auto on that scheduled a pointless re-render
  // of a document whose render already exists
  clearTimeout(state.timer);
  state.rendered = JSON.stringify(payload());
  markStale();
  showView("write");
  // The server may have pruned this render, so probe before trusting it.
  const img = new Image();
  img.onload = () => {
    state.id = e.id; state.pages = e.pages; state.rendered = JSON.stringify(payload());
    show(1);
    $("exportBtn").disabled = false;
    state.statusLine = `${e.pages} page${e.pages === 1 ? "" : "s"} · ${e.chars.toLocaleString()} chars`;
    $("status").textContent = state.statusLine;
  };
  img.onerror = () => {
    // The server keeps renders for a couple of hours, and the library keeps
    // everything needed to make one again: the text and every setting are
    // already back in place, so the entry is simply rendered again rather
    // than left as a dead end that asks the person to do it.
    state.id = null; state.pages = 0; state.rendered = null;
    $("page").hidden = true; $("pager").hidden = true;
    $("empty").hidden = true;
    $("exportBtn").disabled = true;
    state.statusLine = null;
    note("That render had expired on the server, so it is being written again.");
    render();
  };
  img.src = `/preview/${e.id}/1.jpg`;
  $("text").focus();
}

/* -------------------------------------------------------------------- menus */

// The Export menu behaves like a menu from the keyboard as well: opening it
// puts focus on the first choice, the arrows move between the choices on
// offer, and Escape or Tab closes it with focus back on the button.
function menuItems() {
  return [...$("exportPop").querySelectorAll('[role="menuitem"]')].filter((a) => !a.hidden);
}

function setMenu(open, focusFirst) {
  $("exportPop").hidden = !open;
  $("exportBtn").setAttribute("aria-expanded", String(open));
  if (open && focusFirst) { const first = menuItems()[0]; if (first) first.focus(); }
}

$("exportBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  // a click from the keyboard (Enter or Space) has no pointer position
  setMenu($("exportPop").hidden, e.detail === 0);
});

$("exportBtn").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" && !$("exportBtn").disabled) { e.preventDefault(); setMenu(true, true); }
});

$("exportPop").addEventListener("keydown", (e) => {
  const items = menuItems();
  if (!items.length) return;
  const at = items.indexOf(document.activeElement);
  const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
  if (step) {
    e.preventDefault();
    items[(at + step + items.length) % items.length].focus();
  } else if (e.key === "Home" || e.key === "End") {
    e.preventDefault();
    items[e.key === "Home" ? 0 : items.length - 1].focus();
  } else if (e.key === "Escape" || e.key === "Tab") {
    if (e.key === "Escape") e.preventDefault();
    e.stopPropagation();
    setMenu(false);
    $("exportBtn").focus();
  }
});
$("exportPop").addEventListener("click", () => setMenu(false));
document.addEventListener("click", (e) => {
  if (!e.target.closest(".menu")) {
    $("exportPop").hidden = true;
    $("exportBtn").setAttribute("aria-expanded", "false");
  }
});

/* ---------------------------------------------------------------------- PDF */

function takeFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    // the note lives in the compose view; from the library the error would
    // land in a hidden panel and the drop would appear to do nothing
    showView("write");
    note("That is not a PDF.", true);
    return;
  }
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
  // a second press while reading is the way out
  if (state.reading) {
    fetch(`/api/job/${state.reading}/cancel`, { method: "POST" }).catch(() => {});
    return;
  }
  if (!state.file) return;
  // reading lands its Markdown in the editor, over whatever is there
  if ($("text").value.trim() &&
      !(await ask("Reading the PDF replaces the text in the editor. Continue?", "Read it"))) return;

  const body = new FormData();
  body.append("file", state.file);
  body.append("converter", $("converter").value);
  body.append("pages", $("pageRange").value);

  hush();
  $("read").textContent = "Cancel";
  $("status").textContent = "Opening the PDF…";
  $("status").classList.add("is-live");

  try {
    const res = await fetch("/api/convert", { method: "POST", body });
    const started = await res.json();
    if (!res.ok) { note(started.error || "Could not read that PDF.", true); return; }
    state.reading = started.job;

    const done = await poll(started.job, (m) => { $("status").textContent = m + "…"; });
    if (done.cancelled) { $("status").textContent = "Cancelled"; return; }
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
    state.reading = null;
    $("read").textContent = "Read it";
    if ($("status").textContent !== "Cancelled")
      $("status").textContent = state.statusLine || "Ready";
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
  // while a question is up it owns the keyboard: Escape declines, Enter
  // presses the focused button on its own, and nothing reaches the app behind
  if (!$("ask").hidden) {
    if (e.key === "Escape") { e.preventDefault(); askDone(false); }
    return;
  }
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); render(); return; }
  if (/^(TEXTAREA|INPUT|SELECT)$/.test(e.target.tagName)) return;
  if (e.target.isContentEditable) return;   // typing a name, not paging
  if (e.key === "Escape") { $("exportPop").hidden = true; return; }
  if ($("viewWrite").hidden || state.pages < 2) return;
  if (e.key === "ArrowLeft") { e.preventDefault(); show(state.page - 1); }
  if (e.key === "ArrowRight") { e.preventDefault(); show(state.page + 1); }
});

/* ------------------------------------------------------------------ session */

// The editor survives a reload: losing a page of typing to F5 is the kind of
// thing people do not forgive a tool for. Saved a moment after each change,
// not only on beforeunload: that event never fires for a crashed tab, a
// killed process or most of mobile, which are exactly the exits that lose
// the most typing.
const DRAFT_KEY = "t2h.draft";
function saveDraft() {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({
      text: $("text").value, opts: { ...OPTIONS, size: SIZE, paper: PAPER, sheet: SHEET },
      ink: INK, title: state.title,
      // enough to re-attach to the render after a reload, if it still exists
      id: state.id, pages: state.pages, page: state.page, rendered: state.rendered,
    }));
  } catch {}
}
let draftTimer = null;
function draftSoon() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(saveDraft, 800);
}
window.addEventListener("beforeunload", saveDraft);
try {
  const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
  if (draft && draft.text) {
    $("text").value = draft.text;
    state.title = draft.title || null;
    setOptions(draft.opts || OPTIONS, draft.ink || "blue-black");
    // The render this draft came from may still be on the server; showing it
    // again beats a "Nothing rendered" pane over a full editor. Probed first,
    // because the server prunes and a library of dead links teaches nothing.
    if (draft.id) {
      const img = new Image();
      img.onload = () => {
        state.id = draft.id;
        state.pages = draft.pages || 1;
        state.rendered = draft.rendered || null;
        show(Math.min(draft.page || 1, state.pages));
        $("exportBtn").disabled = false;
        state.statusLine = `${state.pages} page${state.pages === 1 ? "" : "s"} · `
          + `${draft.text.length.toLocaleString()} chars`;
        $("status").textContent = state.statusLine;
      };
      img.src = `/preview/${draft.id}/1.jpg`;
    }
  }
} catch {}

count();
syncLook();
