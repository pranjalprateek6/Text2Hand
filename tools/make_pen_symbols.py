"""Draw maths and pseudocode symbols as imperfect pen strokes.

Notes on algorithms lean on characters no handwriting sample covers: the
assignment arrow, comparisons, ceiling brackets, a true minus, a times sign,
a subscript two. Drawn ruler-straight they read as typeset and give the page
away, so each is drawn the way a hand draws it: the control points move a
little, every straight run bows, the ends over- or undershoot, the pressure
changes along the stroke and the whole mark tilts a degree or two. Several
versions of each are written, and the renderer deals them out in turn.

Also here:
  Z   a crossed Z, since the captured one reads as a 2 in "Z.KEY"
  -   extra hyphens of different lengths, beside the captured one

Run from anywhere:   python tools/make_pen_symbols.py
Writes:              myfont/<codepoint>.png and <codepoint>_<n>.png for each
                     symbol below; the captured hyphen 45.png is kept.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

FONT = Path(__file__).resolve().parent.parent / "myfont"
SEED = 17                 # fixed so re-running reproduces the same glyphs
VERSIONS = 5
XH = 63                   # x-height of the captured letters in myfont, in px
PEN = 9.0                 # stroke width matching the captured letters
SS = 4                    # supersampling

# (width, height) in x-heights, and strokes as point lists in a 0..1 box, y down
ARROW = 0.30
SYMBOLS: dict[str, tuple[tuple[float, float], list[list[tuple[float, float]]]]] = {
    "→": ((1.95, 0.78), [[(0, .5), (1, .5)], [(1 - ARROW, .08), (1, .5), (1 - ARROW, .92)]]),
    "←": ((1.95, 0.78), [[(1, .5), (0, .5)], [(ARROW, .08), (0, .5), (ARROW, .92)]]),
    "↔": ((2.15, 0.78), [[(0, .5), (1, .5)], [(1 - ARROW * .8, .08), (1, .5), (1 - ARROW * .8, .92)],
                         [(ARROW * .8, .08), (0, .5), (ARROW * .8, .92)]]),
    "≠": ((1.15, 1.0), [[(0, .32), (1, .32)], [(0, .68), (1, .68)], [(.72, 0), (.28, 1)]]),
    "≥": ((1.0, 1.15), [[(0, 0), (1, .38), (0, .72)], [(0, 1), (1, .98)]]),
    "≤": ((1.0, 1.15), [[(1, 0), (0, .38), (1, .72)], [(0, .98), (1, 1)]]),
    "⌈": ((0.55, 1.55), [[(.9, 0), (.12, 0), (.1, 1)]]),
    "⌉": ((0.55, 1.55), [[(.1, 0), (.88, 0), (.9, 1)]]),
    "−": ((0.85, 0.12), [[(0, .5), (1, .5)]]),
    "–": ((1.0, 0.12), [[(0, .5), (1, .5)]]),
    "×": ((0.75, 0.75), [[(0.05, 0.05), (0.95, 0.95)], [(0.95, 0.04), (0.04, 0.96)]]),
    "Z": ((0.95, 1.45), [[(0.02, 0.03), (0.98, 0.0), (0.04, 0.98), (1.0, 0.97)], [(0.22, 0.5), (0.8, 0.48)]]),
    "₂": ((0.48, 0.62), [[(.05, .28), (.25, .02), (.65, .0), (.9, .25), (.7, .6), (.05, 1), (1, .97)]]),
    "-": ((0.62, 0.12), [[(0, .5), (1, .5)]]),
}
SHARP = set("⌈⌉Z")        # corners a pen turns rather than rounds
KEEP_CAPTURED = set("-")  # the captured glyph stays as the first version


def smooth(points: list[tuple[float, float]], steps: int = 14) -> list[tuple[float, float]]:
    """Catmull-Rom through the points, so a curve reads as one pen movement."""
    if len(points) < 3:
        (x0, y0), (x1, y1) = points
        return [(x0 + (x1 - x0) * t / steps, y0 + (y1 - y0) * t / steps) for t in range(steps + 1)]
    pts = [points[0]] + points + [points[-1]]
    out: list[tuple[float, float]] = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for s in range(steps):
            t = s / steps
            out.append((
                0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t * t
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t ** 3),
                0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t * t
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t ** 3)))
    out.append(pts[-2])
    return out


def draw(ch: str, rng: random.Random) -> Image.Image:
    (wx, hy), strokes = SYMBOLS[ch]
    w, h = wx * XH * rng.uniform(.93, 1.07), hy * XH * rng.uniform(.93, 1.07)
    pad = PEN * 2
    im = Image.new("L", (int((w + 2 * pad) * SS), int((h + 2 * pad) * SS)), 255)
    d = ImageDraw.Draw(im)
    jit = 0.045
    for stroke in strokes:
        pts = [(x + rng.gauss(0, jit), y + rng.gauss(0, jit * (w / max(h, 1)) ** .5)) for x, y in stroke]
        if len(pts) == 2 and ch not in SHARP:
            # a straight run drawn as a gentle bow: a hand never rules a line
            (x0, y0), (x1, y1) = pts
            bow = rng.gauss(0, 0.05)
            pts = [(x0, y0), ((x0 + x1) / 2 - bow * (y1 - y0), (y0 + y1) / 2 + bow * (x1 - x0)), (x1, y1)]
        for end, nxt in ((0, 1), (-1, -2)):       # ends over- or undershoot
            ex, ey = pts[end]
            nx, ny = pts[nxt]
            k = rng.uniform(-0.06, 0.08)
            pts[end] = (ex + (ex - nx) * k, ey + (ey - ny) * k)
        if ch in SHARP and len(pts) > 2:
            path = [q for a, b in zip(pts, pts[1:]) for q in smooth([a, b])]
        else:
            path = smooth(pts)
        phase, freq, amp = rng.uniform(0, 6.3), rng.uniform(2, 4), rng.uniform(0.004, 0.012)
        p0, p1 = rng.uniform(.85, 1.05), rng.uniform(.8, 1.1)
        n = len(path)
        for i in range(n - 1):
            t = i / max(1, n - 1)
            wob = amp * math.sin(phase + freq * t * 6.28)
            r = PEN / 2 * (p0 + (p1 - p0) * t) * (0.82 if t < .04 or t > .96 else 1) * SS
            ax, ay = (pad + path[i][0] * w) * SS, (pad + (path[i][1] + wob) * h) * SS
            bx, by = (pad + path[i + 1][0] * w) * SS, (pad + (path[i + 1][1] + wob) * h) * SS
            d.line([(ax, ay), (bx, by)], fill=28, width=int(2 * r))
            d.ellipse([bx - r, by - r, bx + r, by + r], fill=28)
    im = im.rotate(rng.uniform(-3.5, 3.5), resample=Image.Resampling.BICUBIC, fillcolor=255, expand=True)
    return im.resize((max(1, im.width // SS), max(1, im.height // SS)), Image.Resampling.LANCZOS)


def main() -> None:
    rng = random.Random(SEED)
    for ch in SYMBOLS:
        code = ord(ch)
        keep = ch in KEEP_CAPTURED
        for old in FONT.glob(f"{code}_*.png"):
            old.unlink()
        if not keep:
            (FONT / f"{code}.png").unlink(missing_ok=True)
        for k in range(VERSIONS):
            n = k + 1 if keep else k            # after the captured one, or from the start
            draw(ch, rng).save(FONT / (f"{code}_{n}.png" if n else f"{code}.png"))
    print(f"drew {len(SYMBOLS)} symbols, {VERSIONS} versions each")


if __name__ == "__main__":
    main()
