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

# OCR runs at roughly a second or two per page. Conversion happens on a worker
# thread with progress, so it no longer needs a tighter cap than the text
# extractors and shares MAX_PAGES with them.
OCR_DPI = 300           # what Tesseract wants; lower loses small text
OCR_LANG = "eng"

# Real PDFs are full of typography the handwriting has no glyph for, so those
# characters would silently vanish at render time. Fold them onto the ASCII
# the font actually covers.
_TYPOGRAPHY = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",     # single quotes
    "“": '"', "”": '"', "„": '"', "‟": '"',     # double quotes
    # Angle quotes and primes are quotation marks too, and decomposition leaves
    # them alone, so without these they survive as non-ASCII and are skipped at
    # render time. A prime is what a PDF uses for feet, minutes and derivatives.
    "«": '"', "»": '"', "‹": "'", "›": "'",     # angle quotes
    "′": "'", "″": '"', "‴": '"',               # primes
    "–": "-", "—": "-", "―": "-", "−": "-",     # dashes, minus
    "‐": "-", "‑": "-", "‒": "-", "⁄": "/",     # unicode hyphens, fraction slash
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
    # Latin letters that decomposition cannot split, because they are distinct
    # letters rather than a base plus an accent. Author names hit these.
    "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D",
    "ħ": "h", "ı": "i", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "ß": "ss", "þ": "th", "Þ": "Th", "ð": "d", "Ð": "D",
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

    try:
        with pymupdf.open(path) as doc:
            total = doc.page_count
            probe = min(SCAN_PROBE_PAGES, total)
            # Same cheap heuristic as the reference pipeline: no extractable
            # text on the first few pages means it is images of text, not text.
            has_text = any(doc.load_page(i).get_text().strip() for i in range(probe))
    except Exception:
        # pymupdf's own message includes the server's temp file path, which
        # must not be shown to a browser
        raise ValueError("That file could not be opened as a PDF. "
                         "It may be damaged, or not really a PDF.")
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
# --------------------------------------------------------------------------- #
# Display equations
# --------------------------------------------------------------------------- #
# Text extraction reads a PDF in stream order, which scrambles anything laid
# out in two dimensions: fractions, limits, stacked notation. The structure is
# simply not in the text layer, so it cannot be recovered by reading it better.
#
# What is recoverable is the picture. A display equation is set entirely in
# maths fonts (Computer Modern and friends) while prose is set in the body
# font, so equation lines can be found by font signature, cropped from the
# rendered page, and carried through the Markdown as images. The renderer then
# traces them onto the paper as ink. Inline maths inside prose stays text.
_MATH_FONT = re.compile(r"^(CM|MS[AB]M)|Math", re.I)
# The crop must contain a symbol font, not just CMR digits, or page numbers
# and bare numerals would be cropped as equations.
_MATH_SYMBOL_FONT = re.compile(r"^(CMMI|CMSY|CMEX|MS[AB]M)|Math", re.I)
EQ_DPI = 300            # render resolution for the crops; the renderer assumes it
_EQ_PAD = 3             # points of padding around a crop
_EQ_JOIN = 6            # equation lines closer than this merge into one region


def _equation_regions(page) -> list:
    """Bounding boxes of display-equation lines on a page, merged into groups.

    A line counts as an equation line when none of its characters come from a
    prose font. Aligned groups and fragments of one equation (limits, the bar
    of a fraction's neighbourhood) sit within a few points of each other, so
    nearby boxes merge into one region.
    """
    import pymupdf

    laid_out = page.get_text("dict")
    boxes = []
    prose_rows = []
    for block in laid_out["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            if any(not _MATH_FONT.match(s["font"]) for s in spans):
                prose_rows.append(pymupdf.Rect(line["bbox"]))
                continue                     # something from a prose font: text
            boxes.append((pymupdf.Rect(line["bbox"]),
                          any(_MATH_SYMBOL_FONT.match(s["font"]) for s in spans)))

    # Inline maths sits on the same row as running prose, and cutting it out
    # tears a hole in the middle of a sentence. A maths line that shares most
    # of its height with a real prose line is inline, and stays text.
    def _inline(rect) -> bool:
        for row in prose_rows:
            if row.width < 100:
                continue
            overlap = min(rect.y1, row.y1) - max(rect.y0, row.y0)
            if overlap > 0.5 * rect.height:
                return True
        return False

    boxes = [(r, s) for r, s in boxes if not _inline(r)]

    # One display equation fragments into many boxes: the body, superscript
    # limits, pieces either side of a big operator. They share vertical space,
    # so cluster by vertical overlap alone and union whole clusters, whatever
    # the horizontal gaps. Growing boxes sideways-only left every fragment as
    # its own region, which meant 31 crops for a page with five equations.
    boxes.sort(key=lambda b: b[0].y0)
    regions: list[list] = []                 # [rect, has_symbol_font]
    for rect, symbolic in boxes:
        if regions and rect.y0 <= regions[-1][0].y1 + _EQ_JOIN:
            regions[-1][0] |= rect
            regions[-1][1] = regions[-1][1] or symbolic
        else:
            regions.append([pymupdf.Rect(rect), symbolic])
    # specks and stray operators are not display equations
    kept = [r for r, symbolic in regions
            if symbolic and r.width >= 30 and r.height >= 7]

    # Equation tags, "(4)", sit at the right margin in the prose font, on the
    # same row as their equation. Left alone they survive as orphaned text and
    # the extractor weaves them into junk tables, so hand them to the caller
    # to be redacted with their equation and reused as its caption.
    tags = []
    for block in laid_out["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            text = "".join(s["text"] for s in line["spans"]).strip()
            m = re.fullmatch(r"\((\d+)\)", text)
            rect = pymupdf.Rect(line["bbox"])
            if m and rect.width < 40:
                tags.append((rect, m.group(1)))
    return kept, prose_rows, tags


def _excise_equations(doc, number: int, assets_dir: str, counter: list[int]) -> dict[str, str]:
    """Crop each display equation to a file and stamp a token where it was.

    The token is drawn into the page by a redaction, so the Markdown extractor
    places it exactly where the equation sat in reading order. No text
    matching against scrambled glyphs is involved. Returns token -> Markdown
    replacement.
    """
    import pymupdf

    page = doc.load_page(number)
    regions, prose_rows, tags = _equation_regions(page)
    if not regions:
        return {}

    os.makedirs(assets_dir, exist_ok=True)
    replacements: dict[str, str] = {}
    for rect in regions:
        counter[0] += 1
        k = counter[0]
        pad = pymupdf.Rect(rect.x0 - _EQ_PAD, rect.y0 - _EQ_PAD,
                           rect.x1 + _EQ_PAD, rect.y1 + _EQ_PAD) & page.rect
        # padding must not reach into the prose above or below, or the crop
        # carries the ascenders of a neighbouring line
        for row in prose_rows:
            if row.y1 <= rect.y0 and row.y1 > pad.y0:
                pad.y0 = row.y1 + 0.5
            if row.y0 >= rect.y1 and row.y0 < pad.y1:
                pad.y1 = row.y0 - 0.5
        # forward slashes even on Windows: they read better in the editor, and
        # a backslash path inside a regex replacement is an escape sequence
        crop = os.path.join(assets_dir, f"eq{k}.png").replace("\\", "/")
        page.get_pixmap(clip=pad, dpi=EQ_DPI).save(crop)

        # the paper's own tags make a better caption than "equation", and the
        # tag lines must go with their equation or they survive as orphans
        mine = [t for t in tags
                if min(rect.y1, t[0].y1) - max(rect.y0, t[0].y0) > 0.5 * t[0].height]
        if len(mine) > 1:
            alt = f"equations ({mine[0][1]})-({mine[-1][1]})"
        elif mine:
            alt = f"equation ({mine[0][1]})"
        else:
            alt = "equation"
        for tag_rect, _ in mine:
            page.add_redact_annot(tag_rect)

        token = f"T2HEQ{k}Z"
        replacements[token] = f"\n\n![{alt}]({crop})\n\n"
        # shrink the redaction below the padded crop, or it eats neighbours
        page.add_redact_annot(rect, text=token, fontsize=7)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
    return replacements


def _page_pymupdf(doc, number: int, ctx: dict | None = None) -> str:
    import pymupdf4llm

    replacements = {}
    if ctx and ctx.get("assets_dir"):
        replacements = _excise_equations(doc, number, ctx["assets_dir"], ctx["counter"])

    md = pymupdf4llm.to_markdown(doc, pages=[number], show_progress=False)
    for token, image in replacements.items():
        # the extractor may bold or italicise the token, so match around it;
        # the replacement goes in as a function so nothing in a file path is
        # ever read as a regex escape
        md = re.sub(r"\*{0,2}_{0,2}" + token + r"_{0,2}\*{0,2}",
                    lambda _m, image=image: image, md)
    return md


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


def _page_ocr(doc, number: int, ctx: dict | None = None) -> str:
    """Read one page of images-of-text by rasterising it and running Tesseract.

    Rendering through PyMuPDF rather than pdf2image keeps this to one system
    dependency: no poppler needed, and PyMuPDF is already here.
    """
    import io

    import pytesseract
    from PIL import Image

    binary = tesseract_path()
    if binary:
        pytesseract.pytesseract.tesseract_cmd = binary

    pix = doc.load_page(number).get_pixmap(dpi=OCR_DPI)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    return _unwrap(pytesseract.image_to_string(image, lang=OCR_LANG).strip())


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


# Local converters work a page at a time, so they can stop the moment the
# character budget is spent. Cloud converters process the whole document in one
# API call, so they can only be trimmed afterwards.
_PAGE_CONVERTERS = {"pymupdf": _page_pymupdf, "ocr": _page_ocr}
_WHOLE_CONVERTERS = {"llamaparse": _llamaparse, "mistral": _mistral}
_CONVERTERS = {**_PAGE_CONVERTERS, **_WHOLE_CONVERTERS}


def _convert_by_page(path: str, pages: list[int], converter: str,
                     budget: int | None, progress,
                     assets_dir: str | None = None) -> tuple[str, int, bool]:
    """Convert page by page, stopping once the budget is spent.

    Returns the Markdown, how many pages were actually converted, and whether
    it stopped early. Stopping at a page boundary keeps the result coherent;
    truncating mid-sentence would not.
    """
    import pymupdf

    read = _PAGE_CONVERTERS[converter]
    chunks: list[str] = []
    used = 0
    converted = 0
    ctx = {"assets_dir": assets_dir, "counter": [0]} if assets_dir else None

    with pymupdf.open(path) as doc:
        for done, number in enumerate(pages, 1):
            if progress:
                progress(f"Reading page {done} of {len(pages)}")
            text = normalize(read(doc, number, ctx)).strip()
            if not text:
                converted = done
                continue
            # keep at least one page, otherwise a single huge page yields nothing
            if budget and chunks and used + len(text) > budget:
                return "\n\n".join(chunks), converted, True
            chunks.append(text)
            used += len(text)
            converted = done

    return "\n\n".join(chunks), converted, False


def to_markdown(path: str, converter: str = "pymupdf", page_spec: str = "",
                progress=None, max_chars: int | None = None,
                assets_dir: str | None = None) -> Result:
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

    if scanned and converter != "ocr":
        # No text layer, so a text extractor will return almost nothing.
        ocr_ok, ocr_why = _ocr_ready()
        notes.append(
            "This PDF has no text layer, so it is images of text and a text "
            "extractor will return little or nothing. "
            + ("Switch the converter to Local OCR (Tesseract)."
               if ocr_ok else f"Local OCR needs setting up first: {ocr_why}.")
        )

    if converter in _PAGE_CONVERTERS:
        md, converted, stopped = _convert_by_page(path, pages, converter, max_chars,
                                                  progress, assets_dir=assets_dir)
    else:
        # A cloud converter returns the whole document in one call, so the
        # budget can only be applied after the fact.
        md = normalize(_WHOLE_CONVERTERS[converter](path, pages))
        converted, stopped = len(pages), False
        if max_chars and len(md) > max_chars:
            md, stopped = md[:max_chars].rsplit("\n\n", 1)[0], True

    md = re.sub(r"\n{3,}", "\n\n", md).strip()

    if stopped:
        notes.append(
            f"Stopped after {converted} of {len(pages)} pages, because more "
            f"would be over the {max_chars:,} character limit that rendering accepts."
        )
    # Anything still outside ASCII either has a glyph of its own (the Greek
    # letters and maths symbols do now) or is a script the handwriting cannot
    # draw (Cyrillic, Arabic, CJK and so on). Only the second kind is worth a
    # warning, so check the font before naming a character as missing.
    def _has_glyph(ch: str) -> bool:
        return os.path.exists(os.path.join("myfont", f"{ord(ch)}.png"))

    foreign = {ch for ch in md if ord(ch) > 126 and not _has_glyph(ch)}
    if foreign:
        # Only a letter's Unicode name starts with its script. A symbol's name
        # starts with whatever it is called ("ASTERISK OPERATOR", "FOR ALL"),
        # so grouping those by first word produces nonsense.
        scripts, symbols = set(), False
        for ch in foreign:
            name = unicodedata.name(ch, "")
            if not name:
                continue
            if unicodedata.category(ch).startswith("L"):
                scripts.add(name.split(" ")[0])
            else:
                symbols = True

        parts = [s if s == "CJK" else s.title() for s in sorted(scripts)[:4]]
        if symbols:
            parts.append("mathematical symbols")
        if parts:
            notes.append(
                "This document contains " + ", ".join(parts)
                + ", which the handwriting has no glyphs for. Those characters "
                "are skipped when rendering."
            )
    if not md:
        notes.append("The converter returned no text at all.")

    return Result(markdown=md, converter=converter, pages_converted=converted,
                  total_pages=total, scanned=scanned,
                  truncated=truncated or stopped, notes=notes)
