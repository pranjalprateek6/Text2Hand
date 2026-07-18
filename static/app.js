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
};

const $ = (id) => document.getElementById(id);
const text = $("text");
const counter = $("counter");
const generate = $("generate");
const alertBox = $("alert");
const empty = $("empty");
const output = $("output");
const pages = $("pages");

const MAX_CHARS = 20000;

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

    // Loaded eagerly on purpose: these previews are the whole point of the
    // panel and the user just asked for them, so deferring them is wrong.
    pages.innerHTML = "";
    for (let i = 1; i <= data.pages; i++) {
      const img = document.createElement("img");
      img.className = "page-img";
      img.alt = "Page " + i;
      img.src = `/preview/${data.id}/${i}.jpg`;
      pages.appendChild(img);
    }

    $("outputLabel").textContent =
      data.pages === 1 ? "Output, 1 page" : `Output, ${data.pages} pages`;
    $("downloadPdf").href = `/download/${data.id}/handwriting.pdf`;

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

updateCounter();
