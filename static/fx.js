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

/* ------------------------------------------------------------ ascii sphere */

function sphere(canvas) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const CHARS = "░▒▓█▀▄▌▐│─┤├┴┬╭╮╰╯";
  let time = 0.6, raf = null, running = !REDUCED;

  const resize = () => {
    const dpr = devicePixelRatio || 1;
    const r = canvas.getBoundingClientRect();
    canvas.width = r.width * dpr;
    canvas.height = r.height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };

  const paint = () => {
    const r = canvas.getBoundingClientRect();
    ctx.clearRect(0, 0, r.width, r.height);
    const cx = r.width / 2, cy = r.height / 2;
    const radius = Math.min(r.width, r.height) * 0.52;
    ctx.font = "12px monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    const pts = [];
    for (let phi = 0; phi < Math.PI * 2; phi += 0.15) {
      for (let theta = 0; theta < Math.PI; theta += 0.15) {
        const x = Math.sin(theta) * Math.cos(phi + time * 0.5);
        const y = Math.sin(theta) * Math.sin(phi + time * 0.5);
        const z = Math.cos(theta);
        const rotY = time * 0.3;
        const nx = x * Math.cos(rotY) - z * Math.sin(rotY);
        const nz = x * Math.sin(rotY) + z * Math.cos(rotY);
        const rotX = time * 0.2;
        const ny = y * Math.cos(rotX) - nz * Math.sin(rotX);
        const fz = y * Math.sin(rotX) + nz * Math.cos(rotX);
        pts.push({ x: cx + nx * radius, y: cy + ny * radius, z: fz,
                   ch: CHARS[Math.floor(((fz + 1) / 2) * (CHARS.length - 1))] });
      }
    }
    pts.sort((a, b) => a.z - b.z);
    for (const p of pts) {
      ctx.fillStyle = `rgba(0, 0, 0, ${0.2 + (p.z + 1) * 0.4})`;
      ctx.fillText(p.ch, p.x, p.y);
    }
  };

  const loop = () => {
    if (!running) return;
    paint();
    time += 0.02;
    raf = requestAnimationFrame(loop);
  };

  resize();
  addEventListener("resize", resize);
  // The canvas can measure 0x0 at boot if layout has not settled when the
  // script runs, and a window resize never comes to correct it. Re-measure
  // and repaint whenever the box itself changes size.
  new ResizeObserver(() => { resize(); paint(); }).observe(canvas);
  paint();                                   // a still sphere even if frames never run
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
  document.querySelectorAll(".fx-sphere").forEach(sphere);
  document.querySelectorAll("[data-spotlight]").forEach(spotlight);
  steps();
  reveals();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
