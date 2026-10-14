"""Check that a photo of a filled-in capture sheet reads back correctly.

A sheet is printed from make_capture_sheets, written on with the bundled
glyphs (standing in for a person's pen), then photographed badly in software:
turned, shrunk, seen at an angle, on a grey desk, lit unevenly and saved as a
JPEG. extract_sheet must find every letter in its own cell, and each one must
look like the glyph that was written there.

Used by regression.py; also runs on its own and saves the fake photo:
    python tests/capture_check.py
"""
from __future__ import annotations

import io
import os
import sys

import numpy as np
from PIL import Image, ImageChops, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import extract_sheet  # noqa: E402
import make_capture_sheets as sheets  # noqa: E402

FONT = os.path.join(ROOT, "myfont")


def _source(ch: str) -> Image.Image:
    return Image.open(os.path.join(FONT, f"{ord(ch)}.png")).convert("L")


def filled_sheet(name: str = "letters_min") -> Image.Image:
    """The printed sheet with each cell written in, by the bundled hand."""
    page = {"letters_min": sheets.letters_min, "letters": sheets.letters}[name]()[0].convert("L")
    spec = sheets.SHEETS[name]
    for ch, (x0, y0, x1, y1) in sheets.layout(name)[0]:
        glyph = _source(ch)
        # about 45% of the cell's height for a capital, sat on the cell's rule
        scale = (y1 - y0) * 0.45 / max(glyph.height, 60)
        glyph = glyph.resize((max(1, int(glyph.width * scale)), max(1, int(glyph.height * scale))))
        rule = y0 - 4 + int(spec["cell_h"] * spec["baseline_at"])
        x = x0 + (x1 - x0 - glyph.width) // 2
        y = rule - glyph.height
        region = page.crop((x, y, x + glyph.width, y + glyph.height))
        page.paste(ImageChops.darker(region, glyph), (x, y))
    return page


def photograph(page: Image.Image) -> Image.Image:
    """A poor phone photo of the page: angle, turn, scale, desk, shading, JPEG."""
    w, h = page.size
    small = page.resize((int(w * 0.7), int(h * 0.7)), Image.LANCZOS)
    desk = Image.new("L", (int(w * 0.9), int(h * 0.88)), 120)
    turned = small.rotate(4.5, resample=Image.BICUBIC, expand=True, fillcolor=120)
    desk.paste(turned, (60, 40))
    dw, dh = desk.size
    # keystone: the top of the page further from the camera than the bottom
    coeffs = extract_sheet._perspective(
        [(0, 0), (dw, 0), (dw, dh), (0, dh)],
        [(-40, -10), (dw + 25, 15), (dw - 10, dh + 5), (15, dh - 20)])
    shot = desk.transform(desk.size, Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=120)
    # light falling off across the page
    shade = np.linspace(1.0, 0.78, dw)[None, :] * np.linspace(1.0, 0.9, dh)[:, None]
    shot = Image.fromarray(np.clip(np.asarray(shot, float) * shade, 0, 255).astype(np.uint8))
    shot = shot.filter(ImageFilter.GaussianBlur(0.8))
    buf = io.BytesIO()
    shot.save(buf, "JPEG", quality=80)
    return Image.open(io.BytesIO(buf.getvalue()))


def _shape(im: Image.Image, size: int = 32) -> np.ndarray:
    """A glyph reduced to a small ink mask, centred on its own bounding box."""
    a = np.asarray(im.convert("L"))
    ys, xs = np.nonzero(a < 150)
    if not len(xs):
        return np.zeros((size, size), bool)
    crop = Image.fromarray(a[ys.min():ys.max() + 1, xs.min():xs.max() + 1])
    return np.asarray(crop.resize((size, size), Image.BILINEAR)) < 170


def _overlap(a: np.ndarray, b: np.ndarray) -> float:
    union = (a | b).sum()
    return (a & b).sum() / union if union else 1.0


def run(save_to: str | None = None) -> list[tuple[str, bool, str]]:
    """The checks, as (name, passed, detail)."""
    page = filled_sheet()
    shot = photograph(page)
    if save_to:
        shot.save(save_to)
    out = []
    try:
        found, empty = extract_sheet.extract(shot, "letters_min")
    except extract_sheet.SheetError as exc:
        return [("a phone photo of a sheet is read", False, str(exc))]
    expected = [ch for ch, _ in sheets.layout("letters_min")[0]]
    labels = [ch for ch, _ in found]
    out.append(("a phone photo of a sheet is read",
                labels == expected and not empty,
                f"{len(found)} of {len(expected)} cells" + (f", empty {empty}" if empty else "")))
    # each cut-out must be the glyph written in that cell, not its neighbour's
    scores = {ch: _overlap(_shape(im), _shape(_source(ch))) for ch, im in found}
    worst = min(scores, key=scores.get) if scores else "-"
    mean = float(np.mean(list(scores.values()))) if scores else 0.0
    out.append(("each cell holds the letter written there",
                mean > 0.6 and scores.get(worst, 0) > 0.35,
                f"mean overlap {mean:.2f}, lowest {worst!r} {scores.get(worst, 0):.2f}"))

    # a picture with no sheet in it is refused rather than misread
    try:
        extract_sheet.extract(Image.new("L", (1200, 1600), 200), "letters_min")
        out.append(("a picture without the corner squares is refused", False, "was read"))
    except extract_sheet.SheetError:
        out.append(("a picture without the corner squares is refused", True, ""))
    return out


if __name__ == "__main__":
    results = run(os.path.join(ROOT, "out", "capture_photo.jpg")
                  if os.path.isdir(os.path.join(ROOT, "out")) else None)
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)
