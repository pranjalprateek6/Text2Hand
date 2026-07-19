"use strict";

/* Text2Hand — the reference build's effects, ported to vanilla.
 *
 * The original runs these on framer-motion and GSAP ScrollTrigger. Nothing
 * here needs either: the flap is a timer per tile, the scramble is one rAF
 * loop, the reveals are an IntersectionObserver, and the noise is a canvas
 * painted every other frame. No dependencies, no build.
 *
 * Every effect degrades to its finished state under prefers-reduced-motion,
 * and to its finished state if it never runs at all.
 */

const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ------------------------------------------------------------- split flap */

const FLAP_SET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

function splitFlap(host) {
  const text = (host.dataset.flap || "").toUpperCase();
  const speed = Number(host.dataset.flapSpeed || 80);
  host.textContent = "";
  host.setAttribute("aria-label", text);

  const tiles = [...text].map((ch) => {
    const cell = document.createElement("span");
    cell.className = "flap__c" + (ch === " " ? " flap__c--space" : "");
    cell.setAttribute("aria-hidden", "true");
    cell.textContent = ch === " " ? "" : ch;
    host.appendChild(cell);
    return { cell, ch };
  });

  if (REDUCED) return;

  tiles.forEach(({ cell, ch }, i) => {
    if (ch === " ") return;
    // Each tile flips through the charset, then settles. Later tiles run
    // longer, so the word resolves left to right like a departure board.
    const settleAfter = 8 + i * 3;
    let n = 0;
    cell.classList.add("is-flipping");
    cell.textContent = FLAP_SET[(Math.random() * FLAP_SET.length) | 0];

    setTimeout(() => {
      const timer = setInterval(() => {
        if (n >= settleAfter) {
          clearInterval(timer);
          cell.textContent = ch;
          cell.classList.remove("is-flipping");
          return;
        }
        cell.textContent = FLAP_SET[(Math.random() * FLAP_SET.length) | 0];
        n++;
      }, speed);
    }, i * 120);
  });
}

/* --------------------------------------------------------------- scramble */

const GLYPHS = "!@#$%^&*()_+-=<>?/\\[]{}Xx";

function scrambleOnHover(el) {
  if (REDUCED) return;
  const final = el.textContent;
  let raf = null;

  el.addEventListener("mouseenter", () => {
    if (raf) cancelAnimationFrame(raf);
    const t0 = performance.now();
    const dur = 600;
    const chars = [...final];

    const step = (t) => {
      const k = Math.min((t - t0) / dur, 1);
      const eased = 1 - Math.pow(1 - k, 2);      // power2.out, as the original
      const locked = Math.floor(eased * chars.length);
      el.textContent = chars
        .map((c, i) => (i < locked || c === " " ? c : GLYPHS[(Math.random() * GLYPHS.length) | 0]))
        .join("");
      if (k < 1) raf = requestAnimationFrame(step);
      else el.textContent = final;
    };
    raf = requestAnimationFrame(step);
  });
}

/* ----------------------------------------------------------- canvas noise */

function animatedNoise(canvas) {
  if (REDUCED) return;
  const ctx = canvas.getContext("2d", { willReadFrequently: false });
  if (!ctx) return;

  let frame = 0, raf = null, running = true;
  const size = () => {
    // half resolution, as the original: the grain reads the same and costs a
    // quarter of the pixels
    canvas.width = Math.max(1, canvas.offsetWidth / 2);
    canvas.height = Math.max(1, canvas.offsetHeight / 2);
  };

  const paint = () => {
    const img = ctx.createImageData(canvas.width, canvas.height);
    const d = img.data;
    for (let i = 0; i < d.length; i += 4) {
      const v = (Math.random() * 255) | 0;
      d[i] = d[i + 1] = d[i + 2] = v;
      d[i + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
  };

  const loop = () => {
    if (!running) return;
    if (++frame % 2 === 0) paint();
    raf = requestAnimationFrame(loop);
  };

  size();
  addEventListener("resize", size);
  loop();

  // stop painting when the hero scrolls away, so it costs nothing off-screen
  new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      running = e.isIntersecting;
      if (running) loop();
      else if (raf) cancelAnimationFrame(raf);
    });
  }).observe(canvas);
}

/* ----------------------------------------------------------- scroll reveal */

function reveals() {
  const els = [...document.querySelectorAll(".rv")];
  const showAll = () => els.forEach((el) => el.classList.add("on"));
  if (REDUCED || !("IntersectionObserver" in window)) return showAll();

  // If frames never run (a hidden or prerendered tab), the page must not stay
  // invisible. This fallback is why nothing here is opacity-0 forever.
  setTimeout(() => { if (!document.querySelector(".rv.on")) showAll(); }, 2200);

  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      const wait = Number(e.target.dataset.delay || 0);
      setTimeout(() => e.target.classList.add("on"), wait);
      io.unobserve(e.target);
    });
  }, { threshold: 0.15, rootMargin: "0px 0px -8% 0px" });
  els.forEach((el) => io.observe(el));
}

/* ------------------------------------------------------------------- rail */

function rail() {
  const items = [...document.querySelectorAll(".rail__item[data-goto]")];
  if (!items.length) return;

  items.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = document.getElementById(btn.dataset.goto);
      if (target) target.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth" });
    });
  });

  const sections = items
    .map((b) => document.getElementById(b.dataset.goto))
    .filter(Boolean);
  if (!sections.length) return;

  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      items.forEach((b) => b.classList.toggle("is-on", b.dataset.goto === e.target.id));
    });
  }, { threshold: 0.3 });
  sections.forEach((s) => io.observe(s));
}

/* ------------------------------------------------------------------- boot */

function boot() {
  document.querySelectorAll("[data-flap]").forEach(splitFlap);
  document.querySelectorAll("[data-scramble]").forEach(scrambleOnHover);
  document.querySelectorAll(".fx-noise").forEach(animatedNoise);
  reveals();
  rail();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
