"""Convert documents to Markdown for the handwriting renderer.

Markdown is the contract between document extraction and rendering. Any input
format only has to reach Markdown, and the renderer never learns a second
input grammar. That also means a bad extraction is recoverable: the
intermediate is human-readable, so it can be corrected before rendering.

Local extraction is the default and nothing leaves the machine. Cloud
converters are opt-in, need their own API key in the environment, and are
never selected automatically.
"""
from __future__ import annotations

import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field

MAX_PAGES = 50          # a 400-page report would render for hours; cap it
SCAN_PROBE_PAGES = 3    # how many pages to check when guessing "scanned"

# OCR runs at roughly a second or two per page and conversion is synchronous,
# so it gets a tighter cap than the text extractors.
OCR_MAX_PAGES = 15
OCR_DPI = 300           # what Tesseract wants; lower loses small text
OCR_LANG = "eng"

# Real PDFs are full of typography the handwriting has no glyph for, so those
# characters would silently vanish at render time. Fold them onto the ASCII
# the font actually covers.
_TYPOGRAPHY = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",     # single quotes
    "“": '"', "”": '"', "„": '"', "‟": '"',     # double quotes
    "–": "-", "—": "-", "―": "-", "−": "-",     # dashes, minus
    "…": "...", " ": " ", " ": " ", " ": " ",   # ellipsis, spaces
    "•": "-", "·": "-", "●": "-", "▪": "-",     # bullets
    "×": "x", "™": "(TM)", "®": "(R)", "©": "(C)",
    "ﬁ": "fi", "ﬂ": "fl", "�": "",              # ligatures, mojibake
    "€": "EUR", "£": "GBP", "¥": "JPY", "¢": "c",    # currency
    "°": " deg", "±": "+/-", "÷": "/", "≈": "~",     # maths
    "≤": "<=", "≥": ">=", "≠": "!=",
    "→": "->", "←": "<-", "↔": "<->",                # arrows
    "½": "1/2", "¼": "1/4", "¾": "3/4",              # fractions
    "§": "Sec.", "¶": "", "†": "*", "‡": "**",
}


def normalize(text: str) -> str:
    """Fold text onto the ASCII the handwriting can actually draw.

    OCR of real documents throws off accented letters and currency symbols
    constantly, and none of them have a glyph. Accents are stripped to their
    base letter (é becomes e) rather than dropped, since the letter is what
    matters. Anything still unmappable is deliberately left alone so the
    renderer reports it instead of losing it silently.
    """
    for bad, good in _TYPOGRAPHY.items():
        text = text.replace(bad, good)
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


@dataclass
class Result:
    markdown: str
    converter: str
    pages_converted: int
    total_pages: int
    scanned: bool = False
    truncated: bool = False
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #
def _has(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:
        return False


def tesseract_path() -> str | None:
    """Tesseract is a system binary, not a pip package, so look for it."""
    found = shutil.which("tesseract")
    if found:
        return found
    default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    return default if os.path.exists(default) else None


def _ocr_ready() -> tuple[bool, str]:
    if not _has("pytesseract"):
        return False, "pip install pytesseract"
    if not tesseract_path():
        return False, "install Tesseract (winget install UB-Mannheim.TesseractOCR)"
    return True, ""


def available() -> list[dict]:
    """Every converter, whether it is usable right now, and why not."""
    ocr_ok, ocr_why = _ocr_ready()
    out = [{
        "name": "pymupdf",
        "label": "Local (PyMuPDF)",
        "kind": "local",
        "ready": _has("pymupdf4llm"),
        "reason": "" if _has("pymupdf4llm") else "pip install pymupdf4llm",
    }, {
        "name": "ocr",
        "label": "Local OCR (Tesseract)",
        "kind": "local",
        "ready": ocr_ok,
        "reason": ocr_why,
    }]
    for name, label, module, env in (
        ("llamaparse", "Cloud (LlamaParse)", "llama_parse", "LLAMA_CLOUD_API_KEY"),
        ("mistral", "Cloud (Mistral OCR)", "mistralai", "MISTRAL_API_KEY"),
    ):
        installed, keyed = _has(module), bool(os.getenv(env))
        reason = ""
        if not installed:
            reason = f"pip install {module.replace('_', '-')}"
        elif not keyed:
            reason = f"set {env}"
        out.append({"name": name, "label": label, "kind": "cloud",
                    "ready": installed and keyed, "reason": reason})
    return out


# --------------------------------------------------------------------------- #
# PDF inspection
# --------------------------------------------------------------------------- #
def inspect(path: str) -> tuple[int, bool]:
    """Return (page_count, looks_scanned)."""
    import pymupdf

    with pymupdf.open(path) as doc:
        total = doc.page_count
        probe = min(SCAN_PROBE_PAGES, total)
        # Same cheap heuristic as the reference pipeline: no extractable text on
        # the first few pages means it is images of text, not text.
        has_text = any(doc.load_page(i).get_text().strip() for i in range(probe))
    return total, not has_text


def parse_pages(spec: str, total: int) -> list[int]:
    """Turn "1-10" or "1,3,5-7" into zero-based page indices. Blank means all."""
    spec = (spec or "").strip()
    if not spec:
        return list(range(total))

    wanted: set[int] = set()
    for chunk in re.split(r"[,\s]+", spec):
        if not chunk:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", chunk)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            wanted.update(range(lo - 1, hi))
        elif chunk.isdigit():
            wanted.update([int(chunk) - 1])
        else:
            raise ValueError(f"Could not read page range {chunk!r}")
    pages = sorted(p for p in wanted if 0 <= p < total)
    if not pages:
        raise ValueError("That page range is outside the document")
    return pages


# --------------------------------------------------------------------------- #
# Converters
# --------------------------------------------------------------------------- #
def _pymupdf(path: str, pages: list[int]) -> str:
    import pymupdf4llm

    return pymupdf4llm.to_markdown(path, pages=pages, show_progress=False)


def _unwrap(text: str) -> str:
    """Reflow OCR output into paragraphs.

    Tesseract breaks a line wherever the page did, so its output is hard
    wrapped. Left alone, every visual line would become its own line of
    handwriting. Join lines inside a paragraph, keep blank lines as paragraph
    breaks, and stitch words that were hyphenated across a line break.
    """
    out = []
    for para in re.split(r"\n\s*\n", text):
        joined = ""
        for line in (l.strip() for l in para.splitlines()):
            if not line:
                continue
            if joined.endswith("-"):
                joined = joined[:-1] + line           # re-join a split word
            else:
                joined = (joined + " " + line).strip()
        if joined:
            out.append(joined)
    return "\n\n".join(out)


def _ocr(path: str, pages: list[int]) -> str:
    """Read images of text by rasterising each page and running Tesseract.

    Rendering through PyMuPDF rather than pdf2image keeps this to one system
    dependency: no poppler needed, and PyMuPDF is already here.
    """
    import io

    import pymupdf
    import pytesseract
    from PIL import Image

    binary = tesseract_path()
    if binary:
        pytesseract.pytesseract.tesseract_cmd = binary

    chunks = []
    with pymupdf.open(path) as doc:
        for number in pages:
            pix = doc.load_page(number).get_pixmap(dpi=OCR_DPI)
            image = Image.open(io.BytesIO(pix.tobytes("png")))
            text = _unwrap(pytesseract.image_to_string(image, lang=OCR_LANG).strip())
            if text:
                chunks.append(text)
    return "\n\n".join(chunks)


def _llamaparse(path: str, pages: list[int]) -> str:
    from llama_parse import LlamaParse

    docs = LlamaParse(result_type="markdown").load_data(path)
    return "\n\n".join(d.text for d in docs)


def _mistral(path: str, pages: list[int]) -> str:
    from mistralai import Mistral

    client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
    with open(path, "rb") as fh:
        uploaded = client.files.upload(
            file={"file_name": os.path.basename(path), "content": fh}, purpose="ocr")
    url = client.files.get_signed_url(file_id=uploaded.id).url
    result = client.ocr.process(model="mistral-ocr-latest",
                                document={"type": "document_url", "document_url": url})
    keep = set(pages)
    return "\n\n".join(p.markdown for i, p in enumerate(result.pages) if i in keep)


_CONVERTERS = {"pymupdf": _pymupdf, "ocr": _ocr,
               "llamaparse": _llamaparse, "mistral": _mistral}


def to_markdown(path: str, converter: str = "pymupdf", page_spec: str = "") -> Result:
    if converter not in _CONVERTERS:
        raise ValueError(f"Unknown converter {converter!r}")
    ready = {c["name"]: c for c in available()}[converter]
    if not ready["ready"]:
        raise RuntimeError(f"{ready['label']} is not set up: {ready['reason']}")

    total, scanned = inspect(path)
    pages = parse_pages(page_spec, total)

    notes: list[str] = []
    cap = OCR_MAX_PAGES if converter == "ocr" else MAX_PAGES
    truncated = len(pages) > cap
    if truncated:
        notes.append(f"Only the first {cap} of {len(pages)} selected pages were converted."
                     + (" OCR is slow, so it has a tighter limit." if converter == "ocr" else ""))
        pages = pages[:cap]

    if scanned and converter != "ocr":
        # No text layer, so a text extractor will return almost nothing.
        ocr_ok, ocr_why = _ocr_ready()
        notes.append(
            "This PDF has no text layer, so it is images of text and a text "
            "extractor will return little or nothing. "
            + ("Switch the converter to Local OCR (Tesseract)."
               if ocr_ok else f"Local OCR needs setting up first: {ocr_why}.")
        )

    md = _CONVERTERS[converter](path, pages)
    md = re.sub(r"\n{3,}", "\n\n", normalize(md)).strip()
    if not md:
        notes.append("The converter returned no text at all.")

    return Result(markdown=md, converter=converter, pages_converted=len(pages),
                  total_pages=total, scanned=scanned, truncated=truncated, notes=notes)
