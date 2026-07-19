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
MARK_GAP = 10          # blank columns that can still separate a mark from its word
# Measured on the trailing shape of every captured word: stray marks carry
# 44-165 ink and stand 7-12px tall, while a trailing letter carries 400-954 and
# stands 30-51px. These sit in the gap between the two, well clear of either.
MARK_INK = 250
MARK_HEIGHT = 0.20     # of the row band; real letters measured 0.29 and up
# A comma is not small enough for those limits: it carries 319 ink and stands
# 23px, inside the range of a letter. What gives it away is where it sits. It
# hangs under the writing, starting at 0.74 of the band, while the lowest any
# trailing letter started was 0.46.
MARK_TOP = 0.55
MARK_TOP_INK = 500     # a comma measured 319, the smallest trailing letter 510


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


def trim_trailing_mark(band: np.ndarray, start: int, end: int) -> int:
    """Pull a word's end back past a full stop written tight against it.

    Splitting on gap width alone missed these. A full stop sits nearer to its
    word than WORD_GAP, so it stayed inside the chunk and was cropped into the
    word image, which then drew a stray dot every time that word was used: "it"
    rendered as "it." wherever it appeared. What tells a mark from a letter is
    not how far away it sits but how small it is, so this measures the shape.
    """
    cols = band[:, start:end + 1].sum(axis=0)
    x = len(cols) - 1
    while x >= 0 and cols[x] == 0:      # trailing blanks, if any
        x -= 1
    tail_end = x
    while x >= 0 and cols[x] > 0:       # the last inked run
        x -= 1
    tail_start = x + 1
    gap = 0
    while x >= 0 and cols[x] == 0:      # the blanks before it
        gap += 1
        x -= 1

    if x < 0 or gap < MARK_GAP:         # nothing detached ahead of it
        return end
    piece = band[:, start + tail_start: start + tail_end + 1]
    rows = np.where(piece.any(axis=1))[0]
    if not len(rows):
        return end
    height = rows[-1] - rows[0] + 1
    ink = int(piece.sum())
    tall = band.shape[0]
    small = ink < MARK_INK and height < MARK_HEIGHT * tall          # dots, dashes
    hanging = rows[0] > MARK_TOP * tall and ink < MARK_TOP_INK      # commas
    if small or hanging:
        # Where the word's own ink stops, not where the gap before the mark
        # stops: the crop pads by MARGIN, which would otherwise reach straight
        # back over the gap and pull the mark in again.
        return start + x
    return end


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
            out.append([c[0], trim_trailing_mark(band, c[0], c[1]), c[1]])
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
