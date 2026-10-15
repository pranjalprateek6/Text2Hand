"""Turning text into page files on disk: the part of a render a worker does.

Kept apart from app.py so that a render can run in a worker process that
imports only this, the renderer and Pillow, and none of the web app. It
applies a render's options to the renderer's module globals, which is safe
here because each worker process renders one document at a time.
"""
from __future__ import annotations

import json
import random
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

import text_to_handwriting as t2h

PREVIEW_WIDTH = 900        # downscaled width for the in-page preview
THUMB_WIDTH = 200          # downscaled width for the page-strip thumbnails

# Three real sizes rather than a millimetre slider, for the same reason the
# pens are four colours and not a wheel: every choice should look like a page
# someone could have written. The ruling never moves; a person writing larger
# on the same ruled pad is exactly what larger handwriting is.
SIZES = {"small": 2.5, "normal": 3.0, "large": 3.5}

# The engine has always supported any pen colour; the app just never passed one
# through. Kept to a small set of real pens rather than a colour wheel, because
# an arbitrary hex is how you get handwriting in #00ff00.
INKS = {
    "blue-black": (20, 26, 66),
    "black": (26, 26, 29),
    "blue": (21, 60, 152),
    "red": (150, 28, 24),
}


# Said in every PDF's properties. A page that looks handwritten should not be
# able to pass as handwritten to anyone who checks how it was made.
PDF_INFO = {"title": "Handwritten pages", "creator": "Text2Hand",
            "producer": "Text2Hand", "subject": "Rendered by Text2Hand from typed text"}


PDF_QUALITY = 75               # the PDF's page images (Pillow's default); the full-size page is q95


def build_pdf(path: Path, jpegs: list[Path], dpi: float | None = None) -> Path:
    """Write a PDF of one JPEG per page, embedding each file as it is.

    A JPEG goes into a PDF unchanged (DCTDecode), so nothing is decoded or
    encoded here: this is file reading and a few hundred bytes of PDF
    structure per page. Pages are sized from the image and `dpi` (the A4 or
    Letter page the hand was measured against), and the document's
    properties say it was made by Text2Hand.
    """
    dpi = dpi or pdf_dpi()
    tmp = path.with_name(f".{path.name}.part")
    offsets: list[int] = []
    with open(tmp, "wb") as out:
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

        def obj(num: int, body: bytes) -> None:
            while len(offsets) < num:
                offsets.append(0)
            offsets[num - 1] = out.tell()
            out.write(b"%d 0 obj\n" % num + body + b"\nendobj\n")

        # 1 catalog, 2 page tree, 3 info; then three objects per page
        kids = []
        for n, jpeg in enumerate(jpegs):
            with Image.open(jpeg) as im:            # reads the header only
                w, h = im.size
            data = jpeg.read_bytes()
            img, content, page = 4 + 3 * n, 5 + 3 * n, 6 + 3 * n
            pw, ph = w * 72 / dpi, h * 72 / dpi
            obj(img, b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
                     b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
                     b"/Length %d >>\nstream\n" % (w, h, len(data)) + data + b"\nendstream")
            draw = b"q %.4f 0 0 %.4f 0 0 cm /Im0 Do Q" % (pw, ph)
            obj(content, b"<< /Length %d >>\nstream\n" % len(draw) + draw + b"\nendstream")
            obj(page, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.4f %.4f] "
                      b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
                      % (pw, ph, img, content))
            kids.append(page)
        obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        obj(2, b"<< /Type /Pages /Kids [%s] /Count %d >>"
               % (b" ".join(b"%d 0 R" % k for k in kids), len(kids)))
        info = b" ".join(b"/%s (%s)" % (k.title().encode(), v.encode("ascii", "replace"))
                         for k, v in PDF_INFO.items())
        obj(3, b"<< " + info + b" >>")
        xref = out.tell()
        out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1))
        out.write(b"".join(b"%010d 00000 n \n" % o for o in offsets))
        out.write(b"trailer\n<< /Size %d /Root 1 0 R /Info 3 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                  % (len(offsets) + 1, xref))
    tmp.replace(path)
    return path


def pdf_dpi() -> float:
    """The resolution that makes a page come out A4 in a PDF.

    Pillow assumes 72 dpi unless told, which made every exported page 36 by
    52 inches: right on screen, and absurd the moment anyone printed it.
    """
    return t2h.px_per_mm() * 25.4


PAGES_META = "pages.json"     # each page's fingerprint and first words, beside the pages


def _previous(base: Path | None, header: str) -> dict | None:
    """What an earlier render of the same document recorded, if it can be used."""
    if base is None:
        return None
    try:
        meta = json.loads((base / PAGES_META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if meta.get("header") == header else None


def write_streaming(text: str, folder: Path, as_markdown: bool, on_progress,
                     on_ready=None, header: str = "", base: Path | None = None):
    """Render, writing each page out as it is finished, and return (count, missing).

    The renderer hands over one page at a time and keeps nothing, so the only
    pages in memory are the one being written to disk and the few queued behind
    it. Holding the whole document instead cost 1.7 GB on a thirty page render.

    Writing runs on its own threads so it overlaps the next page being drawn,
    and a semaphore caps how many pages can be waiting: without it a fast
    renderer would just rebuild the pile it was supposed to avoid.
    """
    # One slot per writer thread. Fewer starves the pool and the renderer waits
    # on disk: at half this the same document took three seconds longer. More
    # is just pages queueing in memory, which is the thing being avoided, and
    # measured no faster.
    width = t2h._workers()
    slots = threading.Semaphore(width)
    failures: list[Exception] = []

    # Which previews are on disk, so the studio can show page 1 while page 9
    # is still being drawn. Pages finish out of order across the writer
    # threads, so what is reported is the run of pages complete from the
    # first: a viewer paging forward never meets a hole.
    previewed: set[int] = set()
    ready = [0]
    ready_lock = threading.Lock()

    def mark_ready(i: int) -> None:
        with ready_lock:
            previewed.add(i)
            before = ready[0]
            while ready[0] + 1 in previewed:
                ready[0] += 1
            advanced = ready[0] if ready[0] != before else 0
        if advanced and on_ready:
            on_ready(advanced)

    # Each page is also saved as the compact JPEG the PDF will carry, from
    # the page already in hand, and the PDF is put together at the end by
    # embedding those JPEGs as they are (see build_pdf). Pillow's PDF writer
    # decodes and re-encodes whatever it is given, so a page copied from an
    # earlier render had to be decoded and encoded again just to sit in the
    # PDF; now it is a file copy like the rest of that page.
    def append_pdf(page, i):
        page.convert("RGB").save(folder / f"pdf_{i}.jpg", quality=PDF_QUALITY)

    def on_page(number: int, page):
        slots.acquire()

        def write(page=page, i=number):
            try:
                # A transparent page keeps its alpha in the full-size file,
                # which therefore has to be a PNG; everything shown on screen
                # or put in the PDF is the ink on white, since neither has a
                # use for transparency.
                full = page
                if page.mode == "RGBA":
                    page = Image.new("RGB", full.size, (255, 255, 255))
                    page.paste(full, (0, 0), full)
                # JPEG for the preview: the paper grain makes PNG very
                # inefficient here (roughly 5x larger) and this copy is only
                # ever shown on screen.
                # Halved by box averaging first, which is half the cost of
                # filtering the full page and looks the same at 900px.
                preview = page.reduce(2)
                preview.thumbnail((PREVIEW_WIDTH, PREVIEW_WIDTH * 4), Image.Resampling.LANCZOS)
                preview.save(folder / f"preview_{i}.jpg", quality=82, optimize=True)

                # From the preview rather than the page: a second full-size
                # copy per page in flight is 30 MB that buys nothing, and
                # 900px is still four times the thumbnail's width.
                thumb = preview.copy()
                thumb.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 4), Image.Resampling.LANCZOS)
                thumb.save(folder / f"thumb_{i}.jpg", quality=72, optimize=True)
                mark_ready(i)

                # The full-size page is kept as a high quality JPEG, not a
                # PNG. Paper grain is close to random and barely compresses
                # losslessly, so the PNG was 11 MB and 380 ms a page, the
                # single largest cost in a render, for a file most people
                # never download. This is 2.5 MB and 30 ms, with 4:4:4 colour
                # so thin red ink keeps its edges. A PNG is made from it only
                # when one is asked for.
                if full.mode == "RGBA":
                    full.save(folder / f"page_{i}.png", compress_level=3)
                else:
                    page.save(folder / f"page_{i}.jpg", quality=95, subsampling=0)
                append_pdf(page, i)
            except Exception as exc:
                failures.append(exc)
            finally:
                slots.release()

        pool.submit(write)

    # Pages an earlier render of this document already drew. A seeded render
    # is deterministic, so a page whose fingerprint (everything drawn on it,
    # and its tilt) matches the earlier render's page of the same number is
    # that page, and its files are copied instead of being tilted, encoded
    # and saved again. The earlier render's header must match too: same
    # options, same seed. Paper grain may differ from what a fresh render
    # would draw, which is the one thing the fingerprint leaves out.
    before = _previous(base, header)
    reused: list[int] = []

    def reuse(number: int, key: str) -> bool:
        if (base is None or not before or number > len(before["keys"])
                or before["keys"][number - 1] != key):
            return False
        names = (f"preview_{number}.jpg", f"thumb_{number}.jpg", f"pdf_{number}.jpg")
        full = [f"page_{number}.jpg", f"page_{number}.png"]
        return all((base / n).exists() for n in names) and any((base / n).exists() for n in full)

    def copy(number: int):
        slots.acquire()

        def write(i=number):
            assert base is not None       # only pages reuse() found are copied
            try:
                for name in (f"preview_{i}.jpg", f"thumb_{i}.jpg"):
                    shutil.copyfile(base / name, folder / name)
                mark_ready(i)
                src = base / f"page_{i}.jpg"
                if not src.exists():
                    src = base / f"page_{i}.png"
                shutil.copyfile(src, folder / src.name)
                shutil.copyfile(base / f"pdf_{i}.jpg", folder / f"pdf_{i}.jpg")
                reused.append(i)
            except Exception as exc:
                failures.append(exc)
            finally:
                slots.release()

        pool.submit(write)

    def on_any_page(number: int, page):
        if page is None:
            copy(number)
        else:
            on_page(number, page)

    pool = ThreadPoolExecutor(max_workers=t2h._workers())
    dropped: list[str] = []
    info: dict = {}
    try:
        count, missing = t2h.stream_pages(text, on_any_page, as_markdown=as_markdown,
                                          on_progress=on_progress, dropped=dropped,
                                          reuse=reuse if before else None, info=info)
    finally:
        pool.shutdown(wait=True)
    if failures:
        raise failures[0]
    build_pdf(folder / "handwriting.pdf", [folder / f"pdf_{i}.jpg" for i in range(1, count + 1)])
    (folder / PAGES_META).write_text(json.dumps(
        {"header": header, "keys": info.get("keys", []), "heads": info.get("heads", [])}),
        encoding="utf-8")
    return count, missing, dropped, {"heads": info.get("heads", []), "reused": len(reused)}



PAPERS = ("ruled", "grid", "plain", "none")
SHEETS = ("a4", "letter")


def apply_options(ruled: bool = True, texture: bool = True, skew: bool = True,
                  ink: str = "blue-black", size: str = "normal",
                  paper: str | None = None, sheet: str = "a4") -> None:
    """Set the renderer up for one render. The caller must own the renderer.

    `paper` is ruled, grid or plain; without it the older `ruled` flag
    decides between ruled and plain.
    """
    paper = paper if paper in PAPERS else ("ruled" if ruled else "plain")
    t2h.RULED = paper == "ruled"
    t2h.MARGIN_RULE = paper == "ruled"
    t2h.GRID = paper == "grid"
    t2h.TRANSPARENT = paper == "none"
    t2h.PAPER_SIZE = sheet if sheet in SHEETS else "a4"
    t2h.PAPER_TEXTURE = texture
    t2h.SCAN_SKEW = 0.7 if skew else 0
    # derive_metrics() rescales the glyphs from this and flushes every
    # cache when the scale really changed, so setting it is enough.
    t2h.X_HEIGHT_MM = SIZES.get(size, SIZES["normal"])
    colour = INKS.get(ink, INKS["blue-black"])
    if colour != t2h.INK_COLOR:
        # Glyphs and word images are tinted as they load and then cached,
        # and the jitter pool holds tinted copies too, so a new pen colour
        # has to flush all of them or it silently keeps the old ink.
        t2h.INK_COLOR = colour
        t2h.reset_glyph_caches()


def render_into(folder: Path, text: str, opts: dict, on_progress=None, on_ready=None,
                base: Path | None = None):
    """Render `text` with `opts` into `folder`; return (pages, missing, dropped, info).

    `base` is an earlier render of the same document, whose unchanged pages
    may be copied rather than drawn again; see write_streaming.
    """
    header = json.dumps(opts, sort_keys=True) if opts.get("seed") is not None else ""
    opts = dict(opts)
    as_markdown = bool(opts.pop("as_markdown", False))
    seed = opts.pop("seed", None)
    apply_options(**opts)
    if seed is not None:
        # The same document with the same seed writes the same letters, so
        # fixing a typo on page six does not redraw every word on every page.
        # Pools kept from earlier renders would decide which jittered copy a
        # letter gets, so they start empty. Paper grain is not held to this.
        t2h.reset_jitter_pools()
        random.seed(seed)
    return write_streaming(text, folder, as_markdown, on_progress, on_ready=on_ready,
                           header=header, base=base if header else None)
