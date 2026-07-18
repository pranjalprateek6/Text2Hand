"""Cut a scanned letter sheet into one glyph image per character.

The sheet is rows of separated characters in a known order, so nothing has to
be recognised: find the rows, find the shapes, and read the labels off the
expected text. That only works because the characters do not touch, which is
why the sheet uses separated letters rather than prose. Joined-up writing was
measured at 43% separable and is not usable this way.

Two details do the real work:

  * Shapes closer than JOIN_GAP are one character. A dot sits directly above
    its stem, so i, j and the punctuation marks arrive as two shapes with a
    negative horizontal gap, while genuinely separate characters sit 78px or
    more apart at 300 dpi.
  * Each glyph is saved as its own pixels on white, not as a mask, so the pen
    texture survives. The renderer builds its own alpha from ink darkness.

Run from anywhere:   python tools/extract_glyphs.py
Writes:              myfont_new/<ascii-code>.png
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SCAN = ROOT / "sample" / "scans" / "letters300.png"
OUT = ROOT / "myfont_new"

# What is on the sheet, row by row, in the order it was written.
ROWS = [
    "abcdefghijklmn",
    "opqxyzurst",              # not alphabetical, and v and w are absent
    "ABCDEFGHIJKLMNO",
    "PQRSTUVWXYZ",
    "1234567890.,!?'",
    '"()-:;',
]

INK_LEVEL = 150        # darker than this counts as ink
MIN_BLOB = 30          # ignore specks
JOIN_GAP = 40          # shapes closer than this belong to one character
MARGIN = 6             # padding kept around a saved glyph
PAPER = 205            # lighter than this is flattened to white


def rows_of(ink: np.ndarray, frac: float = 0.12, min_h: int = 40) -> list[tuple[int, int]]:
    profile = np.convolve(ink.sum(axis=1).astype(float), np.ones(21) / 21, mode="same")
    limit = profile.max() * frac
    out, run = [], None
    for y, v in enumerate(profile):
        if v > limit and run is None:
            run = y
        elif v <= limit and run is not None:
            if y - run > min_h:
                out.append((run, y))
            run = None
    if run is not None and len(profile) - run > min_h:
        out.append((run, len(profile)))
    return out


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
    """Fold shapes that are really one character (a stem and its dot) together."""
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
    page = Image.open(SCAN).convert("L")
    grey = np.asarray(page)
    ink = grey < INK_LEVEL

    OUT.mkdir(parents=True, exist_ok=True)
    written, problems = {}, []

    for index, (y0, y1) in enumerate(rows_of(ink)):
        expected = ROWS[index] if index < len(ROWS) else ""
        chars = join_near(blobs(ink[y0:y1]))
        if len(chars) != len(expected):
            problems.append(f"row {index + 1}: found {len(chars)}, expected {len(expected)} ({expected})")
            continue

        for ch, (x0, cy0, x1, cy1) in zip(expected, chars):
            # cy0 and cy1 are relative to the row band, so both offset from y0.
            # Using y1 here would run the crop into the row below.
            crop = grey[max(0, y0 + cy0 - MARGIN): y0 + cy1 + MARGIN,
                        max(0, x0 - MARGIN): x1 + MARGIN]
            clean = np.where(crop > PAPER, 255, crop).astype(np.uint8)
            Image.fromarray(clean).convert("RGB").save(OUT / f"{ord(ch)}.png")
            written[ch] = clean.shape

    print(f"wrote {len(written)} glyphs to {OUT.name}/")
    for note in problems:
        print("  PROBLEM", note)
    missing = [c for c in "abcdefghijklmnopqrstuvwxyz" if c not in written]
    print("  lowercase not on the sheet:", " ".join(missing) or "none")


if __name__ == "__main__":
    main()
