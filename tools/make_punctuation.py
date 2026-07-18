"""Compose the missing punctuation glyphs from marks already in myfont/.

A colon is two of the handwritten periods, a semicolon is a period above a
comma, a double quote is two commas, and an apostrophe is a single comma.
Building these from real marks keeps the stroke weight and style identical to
the rest of the handwriting, which drawing them from scratch would not.

The renderer's RAISED rule lifts the quote and apostrophe to the top of the
line, so they only need the right shape here, not the right height. The
renderer also trims every glyph to its ink, so canvas padding does not matter.

Run from anywhere:   python tools/make_punctuation.py
Overwrites:          myfont/34.png  39.png  58.png  59.png
"""
import shutil
import statistics
from pathlib import Path

from PIL import Image

FONT = Path(__file__).resolve().parent.parent / "myfont"


def stamp(code):
    """Tight RGBA crop of a glyph's ink (light background -> transparent)."""
    src = Image.open(FONT / f"{code}.png").convert("RGBA")
    alpha = src.convert("L").point(lambda p: 0 if p >= 240 else 255 - p)
    src.putalpha(alpha)
    bbox = alpha.getbbox()
    return src.crop(bbox) if bbox else src


def canvas(w, h):
    return Image.new("RGB", (w, h), (255, 255, 255))


def main():
    xh = int(statistics.median(stamp(c).height for c in (97, 99, 101, 111, 109, 110)))

    period, comma = stamp(46), stamp(44)
    pw, ph = period.size
    cw, ch = comma.size

    # Colon: two dots stacked within the x-height
    gap, botpad = int(xh * 0.35), int(xh * 0.12)
    im = canvas(pw + 6, ph + gap + ph + botpad)
    x = (im.width - pw) // 2
    im.paste(period, (x, 0), period)
    im.paste(period, (x, ph + gap), period)
    im.save(FONT / "58.png")

    # Semicolon: a dot above a comma
    gap = int(xh * 0.30)
    im = canvas(max(pw, cw) + 6, ph + gap + ch)
    im.paste(period, ((im.width - pw) // 2, 0), period)
    im.paste(comma, ((im.width - cw) // 2, ph + gap), comma)
    im.save(FONT / "59.png")

    # Double quote: two commas side by side
    gap = int(cw * 0.5)
    im = canvas(cw * 2 + gap + 6, ch)
    im.paste(comma, (3, 0), comma)
    im.paste(comma, (3 + cw + gap, 0), comma)
    im.save(FONT / "34.png")

    # Apostrophe: a single comma, kept byte-identical to the source mark
    shutil.copyfile(FONT / "44.png", FONT / "39.png")

    print('wrote 34.png ("), 39.png (\'), 58.png (:), 59.png (;)')


if __name__ == "__main__":
    main()
