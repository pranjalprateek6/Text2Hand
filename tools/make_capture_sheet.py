"""Build printable sheets for capturing a handwriting sample.

Each sheet prints one line of the story in light grey, with a ruled line
underneath to write it on. Two things make the scan easy to process later:

  * The guide text is grey and the pen is dark, so the guide can be dropped by
    a brightness threshold and only the handwriting is left.
  * Line N always holds story line N, so every stroke has known text next to
    it. Nothing has to be guessed from the image.

Run from anywhere:   python tools/make_capture_sheet.py
Writes:              sample/capture_1.png ... and sample/capture.pdf
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
STORY = ROOT / "sample" / "story.txt"
OUT = ROOT / "sample"

PAGE = (2480, 3508)          # A4 at 300 dpi
MARGIN_X, MARGIN_TOP = 200, 260
BLOCK = 300                  # vertical space per line: guide, rule, room to write
GUIDE_GREY = (176, 176, 176)
RULE_BLUE = (170, 195, 225)
INK = (40, 44, 60)
GUIDE_SIZE, LABEL_SIZE = 44, 30


def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build() -> list[Image.Image]:
    lines = [l.rstrip() for l in STORY.read_text(encoding="utf-8").splitlines() if l.strip()]
    guide, label = _font(GUIDE_SIZE), _font(LABEL_SIZE)

    per_page = (PAGE[1] - MARGIN_TOP - 200) // BLOCK
    pages: list[Image.Image] = []

    for start in range(0, len(lines), per_page):
        page = Image.new("RGB", PAGE, (255, 255, 255))
        d = ImageDraw.Draw(page)
        sheet_no = len(pages) + 1
        d.text((MARGIN_X, 120), "Text2Hand handwriting sample", font=_font(48), fill=INK)
        d.text((MARGIN_X, 190),
               "Write each line on the rule below it, in your normal print hand. "
               f"Sheet {sheet_no}.",
               font=label, fill=(110, 110, 110))

        y = MARGIN_TOP
        for offset, line in enumerate(lines[start:start + per_page]):
            number = start + offset + 1
            d.text((MARGIN_X - 90, y + 6), f"{number:02d}", font=label, fill=(190, 190, 190))
            d.text((MARGIN_X, y), line, font=guide, fill=GUIDE_GREY)
            rule_y = y + BLOCK - 70
            d.line([(MARGIN_X, rule_y), (PAGE[0] - MARGIN_X, rule_y)],
                   fill=RULE_BLUE, width=3)
            y += BLOCK

        pages.append(page)
    return pages


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pages = build()
    for i, page in enumerate(pages, 1):
        page.save(OUT / f"capture_{i}.png")
    pages[0].save(OUT / "capture.pdf", save_all=True, append_images=pages[1:])
    print(f"wrote {len(pages)} sheet(s) to {OUT}/ (capture_N.png and capture.pdf)")


if __name__ == "__main__":
    main()
