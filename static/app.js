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

const MAX_CHARS = 20000;

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

text.addEventListener("input", updateCounter);

$("clear").addEventListener("click", () => {
  text.value = "";
  updateCounter();
  clearAlert();
  output.hidden = true;
  empty.hidden = false;
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

  clearAlert();
  generate.disabled = true;
  generate.textContent = "Rendering...";

  try {
    const res = await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();

    if (!res.ok) {
      showAlert(data.error || "Something went wrong.");
      return;
    }

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
