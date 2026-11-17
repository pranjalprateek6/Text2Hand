"""Write extra versions of each captured digit.

Every digit was captured once, so "10 | 20 | 30" drew the same 0 three times
over. A real second sample is always better, and when one is photographed it
should simply replace these; until then each captured digit gets several
versions of itself, bent through a coarse mesh, slanted a few degrees and
given a slightly heavier or lighter stroke, so they differ in shape and not
only in position. The captured digit stays as the first version.

Run from anywhere:   python tools/make_digit_versions.py
Writes:              myfont/<48..57>_<n>.png (the captured <48..57>.png is kept)
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageFilter

FONT = Path(__file__).resolve().parent.parent / "myfont"
SEED = 23
EXTRA = 5                 # versions written beside each captured digit


def ink_box(im: Image.Image) -> tuple[int, int, int, int]:
    box = im.point(lambda p: 255 if p < 200 else 0).getbbox()
    return box or (0, 0, im.width, im.height)


def bend(g: Image.Image, k: float, rng: random.Random) -> Image.Image:
    """Bend a greyscale glyph (white paper, dark ink) through a 3x3 mesh."""
    w, h = g.size
    pad = max(4, int(0.12 * max(w, h)))
    c = Image.new("L", (w + 2 * pad, h + 2 * pad), 255)
    c.paste(g, (pad, pad))
    cw, ch = c.size
    n, amp = 3, k * min(w, h) + 1
    pts = {(i, j): (i * cw / n + rng.gauss(0, amp), j * ch / n + rng.gauss(0, amp))
           for i in range(n + 1) for j in range(n + 1)}
    mesh = [((int(i * cw / n), int(j * ch / n), int((i + 1) * cw / n), int((j + 1) * ch / n)),
             (*pts[(i, j)], *pts[(i, j + 1)], *pts[(i + 1, j + 1)], *pts[(i + 1, j)]))
            for i in range(n) for j in range(n)]
    return c.transform(c.size, Image.Transform.MESH, mesh, Image.Resampling.BICUBIC, fillcolor=255)


def slant(g: Image.Image, deg: float) -> Image.Image:
    import math
    t = math.tan(math.radians(deg))
    w, h = g.size
    extra = int(abs(t) * h) + 2
    c = Image.new("L", (w + extra, h), 255)
    c.paste(g, (extra // 2, 0))
    return c.transform(c.size, Image.Transform.AFFINE, (1, t, -t * h / 2, 0, 1, 0),
                       Image.Resampling.BICUBIC, fillcolor=255)


def version(src: Image.Image, rng: random.Random) -> Image.Image:
    g = bend(src, rng.uniform(0.04, 0.075), rng)
    g = slant(g, rng.uniform(-7, 7))
    s = rng.uniform(0.94, 1.06)
    g = g.resize((max(1, round(g.width * s)), max(1, round(g.height * rng.uniform(0.95, 1.05) * s))),
                 Image.Resampling.LANCZOS)
    r = rng.random()
    if r < 0.25:
        g = g.filter(ImageFilter.MinFilter(3))        # a heavier stroke
    elif r < 0.4:
        g = g.filter(ImageFilter.MaxFilter(3))        # a lighter one
    g = g.crop(ink_box(g))
    out = Image.new("L", (g.width + 6, g.height + 6), 255)
    out.paste(g, (3, 3))
    return out


def main() -> None:
    rng = random.Random(SEED)
    for d in "0123456789":
        code = ord(d)
        for old in FONT.glob(f"{code}_*.png"):
            old.unlink()
        src = Image.open(FONT / f"{code}.png").convert("L")
        src = src.crop(ink_box(src))
        for k in range(EXTRA):
            version(src, rng).save(FONT / f"{code}_{k + 1}.png")
    print(f"wrote {EXTRA} versions beside each captured digit")


if __name__ == "__main__":
    main()
