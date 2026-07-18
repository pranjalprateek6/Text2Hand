"""Draw hand-styled glyphs for the technical symbols.

These are the characters with no natural building block in the handwriting
sample, so unlike the composed punctuation they have to be drawn. Strokes are
black on white; the renderer derives its own alpha from darkness, recolors to
the pen ink, and adds rotation and scale jitter, so these only need the right
shape and proportions. The small wobble keeps them from looking vector-perfect.

Vertical placement is the renderer's job, via its character sets:
  CENTERED   + = < > ~     sit on the x-height axis
  RAISED     * ^ `         hang high on the line
  (default)  everything else bottom-aligns to the writing line

Run from anywhere:   python tools/make_symbols.py
Overwrites:          myfont/{35,36,37,38,42,43,47,60,61,62,64,
                             91,92,93,94,95,96,123,124,125,126}.png
"""
import random
from pathlib import Path

from PIL import Image, ImageDraw

FONT = Path(__file__).resolve().parent.parent / "myfont"
SEED = 7                   # fixed so re-running reproduces the same glyphs
SW = 5                     # stroke width, tuned to the letter weight


def new(w, h):
    im = Image.new("RGB", (w, h), (255, 255, 255))
    return im, ImageDraw.Draw(im)


def jit(p, j=1.3):
    return (p[0] + random.uniform(-j, j), p[1] + random.uniform(-j, j))


def stroke(d, pts, w=SW, j=1.3, cap=True):
    """A slightly wobbly polyline with rounded ends."""
    P = [jit(p, j) for p in pts]
    d.line(P, fill=(0, 0, 0), width=w, joint="curve")
    if cap:
        r = w / 2.0
        for x, y in (P[0], P[-1]):
            d.ellipse([x - r, y - r, x + r, y + r], fill=(0, 0, 0))


def ring(d, box, w=SW):
    d.ellipse(box, outline=(0, 0, 0), width=w)


def save(im, code):
    im.save(FONT / f"{code}.png")


def main():
    random.seed(SEED)

    # --- full height (baseline .. cap) ------------------------------------ #
    im, d = new(30, 64); stroke(d, [(6, 58), (24, 6)]); save(im, 47)          # /
    im, d = new(30, 64); stroke(d, [(6, 6), (24, 58)]); save(im, 92)          # \
    im, d = new(16, 64); stroke(d, [(8, 6), (8, 58)]); save(im, 124)          # |

    im, d = new(22, 64)                                                       # [
    stroke(d, [(9, 6), (9, 58)]); stroke(d, [(9, 6), (18, 6)])
    stroke(d, [(9, 58), (18, 58)]); save(im, 91)

    im, d = new(22, 64)                                                       # ]
    stroke(d, [(13, 6), (13, 58)]); stroke(d, [(4, 6), (13, 6)])
    stroke(d, [(4, 58), (13, 58)]); save(im, 93)

    im, d = new(24, 64)                                                       # {
    stroke(d, [(18, 6), (12, 11), (11, 24), (6, 32), (11, 40), (12, 53), (18, 58)], j=0.8)
    save(im, 123)

    im, d = new(24, 64)                                                       # }
    stroke(d, [(6, 6), (12, 11), (13, 24), (18, 32), (13, 40), (12, 53), (6, 58)], j=0.8)
    save(im, 125)

    im, d = new(42, 58)                                                       # #
    stroke(d, [(15, 6), (11, 50)]); stroke(d, [(29, 6), (25, 50)])
    stroke(d, [(6, 20), (35, 18)]); stroke(d, [(5, 36), (34, 34)]); save(im, 35)

    im, d = new(30, 56)                                                       # $
    stroke(d, [(24, 12), (14, 7), (7, 14), (12, 24), (21, 30), (16, 42), (7, 45), (5, 38)], j=0.8)
    stroke(d, [(15, 3), (15, 49)]); save(im, 36)

    im, d = new(42, 54)                                                       # %
    stroke(d, [(8, 46), (32, 8)]); ring(d, [6, 8, 18, 20]); ring(d, [24, 34, 36, 46])
    save(im, 37)

    im, d = new(40, 56)                                                       # &
    stroke(d, [(34, 50), (12, 30), (9, 16), (17, 8), (24, 13), (20, 24), (8, 36),
               (11, 47), (24, 49), (34, 34)], j=0.8)
    save(im, 38)

    im, d = new(50, 50)                                                       # @
    ring(d, [6, 6, 44, 44]); ring(d, [18, 17, 32, 33])
    stroke(d, [(32, 21), (34, 33), (40, 31)], j=0.8); save(im, 64)

    # --- superscript marks (RAISED lifts them) ---------------------------- #
    im, d = new(24, 24)                                                       # *
    stroke(d, [(11, 2), (11, 20)]); stroke(d, [(3, 6), (19, 16)])
    stroke(d, [(3, 16), (19, 6)]); save(im, 42)

    im, d = new(24, 18); stroke(d, [(4, 15), (11, 4), (18, 15)]); save(im, 94)  # ^
    im, d = new(15, 18); stroke(d, [(4, 4), (10, 15)]); save(im, 96)            # `

    # --- math symbols (CENTERED puts them on the x-height axis) ----------- #
    im, d = new(28, 28)                                                       # +
    stroke(d, [(4, 14), (24, 14)]); stroke(d, [(14, 4), (14, 24)]); save(im, 43)

    im, d = new(30, 22)                                                       # =
    stroke(d, [(4, 7), (26, 6)]); stroke(d, [(4, 16), (26, 15)]); save(im, 61)

    im, d = new(24, 28); stroke(d, [(20, 4), (5, 14), (20, 24)], j=0.8); save(im, 60)  # <
    im, d = new(24, 28); stroke(d, [(4, 4), (19, 14), (4, 24)], j=0.8); save(im, 62)   # >

    im, d = new(34, 18)                                                       # ~
    stroke(d, [(4, 11), (11, 5), (18, 10), (25, 14), (31, 8)], j=0.7); save(im, 126)

    # --- underscore (sits at the writing line) ---------------------------- #
    im, d = new(40, 14); stroke(d, [(4, 8), (36, 8)]); save(im, 95)            # _

    print("wrote 21 symbols: # $ % & * + / < = > @ [ \\ ] ^ _ ` { | } ~")


if __name__ == "__main__":
    main()
