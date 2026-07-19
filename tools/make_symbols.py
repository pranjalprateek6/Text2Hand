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

# Height of each drawn symbol as a fraction of the captured letters' x-height.
#
# The shapes below are laid out on their own grid, where 64 was meant to be a
# full-height character. That grid no longer matches the hand these sit beside:
# once real handwriting replaced the borrowed font, brackets were coming out at
# 0.90 of the x-height where the captured parentheses are 1.33 and 1.56, and
# "<" at 0.41, small enough that "<move>" read as "move>" on the page.
#
# The fractions are taken from the captured punctuation, which is the only
# honest reference for how large this hand writes a mark: ( is 1.33, ) is 1.56,
# : is 1.35, ! is 1.70, and a full stop is 0.38.
HEIGHT = {
    47: 1.50, 92: 1.50, 124: 1.50,                    # / \ |
    91: 1.45, 93: 1.45, 123: 1.45, 125: 1.45,         # [ ] { }
    35: 1.20, 36: 1.30, 37: 1.10, 38: 1.10, 64: 1.05,  # # $ % & @
    60: 0.85, 62: 0.85, 43: 0.85,                     # < > +
    42: 0.60, 61: 0.50, 94: 0.40, 96: 0.30,           # * = ^ `
    126: 0.30, 95: 0.10,                              # ~ _

    # Greek and maths, drawn because converted papers use them constantly and
    # a skipped character is a hole in an equation. Proportions follow the
    # letters: x-height bodies at 1.0-1.1, ascenders ~1.5, tails add ~0.5.
    945: 1.05,   # alpha
    946: 1.90,   # beta, ascender and tail
    947: 1.60,   # gamma, x body + tail
    948: 1.45,   # delta
    949: 1.00,   # epsilon
    952: 1.50,   # theta
    955: 1.50,   # lambda
    956: 1.55,   # mu, x body + tail
    960: 1.10,   # pi
    963: 1.10,   # sigma
    8712: 0.95,  # element of
    8721: 1.50,  # n-ary summation
    8706: 1.40,  # partial derivative
    8727: 0.60,  # asterisk operator
    8730: 1.60,  # square root
    8734: 0.55,  # infinity
}


def x_height() -> int:
    """The captured letters' x-height, in stored glyph pixels.

    Read from the letter glyphs rather than fixed here, so the drawn symbols
    follow the hand if it is ever recaptured at another size.
    """
    import sys

    sys.path.insert(0, str(FONT.parent))
    import text_to_handwriting as t2h

    return t2h._raw_x_height()


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


def arc_pts(cx, cy, rx, ry, a0, a1, n=26):
    """Sample an elliptical arc as points for stroke(). Degrees, y down."""
    import math
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / (n - 1))),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / (n - 1))))
            for i in range(n)]


def save(im, code):
    """Scale the drawn shape onto the captured hand's scale, then write it.

    Resizing here rather than drawing at final size keeps the coordinates above
    readable as a design. Upscaling costs little sharpness, because the renderer
    scales every glyph file down again to reach its target x-height.
    """
    target = HEIGHT.get(code)
    if target:
        box = im.convert("L").point(lambda p: 0 if p >= 150 else 255).getbbox()
        if box:
            ratio = (target * x_height()) / (box[3] - box[1])
            im = im.resize((max(1, round(im.width * ratio)),
                            max(1, round(im.height * ratio))), Image.LANCZOS)
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

    # --- Greek --------------------------------------------------------------
    # Curves are sampled arcs jittered lightly, so they wobble like the pen
    # rather than like noise.
    im, d = new(54, 48)                                                       # alpha
    stroke(d, arc_pts(20, 24, 15, 19, 20, 340), j=0.6)
    stroke(d, [(37, 7), (38, 19), (40, 33), (46, 42)], j=0.8); save(im, 945)

    im, d = new(38, 88)                                                       # beta
    stroke(d, [(9, 84), (10, 50), (11, 18), (15, 7)], j=0.8)
    stroke(d, arc_pts(12, 22, 13, 14, -90, 90), j=0.6)
    stroke(d, arc_pts(11, 52, 16, 17, -90, 90), j=0.6); save(im, 946)

    im, d = new(34, 82)                                                       # gamma
    stroke(d, [(4, 6), (10, 18), (16, 30)], j=0.8)
    stroke(d, [(30, 5), (24, 16), (18, 28), (16, 44), (15, 62), (11, 78)], j=0.7)
    save(im, 947)

    im, d = new(38, 76)                                                       # delta
    ring(d, [4, 36, 32, 72])
    stroke(d, [(30, 8), (17, 4), (10, 9), (16, 21), (24, 36)], j=0.7); save(im, 948)

    im, d = new(32, 50)                                                       # epsilon
    stroke(d, arc_pts(17, 14, 11, 10, 60, 300), j=0.6)
    stroke(d, arc_pts(16, 36, 12, 12, 60, 300), j=0.6); save(im, 949)

    im, d = new(40, 86)                                                       # theta
    stroke(d, arc_pts(20, 43, 14, 37, 0, 360, n=40), j=0.6)
    stroke(d, [(10, 44), (30, 42)], j=0.8); save(im, 952)

    im, d = new(42, 86)                                                       # lambda
    stroke(d, [(6, 6), (14, 26), (24, 52), (33, 80)], j=0.8)
    stroke(d, [(20, 42), (12, 60), (5, 80)], j=0.8); save(im, 955)

    im, d = new(44, 88)                                                       # mu
    stroke(d, [(9, 6), (9, 52)], j=0.8)
    stroke(d, [(9, 52), (7, 68), (4, 84)], j=0.8)
    # the bowl hangs under the stems like a u: angles 180 -> 0 pass through 90,
    # which is the bottom with y pointing down
    stroke(d, arc_pts(21, 38, 12, 15, 180, 0, n=20), j=0.6)
    stroke(d, [(33, 6), (33, 46), (38, 53)], j=0.8); save(im, 956)

    im, d = new(48, 42)                                                       # pi
    stroke(d, [(3, 10), (15, 6), (30, 8), (45, 5)], j=0.8)
    stroke(d, [(14, 9), (13, 23), (12, 38)], j=0.8)
    stroke(d, [(33, 9), (33, 24), (36, 38), (41, 38)], j=0.8); save(im, 960)

    im, d = new(44, 42)                                                       # sigma
    ring(d, [4, 12, 30, 38])
    stroke(d, [(27, 13), (41, 7)], j=0.8); save(im, 963)

    # --- maths --------------------------------------------------------------
    im, d = new(38, 42)                                                       # element of
    stroke(d, arc_pts(21, 21, 15, 17, 60, 300), j=0.6)
    stroke(d, [(10, 21), (31, 21)], j=0.8); save(im, 8712)

    im, d = new(44, 80)                                                       # summation
    stroke(d, [(40, 9), (8, 5), (22, 39), (7, 74), (41, 76)], j=1.0); save(im, 8721)

    im, d = new(40, 68)                                                       # partial
    ring(d, [6, 32, 32, 64])
    stroke(d, [(10, 12), (19, 5), (28, 9), (31, 20), (31, 38)], j=0.7); save(im, 8706)

    im, d = new(32, 32)                                                       # asterisk operator
    stroke(d, [(16, 4), (16, 28)]); stroke(d, [(6, 10), (26, 22)])
    stroke(d, [(26, 10), (6, 22)]); save(im, 8727)

    im, d = new(50, 82)                                                       # square root
    stroke(d, [(4, 48), (11, 70), (16, 76), (28, 40), (40, 8), (47, 6)], j=0.8)
    save(im, 8730)

    # Drawn large so the loops stay open around the stroke width; the HEIGHT
    # map scales it back down. At 26px tall the loops closed into a blob.
    im, d = new(96, 46)
    import math as _m
    pts = []
    for i in range(56):
        t = 2 * _m.pi * i / 55
        k = 1 + _m.sin(t) ** 2
        pts.append((48 + 42 * _m.cos(t) / k, 23 + 21 * _m.sin(t) * _m.cos(t) / k))
    stroke(d, pts, j=0.5); save(im, 8734)                                     # infinity

    # The console may not be UTF-8 (cp1252 on Windows), so never print the
    # characters themselves.
    print("wrote 21 ascii symbols and 16 greek/maths glyphs")


if __name__ == "__main__":
    main()
