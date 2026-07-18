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

import os
import shutil
import tempfile
import threading
import uuid
import zipfile
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
PREVIEW_WIDTH = 900        # downscaled width for the in-page preview
THUMB_WIDTH = 200          # downscaled width for the page-strip thumbnails
# Rendering runs on a worker thread with progress, so a long document no
# longer risks hanging a request. This cap only exists to stop a runaway job
# filling the disk: roughly CHARS_PER_PAGE of text becomes one ~9 MB page.
MAX_CHARS = 60_000
CHARS_PER_PAGE = 850       # rough, measured from real renders

# The renderer keeps its settings in module globals, so only one render at a
# time may touch them.
_render_lock = threading.Lock()


def _prune() -> None:
    """Keep only the most recent render folders."""
    if not RENDER_ROOT.exists():
        return
    folders = sorted(
        (p for p in RENDER_ROOT.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in folders[KEEP_RENDERS:]:
        shutil.rmtree(old, ignore_errors=True)


def _render(text: str, ruled: bool, texture: bool, skew: bool, as_markdown: bool,
            on_progress=None):
    """Render text to a fresh folder and return (render_id, page_count, missing)."""
    with _render_lock:
        t2h.RULED = ruled
        t2h.MARGIN_RULE = ruled
        t2h.PAPER_TEXTURE = texture
        t2h.SCAN_SKEW = 0.7 if skew else 0
        pages, missing = t2h.render_pages(text, as_markdown=as_markdown,
                                          on_progress=on_progress)

    rid = uuid.uuid4().hex[:12]
    folder = RENDER_ROOT / rid
    folder.mkdir(parents=True, exist_ok=True)

    for i, page in enumerate(pages, 1):
        # JPEG for the preview: the paper grain makes PNG very inefficient here
        # (roughly 5x larger) and this copy is only ever shown on screen.
        preview = page.copy()
        preview.thumbnail((PREVIEW_WIDTH, PREVIEW_WIDTH * 4), Image.LANCZOS)
        preview.save(folder / f"preview_{i}.jpg", quality=82, optimize=True)

        thumb = page.copy()
        thumb.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 4), Image.LANCZOS)
        thumb.save(folder / f"thumb_{i}.jpg", quality=72, optimize=True)

        page.save(folder / f"page_{i}.png")
    pages[0].save(folder / "handwriting.pdf", save_all=True, append_images=pages[1:])

    if on_progress:
        on_progress("previews", len(pages))
    _prune()
    return rid, len(pages), missing


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


def _job_spawn(jid: str, work, failure: str) -> None:
    """Run work(progress) on a thread; whatever it returns is merged into the job.

    Both rendering and conversion outlive a request, so both go through here.
    """
    def runner() -> None:
        try:
            result = work(lambda msg: _job_set(jid, message=msg))
            _job_set(jid, state="done", message="Done", **result)
        except (ValueError, RuntimeError) as exc:
            _job_set(jid, state="error", error=str(exc))     # expected, already readable
        except Exception as exc:
            app.logger.exception("%s job failed", failure)
            _job_set(jid, state="error", error=f"{failure}: {exc}")

    threading.Thread(target=runner, daemon=True).start()


def _safe(rid: str) -> Path:
    """Resolve a render folder, refusing anything that is not a plain id."""
    if not rid.isalnum():
        abort(404)
    folder = RENDER_ROOT / rid
    if not folder.is_dir():
        abort(404)
    return folder


@app.get("/")
def index():
    return render_template("index.html", converters=converters.available())


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
        try:
            result = converters.to_markdown(str(path), converter=converter,
                                            page_spec=page_spec, progress=progress)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

        # Say now if this is too long to render, rather than letting Generate
        # fail after the user has already reviewed it.
        notes = list(result.notes)
        size = len(result.markdown)
        estimate = size // CHARS_PER_PAGE
        if size > MAX_CHARS:
            notes.append(
                f"This is {size:,} characters, about {estimate} handwritten pages, "
                f"over the {MAX_CHARS:,} limit. Convert a smaller page range, "
                "or cut it down before generating."
            )
        elif estimate >= 20:
            notes.append(f"That is roughly {estimate} handwritten pages, "
                         "so rendering will take a while.")

        return {"markdown": result.markdown, "converter": result.converter,
                "pages_converted": result.pages_converted,
                "total_pages": result.total_pages, "scanned": result.scanned,
                "truncated": result.truncated, "notes": notes,
                "chars": size, "estimated_pages": estimate,
                "over_limit": size > MAX_CHARS}

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
    if len(text) > MAX_CHARS:
        over = len(text)
        return jsonify(error=(
            f"That is {over:,} characters, over the {MAX_CHARS:,} limit "
            f"(roughly {over // CHARS_PER_PAGE} handwritten pages). "
            "Convert a page range such as 1-15, or trim the text."
        )), 400

    opts = {
        "ruled": bool(data.get("ruled", True)),
        "texture": bool(data.get("texture", True)),
        "skew": bool(data.get("skew", True)),
        "as_markdown": bool(data.get("markdown", False)),
    }
    def work(progress):
        def on_stage(stage: str, count: int) -> None:
            if stage == "page":
                progress(f"Writing page {count}")
            elif stage == "skew":
                progress("Finishing the pages")
            elif stage == "previews":
                progress("Preparing previews")

        rid, count, missing = _render(text, on_progress=on_stage, **opts)
        return {"id": rid, "pages": count, "missing": missing}

    jid = _job_start("Laying out the text")
    _job_spawn(jid, work, "Could not render")
    return jsonify(job=jid), 202


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
    return send_file(pdf, mimetype="application/pdf",
                     as_attachment=True, download_name=f"page_{n}.pdf")


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
    return send_file(zpath, mimetype="application/zip",
                     as_attachment=True, download_name="handwriting_pages.zip")


@app.get("/download/<rid>/handwriting.pdf")
def download_pdf(rid: str):
    path = _safe(rid) / "handwriting.pdf"
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="application/pdf",
                     as_attachment=True, download_name="handwriting.pdf")


@app.get("/download/<rid>/page_<int:n>.png")
def download_page(rid: str, n: int):
    path = _safe(rid) / f"page_{n}.png"
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/png",
                     as_attachment=True, download_name=f"page_{n}.png")


if __name__ == "__main__":
    RENDER_ROOT.mkdir(parents=True, exist_ok=True)
    app.run(debug=True)
