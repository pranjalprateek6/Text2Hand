"""Cut a scanned paragraph into one image per word.

Whole words are the part of joined-up handwriting that can be captured
reliably: word gaps are wide and unambiguous, while the letters inside a word
run together and cannot be split (measured at 43% separable). Rendering a
common word from one of these images keeps the writer's real joins, which
assembling it from separate letters cannot.

Alignment is positional, not visual. Line N of the scan holds line N of the
known text, so each word gets its label from the text rather than from
recognition. Any line whose word count disagrees with the text is skipped
rather than guessed at, because a silent mislabel would be permanent.

Run from anywhere:   python tools/extract_words.py
Writes:              wordfont/<word>.png
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SCAN = ROOT / "sample" / "scans" / "para300.png"
OUT = ROOT / "wordfont"

# The paragraph as it was actually laid out on the page, one entry per line.
# Note line 1: the word "a" was skipped when writing, so it is absent here too.
# The count check caught that rather than quietly shifting every later label.
WRITTEN = [
    "I have box of quartz and jade which was",
    "given to him by the old woman at the shop.",
    "She said that they were from a place we",
    "had not seen, and there is no map for it.",
    "He has kept one on his desk as an odd",
    "sign of luck, but all the rest are with",
    "her now. When you look in this drawer",
    "you will find just enough. If not, ask her,",
    "or so I think.",
]

INK_LEVEL = 150
WORD_GAP = 46          # blank columns that separate two words
TINY_INK = 600         # measured: strays are ~7px, punctuation 128-477, "I" and "a" 756+
MARGIN = 8


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


def words_in(band: np.ndarray) -> list[list[int]]:
    """Split a line into words, folding stray punctuation back onto its word."""
    cols = band.sum(axis=0)
    chunks, run, gap = [], None, 0
    for x, v in enumerate(cols):
        if v > 0:
            if run is None:
                run = x
            gap = 0
        else:
            if run is not None:
                gap += 1
                if gap > WORD_GAP:
                    chunks.append([run, x - gap])
                    run = None
    if run is not None:
        chunks.append([run, len(cols)])

    # Ink area, not width, decides what is a word. A comma and the word "a" are
    # both thin and short, but "a" carries several times the ink. Judging by
    # size alone swallowed "a" into the word before it, and because a stray mark
    # elsewhere on the line added one back, the count still matched and the
    # whole line was silently mislabelled.
    # Each entry is [start, end of the word, end including any punctuation].
    # The punctuation counts towards this being one word, but is left out of the
    # crop: an image of "luck," would otherwise draw its comma a second time
    # when the renderer adds the one from the text.
    out: list[list[int]] = []
    for c in chunks:
        area = int(band[:, c[0]:c[1] + 1].sum())
        if area >= TINY_INK:
            out.append([c[0], c[1], c[1]])
        elif out:
            out[-1][2] = c[1]
        # else: a speck with nothing to attach to, so drop it
    return out


def main() -> None:
    page = Image.open(SCAN).convert("L")
    grey = np.asarray(page)
    ink = grey < INK_LEVEL
    rows = rows_of(ink)

    OUT.mkdir(parents=True, exist_ok=True)
    saved, skipped = {}, []

    for index, (y0, y1) in enumerate(rows):
        expected = WRITTEN[index].split() if index < len(WRITTEN) else []
        found = words_in(ink[y0:y1])
        if len(found) != len(expected):
            skipped.append(f"line {index + 1}: found {len(found)}, expected {len(expected)}")
            continue

        for word, (x0, x1, _full) in zip(expected, found):
            # Keep the case that was actually written: the image of "She" must
            # not be served up for "she". File names cannot carry that on a
            # case-insensitive filesystem, so an index records it instead.
            label = word.strip(".,!?;:\"'")
            if not label or label in saved:
                continue
            name = f"{len(saved):03d}.png"
            crop = grey[max(0, y0 - MARGIN): y1 + MARGIN,
                        max(0, x0 - MARGIN): x1 + MARGIN]
            clean = np.where(crop > 205, 255, crop).astype(np.uint8)
            Image.fromarray(clean).convert("RGB").save(OUT / name)
            saved[label] = name

    (OUT / "index.json").write_text(json.dumps(saved, indent=1), encoding="utf-8")
    print(f"wrote {len(saved)} word images to {OUT.name}/")
    print(" ", " ".join(sorted(saved, key=str.lower)))
    for note in skipped:
        print("  SKIPPED", note)


if __name__ == "__main__":
    main()
