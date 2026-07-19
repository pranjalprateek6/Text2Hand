"use strict";

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
const text = $("text");
const counter = $("counter");
const generate = $("generate");
const alertBox = $("alert");
const empty = $("empty");
const output = $("output");

// the server derives this from how large the handwriting is
const MAX_CHARS = window.MAX_CHARS || 60000;

// current render: which id, how many pages, which one is on screen
const view = { id: null, pages: 0, current: 1 };

function showPage(n) {
  if (!view.id) return;
  view.current = Math.min(Math.max(n, 1), view.pages);

  $("mainPage").src = `/preview/${view.id}/${view.current}.jpg`;
  $("mainPage").alt = `Page ${view.current}`;
  $("pageCounter").textContent = `${view.current} / ${view.pages}`;
  $("prevPage").disabled = view.current === 1;
  $("nextPage").disabled = view.current === view.pages;

  $("dlPagePng").href = `/download/${view.id}/page_${view.current}.png`;
  $("dlPagePdf").href = `/download/${view.id}/page_${view.current}.pdf`;

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
  $("viewerNav").hidden = !multi;
  strip.hidden = !multi;

  $("downloadPdf").href = `/download/${id}/handwriting.pdf`;
  $("dlZip").href = `/download/${id}/pages.zip`;
  $("outputLabel").textContent = count === 1 ? "Output, 1 page" : `Output, ${count} pages`;

  showPage(1);
}

function updateCounter() {
  const n = text.value.length;
  counter.textContent = n.toLocaleString() + (n === 1 ? " character" : " characters");
  counter.classList.toggle("counter--over", n > MAX_CHARS);
}

function showAlert(message, kind) {
  alertBox.textContent = message;
  alertBox.className = "alert" + (kind === "note" ? " alert--note" : "");
  alertBox.hidden = false;
}

function clearAlert() {
  alertBox.hidden = true;
}

function clearOutput() {
  output.hidden = true;
  empty.hidden = false;
  view.id = null;
  view.pages = 0;
  view.current = 1;
}

text.addEventListener("input", updateCounter);

$("clear").addEventListener("click", () => {
  text.value = "";
  updateCounter();
  clearAlert();
  clearOutput();
  text.focus();
});

document.querySelectorAll(".examples__chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    text.value = SAMPLES[chip.dataset.sample] || "";
    // picking the Markdown sample should switch the mode on, and off again
    // for the plain-text samples
    $("markdown").checked = chip.dataset.sample === "markdown";
    updateCounter();
    text.focus();
  });
});

generate.addEventListener("click", async () => {
  const body = {
    text: text.value,
    ruled: $("ruled").checked,
    texture: $("texture").checked,
    skew: $("skew").checked,
    markdown: $("markdown").checked,
  };

  if (!body.text.trim()) {
    showAlert("Write some text first.");
    return;
  }

  // Drop the previous result up front. Leaving it on screen makes a failed
  // render look like it succeeded, and makes a running one show stale pages.
  clearAlert();
  clearOutput();
  generate.disabled = true;
  generate.textContent = "Rendering...";

  try {
    const res = await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const started = await res.json();
    if (!res.ok) {
      showAlert(started.error || "Something went wrong.");
      return;
    }

    // Long documents outlive a request, so the render runs on the server and
    // we poll it, showing whatever stage it reports.
    const data = await pollJob(started.job, generate);
    if (!data) return;

    buildViewer(data.id, data.pages);
    empty.hidden = true;
    output.hidden = false;

    if (data.missing && data.missing.length) {
      showAlert(
        "Skipped characters with no glyph: " + data.missing.join(" "),
        "note"
      );
    }
  } catch (err) {
    showAlert("Could not reach the server. Is it still running?");
  } finally {
    generate.disabled = false;
    generate.textContent = "Generate";
  }
});

// Both rendering and conversion run as server-side jobs, so both poll here and
// report their stage on whichever button started them.
async function pollJob(jobId, button) {
  while (true) {
    await new Promise((r) => setTimeout(r, 400));
    const res = await fetch(`/api/job/${jobId}`);
    if (!res.ok) {
      showAlert("Lost track of that job.");
      return null;
    }
    const job = await res.json();
    if (job.state === "running") {
      button.textContent = job.message + "...";
      continue;
    }
    if (job.state === "error") {
      showAlert(job.error || "That job failed.");
      return null;
    }
    return job;
  }
}

// --- PDF to Markdown ------------------------------------------------------ #
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
  btn.textContent = "Converting...";

  try {
    const res = await fetch("/api/convert", { method: "POST", body });
    const started = await res.json();
    if (!res.ok) {
      showAlert(started.error || "Could not convert that PDF.");
      return;
    }

    // OCR in particular can run for a minute or more, so it is a job too and
    // reports which page it is reading.
    const data = await pollJob(started.job, btn);
    if (!data) return;

    // The textarea is the review pane: extraction is never perfect, so the
    // Markdown lands here to be corrected before it is rendered.
    text.value = data.markdown;
    $("markdown").checked = true;
    updateCounter();

    const bits = [
      `Converted ${data.pages_converted} of ${data.total_pages} pages with ${data.converter}.`,
      "Review the Markdown below, then Generate.",
      ...(data.notes || []),
    ];
    showAlert(bits.join(" "), data.notes && data.notes.length ? "" : "note");
    text.focus();
  } catch (err) {
    showAlert("Could not reach the server. Is it still running?");
  } finally {
    btn.disabled = false;
    btn.textContent = "Convert";
  }
});

$("prevPage").addEventListener("click", () => showPage(view.current - 1));
$("nextPage").addEventListener("click", () => showPage(view.current + 1));

// Arrow keys page through the result, but never while the user is typing.
document.addEventListener("keydown", (e) => {
  if (output.hidden || view.pages < 2) return;
  const tag = e.target && e.target.tagName;
  if (tag === "TEXTAREA" || tag === "INPUT") return;
  if (e.key === "ArrowLeft") {
    e.preventDefault();
    showPage(view.current - 1);
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    showPage(view.current + 1);
  }
});

updateCounter();
