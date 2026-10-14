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
import time
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
# Without a cap an upload of any size is written to disk in full before a
# single line of this code runs. 50 MB is a very long scanned PDF.
MAX_UPLOAD_MB = int(os.environ.get("T2H_MAX_UPLOAD_MB", "50"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

RENDER_ROOT = Path(os.environ.get("T2H_RENDER_ROOT")
                   or Path(tempfile.gettempdir()) / "text2hand_web")
# Renders are kept for a while rather than only the newest few. Keeping five in
# total meant that once two people used the app, each one's renders deleted the
# other's downloads within minutes. The count cap stays as a backstop on disk.
RENDER_TTL = int(os.environ.get("T2H_RENDER_TTL_MIN", "120")) * 60
# Equation crops live apart from the renders, in the one folder Markdown images
# are allowed to come from. The Markdown names them by a relative path inside
# it, so nothing a browser sees or sends is a path on this machine.
ASSET_ROOT = RENDER_ROOT / "assets"
t2h.IMAGE_ROOT = str(ASSET_ROOT)
# However recent, never more than this many renders on disk at once.
KEEP_RENDERS = int(os.environ.get("T2H_KEEP_RENDERS", "40"))
KEEP_EQ_ASSETS = 8      # equation-crop folders, one per PDF conversion
PREVIEW_WIDTH = 900        # downscaled width for the in-page preview
THUMB_WIDTH = 200          # downscaled width for the page-strip thumbnails
# Rendering runs on a worker thread with progress, so a long document no longer
# risks hanging a request. The cap exists to stop a runaway job filling the disk,
# and is expressed in pages because that is the real cost: each page is a ~9 MB
# image and a second or so of work. How much text fits on a page depends on how
# large the handwriting is, so it is asked of the renderer rather than assumed.
MAX_OUTPUT_PAGES = 80


# The renderer keeps its settings in module globals, so only one render at a
# time may touch them.
_render_lock = threading.Lock()

# Three real sizes rather than a millimetre slider, for the same reason the
# pens are four colours and not a wheel: every choice should look like a page
# someone could have written. The ruling never moves; a person writing larger
# on the same ruled pad is exactly what larger handwriting is.
SIZES = {"small": 2.5, "normal": 3.0, "large": 3.5}
_page_sizes: dict[str, int] = {}


def page_size(size: str = "normal") -> int:
    """Characters that fit on a page in this hand size, measured once.

    It used to be asked of the renderer on every call, which meant two things:
    it reported whatever size the last render had left in the module globals,
    so one large render shrank everyone's limit until someone rendered normal
    again; and it re-derived those globals without the render lock, from
    request threads, underneath a render in progress.
    """
    size = size if size in SIZES else "normal"
    if size not in _page_sizes:
        with _render_lock:
            if size not in _page_sizes:
                t2h.X_HEIGHT_MM = SIZES[size]       # every render sets it again
                _page_sizes[size] = max(1, t2h.chars_per_page())
    return _page_sizes[size]


def render_limit(size: str = "normal") -> int:
    return MAX_OUTPUT_PAGES * page_size(size)


def _pdf_dpi() -> float:
    """The resolution that makes a page come out A4 in a PDF.

    Pillow assumes 72 dpi unless told, which made every exported page 36 by
    52 inches: right on screen, and absurd the moment anyone printed it.
    """
    return t2h.px_per_mm() * 25.4


def _prune() -> None:
    """Drop renders past their time or past the cap, and old equation crops.

    Equation assets are pruned on their own clock: they are referenced by
    Markdown sitting in someone's editor, which outlives the renders made from
    it, so tying their lifetime to the render count would break the images out
    from under an edit session five renders in.
    """
    def newest_first(root: Path, keep) -> list[Path]:
        if not root.exists():
            return []
        return sorted((p for p in root.iterdir() if p.is_dir() and keep(p)),
                      key=lambda p: p.stat().st_mtime, reverse=True)

    cutoff = time.time() - RENDER_TTL
    # Only plain ids are renders. An upload_ folder belongs to a conversion
    # that may still be reading it, and is removed by that conversion.
    renders = newest_first(RENDER_ROOT, lambda p: p.name.isalnum())
    expired = [p for p in renders[:KEEP_RENDERS] if p.stat().st_mtime < cutoff]
    assets = newest_first(ASSET_ROOT, lambda p: p.name.startswith("eq_"))
    for old in renders[KEEP_RENDERS:] + expired + assets[KEEP_EQ_ASSETS:]:
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
    dpi = _pdf_dpi()

    def append_pdf(page, i):
        with turn:
            while next_up[0] != i:
                turn.wait()
            try:
                page.convert("RGB").save(pdf_path, append=i > 1, resolution=dpi)
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

                # The full-size page is kept as a high quality JPEG, not a
                # PNG. Paper grain is close to random and barely compresses
                # losslessly, so the PNG was 11 MB and 380 ms a page, the
                # single largest cost in a render, for a file most people
                # never download. This is 2.5 MB and 30 ms, with 4:4:4 colour
                # so thin red ink keeps its edges. A PNG is made from it only
                # when one is asked for.
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


def _pages_on_disk(folder: Path) -> list[Path]:
    """The full-size page images, in page order: page_10 after page_2."""
    return sorted(folder.glob("page_*.jpg"), key=lambda p: int(p.stem.split("_")[1]))


def _combine_pdf(folder: Path) -> Path:
    """Build the whole-document PDF from the pages already on disk.

    Appended a page at a time. Pillow's save_all collects everything handed to
    it into a list first, so passing pages one by one that way still ends with
    all thirty of them in memory, which is what writing them out as they were
    finished was meant to avoid. Appending instead holds one page and costs
    about 0.4% of file size in superseded page tables.
    """
    pdf = folder / "handwriting.pdf"
    pngs = _pages_on_disk(folder)
    if not pngs:
        raise FileNotFoundError("no pages to combine")
    if pdf.exists():
        pdf.unlink()                    # appending onto a stale file would grow it
    for n, path in enumerate(pngs):
        with Image.open(path) as page:
            page.convert("RGB").save(pdf, append=n > 0, resolution=_pdf_dpi())
    return pdf


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
# Every job is a thread, and renders queue behind one lock at up to a gigabyte
# each, so an unbounded queue is a way to take the host down with a loop of
# requests. Past this many unfinished jobs a new one is refused with a 429
# rather than accepted and left to wait.
MAX_ACTIVE_JOBS = int(os.environ.get("T2H_MAX_ACTIVE_JOBS", "4"))


class Busy(Exception):
    """Too many jobs are already running or waiting."""


def _job_set(jid: str, **fields) -> None:
    with JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid].update(fields)


def _job_start(message: str) -> str:
    jid = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        active = sum(1 for j in JOBS.values() if j["state"] == "running")
        if active >= MAX_ACTIVE_JOBS:
            raise Busy()
        JOBS[jid] = {"state": "running", "message": message, "error": None}
        # only finished jobs are forgotten: dropping a running one leaves its
        # browser polling a job that no longer exists
        finished = [k for k, j in JOBS.items() if j["state"] != "running"]
        for old in finished[:max(0, len(JOBS) - KEEP_JOBS)]:
            JOBS.pop(old, None)
    return jid


@app.errorhandler(Busy)
def _busy(_exc):
    return jsonify(error="The server is busy with other documents. "
                         "Try again in a minute."), 429


@app.errorhandler(413)
def _too_large(_exc):
    return jsonify(error=f"That file is over the {MAX_UPLOAD_MB} MB upload limit."), 413


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
        except BaseException as exc:
            # BaseException, not Exception: the renderer raises SystemExit when
            # it finds no glyphs, and anything that escaped here would leave
            # the job reading "running" forever, holding a slot in the queue
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


def _build_once(path: Path, build) -> Path:
    """Make a download on first request, so no one is ever served half of it.

    `build` writes to the temporary path it is given, which is then renamed
    into place in one step. Built in place instead, a second request arriving
    mid-build found the file already there and sent the truncated part. Two
    simultaneous first requests now both build, and whichever rename lands
    last wins with an identical file.
    """
    if not path.exists():
        tmp = path.with_name(f".{uuid.uuid4().hex[:8]}.{path.name}")
        try:
            build(tmp)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
    return path


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


def _landing_stats() -> dict:
    """The numbers the landing page brags with, counted from the shipped
    assets when the page is served. They were hardcoded once, and were wrong
    within a week: the glyph count grew, the Greek coverage doubled, and the
    page went on advertising the old figures. A number on a page that sells
    precision has to be one the code cannot let rot.
    """
    codes = set()
    for name in os.listdir(t2h.FONT_DIR):
        stem, dot, ext = name.partition(".")
        if ext != "png" or not stem[:1].isdigit():
            continue
        digits = ""
        for ch in stem:
            if not ch.isdigit():
                break
            digits += ch
        codes.add(int(digits))
    try:
        import json
        with open(os.path.join(t2h.WORD_DIR, "index.json"), encoding="utf-8") as fh:
            words = len(json.load(fh))
    except (OSError, ValueError):
        words = 0
    drawn = len([c for c in codes if c >= 880])       # the Greek block and up
    return {"chars_page": page_size(), "glyphs": len(codes), "words": words,
            "drawn": drawn, "aliased": len(t2h.GLYPH_ALIASES),
            "maths": drawn + len(t2h.GLYPH_ALIASES),
            "max_pages": MAX_OUTPUT_PAGES}


@app.get("/")
def landing():
    """The front door. A placeholder until the real landing page is designed;
    the tool itself lives at /studio."""
    return render_template("landing.html", stats=_landing_stats())


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
    # The extension is only what the file is called. A PDF announces itself
    # within its first kilobyte, and anything that does not is refused before
    # it reaches a parser.
    head = upload.stream.read(1024)
    upload.stream.seek(0)
    if b"%PDF-" not in head:
        return jsonify(error="That file is not really a PDF."), 400

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
        eq_dir = ASSET_ROOT / f"eq_{uuid.uuid4().hex[:12]}"
        try:
            # Hand the renderer's own limit down, so conversion can never
            # produce more text than Generate would accept.
            result = converters.to_markdown(str(path), converter=converter,
                                            page_spec=page_spec, progress=progress,
                                            max_chars=render_limit(),
                                            assets_dir=str(eq_dir),
                                            asset_ref=eq_dir.name)
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

    try:
        jid = _job_start("Opening the PDF")
    except Busy:
        shutil.rmtree(folder, ignore_errors=True)     # nothing will ever read it
        raise
    _job_spawn(jid, work, "Could not convert")
    return jsonify(job=jid), 202


def converters_safe_name(filename: str) -> str:
    """Keep only a plain file name, never a path."""
    return os.path.basename(filename).replace("\\", "_") or "upload.pdf"


@app.post("/api/render")
def api_render():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    raw = data.get("text")
    size = str(data.get("size", "normal"))
    # Pasted text is the commonest input there is, and text from a word
    # processor is full of curly quotes, dashes and accents. Those used to be
    # folded only for PDFs, so typed "don’t" came out as "dont" and "café" as
    # "caf". Folded here too, with the same table.
    text = converters.normalize(raw if isinstance(raw, str) else "").strip()
    if not text:
        return jsonify(error="Write some text first."), 400
    limit = render_limit(size)
    if len(text) > limit:
        over = len(text)
        return jsonify(error=(
            f"That is {over:,} characters, roughly {over // page_size(size)} handwritten "
            f"pages, over the {MAX_OUTPUT_PAGES}-page limit. "
            "Convert a smaller page range, or trim the text."
        )), 400

    opts = {
        "ruled": bool(data.get("ruled", True)),
        "texture": bool(data.get("texture", True)),
        "skew": bool(data.get("skew", True)),
        "as_markdown": bool(data.get("markdown", False)),
        "ink": str(data.get("ink", "blue-black")),
        "size": size,
    }
    # How many pages this is probably going to be, so a long render counts
    # itself against a visible total instead of just counting. The ~ is
    # honest: pagination is discovered as the text flows.
    guess = max(1, round(len(text) / page_size(size)))

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
    src = folder / f"page_{n}.jpg"
    if not src.exists():
        abort(404)

    def build(tmp: Path) -> None:
        with Image.open(src) as page:          # closed, or Windows cannot prune it
            page.convert("RGB").save(tmp, "PDF", resolution=_pdf_dpi())

    pdf = _build_once(folder / f"page_{n}.pdf", build)
    stem = _dl_stem()
    return send_file(pdf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{stem} page {n}.pdf" if stem else f"page_{n}.pdf")


@app.get("/download/<rid>/pages.zip")
def download_zip(rid: str):
    """Every full-size page plus the combined PDF, zipped on first request."""
    folder = _safe(rid)

    def build(tmp: Path) -> None:
        # Stored, not deflated: JPEG and PDF pages are already compressed,
        # and deflating them again took 15 seconds on a 60 page document to
        # save almost nothing.
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
            for p in _pages_on_disk(folder):
                z.write(p, p.name)
            combined = folder / "handwriting.pdf"
            if combined.exists():
                z.write(combined, combined.name)

    zpath = _build_once(folder / "pages.zip", build)
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
    folder = _safe(rid)
    src = folder / f"page_{n}.jpg"
    if not src.exists():
        abort(404)

    def build(tmp: Path) -> None:
        with Image.open(src) as page:
            page.save(tmp, "PNG", compress_level=3, dpi=(_pdf_dpi(),) * 2)

    path = _build_once(folder / f"page_{n}.png", build)
    stem = _dl_stem()
    return send_file(path, mimetype="image/png", as_attachment=True,
                     download_name=f"{stem} page {n}.png" if stem else f"page_{n}.png")


def warm() -> None:
    """Do the first visitor's slow work before there is a first visitor.

    The first studio load used to take 1.3 s, measuring the hand, and the
    first render paid again for loading every glyph and word image and for
    texturing a blank sheet. All of it is the same for everyone, so it is done
    once at startup on a background thread. A WSGI server should call this
    after importing the app.
    """
    for size in SIZES:
        page_size(size)
    with _render_lock:
        t2h.X_HEIGHT_MM = SIZES["normal"]
        t2h.derive_metrics()
        t2h._load_words()
        t2h.Sheet().close()                 # builds the default blank sheet
    import markdown_blocks  # noqa: F401  (pulls in markdown and bs4)


if __name__ == "__main__":
    RENDER_ROOT.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=warm, daemon=True).start()
    # The Werkzeug debugger runs arbitrary code for anyone who can reach it, so
    # it is opt-in, and the server listens on this machine only unless told
    # otherwise. Anything public belongs behind a real WSGI server anyway.
    app.run(host=os.environ.get("T2H_HOST", "127.0.0.1"),
            port=int(os.environ.get("T2H_PORT", "5000")),
            debug=os.environ.get("T2H_DEBUG") == "1")
