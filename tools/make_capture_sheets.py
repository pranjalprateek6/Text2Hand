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
    items = [w for w in (OUT / list_name).read_text(encoding="utf-8").split() if w]
    return build(items, cols=3, rows=9, cell_h=330,
                 title="Text2Hand word sample",
                 hint="Write each word once on the line, in your normal joined hand.",
                 guide_size=40, baseline_at=0.72)


def letters() -> list[Image.Image]:
    items: list[str] = []
    for ch in "abcdefghijklmnopqrstuvwxyz":
        items += [ch] * 3                       # three each, for real variants
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        items += [ch] * 2
    for ch in "0123456789":
        items += [ch] * 2
    for ch in ".,!?'\"()-:;":
        items += [ch] * 2
    return build(items, cols=8, rows=11, cell_h=270,
                 title="Text2Hand letter sample",
                 hint="One character per box, centred on the line. Do not join them.",
                 guide_size=34, baseline_at=0.68)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("writing capture sheets to", OUT)
    # Start with the short list. The full one is the same thing at scale, worth
    # writing only once a rendered page shows the approach reads as one hand.
    save(words("words_short.txt"), "words_short")
    save(words("words_full.txt"), "words_full")
    save(letters(), "letters")


if __name__ == "__main__":
    main()
