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
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import Image

import converters
import render_core
import text_to_handwriting as t2h
from render_core import PDF_INFO, SIZES, pdf_dpi

log = logging.getLogger("text2hand")

app = Flask(__name__)
# Nothing is cached by default. Static files are the exception, and only
# because every link to one carries its version (see _static_version), so a
# browser can keep it for a year and still never pair an old script with a
# newer server.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Behind a reverse proxy (Fly, Render, nginx) every request arrives from the
# proxy's address, so per-client limits would treat all visitors as one. Set
# T2H_TRUST_PROXY=1 only when such a proxy is really in front: otherwise a
# client could claim any address it liked in X-Forwarded-For.
if os.environ.get("T2H_TRUST_PROXY") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# What a page may load and run. Everything the app serves is its own, scripts
# included (no inline script, no eval); styles may be inline because the
# markup sets a few custom properties that way; fonts come from Google; images
# may be data: URLs, which is how the library keeps its thumbnails.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data: blob:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@app.after_request
def _headers(response):
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.endpoint == "static" and request.args.get("v"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.endpoint in ("preview", "thumb"):
        # a render's pages never change once written; they only expire
        response.headers["Cache-Control"] = "private, max-age=7200"
    return response


@app.url_defaults
def _static_version(endpoint, values):
    """Give every static link its file's version, so caching it is safe."""
    if endpoint == "static" and "filename" in values and "v" not in values:
        path = Path(app.static_folder) / values["filename"]
        try:
            values["v"] = int(path.stat().st_mtime)
        except OSError:
            pass
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
# Rendering runs on a worker thread with progress, so a long document no longer
# risks hanging a request. The cap exists to stop a runaway job filling the disk,
# and is expressed in pages because that is the real cost: each page is a ~9 MB
# image and a second or so of work. How much text fits on a page depends on how
# large the handwriting is, so it is asked of the renderer rather than assumed.
MAX_OUTPUT_PAGES = 80


# The renderer keeps its settings in module globals, so only one render at a
# time may touch them.
_render_lock = threading.Lock()

_page_sizes: dict[tuple, int] = {}


def page_size(size: str = "normal", sheet: str = "a4") -> int:
    """Characters that fit on a page in this hand size, measured once.

    It used to be asked of the renderer on every call, which meant two things:
    it reported whatever size the last render had left in the module globals,
    so one large render shrank everyone's limit until someone rendered normal
    again; and it re-derived those globals without the render lock, from
    request threads, underneath a render in progress.
    """
    size = size if size in SIZES else "normal"
    sheet = sheet if sheet in render_core.SHEETS else "a4"
    key = (size, sheet)
    if key not in _page_sizes:
        with _render_lock:
            if key not in _page_sizes:
                t2h.X_HEIGHT_MM = SIZES[size]       # every render sets these again
                t2h.PAPER_SIZE = sheet
                _page_sizes[key] = max(1, t2h.chars_per_page())
    return _page_sizes[key]


def render_limit(size: str = "normal", sheet: str = "a4") -> int:
    return MAX_OUTPUT_PAGES * page_size(size, sheet)


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


def _pages_on_disk(folder: Path) -> list[Path]:
    """The full-size page images, in page order: page_10 after page_2.

    A page on paper is kept as page_N.jpg; a transparent one as page_N.png,
    since JPEG has no alpha. A PNG made later for download sits beside a JPEG
    and is not a page of its own.
    """
    pages = {}
    for p in folder.glob("page_*.*"):
        if p.suffix in (".jpg", ".png") and p.stem.split("_")[1].isdigit():
            n = int(p.stem.split("_")[1])
            if p.suffix == ".jpg" or n not in pages:
                pages[n] = p
    return [pages[n] for n in sorted(pages)]


def _page_source(folder: Path, n: int) -> Path | None:
    """The full-size file for page n, whichever kind it is."""
    for name in (f"page_{n}.jpg", f"page_{n}.png"):
        if (folder / name).exists():
            return folder / name
    return None


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
            _on_white(page).save(pdf, append=n > 0, resolution=pdf_dpi(),
                                     **({} if n else PDF_INFO))
    return pdf


def _on_white(page: Image.Image) -> Image.Image:
    """A page as RGB, with any transparency laid on white paper."""
    if page.mode in ("RGBA", "LA", "P"):
        rgba = page.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, (0, 0), rgba)
        return flat
    return page.convert("RGB")


def _render(text: str, ruled: bool, texture: bool, skew: bool, as_markdown: bool,
            ink: str = "blue-black", size: str = "normal", on_progress=None,
            on_ready=None, paper: str = "ruled", sheet: str = "a4"):
    """Render text to a fresh folder and return (render_id, page_count, missing)."""
    rid = uuid.uuid4().hex[:12]
    folder = RENDER_ROOT / rid
    folder.mkdir(parents=True, exist_ok=True)
    opts = {"ruled": ruled, "texture": texture, "skew": skew, "as_markdown": as_markdown,
            "ink": ink, "size": size, "paper": paper, "sheet": sheet}
    ready = (lambda n: on_ready(rid, n)) if on_ready else None
    try:
        pool = _render_pool()
        if pool is not None:
            count, missing = pool.render(text, opts, folder, on_progress, ready)
        else:
            with _render_lock:
                count, missing = render_core.render_into(folder, text, opts,
                                                         on_progress, ready)
    except BaseException:
        # a stopped or failed render leaves nothing anyone can download
        shutil.rmtree(folder, ignore_errors=True)
        raise

    if on_progress:
        on_progress("previews", count)
    _prune()
    return rid, count, missing


# Worker processes that render side by side; see render_pool.py. Zero renders
# in this process behind the lock instead, one at a time. Only an entry point
# guarded by __main__ may start workers, so the default here is zero and
# app.py's own __main__ and wsgi.py turn them on.
RENDER_PROCS = int(os.environ.get("T2H_RENDER_PROCS", "0"))
_pool = None
_pool_lock = threading.Lock()


def _render_pool():
    global _pool
    if RENDER_PROCS <= 0:
        return None
    with _pool_lock:
        if _pool is None:
            from render_pool import RenderPool
            _pool = RenderPool(RENDER_PROCS)
    return _pool


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
# And past this many from one visitor, so a single browser (or a script in a
# loop) cannot take every slot and lock everyone else out.
MAX_JOBS_PER_CLIENT = int(os.environ.get("T2H_MAX_JOBS_PER_CLIENT", "2"))


class Busy(Exception):
    """Too many jobs are already running or waiting."""

    def __init__(self, message: str = "The server is busy with other documents. "
                                      "Try again in a minute.") -> None:
        super().__init__(message)
        self.message = message


def _client() -> str:
    """Who is asking, for per-client limits. The proxy's word only if trusted."""
    return request.remote_addr or "unknown"


def _job_set(jid: str, **fields) -> None:
    with JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid].update(fields)


def _job_start(message: str) -> str:
    jid = uuid.uuid4().hex[:12]
    client = _client()
    with JOBS_LOCK:
        running = [j for j in JOBS.values() if j["state"] == "running"]
        if sum(1 for j in running if j.get("_client") == client) >= MAX_JOBS_PER_CLIENT:
            raise Busy("You already have documents rendering. Wait for one to "
                       "finish, or cancel it.")
        if len(running) >= MAX_ACTIVE_JOBS:
            raise Busy()
        # who started it stays here: fields starting with _ are never sent
        JOBS[jid] = {"state": "running", "message": message, "error": None,
                     "_client": client}
        # only finished jobs are forgotten: dropping a running one leaves its
        # browser polling a job that no longer exists
        finished = [k for k, j in JOBS.items() if j["state"] != "running"]
        for old in finished[:max(0, len(JOBS) - KEEP_JOBS)]:
            JOBS.pop(old, None)
    return jid


@app.errorhandler(Busy)
def _busy(exc):
    return jsonify(error=exc.message), 429


@app.errorhandler(413)
def _too_large(_exc):
    return jsonify(error=f"That file is over the {MAX_UPLOAD_MB} MB upload limit."), 413


class JobCancelled(t2h.RenderStopped):
    """Raised inside a worker when its job has been asked to stop.

    A RenderStopped, so the renderer lets it through its progress hook and
    the render really ends at the next page instead of running to the end.
    """


def _job_spawn(jid: str, work, failure: str) -> None:
    """Run work(progress) on a thread; whatever it returns is merged into the job.

    Both rendering and conversion outlive a request, so both go through here.
    Cancellation rides on progress: both workers report between pages, which
    is also the only place stopping cleanly is possible, so the progress call
    doubles as the cancellation point. No page is ever half-drawn.
    """
    def progress(msg: str | None = None, **fields) -> None:
        """Report a stage, and stop here if the job was cancelled.

        Fields alone, with no message, are published without the
        cancellation check: they come from writer threads, where stopping is
        not safe, and only say what is already done.
        """
        if msg is None:
            _job_set(jid, **fields)
            return
        with JOBS_LOCK:
            cancelled = JOBS.get(jid, {}).get("cancel")
        if cancelled:
            raise JobCancelled()
        _job_set(jid, message=msg, **fields)

    def runner() -> None:
        started = time.perf_counter()
        try:
            result = work(progress)
            _job_set(jid, state="done", message="Done", **result)
            # one line per finished job, enough to see load and cost in a log
            log.info("job %s done in %.2fs %s", jid, time.perf_counter() - started,
                     {k: result[k] for k in ("pages", "pages_converted", "chars") if k in result})
        except JobCancelled:
            _job_set(jid, state="cancelled", message="Cancelled")
            log.info("job %s cancelled after %.2fs", jid, time.perf_counter() - started)
        except (ValueError, RuntimeError) as exc:
            _job_set(jid, state="error", error=str(exc))     # expected, already readable
            log.info("job %s refused: %s", jid, exc)
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


@app.get("/healthz")
def healthz():
    """For a load balancer or uptime check: up, and how loaded.

    Deliberately cheap. It reports the queue rather than rendering anything,
    so a busy server answers it as fast as an idle one.
    """
    with JOBS_LOCK:
        active = sum(1 for j in JOBS.values() if j["state"] == "running")
    return jsonify(ok=True, active_jobs=active, max_active_jobs=MAX_ACTIVE_JOBS,
                   render_processes=RENDER_PROCS, warm=bool(_page_sizes))


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
    sheet = str(data.get("sheet", "a4"))
    ruled = bool(data.get("ruled", True))
    paper = str(data.get("paper") or ("ruled" if ruled else "plain"))
    # Pasted text is the commonest input there is, and text from a word
    # processor is full of curly quotes, dashes and accents. Those used to be
    # folded only for PDFs, so typed "don’t" came out as "dont" and "café" as
    # "caf". Folded here too, with the same table.
    text = converters.normalize(raw if isinstance(raw, str) else "").strip()
    if not text:
        return jsonify(error="Write some text first."), 400
    limit = render_limit(size, sheet)
    if len(text) > limit:
        over = len(text)
        return jsonify(error=(
            f"That is {over:,} characters, roughly {over // page_size(size, sheet)} handwritten "
            f"pages, over the {MAX_OUTPUT_PAGES}-page limit. "
            "Convert a smaller page range, or trim the text."
        )), 400

    opts = {
        "ruled": ruled,
        "paper": paper,
        "sheet": sheet,
        "texture": bool(data.get("texture", True)),
        "skew": bool(data.get("skew", True)),
        "as_markdown": bool(data.get("markdown", False)),
        "ink": str(data.get("ink", "blue-black")),
        "size": size,
    }
    # How many pages this is probably going to be, so a long render counts
    # itself against a visible total instead of just counting. The ~ is
    # honest: pagination is discovered as the text flows.
    guess = max(1, round(len(text) / page_size(size, sheet)))

    def work(progress):
        def on_stage(stage: str, count: int) -> None:
            if stage == "page":
                progress(f"Writing page {count} of ~{max(guess, count)}")
            elif stage == "skew":
                progress("Finishing the pages")
            elif stage == "previews":
                progress("Preparing previews")

        def on_ready(rid: str, n: int) -> None:
            progress(id=rid, ready=n)

        rid, count, missing = _render(text, on_progress=on_stage, on_ready=on_ready, **opts)
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
        snapshot = {k: v for k, v in job.items() if not k.startswith("_")} if job else None
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
    src = _page_source(folder, n)
    if src is None:
        abort(404)

    def build(tmp: Path) -> None:
        with Image.open(src) as page:          # closed, or Windows cannot prune it
            _on_white(page).save(tmp, "PDF", resolution=pdf_dpi(), **PDF_INFO)

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
    src = _page_source(folder, n)
    if src is None:
        abort(404)

    def build(tmp: Path) -> None:
        with Image.open(src) as page:
            page.save(tmp, "PNG", compress_level=3, dpi=(pdf_dpi(),) * 2)

    # a transparent render already wrote its page as a PNG
    path = src if src.suffix == ".png" else _build_once(folder / f"page_{n}.png", build)
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
    pool = _render_pool()
    if pool is not None:
        pool.warm()                         # each worker loads the hand itself
    else:
        with _render_lock:
            render_core.apply_options()     # the defaults: A4, ruled, blue-black
            t2h.derive_metrics()
            t2h._load_words()
            t2h.Sheet().close()             # builds the default blank sheet
    import markdown_blocks  # noqa: F401  (pulls in markdown and bs4)


if __name__ == "__main__":
    RENDER_PROCS = int(os.environ.get("T2H_RENDER_PROCS", "2"))
    logging.basicConfig(level=os.environ.get("T2H_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    RENDER_ROOT.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=warm, daemon=True).start()
    # The Werkzeug debugger runs arbitrary code for anyone who can reach it, so
    # it is opt-in, and the server listens on this machine only unless told
    # otherwise. Anything public belongs behind a real WSGI server anyway.
    app.run(host=os.environ.get("T2H_HOST", "127.0.0.1"),
            port=int(os.environ.get("T2H_PORT", "5000")),
            debug=os.environ.get("T2H_DEBUG") == "1")
