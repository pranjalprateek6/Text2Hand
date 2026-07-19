"""Recover letters the letter sheet did not capture well, from elsewhere in the
same hand.

Two cases needed this:

  * v and w were skipped when the letter sheet was written, so they were the
    only two still coming from a different hand.
  * x was written on the sheet as a single curl that reads as a u. There is no
    crossed lowercase x anywhere in the captured writing, so it is taken from
    the capital X and scaled down to x-height instead. That is a real shape
    from the same hand, and a lowercase letter that reads as a different one
    is worse than a slightly formal x.

f looked like it belonged here too, since it read as a J. It did not: the sheet
glyph was right and the renderer was placing it wrong, sitting its tail on the
rule instead of below it. Adding f to DESCENDERS fixed it. The crossed f in the
word "for" was the obvious salvage source and is a worse glyph, because the word
image clips its tail at the bottom edge.

Cutting a letter out of joined writing is normally unreliable, at 43% of words
separable, but it is safe under two conditions that hold here:

  * the word splits into exactly as many shapes as it has letters, so the
    position of each letter is unambiguous, and
  * the result is looked at before it is kept.

Word images and glyph files are written at different sizes, so both sources are
rescaled onto the glyph files' own x-height. Everything here writes glyph files
at their stored size, before the renderer applies GLYPH_SCALE.

Run from anywhere:   python tools/salvage_letters.py
Writes:              myfont/118*.png (v), 119*.png (w), 120.png (x)
"""
from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORDS = ROOT / "wordfont"
FONT = ROOT / "myfont"

# (word, which letter of it, what that letter is). Order matters: the first
# entry for a letter becomes the default glyph, the rest become variants.
SALVAGE = [
    ("we", 0, "w"), ("was", 0, "w"), ("will", 0, "w"), ("woman", 0, "w"),
    ("have", 2, "v"), ("given", 2, "v"),
]

# Lowercase letters taken from their capital, scaled to x-height. Only for
# letters whose two cases are the same shape, where the sheet's lowercase is
# unusable.
FROM_CAPITAL = [("x", "X")]

INK_LEVEL = 150
MIN_BLOB = 60
JOIN_GAP = 6           # tighter than the letter sheet: these letters nearly touch
MARGIN = 6
PAPER = 205


def blobs(mask: np.ndarray) -> list[list[int]]:
    h, w = mask.shape
    seen = np.zeros_like(mask, bool)
    found = []
    for y in range(h):
        for x in range(w):
            if mask[y, x] and not seen[y, x]:
                queue = deque([(y, x)])
                seen[y, x] = True
                xs, ys, n = [x], [y], 0
                while queue:
                    cy, cx = queue.popleft()
                    n += 1
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            ny, nx = cy + dy, cx + dx
                            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                                seen[ny, nx] = True
                                queue.append((ny, nx))
                                xs.append(nx)
                                ys.append(ny)
                if n >= MIN_BLOB:
                    found.append([min(xs), min(ys), max(xs), max(ys)])
    return sorted(found, key=lambda b: b[0])


def join_near(boxes: list[list[int]]) -> list[list[int]]:
    if not boxes:
        return []
    merged = [list(boxes[0])]
    for box in boxes[1:]:
        last = merged[-1]
        if box[0] - last[2] < JOIN_GAP:
            last[0], last[1] = min(last[0], box[0]), min(last[1], box[1])
            last[2], last[3] = max(last[2], box[2]), max(last[3], box[3])
        else:
            merged.append(list(box))
    return merged


def ink_height(image: Image.Image, threshold: int) -> int:
    grey = image.convert("L")
    box = grey.point(lambda p: 0 if p >= threshold else 255 - p).getbbox()
    return (box[3] - box[1]) if box else 0


def x_height_of_files(t2h, skip: str) -> int:
    """Median x-height of the stored glyph files, ignoring one letter.

    The letter being replaced has to be left out, or a bad glyph helps set the
    size its own replacement is measured against.
    """
    heights = []
    for letter in "acemnorsuvwz":
        if letter == skip:
            continue
        path = FONT / f"{ord(letter)}.png"
        if path.exists():
            heights.append(ink_height(Image.open(path), t2h.INK_THRESHOLD))
    heights = sorted(h for h in heights if h)
    return heights[len(heights) // 2] if heights else 1


def main() -> None:
    import text_to_handwriting as t2h
    t2h.derive_metrics()
    # Word images and glyph files are written at different sizes. Each is
    # rendered at its own scale, so the ratio of the two converts between them.
    scale = (t2h.WORD_SCALE or 1.0) / (t2h.GLYPH_SCALE or 1.0)

    index = json.loads((WORDS / "index.json").read_text(encoding="utf-8"))
    seen: dict[str, int] = {}

    for word, position, letter in SALVAGE:
        if word not in index:
            print(f"  skip {word}: not captured")
            continue
        grey = np.asarray(Image.open(WORDS / index[word]).convert("L"))
        shapes = join_near(blobs(grey < INK_LEVEL))
        if len(shapes) != len(word):
            print(f"  skip {word}: {len(shapes)} shapes for {len(word)} letters")
            continue

        x0, y0, x1, y1 = shapes[position]
        crop = grey[max(0, y0 - MARGIN):y1 + MARGIN, max(0, x0 - MARGIN):x1 + MARGIN]
        clean = np.where(crop > PAPER, 255, crop).astype(np.uint8)
        image = Image.fromarray(clean).convert("RGB")
        image = image.resize((max(1, round(image.width * scale)),
                             max(1, round(image.height * scale))), Image.LANCZOS)

        n = seen.get(letter, 0)
        name = f"{ord(letter)}.png" if n == 0 else f"{ord(letter)}_{n}.png"
        image.save(FONT / name)
        seen[letter] = n + 1
        print(f"  {letter} from {word!r} -> myfont/{name}")

    print(f"words scaled by {scale:.2f} to match the letter sheet")

    for letter, capital in FROM_CAPITAL:
        source = FONT / f"{ord(capital)}.png"
        if not source.exists():
            print(f"  skip {letter}: no capital {capital}")
            continue
        image = Image.open(source).convert("RGB")
        target = x_height_of_files(t2h, skip=letter)
        have = ink_height(image, t2h.INK_THRESHOLD)
        if not have:
            print(f"  skip {letter}: capital {capital} has no ink")
            continue
        ratio = target / have
        image = image.resize((max(1, round(image.width * ratio)),
                              max(1, round(image.height * ratio))), Image.LANCZOS)
        image.save(FONT / f"{ord(letter)}.png")
        print(f"  {letter} from capital {capital} -> myfont/{ord(letter)}.png "
              f"(scaled {ratio:.2f} to x-height {target})")


if __name__ == "__main__":
    main()
