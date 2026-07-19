"use strict";

/* Text2Hand front end.
 *
 * The canvas holds one sheet of paper and everything else is summoned. The
 * composer slides up when there is text to write or edit and closes as soon as
 * it is sent, so nothing stays on screen that is not the page itself.
 *
 * Two things run on the server and are polled: reading a PDF into Markdown, and
 * writing text out to pages. Everything else here is local state.
 */

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

const $ = (id) => document.getElementById(id);

// both derived on the server from how large the handwriting is
const MAX_CHARS = window.MAX_CHARS || 60000;
const PAGE_SIZE = window.PAGE_SIZE || 1200;

const ZOOMS = [0.5, 0.75, 1, 1.5, 2, 3];

const state = {
  id: null,          // current render
  pages: 0,
  page: 1,
  zoom: 1,           // 1 means fit the canvas
  busy: false,
  written: "",       // the text the pages on screen were made from
};

/* ----------------------------------------------------------------- paper */

// Mirrors the width the stylesheet gives .sheet at rest, so a zoom level is a
// multiple of the same fit rather than of a separately measured one.
const BAR_H = 54, CHROME_V = 130;
function fitWidth() {
  return Math.min(640, window.innerWidth - 68,
                  (window.innerHeight - BAR_H - CHROME_V) * 0.7072);
}

function applyZoom() {
  const sheet = $("sheet");
  if (state.zoom === 1) {
    sheet.classList.remove("sheet--sized");
    sheet.style.width = "";
  } else {
    sheet.classList.add("sheet--sized");
    sheet.style.width = Math.round(fitWidth() * state.zoom) + "px";
  }
  $("zoomLevel").textContent = state.zoom === 1 ? "Fit" : Math.round(state.zoom * 100) + "%";
}

function stepZoom(dir) {
  const i = ZOOMS.indexOf(state.zoom);
  const next = ZOOMS[Math.min(Math.max((i < 0 ? 2 : i) + dir, 0), ZOOMS.length - 1)];
  state.zoom = next;
  applyZoom();
}

$("zoomIn").addEventListener("click", () => stepZoom(1));
$("zoomOut").addEventListener("click", () => stepZoom(-1));
$("zoomLevel").addEventListener("click", () => { state.zoom = 1; applyZoom(); });
window.addEventListener("resize", () => { if (state.zoom !== 1) applyZoom(); });

/* ------------------------------------------------------------------ dock */

function showPage(n) {
  if (!state.id) return;
  state.page = Math.min(Math.max(n, 1), state.pages);

  $("pageImg").src = `/preview/${state.id}/${state.page}.jpg`;
  $("pageImg").alt = `Page ${state.page}`;
  $("prev").disabled = state.page === 1;
  $("next").disabled = state.page === state.pages;
  $("count").textContent = `${state.page} / ${state.pages}`;

  document.querySelectorAll(".dot").forEach((d, i) => {
    d.classList.toggle("dot--on", i + 1 === state.page);
    d.setAttribute("aria-selected", String(i + 1 === state.page));
  });

  $("dlPagePng").href = `/download/${state.id}/page_${state.page}.png`;
  $("dlPagePdf").href = `/download/${state.id}/page_${state.page}.pdf`;
}

function buildDock(count) {
  const dots = $("dots");
  dots.innerHTML = "";
  // Dots read at a glance but stop being useful once there are a lot of them,
  // so past eight pages this falls back to a plain counter.
  const useDots = count > 1 && count <= 8;
  if (useDots) {
    for (let i = 1; i <= count; i++) {
      const b = document.createElement("button");
      b.className = "dot";
      b.type = "button";
      b.setAttribute("role", "tab");
      b.title = `Page ${i}`;
      b.addEventListener("click", () => showPage(i));
      dots.appendChild(b);
    }
  }
  dots.hidden = !useDots;
  $("count").hidden = useDots || count < 2;
  $("prev").hidden = count < 2;
  $("next").hidden = count < 2;
}

$("prev").addEventListener("click", () => showPage(state.page - 1));
$("next").addEventListener("click", () => showPage(state.page + 1));

/* -------------------------------------------------------------- composer */

function openComposer(tab) {
  $("scrim").hidden = false;
  $("composer").hidden = false;
  $("dock").classList.add("dock--away");
  if (tab) showTab(tab);
  setTimeout(() => $("text").focus(), 60);
}

function closeComposer() {
  $("scrim").hidden = true;
  $("composer").hidden = true;
  $("dock").classList.remove("dock--away");
}

function showTab(which) {
  const onType = which === "type";
  $("tabType").setAttribute("aria-selected", String(onType));
  $("tabPdf").setAttribute("aria-selected", String(!onType));
  $("paneType").hidden = !onType;
  $("panePdf").hidden = onType;
}

$("edit").addEventListener("click", () => openComposer());
$("startBtn").addEventListener("click", () => openComposer("type"));
$("closeComposer").addEventListener("click", closeComposer);
$("scrim").addEventListener("click", closeComposer);
$("tabType").addEventListener("click", () => showTab("type"));
$("tabPdf").addEventListener("click", () => showTab("pdf"));

document.querySelectorAll(".pill").forEach((pill) => {
  pill.addEventListener("click", () => {
    $("text").value = SAMPLES[pill.dataset.sample] || "";
    // the Markdown sample needs the mode on, the plain ones need it off
    $("markdown").checked = pill.dataset.sample === "markdown";
    tally();
    $("text").focus();
  });
});

function tally() {
  const n = $("text").value.length;
  const pages = Math.max(1, Math.ceil(n / PAGE_SIZE));
  $("tally").textContent = n
    ? `${n.toLocaleString()} characters, about ${pages} page${pages === 1 ? "" : "s"}`
    : "0 characters";
  $("tally").classList.toggle("tally--over", n > MAX_CHARS);
}
$("text").addEventListener("input", tally);

/* ---------------------------------------------------------------- alerts */

function say(message, kind) {
  $("alert").textContent = message;
  $("alert").className = "alert" + (kind === "note" ? " alert--note" : "");
  $("alert").hidden = false;
}
function hush() { $("alert").hidden = true; }

/* ----------------------------------------------------------------- menus */

function closeMenus() {
  ["paperPop", "exportPop"].forEach((id) => { $(id).hidden = true; });
  ["paperBtn", "exportBtn"].forEach((id) => $(id).setAttribute("aria-expanded", "false"));
}

function toggleMenu(btnId, popId) {
  const open = $(popId).hidden;
  closeMenus();
  $(popId).hidden = !open;
  $(btnId).setAttribute("aria-expanded", String(open));
}

$("paperBtn").addEventListener("click", (e) => { e.stopPropagation(); toggleMenu("paperBtn", "paperPop"); });
$("exportBtn").addEventListener("click", (e) => { e.stopPropagation(); toggleMenu("exportBtn", "exportPop"); });
document.addEventListener("click", (e) => {
  if (!e.target.closest(".menu")) closeMenus();
});

// Paper settings only affect drawing, so changing one redraws what is already
// on screen rather than waiting to be asked.
["ruled", "texture", "skew"].forEach((id) => {
  $(id).addEventListener("change", () => {
    if (state.written.trim()) render(state.written);
  });
});

/* ------------------------------------------------------------------ jobs */

async function pollJob(jobId, onStage) {
  while (true) {
    await new Promise((r) => setTimeout(r, 400));
    const res = await fetch(`/api/job/${jobId}`);
    if (!res.ok) return { error: "Lost track of that job." };
    const job = await res.json();
    if (job.state === "running") { if (onStage) onStage(job.message); continue; }
    if (job.state === "error") return { error: job.error || "That job failed." };
    return job;
  }
}

function setBusy(on, what) {
  state.busy = on;
  $("busy").hidden = !on;
  $("busyText").textContent = what || "Writing";
  if (on) $("blank").hidden = true;
}

/* ---------------------------------------------------------------- render */

async function render(source) {
  const body = {
    text: source,
    ruled: $("ruled").checked,
    texture: $("texture").checked,
    skew: $("skew").checked,
    markdown: $("markdown").checked,
  };

  closeComposer();
  // The old pages have to go: leaving them up makes a failed run look like it
  // worked, and a running one show a page from the last attempt.
  $("pageImg").hidden = true;
  $("exportBtn").disabled = true;
  setBusy(true, "Laying out the text");

  try {
    const res = await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const started = await res.json();
    if (!res.ok) return failed(started.error || "Something went wrong.");

    const data = await pollJob(started.job, (m) => { $("busyText").textContent = m; });
    if (data.error) return failed(data.error);

    state.id = data.id;
    state.pages = data.pages;
    state.written = source;

    buildDock(data.pages);
    setBusy(false);
    $("pageImg").hidden = false;
    $("blank").hidden = true;
    $("exportBtn").disabled = false;
    $("zoomer").hidden = false;
    $("dlPdf").href = `/download/${data.id}/handwriting.pdf`;
    $("dlZip").href = `/download/${data.id}/pages.zip`;
    $("dlZip").hidden = data.pages < 2;       // a single page has nothing to zip
    showPage(1);

    if (data.missing && data.missing.length) {
      openComposer();
      say("Skipped characters with no glyph: " + data.missing.join(" "), "note");
    }
  } catch (err) {
    failed("Could not reach the server. Is it still running?");
  }
}

function failed(message) {
  setBusy(false);
  if (!state.id) $("blank").hidden = false;
  else $("pageImg").hidden = false;
  openComposer();
  say(message);
}

$("write").addEventListener("click", () => {
  const source = $("text").value;
  if (!source.trim()) {
    showTab("type");
    say("Write something first.");
    $("text").focus();
    return;
  }
  hush();
  render(source);
});

/* ------------------------------------------------------------ PDF to text */

function chooseFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) { say("That is not a PDF."); return; }
  const kb = file.size / 1024;
  $("fileName").textContent = file.name;
  $("fileSize").textContent = kb > 1024 ? (kb / 1024).toFixed(1) + " MB" : Math.max(1, Math.round(kb)) + " KB";
  $("chosen").hidden = false;
  $("drop").hidden = true;
}

$("pdfFile").addEventListener("change", () => chooseFile($("pdfFile").files[0]));
$("fileClear").addEventListener("click", (e) => {
  e.preventDefault();
  $("pdfFile").value = "";
  $("chosen").hidden = true;
  $("drop").hidden = false;
});

// A PDF can be dropped anywhere in the window, not only on the small target.
let dragDepth = 0;
window.addEventListener("dragenter", (e) => {
  if (!e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
  e.preventDefault();
  if (++dragDepth === 1) $("dropveil").hidden = false;
});
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("dragleave", () => {
  if (--dragDepth <= 0) { dragDepth = 0; $("dropveil").hidden = true; }
});
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("dropveil").hidden = true;
  const file = e.dataTransfer && e.dataTransfer.files[0];
  if (!file) return;
  openComposer("pdf");
  const box = new DataTransfer();
  box.items.add(file);
  $("pdfFile").files = box.files;
  chooseFile(file);
});

$("convert").addEventListener("click", async () => {
  const file = $("pdfFile").files[0];
  if (!file) { say("Choose a PDF first."); return; }

  const body = new FormData();
  body.append("file", file);
  body.append("converter", $("converter").value);
  body.append("pages", $("pageRange").value);

  hush();
  $("convert").disabled = true;
  closeComposer();
  setBusy(true, "Opening the PDF");

  try {
    const res = await fetch("/api/convert", { method: "POST", body });
    const started = await res.json();
    if (!res.ok) { setBusy(false); failed(started.error || "Could not read that PDF."); return; }

    // OCR can run for a minute or more, so it reports which page it is on.
    const data = await pollJob(started.job, (m) => { $("busyText").textContent = m; });
    setBusy(false);
    if (!state.id) $("blank").hidden = false; else $("pageImg").hidden = false;
    if (data.error) { openComposer("pdf"); say(data.error); return; }

    // Extraction is never perfect, so the Markdown lands back in the composer
    // to be read over before anything is written out.
    $("text").value = data.markdown;
    $("markdown").checked = true;
    tally();
    openComposer("type");
    say(`Read ${data.pages_converted} of ${data.total_pages} pages with ${data.converter}. `
        + "Check it over, then write it out. "
        + (data.notes || []).join(" "),
        data.notes && data.notes.length ? "" : "note");
  } catch (err) {
    setBusy(false);
    failed("Could not reach the server. Is it still running?");
  } finally {
    $("convert").disabled = false;
  }
});

/* -------------------------------------------------------------- keyboard */

document.addEventListener("keydown", (e) => {
  const typing = /^(TEXTAREA|INPUT|SELECT)$/.test(e.target.tagName);
  const composerOpen = !$("composer").hidden;

  if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && composerOpen) {
    e.preventDefault();
    $("write").click();
    return;
  }
  if (e.key === "Escape") {
    if (!$("paperPop").hidden || !$("exportPop").hidden) { closeMenus(); return; }
    if (composerOpen) closeComposer();
    return;
  }
  if (typing || composerOpen) return;

  if (e.key === "e" || e.key === "E") { e.preventDefault(); openComposer(); }
  else if (e.key === "ArrowLeft" && state.pages > 1) { e.preventDefault(); showPage(state.page - 1); }
  else if (e.key === "ArrowRight" && state.pages > 1) { e.preventDefault(); showPage(state.page + 1); }
  else if (e.key === "+" || e.key === "=") { e.preventDefault(); stepZoom(1); }
  else if (e.key === "-") { e.preventDefault(); stepZoom(-1); }
});

applyZoom();
tally();
buildDock(0);
