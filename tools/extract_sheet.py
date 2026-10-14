"""Read a filled-in capture sheet, from a scan or a phone photo.

    python tools/extract_sheet.py photo.jpg --sheet letters_min
    python tools/extract_sheet.py scan.png --sheet letters --page 2
    python tools/extract_sheet.py scan.png --sheet words_short --page 1

The sheets from make_capture_sheets.py carry a solid square in each corner.
They are found in the picture, the picture is mapped back onto the printed
layout (which undoes the shift, scale, turn and angle of a photo), and then
every cell is cut out at the position the layout gives it. Nothing is
recognised and nothing is counted: a cell's label is what was printed in it.

Ink is told from paper relative to each cell's own paper, not by a fixed
level, so a photo lit unevenly still reads. The printed guide letter and the
blue rule are lighter than any pen and drop out with the paper.

Results are staged, never written over the working hand:
  letters  ->  myfont_new/<code>.png, <code>_1.png ... (several of a letter
               become variants)
  words    ->  wordfont_new/<n>.png and wordfont_new/index.json
A review.png contact sheet is written beside them. Look it over, then copy
what you keep into myfont/ or wordfont/.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_capture_sheets as sheets  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

DARK = 0.45            # of the paper level: darker than this is a mark or ink
INK = 0.62             # of a cell's paper level: darker than this is pen
MIN_INK = 40           # pixels: a cell with less ink than this is empty
MARGIN = 8             # padding kept around a saved glyph


class SheetError(Exception):
    """The picture could not be read as the sheet asked for."""


def _components(mask: np.ndarray, min_size: int = 1) -> list[tuple[int, int, int, int, int]]:
    """Connected shapes in a mask, as (x0, y0, x1, y1, pixel_count)."""
    h, w = mask.shape
    seen = np.zeros_like(mask, bool)
    found = []
    ys, xs = np.nonzero(mask)
    for y, x in zip(ys, xs):
        if seen[y, x]:
            continue
        queue = deque([(y, x)])
        seen[y, x] = True
        x0 = x1 = x
        y0 = y1 = y
        n = 0
        while queue:
            cy, cx = queue.popleft()
            n += 1
            x0, x1, y0, y1 = min(x0, cx), max(x1, cx), min(y0, cy), max(y1, cy)
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        if n >= min_size:
            found.append((x0, y0, x1, y1, n))
    return found


def find_marks(grey: np.ndarray) -> list[tuple[float, float]]:
    """Centres of the four corner squares: top left, top right, bottom right, bottom left.

    Searched at a reduced size for speed, then each centre is taken as the
    centroid of the dark pixels around it at full size.
    """
    h, w = grey.shape
    step = max(1, round(max(h, w) / 900))
    small = grey[::step, ::step].astype(float)
    paper = np.percentile(small, 90)
    mask = small < paper * DARK
    sh, sw = mask.shape
    side = min(sh, sw)

    candidates = []
    for x0, y0, x1, y1, n in _components(mask, min_size=12):
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if not (0.012 * side <= bw <= 0.09 * side and 0.012 * side <= bh <= 0.09 * side):
            continue
        if not 0.6 <= bw / bh <= 1.6:
            continue                          # a square seen at an angle, not a line
        if n / (bw * bh) < 0.7:
            continue                          # solid, unlike any letter
        candidates.append(((x0 + x1) / 2, (y0 + y1) / 2, bw, bh))
    if len(candidates) < 4:
        raise SheetError(f"found {len(candidates)} of the 4 corner squares; keep all four "
                         "in the picture, flat and in focus")

    corners = [(0, 0), (sw, 0), (sw, sh), (0, sh)]
    chosen = []
    for cx, cy in corners:
        best = min(candidates, key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)
        chosen.append(best)
    if len({(c[0], c[1]) for c in chosen}) < 4:
        raise SheetError("could not tell the four corner squares apart")

    centres = []
    for x, y, bw, bh in chosen:
        # refine at full resolution: the dark pixels in a window around it
        fx, fy = x * step, y * step
        r = int(max(bw, bh) * step)
        x0, x1 = int(max(0, fx - r)), int(min(w, fx + r))
        y0, y1 = int(max(0, fy - r)), int(min(h, fy + r))
        win = grey[y0:y1, x0:x1].astype(float)
        dark = win < np.percentile(grey, 90) * DARK
        if dark.any():
            yy, xx = np.nonzero(dark)
            fx, fy = x0 + xx.mean(), y0 + yy.mean()
        centres.append((fx, fy))
    return centres


def _perspective(template: list[tuple[float, float]],
                 picture: list[tuple[float, float]]) -> list[float]:
    """Coefficients mapping template points to picture points, for Image.transform."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(template, picture):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs += [u, v]
    return list(np.linalg.solve(np.array(rows, float), np.array(rhs, float)))


def flatten(picture: Image.Image) -> Image.Image:
    """The picture mapped back onto the printed page, as greyscale."""
    grey = ImageOps.exif_transpose(picture).convert("L")
    marks = find_marks(np.asarray(grey))
    coeffs = _perspective(sheets.MARKS, marks)
    return grey.transform(sheets.PAGE, Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=255)


def cut_cell(page: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray | None:
    """The ink in one cell, cropped and cleaned onto white, or None if empty."""
    x0, y0, x1, y1 = box
    cell = page[y0:y1, x0:x1].astype(float)
    paper = max(1.0, float(np.percentile(cell, 75)))
    mask = cell < paper * INK

    # keep only real strokes: specks of dust and grain are dropped, while the
    # dot of an i, a full stop and a comma are well above this size
    keep = np.zeros_like(mask)
    for bx0, by0, bx1, by1, _n in _components(mask, min_size=12):
        keep[by0:by1 + 1, bx0:bx1 + 1] |= mask[by0:by1 + 1, bx0:bx1 + 1]
    if keep.sum() < MIN_INK:
        return None
    ys, xs = np.nonzero(keep)
    top, bottom = max(0, ys.min() - MARGIN), min(cell.shape[0], ys.max() + MARGIN + 1)
    left, right = max(0, xs.min() - MARGIN), min(cell.shape[1], xs.max() + MARGIN + 1)
    # Everything that is not pen goes to white: the printed rule under the
    # letter and the guide beside it are lighter than ink but darker than
    # paper, and left in they would be drawn under every copy of the glyph.
    # The strokes keep a two pixel fringe for their soft edges.
    near = keep.copy()
    for _ in range(2):
        grown = near.copy()
        grown[1:] |= near[:-1]
        grown[:-1] |= near[1:]
        grown[:, 1:] |= near[:, :-1]
        grown[:, :-1] |= near[:, 1:]
        near = grown
    cell = np.where(near, cell, 255.0)
    crop = cell[top:bottom, left:right] * (255.0 / paper)
    crop = np.where(crop > 255 * 0.8, 255, crop)        # paper to clean white
    return np.clip(crop, 0, 255).astype(np.uint8)


def extract(picture: Image.Image, sheet: str, page: int = 1) -> tuple[list, list[str]]:
    """Cut a picture of `sheet` into (label, image) pairs; also the empty cells."""
    pages = sheets.layout(sheet)
    if not 1 <= page <= len(pages):
        raise SheetError(f"{sheet} has {len(pages)} page(s), not {page}")
    flat = np.asarray(flatten(picture))
    found, empty = [], []
    for label, box in pages[page - 1]:
        glyph = cut_cell(flat, box)
        if glyph is None:
            empty.append(label)
        else:
            found.append((label, Image.fromarray(glyph)))
    return found, empty


def _review(found: list, path: Path) -> None:
    """A contact sheet of everything cut out, labelled, for checking by eye."""
    cols, size = 10, 120
    rows = max(1, -(-len(found) // cols))
    sheet = Image.new("L", (cols * size, rows * (size + 24)), 255)
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(found):
        x, y = (i % cols) * size, (i // cols) * (size + 24)
        thumb = im.copy()
        thumb.thumbnail((size - 12, size - 12))
        sheet.paste(thumb, (x + (size - thumb.width) // 2, y + 4))
        d.text((x + 6, y + size + 4), label, fill=0)
    sheet.save(path)


def save(found: list, sheet: str, out: Path | None = None) -> Path:
    words = sheet.startswith("words")
    out = out or ROOT / ("wordfont_new" if words else "myfont_new")
    out.mkdir(parents=True, exist_ok=True)
    if words:
        index_path = out / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        start = len(index)
        for n, (label, im) in enumerate(found, start):
            name = f"{n:03d}.png"
            im.convert("RGB").save(out / name)
            index[label] = name
        index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    else:
        for label, im in found:
            code = ord(label)
            name, k = f"{code}.png", 1
            while (out / name).exists():               # a second sample is a variant
                name, k = f"{code}_{k}.png", k + 1
            im.convert("RGB").save(out / name)
    _review(found, out / "review.png")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("picture", help="scan or photo of a filled-in sheet")
    ap.add_argument("--sheet", required=True, choices=sorted(sheets.SHEETS),
                    help="which sheet the picture is of")
    ap.add_argument("--page", type=int, default=1, help="page of that sheet (default 1)")
    ap.add_argument("--out", type=Path, help="folder to stage into")
    args = ap.parse_args()
    try:
        found, empty = extract(Image.open(args.picture), args.sheet, args.page)
    except SheetError as exc:
        sys.exit(f"could not read {args.picture}: {exc}")
    out = save(found, args.sheet, args.out)
    print(f"cut {len(found)} cells into {out}; check {out / 'review.png'}")
    if empty:
        print(f"  {len(empty)} empty: {' '.join(empty)}")


if __name__ == "__main__":
    main()
