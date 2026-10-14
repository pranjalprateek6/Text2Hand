"""Build printable sheets for capturing handwriting.

Two sheets, because measuring a real sample showed that letters cannot be cut
out of joined-up writing: only 43% of words separated into their letters, while
words themselves separated 42 times out of 42. Both sheets below rely only on
the part that works.

  words    one word per cell, written normally, so the joins inside a common
           word are captured exactly as they are written. The commonest few
           hundred words cover most of any page.
  letters  one character per box, because a box forces separation. Used for
           everything the word list does not cover.

Every cell is a known rectangle holding known content, so extraction never has
to guess: crop the cell, drop the grey guide by brightness, keep the ink.

Each sheet carries a solid square in every corner. A scan or a phone photo is
never the printed page exactly: it is shifted, scaled, turned and seen at an
angle. The four squares are found in the picture and the picture is mapped
back onto the printed layout, after which every cell is where `layout()` says
it is. That is what lets a photo of any of these sheets be read, rather than
only the one scan the extraction was first written against.

Run from anywhere:   python tools/make_capture_sheets.py
Writes:              sample/words_N.png, sample/letters_N.png, and PDFs
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "sample"

PAGE = (2480, 3508)                  # A4 at 300 dpi
MARGIN_X, MARGIN_TOP, MARGIN_BOT = 150, 300, 140

GUIDE = (198, 198, 198)              # grey enough to drop by threshold
BORDER = (228, 228, 228)
BASELINE = (170, 195, 225)
INK = (40, 44, 60)
MUTED = (120, 120, 120)

MARK = 70                            # registration square, px at 300 dpi
MARK_INSET = 50                      # from the page edge to the square
# centres of the four squares: top left, top right, bottom right, bottom left
MARKS = [
    (MARK_INSET + MARK // 2, MARK_INSET + MARK // 2),
    (PAGE[0] - MARK_INSET - MARK // 2, MARK_INSET + MARK // 2),
    (PAGE[0] - MARK_INSET - MARK // 2, PAGE[1] - MARK_INSET - MARK // 2),
    (MARK_INSET + MARK // 2, PAGE[1] - MARK_INSET - MARK // 2),
]

# What each sheet holds and how it is laid out, shared with the extractor so
# the two can never disagree about where a cell is.
SHEETS = {
    "letters_min": dict(cols=8, rows=10, cell_h=290, baseline_at=0.68),
    "letters": dict(cols=8, rows=11, cell_h=270, baseline_at=0.68),
    "words_short": dict(cols=3, rows=9, cell_h=330, baseline_at=0.72),
    "words_full": dict(cols=3, rows=9, cell_h=330, baseline_at=0.72),
}


def sheet_items(name: str) -> list[str]:
    """The cell contents of a sheet, in writing order."""
    if name == "letters_min":
        return list("abcdefghijklmnopqrstuvwxyz"
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    "0123456789" ".,!?'\"()-:;")
    if name == "letters":
        items: list[str] = []
        for ch in "abcdefghijklmnopqrstuvwxyz":
            items += [ch] * 3                   # three each, for real variants
        for group in ("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789", ".,!?'\"()-:;"):
            for ch in group:
                items += [ch] * 2
        return items
    if name in ("words_short", "words_full"):
        return [w for w in (OUT / f"{name}.txt").read_text(encoding="utf-8").split() if w]
    raise ValueError(f"no sheet called {name!r}; choose from {', '.join(SHEETS)}")


def layout(name: str) -> list[list[tuple[str, tuple[int, int, int, int]]]]:
    """Per page, each cell's content and its writing area (x0, y0, x1, y1).

    The writing area is the cell inside its border, which is exactly where
    the pen goes and nothing printed but the light guide and rule.
    """
    spec = SHEETS[name]
    cols, rows, cell_h = spec["cols"], spec["rows"], spec["cell_h"]
    cell_w = (PAGE[0] - 2 * MARGIN_X) // cols
    items = sheet_items(name)
    per_page = cols * rows
    pages = []
    for start in range(0, len(items), per_page):
        cells = []
        for index, item in enumerate(items[start:start + per_page]):
            cx = MARGIN_X + (index % cols) * cell_w
            cy = MARGIN_TOP + (index // cols) * cell_h
            cells.append((item, (cx + 4, cy + 4, cx + cell_w - 16, cy + cell_h - 16)))
        pages.append(cells)
    return pages


def _marks(d: ImageDraw.ImageDraw) -> None:
    for x, y in MARKS:
        d.rectangle([x - MARK // 2, y - MARK // 2, x + MARK // 2, y + MARK // 2], fill=(0, 0, 0))


def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build(cells: list[str], cols: int, rows: int, cell_h: int,
          title: str, hint: str, guide_size: int, baseline_at: float) -> list[Image.Image]:
    """Lay out `cells` on as many pages as needed, one item per cell."""
    cell_w = (PAGE[0] - 2 * MARGIN_X) // cols
    per_page = cols * rows
    pages: list[Image.Image] = []

    for start in range(0, len(cells), per_page):
        page = Image.new("RGB", PAGE, (255, 255, 255))
        d = ImageDraw.Draw(page)
        _marks(d)
        d.text((MARGIN_X, 120), title, font=_font(52), fill=INK)
        d.text((MARGIN_X, 196), f"{hint}  Sheet {len(pages) + 1}.",
               font=_font(30), fill=MUTED)

        for index, item in enumerate(cells[start:start + per_page]):
            cx = MARGIN_X + (index % cols) * cell_w
            cy = MARGIN_TOP + (index // cols) * cell_h
            d.rectangle([cx, cy, cx + cell_w - 12, cy + cell_h - 12],
                        outline=BORDER, width=2)
            d.text((cx + 16, cy + 12), item, font=_font(guide_size), fill=GUIDE)
            rule = cy + int(cell_h * baseline_at)
            d.line([(cx + 16, rule), (cx + cell_w - 28, rule)], fill=BASELINE, width=3)

        pages.append(page)
    return pages


def save(pages: list[Image.Image], stem: str) -> None:
    for i, page in enumerate(pages, 1):
        page.save(OUT / f"{stem}_{i}.png")
    pages[0].save(OUT / f"{stem}.pdf", save_all=True, append_images=pages[1:])
    print(f"  {stem}: {len(pages)} sheet(s)")


def words(list_name: str = "words_short.txt") -> list[Image.Image]:
    name = list_name.removesuffix(".txt")
    spec = SHEETS[name]
    return build(sheet_items(name), cols=spec["cols"], rows=spec["rows"], cell_h=spec["cell_h"],
                 title="Text2Hand word sample",
                 hint="Write each word once on the line, in your normal joined hand.",
                 guide_size=40, baseline_at=spec["baseline_at"])


def paragraph() -> list[Image.Image]:
    """One page: write the paragraph, one line per rule.

    Prose cannot give up its letters in a joined hand, but it gives up its
    words, and this paragraph holds 49 of the 50 commonest words.
    """
    text = (OUT / "paragraph.txt").read_text(encoding="utf-8")
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    page = Image.new("RGB", PAGE, (255, 255, 255))
    d = ImageDraw.Draw(page)
    _marks(d)
    d.text((MARGIN_X, 120), "Text2Hand paragraph sample", font=_font(52), fill=INK)
    d.text((MARGIN_X, 196),
           "Write each line on the rule below it, in your normal joined hand. "
           "Leave clear gaps between words.", font=_font(30), fill=MUTED)

    y = MARGIN_TOP + 40
    block = 340
    for n, line in enumerate(lines, 1):
        d.text((MARGIN_X - 80, y + 8), f"{n}", font=_font(30), fill=(200, 200, 200))
        d.text((MARGIN_X, y), line, font=_font(42), fill=GUIDE)
        rule = y + block - 90
        d.line([(MARGIN_X, rule), (PAGE[0] - MARGIN_X, rule)], fill=BASELINE, width=3)
        y += block
    return [page]


def letters_min() -> list[Image.Image]:
    """One page: a single sample of each character, the smallest useful set."""
    spec = SHEETS["letters_min"]
    return build(sheet_items("letters_min"), cols=spec["cols"], rows=spec["rows"],
                 cell_h=spec["cell_h"],
                 title="Text2Hand letter sample",
                 hint="One character per box, sitting on the line. Do not join them.",
                 guide_size=34, baseline_at=spec["baseline_at"])


def letters() -> list[Image.Image]:
    spec = SHEETS["letters"]
    return build(sheet_items("letters"), cols=spec["cols"], rows=spec["rows"],
                 cell_h=spec["cell_h"],
                 title="Text2Hand letter sample",
                 hint="One character per box, centred on the line. Do not join them.",
                 guide_size=34, baseline_at=spec["baseline_at"])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("writing capture sheets to", OUT)
    print(" the two-page minimum:")
    save(paragraph(), "paragraph")
    save(letters_min(), "letters_min")
    print(" larger optional sets:")
    save(words("words_short.txt"), "words_short")
    save(words("words_full.txt"), "words_full")
    save(letters(), "letters")


if __name__ == "__main__":
    main()
