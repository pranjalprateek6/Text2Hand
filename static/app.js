"use strict";

/* Text2Hand front end.
 *
 * Two jobs run on the server and are polled here: extracting a PDF to Markdown,
 * and rendering text to pages. Everything else is local state: which source tab
 * is showing, which page the viewer is on, and whether the page is fitted or at
 * actual size.
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

// the server derives both of these from how large the handwriting is
const MAX_CHARS = window.MAX_CHARS || 60000;
const PAGE_SIZE = window.PAGE_SIZE || 1200;

const text = $("text");

// current render: which id, how many pages, which one is on screen
const view = { id: null, pages: 0, current: 1, fit: true };

/* ---------------------------------------------------------------- source */

function showTab(which) {
  const onText = which === "text";
  $("tabText").setAttribute("aria-selected", String(onText));
  $("tabPdf").setAttribute("aria-selected", String(!onText));
  $("paneText").hidden = !onText;
  $("panePdf").hidden = onText;
}

$("tabText").addEventListener("click", () => showTab("text"));
$("tabPdf").addEventListener("click", () => showTab("pdf"));

function fileLabel(file) {
  const kb = file.size / 1024;
  $("fileName").textContent = file.name;
  $("fileSize").textContent = kb > 1024
    ? (kb / 1024).toFixed(1) + " MB"
    : Math.max(1, Math.round(kb)) + " KB";
  $("fileCard").hidden = false;
  $("drop").hidden = true;
}

$("pdfFile").addEventListener("change", () => {
  const file = $("pdfFile").files[0];
  if (file) fileLabel(file);
});

$("fileClear").addEventListener("click", (e) => {
  e.preventDefault();
  $("pdfFile").value = "";
  $("fileCard").hidden = true;
  $("drop").hidden = false;
});

// Drag and drop. The label already forwards clicks to the input, so this only
// has to deal with the drop itself and the hover styling.
const drop = $("drop");
["dragenter", "dragover"].forEach((type) => {
  drop.addEventListener(type, (e) => {
    e.preventDefault();
    drop.classList.add("is-over");
  });
});
["dragleave", "drop"].forEach((type) => {
  drop.addEventListener(type, (e) => {
    e.preventDefault();
    drop.classList.remove("is-over");
  });
});
drop.addEventListener("drop", (e) => {
  const file = e.dataTransfer && e.dataTransfer.files[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showAlert("That is not a PDF.");
    return;
  }
  $("pdfFile").files = e.dataTransfer.files;
  fileLabel(file);
});

/* --------------------------------------------------------------- counter */

function updateCounter() {
  const n = text.value.length;
  $("counter").textContent = n.toLocaleString() + (n === 1 ? " character" : " characters");
  $("counter").classList.toggle("meta--over", n > MAX_CHARS);
  // Roughly how much paper this is. Worth knowing before waiting for a render.
  const pages = Math.max(1, Math.ceil(n / PAGE_SIZE));
  $("estimate").textContent = n ? "about " + pages + (pages === 1 ? " page" : " pages") : "";
}

text.addEventListener("input", updateCounter);

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    text.value = SAMPLES[chip.dataset.sample] || "";
    // picking the Markdown sample should switch the mode on, and off again
    // for the plain-text samples
    $("markdown").checked = chip.dataset.sample === "markdown";
    updateCounter();
    text.focus();
  });
});

/* ---------------------------------------------------------------- alerts */

function showAlert(message, kind) {
  $("alert").textContent = message;
  $("alert").className = "alert" + (kind === "note" ? " alert--note" : "");
  $("alertWrap").hidden = false;
}

function clearAlert() {
  $("alertWrap").hidden = true;
}

/* ---------------------------------------------------------------- viewer */

function setBusy(on, title) {
  $("busy").hidden = !on;
  if (title) $("busyTitle").textContent = title;
  if (on) {
    $("empty").hidden = true;
    $("stage").hidden = true;
    $("thumbs").hidden = true;
  }
}

function clearOutput() {
  view.id = null;
  view.pages = 0;
  view.current = 1;
  $("stage").hidden = true;
  $("thumbs").hidden = true;
  $("pager").hidden = true;
  $("outActions").hidden = true;
  $("stepOut").classList.remove("step--on");
  $("outputLabel").textContent = "Pages";
  $("empty").hidden = false;
}

function showPage(n) {
  if (!view.id) return;
  view.current = Math.min(Math.max(n, 1), view.pages);

  $("mainPage").src = `/preview/${view.id}/${view.current}.jpg`;
  $("mainPage").alt = `Page ${view.current}`;
  $("pageCounter").textContent = `${view.current} / ${view.pages}`;
  $("prevPage").disabled = view.current === 1;
  $("nextPage").disabled = view.current === view.pages;

  $("dlPagePng").href = `/download/${view.id}/page_${view.current}.png`;

  document.querySelectorAll(".thumb").forEach((t, i) => {
    t.classList.toggle("thumb--active", i + 1 === view.current);
  });
}

function buildViewer(id, count) {
  view.id = id;
  view.pages = count;

  const strip = $("thumbs");
  strip.innerHTML = "";
  for (let i = 1; i <= count; i++) {
    const btn = document.createElement("button");
    btn.className = "thumb";
    btn.title = `Page ${i}`;
    btn.addEventListener("click", () => showPage(i));
    const img = document.createElement("img");
    img.src = `/thumb/${id}/${i}.jpg`;
    img.alt = `Page ${i} thumbnail`;
    btn.appendChild(img);
    strip.appendChild(btn);
  }

  // A single page needs no navigation, so do not show a carousel for it.
  const multi = count > 1;
  $("pager").hidden = !multi;
  strip.hidden = !multi;

  $("downloadPdf").href = `/download/${id}/handwriting.pdf`;
  $("dlZip").href = `/download/${id}/pages.zip`;
  $("dlZip").hidden = !multi;
  $("outputLabel").textContent = count === 1 ? "1 page" : `${count} pages`;
  $("stepOut").classList.add("step--on");

  $("empty").hidden = true;
  $("busy").hidden = true;
  $("stage").hidden = false;
  $("outActions").hidden = false;
  showPage(1);
}

$("zoom").addEventListener("click", () => {
  view.fit = !view.fit;
  $("mainPage").classList.toggle("page-img--fit", view.fit);
  $("zoom").textContent = view.fit ? "Actual size" : "Fit page";
  $("zoom").setAttribute("aria-pressed", String(!view.fit));
});

$("prevPage").addEventListener("click", () => showPage(view.current - 1));
$("nextPage").addEventListener("click", () => showPage(view.current + 1));

// Arrow keys page through the result, but never while the user is typing.
document.addEventListener("keydown", (e) => {
  if ($("stage").hidden || view.pages < 2) return;
  const tag = e.target && e.target.tagName;
  if (tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT") return;
  if (e.key === "ArrowLeft") {
    e.preventDefault();
    showPage(view.current - 1);
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    showPage(view.current + 1);
  }
});

/* ------------------------------------------------------------------ jobs */

// Both rendering and conversion run as server-side jobs, so both poll here and
// report their stage in the same place.
async function pollJob(jobId, onStage) {
  while (true) {
    await new Promise((r) => setTimeout(r, 400));
    const res = await fetch(`/api/job/${jobId}`);
    if (!res.ok) {
      showAlert("Lost track of that job.");
      return null;
    }
    const job = await res.json();
    if (job.state === "running") {
      if (onStage) onStage(job.message);
      continue;
    }
    if (job.state === "error") {
      showAlert(job.error || "That job failed.");
      return null;
    }
    return job;
  }
}

$("generate").addEventListener("click", async () => {
  const body = {
    text: text.value,
    ruled: $("ruled").checked,
    texture: $("texture").checked,
    skew: $("skew").checked,
    markdown: $("markdown").checked,
  };

  if (!body.text.trim()) {
    showTab("text");
    showAlert("Write some text first.");
    text.focus();
    return;
  }

  // Drop the previous result up front. Leaving it on screen makes a failed
  // render look like it succeeded, and makes a running one show stale pages.
  clearAlert();
  clearOutput();
  setBusy(true, "Laying out the text");
  $("generate").disabled = true;
  $("generateLabel").textContent = "Writing...";

  try {
    const res = await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const started = await res.json();
    if (!res.ok) {
      showAlert(started.error || "Something went wrong.");
      setBusy(false);
      $("empty").hidden = false;
      return;
    }

    const data = await pollJob(started.job, (msg) => { $("busyTitle").textContent = msg; });
    if (!data) {
      setBusy(false);
      $("empty").hidden = false;
      return;
    }

    buildViewer(data.id, data.pages);
    if (data.missing && data.missing.length) {
      showAlert("Skipped characters with no glyph: " + data.missing.join(" "), "note");
    }
  } catch (err) {
    showAlert("Could not reach the server. Is it still running?");
    setBusy(false);
    $("empty").hidden = false;
  } finally {
    $("generate").disabled = false;
    $("generateLabel").textContent = "Write it out";
  }
});

$("clear").addEventListener("click", () => {
  text.value = "";
  updateCounter();
  clearAlert();
  clearOutput();
  setBusy(false);
  text.focus();
});

/* ---------------------------------------------------------- PDF to text */

$("convert").addEventListener("click", async () => {
  const file = $("pdfFile").files[0];
  if (!file) {
    showAlert("Choose a PDF first.");
    return;
  }

  const body = new FormData();
  body.append("file", file);
  body.append("converter", $("converter").value);
  body.append("pages", $("pageRange").value);

  clearAlert();
  const btn = $("convert");
  btn.disabled = true;
  setBusy(true, "Opening the PDF");

  try {
    const res = await fetch("/api/convert", { method: "POST", body });
    const started = await res.json();
    if (!res.ok) {
      showAlert(started.error || "Could not convert that PDF.");
      setBusy(false);
      $("empty").hidden = false;
      return;
    }

    // OCR in particular can run for a minute or more, so it is a job too and
    // reports which page it is reading.
    const data = await pollJob(started.job, (msg) => { $("busyTitle").textContent = msg; });
    setBusy(false);
    $("empty").hidden = false;
    if (!data) return;

    // The editor is the review pane: extraction is never perfect, so the
    // Markdown lands there to be corrected before it is rendered.
    text.value = data.markdown;
    $("markdown").checked = true;
    updateCounter();
    showTab("text");

    const bits = [
      `Read ${data.pages_converted} of ${data.total_pages} pages with ${data.converter}.`,
      "Check it over, then write it out.",
      ...(data.notes || []),
    ];
    showAlert(bits.join(" "), data.notes && data.notes.length ? "" : "note");
    text.focus();
  } catch (err) {
    showAlert("Could not reach the server. Is it still running?");
    setBusy(false);
    $("empty").hidden = false;
  } finally {
    btn.disabled = false;
  }
});

updateCounter();
