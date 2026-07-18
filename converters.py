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
from dataclasses import dataclass, field

MAX_PAGES = 50          # a 400-page report would render for hours; cap it
SCAN_PROBE_PAGES = 3    # how many pages to check when guessing "scanned"

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
    "ﬁ": "fi", "ﬂ": "fl", "�": "",                   # ligatures, mojibake
}


def normalize(text: str) -> str:
    for bad, good in _TYPOGRAPHY.items():
        text = text.replace(bad, good)
    return text


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


def available() -> list[dict]:
    """Every converter, whether it is usable right now, and why not."""
    out = [{
        "name": "pymupdf",
        "label": "Local (PyMuPDF)",
        "kind": "local",
        "ready": _has("pymupdf4llm"),
        "reason": "" if _has("pymupdf4llm") else "pip install pymupdf4llm",
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


_CONVERTERS = {"pymupdf": _pymupdf, "llamaparse": _llamaparse, "mistral": _mistral}


def to_markdown(path: str, converter: str = "pymupdf", page_spec: str = "") -> Result:
    if converter not in _CONVERTERS:
        raise ValueError(f"Unknown converter {converter!r}")
    ready = {c["name"]: c for c in available()}[converter]
    if not ready["ready"]:
        raise RuntimeError(f"{ready['label']} is not set up: {ready['reason']}")

    total, scanned = inspect(path)
    pages = parse_pages(page_spec, total)

    notes: list[str] = []
    truncated = len(pages) > MAX_PAGES
    if truncated:
        notes.append(f"Only the first {MAX_PAGES} of {len(pages)} selected pages were converted.")
        pages = pages[:MAX_PAGES]

    if scanned:
        # No text layer, so a text extractor will return almost nothing.
        if converter == "pymupdf":
            where = tesseract_path()
            notes.append(
                "This PDF has no text layer, so it is images of text. Local "
                "extraction will return little or nothing. "
                + ("Try a cloud converter, which does OCR."
                   if not where else "OCR is available but not wired up yet; try a cloud converter.")
            )

    md = _CONVERTERS[converter](path, pages)
    md = re.sub(r"\n{3,}", "\n\n", normalize(md)).strip()
    if not md:
        notes.append("The converter returned no text at all.")

    return Result(markdown=md, converter=converter, pages_converted=len(pages),
                  total_pages=total, scanned=scanned, truncated=truncated, notes=notes)
