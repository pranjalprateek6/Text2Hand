"use strict";

/* Text2Hand.
 *
 * Two screens: write, and read what came back. The server does the two slow
 * things (reading a PDF into Markdown, writing text out to pages) as jobs that
 * are polled; everything else here is a handful of local state.
 */

const $ = (id) => document.getElementById(id);

const MAX_CHARS = window.MAX_CHARS || 60000;
const PAGE_SIZE = window.PAGE_SIZE || 1200;

const state = { id: null, pages: 0, page: 1, file: null };

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
    // the Markdown sample needs that mode on, the plain ones need it off
    if (OPTIONS.markdown !== (which === "markdown")) $("tMarkdown").click();
    count();
    $("text").focus();
  });
});

/* ------------------------------------------------------------------ toggles */

// Each toggle is a button that remembers its own state, so there is no form to
// read and nothing to keep in sync.
const OPTIONS = { markdown: false, ruled: true, texture: true, skew: true };

Object.keys(OPTIONS).forEach((key) => {
  const el = $("t" + key[0].toUpperCase() + key.slice(1));
  el.addEventListener("click", () => {
    OPTIONS[key] = !OPTIONS[key];
    el.classList.toggle("tog--on", OPTIONS[key]);
    el.setAttribute("aria-pressed", String(OPTIONS[key]));
    // A paper setting only changes how it is drawn, so redraw what is already
    // on screen rather than making someone press the button again. Only while
    // the pages are actually showing, though: doing it from the writing screen
    // threw you into a result you had not asked to see again.
    if (key !== "markdown" && state.id && !$("out").hidden) writeItOut();
  });
});

/* -------------------------------------------------------------------- count */

function count() {
  const n = $("text").value.length;
  const pages = Math.max(1, Math.ceil(n / PAGE_SIZE));
  $("count").textContent = n ? `${n.toLocaleString()} · ~${pages}p` : "0";
  $("count").classList.toggle("is-over", n > MAX_CHARS);
  $("tries").hidden = n > 0;
}
$("text").addEventListener("input", count);

/* -------------------------------------------------------------------- notes */

function note(message, bad) {
  const el = $("note");
  el.textContent = message;
  el.classList.toggle("is-bad", !!bad);
  el.hidden = false;
}
function clearNote() { $("note").hidden = true; }

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

function busy(on, label) {
  $("go").classList.toggle("is-busy", on);
  $("go").disabled = on;
  if (label) $("goText").textContent = label;
  if (!on) $("goText").textContent = "Write it out";
}

/* -------------------------------------------------------------------- write */

async function writeItOut() {
  const text = $("text").value;
  if (!text.trim()) { note("Type something first."); $("text").focus(); return; }

  clearNote();
  busy(true, "Laying out");

  try {
    const res = await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        ruled: OPTIONS.ruled,
        texture: OPTIONS.texture,
        skew: OPTIONS.skew,
        markdown: OPTIONS.markdown,
      }),
    });
    const started = await res.json();
    if (!res.ok) { busy(false); note(started.error || "That did not work.", true); return; }

    const done = await poll(started.job, (m) => { $("goText").textContent = m; });
    busy(false);
    if (done.error) { note(done.error, true); return; }

    state.id = done.id;
    state.pages = done.pages;
    show(1, 0);
    reveal();

    if (done.missing && done.missing.length) {
      note("No glyph for: " + done.missing.join(" "));
    }
  } catch (err) {
    busy(false);
    note("Could not reach the server. Is it still running?", true);
  }
}

$("go").addEventListener("click", writeItOut);

/* ------------------------------------------------------------------- result */

function reveal() {
  $("write").hidden = true;
  $("out").hidden = false;
  // replay the landing animation each time a fresh set of pages arrives
  const paper = $("paper");
  paper.style.animation = "none";
  void paper.offsetWidth;
  paper.style.animation = "";
}

function show(n, dir) {
  state.page = Math.min(Math.max(n, 1), state.pages);
  const paper = $("paper");

  if (dir) {
    paper.classList.remove("turn-next", "turn-prev");
    void paper.offsetWidth;                       // restart the animation
    paper.classList.add(dir > 0 ? "turn-next" : "turn-prev");
    // swap the image at the halfway point, where the page is edge on
    setTimeout(() => { $("page").src = `/preview/${state.id}/${state.page}.jpg`; }, 210);
  } else {
    $("page").src = `/preview/${state.id}/${state.page}.jpg`;
  }

  $("page").alt = `Page ${state.page}`;
  $("n").textContent = `${state.page} / ${state.pages}`;
  $("prev").disabled = state.page === 1;
  $("next").disabled = state.page === state.pages;
  $("dlPdf").href = `/download/${state.id}/handwriting.pdf`;
  $("dlPng").href = `/download/${state.id}/page_${state.page}.png`;
}

$("prev").addEventListener("click", () => show(state.page - 1, -1));
$("next").addEventListener("click", () => show(state.page + 1, 1));

$("again").addEventListener("click", () => {
  $("out").hidden = true;
  $("write").hidden = false;
  $("text").focus();
});

// the little arrow drops out of the button on the way to the file
[$("dlPdf"), $("dlPng")].forEach((el) => {
  el.addEventListener("click", () => {
    el.classList.remove("is-off");
    void el.offsetWidth;
    el.classList.add("is-off");
  });
});

document.addEventListener("keydown", (e) => {
  if (/^(TEXTAREA|INPUT|SELECT)$/.test(e.target.tagName)) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); writeItOut(); }
    return;
  }
  if ($("out").hidden || state.pages < 2) return;
  if (e.key === "ArrowLeft") { e.preventDefault(); show(state.page - 1, -1); }
  if (e.key === "ArrowRight") { e.preventDefault(); show(state.page + 1, 1); }
});

/* ---------------------------------------------------------------------- PDF */

function takeFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) { note("That is not a PDF.", true); return; }
  state.file = file;
  $("pdfName").textContent = file.name;
  $("pdf").hidden = false;
  clearNote();
}

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
  body.append("pages", $("pages").value);

  clearNote();
  $("read").disabled = true;
  $("read").textContent = "Reading";
  const ring = $("pdfRing");
  ring.classList.add("is-busy");
  ring.style.setProperty("--p", "0.28turn");

  try {
    const res = await fetch("/api/convert", { method: "POST", body });
    const started = await res.json();
    if (!res.ok) { note(started.error || "Could not read that PDF.", true); return; }

    // OCR can run for a minute, and it reports which page it is on, so the ring
    // creeps forward instead of spinning at nothing.
    let seen = 0;
    const done = await poll(started.job, (m) => {
      const at = /page (\d+) of (\d+)/i.exec(m || "");
      if (at) {
        seen = Math.min(0.95, Number(at[1]) / Number(at[2]));
        ring.classList.remove("is-busy");
        ring.style.setProperty("--p", seen.toFixed(3) + "turn");
      }
    });
    if (done.error) { note(done.error, true); return; }

    ring.classList.remove("is-busy");
    ring.style.setProperty("--p", "1turn");

    // Extraction is never perfect, so the Markdown lands in the box to be read
    // over before it is written out.
    $("text").value = done.markdown;
    if (!OPTIONS.markdown) $("tMarkdown").click();
    count();
    $("pdf").hidden = true;
    state.file = null;
    $("file").value = "";
    $("text").focus();
    note(`Read ${done.pages_converted} of ${done.total_pages} pages. `
         + "Check it, then write it out. " + (done.notes || []).join(" "));
  } catch (err) {
    note("Could not reach the server. Is it still running?", true);
  } finally {
    $("read").disabled = false;
    $("read").textContent = "Read it";
    ring.classList.remove("is-busy");
  }
});

/* a PDF can be dropped anywhere, or onto the box itself */
let depth = 0;
window.addEventListener("dragenter", (e) => {
  if (!e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
  e.preventDefault();
  if (++depth === 1) { $("veil").hidden = false; $("box").classList.add("is-drop"); }
});
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("dragleave", () => {
  if (--depth <= 0) { depth = 0; $("veil").hidden = true; $("box").classList.remove("is-drop"); }
});
window.addEventListener("drop", (e) => {
  e.preventDefault();
  depth = 0;
  $("veil").hidden = true;
  $("box").classList.remove("is-drop");
  if ($("out").hidden === false) { $("out").hidden = true; $("write").hidden = false; }
  takeFile(e.dataTransfer && e.dataTransfer.files[0]);
});

count();
