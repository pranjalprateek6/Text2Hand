"""Turning text into page files on disk: the part of a render a worker does.

Kept apart from app.py so that a render can run in a worker process that
imports only this, the renderer and Pillow, and none of the web app. It
applies a render's options to the renderer's module globals, which is safe
here because each worker process renders one document at a time.
"""
from __future__ import annotations

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


def pdf_dpi() -> float:
    """The resolution that makes a page come out A4 in a PDF.

    Pillow assumes 72 dpi unless told, which made every exported page 36 by
    52 inches: right on screen, and absurd the moment anyone printed it.
    """
    return t2h.px_per_mm() * 25.4


def write_streaming(text: str, folder: Path, as_markdown: bool, on_progress,
                     on_ready=None):
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

    # The whole-document PDF is appended here, from the page already in hand.
    # Building it afterwards from the PNGs meant decoding every page back off
    # disk, and that decode was 87% of the cost: 4.1s of a 4.7s rebuild against
    # 20ms a page to append. Appending has to happen in page order, so a page
    # waits its turn, which is cheap at that price.
    pdf_path = folder / "handwriting.pdf"
    if pdf_path.exists():
        pdf_path.unlink()
    turn = threading.Condition()
    next_up = [1]
    dpi = pdf_dpi()

    def append_pdf(page, i):
        with turn:
            while next_up[0] != i:
                turn.wait()
            try:
                page.convert("RGB").save(pdf_path, append=i > 1, resolution=dpi,
                                         **({} if i > 1 else PDF_INFO))
            finally:
                # advanced even if this page failed, or every page behind it
                # would wait for a turn that never comes
                next_up[0] += 1
                turn.notify_all()

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
                preview.thumbnail((PREVIEW_WIDTH, PREVIEW_WIDTH * 4), Image.LANCZOS)
                preview.save(folder / f"preview_{i}.jpg", quality=82, optimize=True)

                # From the preview rather than the page: a second full-size
                # copy per page in flight is 30 MB that buys nothing, and
                # 900px is still four times the thumbnail's width.
                thumb = preview.copy()
                thumb.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 4), Image.LANCZOS)
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

    pool = ThreadPoolExecutor(max_workers=t2h._workers())
    try:
        count, missing = t2h.stream_pages(text, on_page, as_markdown=as_markdown,
                                          on_progress=on_progress)
    finally:
        pool.shutdown(wait=True)
    if failures:
        raise failures[0]
    return count, missing



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


def render_into(folder: Path, text: str, opts: dict, on_progress=None, on_ready=None):
    """Render `text` with `opts` into `folder`; return (page_count, missing)."""
    opts = dict(opts)
    as_markdown = bool(opts.pop("as_markdown", False))
    apply_options(**opts)
    return write_streaming(text, folder, as_markdown, on_progress, on_ready=on_ready)
