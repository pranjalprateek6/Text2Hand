"use strict";

/* Text2Hand — the Optimus template's behaviours, without React.
 *
 * Each effect is a copy of what the original's hooks do: the nav condenses
 * into a floating pill after 20px of scroll, the hero's last word swaps every
 * 2.5s with a staggered per-character blur entrance, the how-it-works step
 * advances on a timer (a click pins it), the ASCII sphere is a canvas of
 * block characters projected from a rotating sphere, and reveals ride an
 * IntersectionObserver. Everything settles to its finished state under
 * reduced motion or when frames never run.
 */

const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ----------------------------------------------------------------- navbar */

function shrinkNav() {
  const nav = document.querySelector("[data-nav]");
  if (!nav) return;
  const apply = () => nav.classList.toggle("is-scrolled", scrollY > 20);
  addEventListener("scroll", apply, { passive: true });
  apply();
}

/* ------------------------------------------------------------ word rotate */

function wordRotator(host) {
  const words = (host.dataset.words || "").split(",").map((w) => w.trim()).filter(Boolean);
  if (!words.length) return;

  const swap = (word) => {
    host.textContent = "";
    [...word].forEach((ch, i) => {
      const s = document.createElement("span");
      s.className = "char-in";
      s.style.animationDelay = `${i * 50}ms`;
      s.textContent = ch;
      host.appendChild(s);
    });
  };

  swap(words[0]);
  if (REDUCED || words.length < 2) return;
  let at = 0;
  setInterval(() => { at = (at + 1) % words.length; swap(words[at]); }, 2500);
}

/* -------------------------------------------------------------- ascii page */

/* The template shipped an ASCII sphere here. Ours is an ASCII page: a tilted
   A4 sheet sketched in characters, faint blue rules and a red margin, with
   letter-like marks arriving line by line behind a pen cursor. When the page
   fills, it clears and starts over: the product, drawn in the template's own
   medium. */
function asciiPage(canvas) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const INK = "aeimnorsuvwx";
  const CELL = 13;
  let t = 0, written = 0, hold = 0, seed = 7, raf = null, running = !REDUCED;

  const resize = () => {
    const dpr = devicePixelRatio || 1;
    const r = canvas.getBoundingClientRect();
    canvas.width = r.width * dpr;
    canvas.height = r.height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };

  // one deterministic mark per cell, so written text does not flicker
  const mark = (row, col) => INK[(row * 31 + col * 7 + seed * 13) % INK.length];

  const paint = () => {
    const r = canvas.getBoundingClientRect();
    if (!r.width) return;
    ctx.clearRect(0, 0, r.width, r.height);

    const ph = r.height * 0.84;               // A4, portrait
    const pw = ph / 1.414;
    const x0 = (r.width - pw) / 2, y0 = (r.height - ph) / 2;
    const cols = Math.floor(pw / CELL), rows = Math.floor(ph / CELL);
    const cx = r.width / 2, cy = r.height / 2;

    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-0.055 + Math.sin(t * 0.5) * 0.02);   // resting tilt plus a slow sway
    ctx.translate(-cx, -cy);
    ctx.font = "12px monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    const at = (c, row, col, alpha, colour) => {
      ctx.fillStyle = colour || `rgba(0, 0, 0, ${alpha})`;
      ctx.fillText(c, x0 + col * CELL + CELL / 2, y0 + row * CELL + CELL / 2);
    };

    // the sheet's edge
    for (let c = 0; c < cols; c++) { at("─", 0, c, 0.3); at("─", rows - 1, c, 0.3); }
    for (let rw = 0; rw < rows; rw++) { at("│", rw, 0, 0.3); at("│", rw, cols - 1, 0.3); }
    at("╭", 0, 0, 0.35); at("╮", 0, cols - 1, 0.35);
    at("╰", rows - 1, 0, 0.35); at("╯", rows - 1, cols - 1, 0.35);

    // ruling every other row, and the red margin two cells in
    for (let rw = 2; rw < rows - 1; rw += 2) {
      for (let c = 1; c < cols - 1; c++) at("─", rw, c, 0, "rgba(70, 110, 180, 0.13)");
    }
    for (let rw = 1; rw < rows - 1; rw++) at("│", rw, 2, 0, "rgba(180, 60, 50, 0.18)");

    // the writing: lines sit on the rules, indented past the margin
    const lineRows = [];
    for (let rw = 2; rw < rows - 2; rw += 2) lineRows.push(rw - 1);
    const perLine = cols - 5;
    const total = lineRows.length * perLine;
    const done = Math.min(written, total);
    for (let i = 0; i < done; i++) {
      const row = lineRows[Math.floor(i / perLine)];
      const col = 4 + (i % perLine);
      at(mark(row, col), row, col, 0.55);
    }
    // the pen, blinking at the write head
    if (done < total && Math.floor(t * 2.4) % 2 === 0) {
      at("▌", lineRows[Math.floor(done / perLine)], 4 + (done % perLine), 0.8);
    }
    ctx.restore();
    return total;
  };

  const loop = () => {
    if (!running) return;
    const total = paint() || 0;
    t += 0.016;
    if (written >= total && total > 0) {
      if (++hold > 110) { written = 0; hold = 0; seed = (seed * 17 + 3) % 97; }
    } else {
      written += 1.6;                        // a hurried but human pace
    }
    raf = requestAnimationFrame(loop);
  };

  resize();
  addEventListener("resize", resize);
  // The canvas can measure 0x0 at boot if layout has not settled when the
  // script runs, and a window resize never comes to correct it. Re-measure
  // and repaint whenever the box itself changes size.
  new ResizeObserver(() => { resize(); paint(); }).observe(canvas);
  // A tab restored from prerender ran no frames and may hold a blank bitmap;
  // becoming visible is the moment to make it true.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { resize(); paint(); }
  });
  if (REDUCED) written = 1e9;                // a finished page, held still
  paint();                                   // painted once even if frames never run
  if (!REDUCED) {
    new IntersectionObserver((es) => {
      es.forEach((e) => {
        running = e.isIntersecting;
        if (running) { if (raf) cancelAnimationFrame(raf); loop(); }
        else if (raf) cancelAnimationFrame(raf);
      });
    }).observe(canvas);
  }
}

/* -------------------------------------------------------------- keys to ink */

/* The left-hand companion to the ASCII page: typed characters drift across
   the canvas and, midway, stop being type. Each one dissolves into a short
   wobbly pen stroke that fades as it goes: the product's whole transformation
   in one loop. Same manners as the page: still under reduced motion, paused
   offscreen, repainted on resize and on becoming visible. */
function keysToInk(canvas) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const SRC = "typed text becomes ink";
  const N = 13;
  let t = 0, raf = null, running = !REDUCED;

  // each particle's shape is fixed at boot, so strokes wobble like a pen
  // rather than boiling like static
  const P = Array.from({ length: N }, (_, i) => ({
    ch: SRC[i % SRC.length] === " " ? "·" : SRC[i % SRC.length],
    lane: (i + 0.6) / (N + 0.6),
    speed: 0.045 + (i % 5) * 0.011,
    phase: (i * 0.31) % 1,
    seg: Array.from({ length: 7 }, () => Math.random() - 0.5),
    amp: 3 + Math.random() * 4,
  }));

  const resize = () => {
    const dpr = devicePixelRatio || 1;
    const r = canvas.getBoundingClientRect();
    canvas.width = r.width * dpr;
    canvas.height = r.height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };

  const paint = () => {
    const r = canvas.getBoundingClientRect();
    if (!r.width) return;
    ctx.clearRect(0, 0, r.width, r.height);
    for (const p of P) {
      const prog = (p.phase + t * p.speed) % 1;
      const x = r.width * (0.06 + prog * 0.88);
      const y = r.height * p.lane + Math.sin(t * 0.8 + p.lane * 9) * 4;
      if (prog < 0.48) {
        // still type: crisp, monospaced, upright
        const alpha = Math.min(1, prog / 0.08) * 0.55;
        ctx.font = "15px monospace";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = `rgba(40, 36, 26, ${alpha})`;
        ctx.fillText(p.ch, x, y);
      } else {
        // ink now: a short wobbling stroke, fading as it travels
        const fade = 1 - (prog - 0.48) / 0.52;
        ctx.strokeStyle = `rgba(28, 36, 82, ${0.5 * fade})`;
        ctx.lineWidth = 2;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.beginPath();
        for (let k = 0; k < 7; k++) {
          const px = x - 13 + k * 4.4;
          const py = y + p.seg[k] * p.amp + Math.sin(t * 1.6 + k * 1.3) * 1.1;
          k ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
        }
        ctx.stroke();
      }
    }
  };

  const loop = () => {
    if (!running) return;
    paint();
    t += 0.016;
    raf = requestAnimationFrame(loop);
  };

  resize();
  addEventListener("resize", resize);
  new ResizeObserver(() => { resize(); paint(); }).observe(canvas);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { resize(); paint(); }
  });
  if (REDUCED) t = 5;                        // a settled mid-flight frame
  paint();                                   // painted once even if frames never run
  if (!REDUCED) {
    new IntersectionObserver((es) => {
      es.forEach((e) => {
        running = e.isIntersecting;
        if (running) { if (raf) cancelAnimationFrame(raf); loop(); }
        else if (raf) cancelAnimationFrame(raf);
      });
    }).observe(canvas);
  }
}

/* -------------------------------------------------------------- how it works */

function steps() {
  const items = [...document.querySelectorAll("[data-step]")];
  const codes = [...document.querySelectorAll("[data-step-code]")];
  if (!items.length) return;

  let at = 0, pinned = false, timer = null;
  const show = (n) => {
    at = n;
    items.forEach((el, i) => el.classList.toggle("is-active", i === n));
    codes.forEach((el, i) => { el.hidden = i !== n; });
  };
  show(0);

  items.forEach((el, i) => el.addEventListener("click", () => {
    pinned = true;
    if (timer) clearInterval(timer);
    show(i);
  }));

  if (REDUCED) return;
  timer = setInterval(() => { if (!pinned) show((at + 1) % items.length); }, 3200);
}

/* ---------------------------------------------------------------- spotlight */

function spotlight(box) {
  if (REDUCED) return;
  box.addEventListener("mousemove", (e) => {
    const r = box.getBoundingClientRect();
    box.style.setProperty("--mx", `${((e.clientX - r.left) / r.width) * 100}%`);
    box.style.setProperty("--my", `${((e.clientY - r.top) / r.height) * 100}%`);
  });
}

/* ------------------------------------------------------------------ reveal */

function reveals() {
  const els = [...document.querySelectorAll(".rv")];
  const all = () => els.forEach((el) => el.classList.add("on"));
  if (REDUCED || !("IntersectionObserver" in window)) return all();

  // a hidden or prerendered tab runs no frames; never leave the page invisible
  setTimeout(() => { if (!document.querySelector(".rv.on")) all(); }, 2200);

  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      const wait = Number(e.target.dataset.delay || 0);
      setTimeout(() => e.target.classList.add("on"), wait);
      io.unobserve(e.target);
    });
  }, { threshold: 0.12, rootMargin: "0px 0px -6% 0px" });
  els.forEach((el) => io.observe(el));
}

/* -------------------------------------------------------------------- boot */

function boot() {
  shrinkNav();
  document.querySelectorAll("[data-words]").forEach(wordRotator);
  document.querySelectorAll(".fx-page").forEach(asciiPage);
  document.querySelectorAll(".fx-keys").forEach(keysToInk);
  document.querySelectorAll("[data-spotlight]").forEach(spotlight);
  steps();
  reveals();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
