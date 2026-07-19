"""Recover letters missing from the letter sheet by cutting them out of words.

v and w were skipped when the letter sheet was written, so they were the only
two characters still coming from a different hand. Cutting a letter out of
joined writing is normally unreliable, at 43% of words separable, but it is
safe under two conditions that hold here:

  * the word splits into exactly as many shapes as it has letters, so the
    position of each letter is unambiguous, and
  * the result is looked at before it is kept.

Word images are written smaller than the letter sheet, so each salvaged letter
is scaled by the same x-height ratio the renderer uses to mix the two.

Run from anywhere:   python tools/salvage_letters.py
Writes:              myfont/118*.png (v) and myfont/119*.png (w)
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


def main() -> None:
    import text_to_handwriting as t2h
    t2h.derive_metrics()
    scale = t2h.WORD_SCALE or 1.0        # words are written smaller than letters

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

    print(f"scaled by {scale:.2f} to match the letter sheet")


if __name__ == "__main__":
    main()
