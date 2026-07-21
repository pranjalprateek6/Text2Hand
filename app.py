"""Text2Hand web app: a small Flask wrapper around the renderer.

Type text in the browser, get handwritten pages back. The heavy lifting is all
in text_to_handwriting.py; this only handles requests, per-render options and
serving the results.

Renders are written to a temp directory keyed by a random id rather than kept
in memory, because a full page is ~25 MB uncompressed and several requests
would add up fast. The oldest renders are pruned as new ones arrive.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import Image

import converters
import text_to_handwriting as t2h

app = Flask(__name__)
# Never cache static assets. Otherwise an edited CSS or JS file can leave a
# stale front end talking to a newer server, which looks like a broken app.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

RENDER_ROOT = Path(tempfile.gettempdir()) / "text2hand_web"
# A full-res page is roughly 9 MB, and a long document can be 50+ pages, so
# keeping many old renders around costs gigabytes. Keep only a few.
KEEP_RENDERS = 5
KEEP_EQ_ASSETS = 8      # equation-crop folders, one per PDF conversion
PREVIEW_WIDTH = 900        # downscaled width for the in-page preview
THUMB_WIDTH = 200          # downscaled width for the page-strip thumbnails
# Rendering runs on a worker thread with progress, so a long document no longer
# risks hanging a request. The cap exists to stop a runaway job filling the disk,
# and is expressed in pages because that is the real cost: each page is a ~9 MB
# image and a second or so of work. How much text fits on a page depends on how
# large the handwriting is, so it is asked of the renderer rather than assumed.
MAX_OUTPUT_PAGES = 80


def page_size() -> int:
    return max(1, t2h.chars_per_page())


def render_limit() -> int:
    return MAX_OUTPUT_PAGES * page_size()


# The renderer keeps its settings in module globals, so only one render at a
# time may touch them.
_render_lock = threading.Lock()


def _prune() -> None:
    """Keep only the most recent render folders, and a few equation crops.

    Equation assets are pruned on their own clock: they are referenced by
    Markdown sitting in someone's editor, which outlives the renders made from
    it, so tying their lifetime to the render count would break the images out
    from under an edit session five renders in.
    """
    if not RENDER_ROOT.exists():
        return
    folders = sorted(
        (p for p in RENDER_ROOT.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    renders = [p for p in folders if not p.name.startswith("eq_")]
    assets = [p for p in folders if p.name.startswith("eq_")]
    for old in renders[KEEP_RENDERS:] + assets[KEEP_EQ_ASSETS:]:
        shutil.rmtree(old, ignore_errors=True)


# The engine has always supported any pen colour; the app just never passed one
# through. Kept to a small set of real pens rather than a colour wheel, because
# an arbitrary hex is how you get handwriting in #00ff00.
INKS = {
    "blue-black": (20, 26, 66),
    "black": (26, 26, 29),
    "blue": (21, 60, 152),
    "red": (150, 28, 24),
}


def _write_streaming(text: str, folder: Path, as_markdown: bool, on_progress):
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

    def append_pdf(page, i):
        with turn:
            while next_up[0] != i:
                turn.wait()
            try:
                page.convert("RGB").save(pdf_path, append=i > 1)
            finally:
                # advanced even if this page failed, or every page behind it
                # would wait for a turn that never comes
                next_up[0] += 1
                turn.notify_all()

    def on_page(number: int, page):
        slots.acquire()

        def write(page=page, i=number):
            try:
                # JPEG for the preview: the paper grain makes PNG very
                # inefficient here (roughly 5x larger) and this copy is only
                # ever shown on screen.
                preview = page.copy()
                preview.thumbnail((PREVIEW_WIDTH, PREVIEW_WIDTH * 4), Image.LANCZOS)
                preview.save(folder / f"preview_{i}.jpg", quality=82, optimize=True)

                # From the preview rather than the page: a second full-size
                # copy per page in flight is 30 MB that buys nothing, and
                # 900px is still four times the thumbnail's width.
                thumb = preview.copy()
                thumb.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 4), Image.LANCZOS)
                thumb.save(folder / f"thumb_{i}.jpg", quality=72, optimize=True)

                # Level 3 rather than zlib's default 6. A page is mostly paper
                # grain, which is close to random and barely compresses, so the
                # extra effort buys about 5% of file size for twice the time.
                page.save(folder / f"page_{i}.png", compress_level=3)
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


def _combine_pdf(folder: Path) -> Path:
    """Build the whole-document PDF from the pages already on disk.

    Appended a page at a time. Pillow's save_all collects everything handed to
    it into a list first, so passing pages one by one that way still ends with
    all thirty of them in memory, which is what writing them out as they were
    finished was meant to avoid. Appending instead holds one page and costs
    about 0.4% of file size in superseded page tables.
    """
    pdf = folder / "handwriting.pdf"
    pngs = sorted(folder.glob("page_*.png"), key=lambda p: int(p.stem.split("_")[1]))
    if not pngs:
        raise FileNotFoundError("no pages to combine")
    if pdf.exists():
        pdf.unlink()                    # appending onto a stale file would grow it
    for n, path in enumerate(pngs):
        with Image.open(path) as page:
            page.convert("RGB").save(pdf, append=n > 0)
    return pdf


# Three real sizes rather than a millimetre slider, for the same reason the
# pens are four colours and not a wheel: every choice should look like a page
# someone could have written. The ruling never moves; a person writing larger
# on the same ruled pad is exactly what larger handwriting is.
SIZES = {"small": 2.5, "normal": 3.0, "large": 3.5}


def _render(text: str, ruled: bool, texture: bool, skew: bool, as_markdown: bool,
            ink: str = "blue-black", size: str = "normal", on_progress=None):
    """Render text to a fresh folder and return (render_id, page_count, missing)."""
    with _render_lock:
        t2h.RULED = ruled
        t2h.MARGIN_RULE = ruled
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
        rid = uuid.uuid4().hex[:12]
        folder = RENDER_ROOT / rid
        folder.mkdir(parents=True, exist_ok=True)
        count, missing = _write_streaming(text, folder, as_markdown, on_progress)

    if on_progress:
        on_progress("previews", count)
    _prune()
    return rid, count, missing


# --------------------------------------------------------------------------- #
# Background jobs
# --------------------------------------------------------------------------- #
# Rendering a long document takes far longer than a request should, so it runs
# on a worker thread and the browser polls for progress.
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
KEEP_JOBS = 40


def _job_set(jid: str, **fields) -> None:
    with JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid].update(fields)


def _job_start(message: str) -> str:
    jid = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[jid] = {"state": "running", "message": message, "error": None}
        for old in list(JOBS)[:-KEEP_JOBS]:
            JOBS.pop(old, None)
    return jid


class JobCancelled(Exception):
    """Raised inside a worker when its job has been asked to stop."""


def _job_spawn(jid: str, work, failure: str) -> None:
    """Run work(progress) on a thread; whatever it returns is merged into the job.

    Both rendering and conversion outlive a request, so both go through here.
    Cancellation rides on progress: both workers report between pages, which
    is also the only place stopping cleanly is possible, so the progress call
    doubles as the cancellation point. No page is ever half-drawn.
    """
    def progress(msg: str) -> None:
        with JOBS_LOCK:
            cancelled = JOBS.get(jid, {}).get("cancel")
        if cancelled:
            raise JobCancelled()
        _job_set(jid, message=msg)

    def runner() -> None:
        try:
            result = work(progress)
            _job_set(jid, state="done", message="Done", **result)
        except JobCancelled:
            _job_set(jid, state="cancelled", message="Cancelled")
        except (ValueError, RuntimeError) as exc:
            _job_set(jid, state="error", error=str(exc))     # expected, already readable
        except Exception as exc:
            app.logger.exception("%s job failed", failure)
            _job_set(jid, state="error", error=f"{failure}: {exc}")

    threading.Thread(target=runner, daemon=True).start()


# Converting the same PDF twice is pure repetition: extraction is settled by
# the file and the page range, and gives the same Markdown every time. Renders
# are deliberately not cached. Every one draws its own jitter, so a second
# render of the same text is a different hand, and handing back the first would
# make the button look broken.
CONVERSIONS = KEEP_EQ_ASSETS    # matched, so a kept conversion keeps its crops
_conversions: dict[str, dict] = {}
_conversions_lock = threading.Lock()


def _upload_key(path: Path, *parts: str) -> str:
    """Identify a conversion by the bytes uploaded and how they were asked for."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    for part in parts:
        h.update(b"\0" + part.encode())
    return h.hexdigest()


def _dl_stem() -> str:
    """Filename stem for a download, from the ?name= the studio sends.

    The studio names files after the document's title, so an export of "Deep
    Learning Notes" is not one more handwriting.pdf in a full Downloads
    folder. The title is user text, so it is reduced to characters every
    filesystem accepts before it may become a filename; empty (or absent)
    means the caller's default name stands.
    """
    name = re.sub(r"[^\w \-]+", "", request.args.get("name", ""))
    return re.sub(r"\s+", " ", name).strip()[:60]


def _safe(rid: str) -> Path:
    """Resolve a render folder, refusing anything that is not a plain id."""
    if not rid.isalnum():
        abort(404)
    folder = RENDER_ROOT / rid
    if not folder.is_dir():
        abort(404)
    return folder


@app.get("/")
def landing():
    """The front door. A placeholder until the real landing page is designed;
    the tool itself lives at /studio."""
    return render_template("landing.html")


@app.get("/studio")
def studio():
    # page_size goes down too, so the editor can say how much paper the text is
    # before anyone waits for a render.
    return render_template("studio.html", converters=converters.available(),
                           max_chars=render_limit(), page_size=page_size())


@app.post("/api/convert")
def api_convert():
    """PDF in, Markdown out. The user reviews and edits it before rendering.

    Extraction is never perfect, so this deliberately stops at Markdown rather
    than rendering straight through: the intermediate is readable, so a bad
    heading is a ten-second fix instead of a dead end.
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify(error="Choose a PDF first."), 400
    if not upload.filename.lower().endswith(".pdf"):
        return jsonify(error="Only PDF files are supported."), 400

    name = converters_safe_name(upload.filename)
    folder = RENDER_ROOT / f"upload_{uuid.uuid4().hex[:12]}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    upload.save(path)

    converter = request.form.get("converter", "pymupdf")
    page_spec = request.form.get("pages", "")

    def work(progress):
        key = _upload_key(path, converter, page_spec, str(render_limit()))
        with _conversions_lock:
            hit = _conversions.get(key)
        # The crops are referenced by path from the cached Markdown, so a
        # conversion is only still usable while its folder survives pruning.
        if hit and Path(hit["assets"]).is_dir():
            shutil.rmtree(folder, ignore_errors=True)
            os.utime(hit["assets"], None)   # touched, so use keeps it from pruning
            progress("Already converted")
            return dict(hit["response"])

        # Display equations are cropped into this folder and referenced from
        # the Markdown; it lives beyond the upload so the render can find them.
        eq_dir = RENDER_ROOT / f"eq_{uuid.uuid4().hex[:12]}"
        try:
            # Hand the renderer's own limit down, so conversion can never
            # produce more text than Generate would accept.
            result = converters.to_markdown(str(path), converter=converter,
                                            page_spec=page_spec, progress=progress,
                                            max_chars=render_limit(),
                                            assets_dir=str(eq_dir))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

        # Say now if this is too long to render, rather than letting Generate
        # fail after the user has already reviewed it.
        notes = list(result.notes)
        size = len(result.markdown)
        estimate = size // page_size()
        if size > render_limit():
            notes.append(
                f"This is {size:,} characters, about {estimate} handwritten pages, "
                f"over the {MAX_OUTPUT_PAGES}-page limit. Convert a smaller page "
                "range, or cut it down before generating."
            )
        elif estimate >= 20:
            notes.append(f"That is roughly {estimate} handwritten pages, "
                         "so rendering will take a while.")

        response = {"markdown": result.markdown, "converter": result.converter,
                    "pages_converted": result.pages_converted,
                    "total_pages": result.total_pages, "scanned": result.scanned,
                    "truncated": result.truncated, "notes": notes,
                    "chars": size, "estimated_pages": estimate,
                    "over_limit": size > render_limit()}
        with _conversions_lock:
            _conversions[key] = {"assets": str(eq_dir), "response": dict(response)}
            while len(_conversions) > CONVERSIONS:      # oldest out first
                _conversions.pop(next(iter(_conversions)))
        return response

    jid = _job_start("Opening the PDF")
    _job_spawn(jid, work, "Could not convert")
    return jsonify(job=jid), 202


def converters_safe_name(filename: str) -> str:
    """Keep only a plain file name, never a path."""
    return os.path.basename(filename).replace("\\", "_") or "upload.pdf"


@app.post("/api/render")
def api_render():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(error="Write some text first."), 400
    limit = render_limit()
    if len(text) > limit:
        over = len(text)
        return jsonify(error=(
            f"That is {over:,} characters, roughly {over // page_size()} handwritten "
            f"pages, over the {MAX_OUTPUT_PAGES}-page limit. "
            "Convert a smaller page range, or trim the text."
        )), 400

    opts = {
        "ruled": bool(data.get("ruled", True)),
        "texture": bool(data.get("texture", True)),
        "skew": bool(data.get("skew", True)),
        "as_markdown": bool(data.get("markdown", False)),
        "ink": str(data.get("ink", "blue-black")),
        "size": str(data.get("size", "normal")),
    }
    # How many pages this is probably going to be, so a long render counts
    # itself against a visible total instead of just counting. The ~ is
    # honest: pagination is discovered as the text flows.
    guess = max(1, round(len(text) / page_size()))

    def work(progress):
        def on_stage(stage: str, count: int) -> None:
            if stage == "page":
                progress(f"Writing page {count} of ~{max(guess, count)}")
            elif stage == "skew":
                progress("Finishing the pages")
            elif stage == "previews":
                progress("Preparing previews")

        rid, count, missing = _render(text, on_progress=on_stage, **opts)
        return {"id": rid, "pages": count, "missing": missing}

    jid = _job_start("Laying out the text")
    _job_spawn(jid, work, "Could not render")
    return jsonify(job=jid), 202


@app.post("/api/job/<jid>/cancel")
def api_job_cancel(jid: str):
    """Ask a running job to stop. It stops at its next page boundary."""
    with JOBS_LOCK:
        job = JOBS.get(jid)
        if job is None:
            return jsonify(error="No such job."), 404
        if job["state"] == "running":
            job["cancel"] = True
    return jsonify(ok=True)


@app.get("/api/job/<jid>")
def api_job(jid: str):
    with JOBS_LOCK:
        job = JOBS.get(jid)
        snapshot = dict(job) if job else None
    if snapshot is None:
        return jsonify(error="No such job."), 404
    return jsonify(snapshot)


@app.get("/preview/<rid>/<int:n>.jpg")
def preview(rid: str, n: int):
    path = _safe(rid) / f"preview_{n}.jpg"
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/jpeg")


@app.get("/thumb/<rid>/<int:n>.jpg")
def thumb(rid: str, n: int):
    path = _safe(rid) / f"thumb_{n}.jpg"
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/jpeg")


@app.get("/download/<rid>/page_<int:n>.pdf")
def download_page_pdf(rid: str, n: int):
    """Single page as a PDF, built on first request and then cached."""
    folder = _safe(rid)
    pdf = folder / f"page_{n}.pdf"
    if not pdf.exists():
        src = folder / f"page_{n}.png"
        if not src.exists():
            abort(404)
        Image.open(src).convert("RGB").save(pdf)
    stem = _dl_stem()
    return send_file(pdf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{stem} page {n}.pdf" if stem else f"page_{n}.pdf")


@app.get("/download/<rid>/pages.zip")
def download_zip(rid: str):
    """Every page PNG plus the combined PDF, zipped on first request."""
    folder = _safe(rid)
    zpath = folder / "pages.zip"
    if not zpath.exists():
        pngs = sorted(folder.glob("page_*.png"),
                      key=lambda p: int(p.stem.split("_")[1]))   # page_10 after page_2
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for p in pngs:
                z.write(p, p.name)
            combined = folder / "handwriting.pdf"
            if combined.exists():
                z.write(combined, combined.name)
    stem = _dl_stem()
    return send_file(zpath, mimetype="application/zip", as_attachment=True,
                     download_name=f"{stem} pages.zip" if stem else "handwriting_pages.zip")


@app.get("/download/<rid>/handwriting.pdf")
def download_pdf(rid: str):
    folder = _safe(rid)
    path = folder / "handwriting.pdf"
    if not path.exists():
        # The combined PDF is built after the pages, so a process that stopped
        # in between can leave the pages without it. Rebuilding from them costs
        # a second and saves the render; only a render whose PNGs are missing
        # too is genuinely unrecoverable.
        try:
            _combine_pdf(folder)
        except FileNotFoundError:
            abort(404)
    stem = _dl_stem()
    return send_file(path, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{stem}.pdf" if stem else "handwriting.pdf")


@app.get("/download/<rid>/page_<int:n>.png")
def download_page(rid: str, n: int):
    path = _safe(rid) / f"page_{n}.png"
    if not path.exists():
        abort(404)
    stem = _dl_stem()
    return send_file(path, mimetype="image/png", as_attachment=True,
                     download_name=f"{stem} page {n}.png" if stem else f"page_{n}.png")


if __name__ == "__main__":
    RENDER_ROOT.mkdir(parents=True, exist_ok=True)
    app.run(debug=True)
