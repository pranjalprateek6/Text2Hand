"""Render the landing page's pictures with the current renderer.

    python tools/make_landing_images.py

The landing page shows real output, so its pictures go stale whenever the
renderer improves: the July versions still showed tables as separate boxes
long after they were drawn as one grid. This makes them again, from fixed
text and fixed seeds, through the same /api/render the studio uses:

  static/sample_letter.jpg   the hero: a short letter on ruled paper
  static/sample_page.jpg     the showcase: a page of study notes
  static/sample_eq.jpg       the attention equation written out in the hand,
                             fraction and root included
  static/og.jpg              the 1200x630 picture shown when the link is shared

The paper grain is random on every run; the writing is not.
"""
from __future__ import annotations

import io
import os
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("T2H_RENDER_PROCS", "0")
os.environ.setdefault("T2H_RENDER_ROOT", tempfile.mkdtemp(prefix="t2h_landing_"))
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageOps  # noqa: E402

import app  # noqa: E402

STATIC = ROOT / "static"

LETTER = """Dear Maya,

Thank you for the parcel. The tea arrived on the wettest morning of the year, which felt like good timing, and the little notebook is already half full of lists I will never finish.

The garden has given up on tomatoes and taken up slugs instead. Dad says it is a phase. I say it is a lifestyle. Either way the basil survived, so there will be pesto when you visit in May.

I finally framed the photo from the lake, the one where the dog is mostly a blur. It is on the shelf by the window and everyone who visits asks about it.

Write back soon, and tell me everything about the new flat.

With love,
Asha"""

# one paragraph to a line: plain text keeps every line break it is given
NOTES = """# Thermodynamics, week 3

## Entropy

- Entropy measures how many ways a system can be arranged
- In an isolated system it never decreases
    - which is why heat flows from hot to cold
- Units are joules per kelvin

1. Write the energy balance first
2. Then check the sign of the entropy change

> A process is reversible only if the total entropy stays the same.

| Process | Change in S |
|---|---|
| Free expansion | positive |
| Reversible cycle | zero |
| Melting ice | positive |

## Free energy

At constant temperature and pressure, a change happens on its own only if the Gibbs energy falls: G = H - TS, so a big enough rise in entropy can pay for an uphill change in enthalpy.

---

Remember: the Greek letters still need care, like a Carnot efficiency of 1 - Tc / Th, and the sum over states in Z."""


def render(text: str, seed: int, **opts) -> Image.Image:
    """Page 1 of a render through the real endpoint, as a full-size image."""
    client = app.app.test_client()
    started = client.post("/api/render", json={"text": text, "seed": seed, **opts})
    if started.status_code != 202:
        raise SystemExit(f"render refused: {started.get_json()}")
    jid = started.get_json()["job"]
    while True:
        job = client.get(f"/api/job/{jid}").get_json()
        if job["state"] != "running":
            break
        time.sleep(0.1)
    if job["state"] != "done":
        raise SystemExit(f"render {job['state']}: {job.get('error')}")
    data = client.get(f"/download/{job['id']}/page_1.png").data
    return Image.open(io.BytesIO(data)).convert("RGB")


def save_jpeg(im: Image.Image, name: str, width: int, quality: int = 84) -> None:
    im = im.copy()
    im.thumbnail((width, width * 3), Image.LANCZOS)
    im.save(STATIC / name, quality=quality, optimize=True, progressive=True)
    print(f"  {name}: {im.width}x{im.height}, {(STATIC / name).stat().st_size // 1024} KB")


def crop_to_writing(page: Image.Image, keep_below: int = 0, pad: int = 70) -> Image.Image:
    """The page cut to the area that has writing on it, plus a margin."""
    grey = ImageOps.invert(page.convert("L")).point(lambda v: 255 if v > 110 else 0)
    box = grey.getbbox()
    if not box:
        return page
    x0, y0, x1, y1 = box
    return page.crop((0, max(0, y0 - pad), page.width, min(page.height, max(y1, keep_below) + pad)))


def written_equation() -> Image.Image:
    """The attention equation written in the captured hand, on ruled paper.

    The page writer sets text in lines, not fractions, so this one picture is
    composed from the hand's own glyphs: the fraction bar and the root's
    overbar are pen lines in the same ink, and nothing is typeset.
    """
    import render_core
    import text_to_handwriting as t2h

    render_core.apply_options(paper="ruled", ink="blue-black", texture=True, skew=False)
    t2h.derive_metrics()
    rng = random.Random(1706)
    random.seed(1706)
    pages: list[Image.Image] = []
    sheet = t2h.Sheet(on_page=lambda n, page: pages.append(page))   # opens its first page

    S = 1.3                                  # a showcase: written a little large
    X = round(t2h.X_HEIGHT * S)
    gap = round(X * 0.12)

    def glyph(ch: str, scale: float = 1.0) -> Image.Image:
        g = rng.choice(t2h.glyph_variants(ch))
        return t2h.jittered(g, scale * S)

    def run(text: str, scale: float = 1.0) -> list:
        """(image, dx, dy-from-baseline) for each glyph, and the run's width."""
        out, x = [], 0
        for ch in text:
            if ch == " ":
                x += round(t2h.SPACE_WIDTH * S * scale * 0.7)
                continue
            g = glyph(ch, scale)
            if ch in t2h.CENTERED:
                dy = -round(X * scale / 2) - g.height // 2
            else:
                dy = -g.height + t2h._drop_below_line(ch, g.height)
            out.append((g, x, dy))
            x += g.width + round(gap * scale)
        return out, x - round(gap * scale)

    def stamp(pieces: list, x0: int, base: int) -> None:
        for g, dx, dy in pieces:
            sheet._stamp(g, x0 + dx, base + dy, rng.uniform(0.86, 1.0))

    def pen(points: list) -> None:
        wob = [(x, y + rng.uniform(-1.5, 1.5)) for x, y in points]
        sheet._line(wob, max(3, round(X * 0.075)))

    left, wl = run("Attention(Q, K, V) = softmax(")
    num, wn = run("QK")
    sup, ws = run("T", 0.62)
    root = glyph("\u221a")
    den_d, wd = run("d")
    sub, wk = run("k", 0.62)
    right, wr = run(")V")

    num_w = wn + gap + ws
    den_w = root.width + gap + wd + gap + wk
    frac_w = max(num_w, den_w) + 2 * gap
    total = wl + gap + frac_w + gap + wr
    x = (sheet.width - total) // 2

    base = t2h.MARGIN_T + 4 * t2h.LINE_HEIGHT          # on a ruled line, with room above
    axis = base - X // 2                               # where = and the bar sit
    stamp(left, x, base)
    x += wl + gap

    # numerator, over the bar, with T raised
    nx = x + (frac_w - num_w) // 2
    nbase = axis - round(X * 0.5)
    stamp(num, nx, nbase)
    cap = max(g.height for g, _, _ in num)
    stamp(sup, nx + wn + gap, nbase - round(cap * 0.55))
    # the bar
    pen([(x, axis), (x + frac_w // 2, axis + 1), (x + frac_w, axis)])
    # denominator: the root, then d with k lowered, and the root's overbar
    dx = x + (frac_w - den_w) // 2
    # the root's overbar keeps clear of the fraction bar above it
    dtall = max(g.height for g, _, _ in den_d)
    rh = dtall + round(X * 0.45)
    top = axis + round(X * 0.8)
    dbase = top + rh - round(X * 0.12)
    root = root.resize((max(1, round(root.width * rh / root.height)), rh), Image.LANCZOS)
    sheet._stamp(root, dx, top, 0.95)
    rx = dx + root.width - round(root.width * 0.08)
    stamp(den_d, rx + gap, dbase)
    stamp(sub, rx + gap + wd + gap, dbase + round(X * 0.25))
    pen([(rx - 2, top + 2), (dx + den_w + gap, top + 2)])
    x += frac_w + gap
    stamp(right, x, base)

    sheet.finish()
    page = pages[-1].convert("RGB")
    # the strip of page the equation is written on, with a little paper
    # above and below it
    ink = ImageOps.invert(page.convert("L")).point(lambda v: 255 if v > 150 else 0).getbbox()
    pad = round(0.45 * t2h.LINE_HEIGHT)
    return page.crop((0, max(0, ink[1] - pad), page.width, min(page.height, ink[3] + pad)))


def main() -> None:
    print("rendering landing images with the current renderer")
    letter = render(LETTER, 20261017, paper="ruled", ink="blue-black")
    save_jpeg(letter, "sample_letter.jpg", 1100)

    notes = render(NOTES, 4242, markdown=True, paper="ruled", ink="blue-black")
    save_jpeg(notes, "sample_page.jpg", 1100)

    save_jpeg(written_equation(), "sample_eq.jpg", 1100)

    # the shared-link picture: the top of the letter, on its own paper
    top = letter.crop((0, 0, letter.width, round(letter.width * 630 / 1200)))
    og = top.resize((1200, 630), Image.LANCZOS)
    og.save(STATIC / "og.jpg", quality=86, optimize=True, progressive=True)
    print(f"  og.jpg: 1200x630, {(STATIC / 'og.jpg').stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
