"""Turn Markdown into a flat list of blocks the handwriting renderer can draw.

Markdown is parsed to HTML and walked with BeautifulSoup rather than with a
hand-rolled parser, because nesting (lists inside lists) is exactly where
hand-rolled Markdown parsers go wrong.

Inline emphasis is deliberately dropped. Converted PDFs are almost free of it
(33 bold spans in 16,000 lines of a real converted annual report) and
handwriting has no bold, so threading inline runs through the renderer would
buy very little. Bold and italic can be revisited once there is evidence they
show up in the documents people actually feed in.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString
from markdown import markdown

HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
# Containers that only group other blocks. Raw HTML in the Markdown, and the
# footnotes extension, arrive wrapped in these; walking into them keeps their
# contents instead of dropping the whole block for having an unknown tag.
CONTAINERS = ("div", "section", "article", "aside", "main", "header", "footer",
              "figure", "details", "nav", "center")


@dataclass
class Block:
    """One drawable thing. `kind` decides how the renderer lays it out."""
    kind: str                                  # heading para line item quote code rule image table
    text: str = ""
    level: int = 0                             # heading level, list depth, or 1 for a table header
    marker: str = ""                           # bullet or number drawn before a list item
    cells: list[str] = field(default_factory=list)   # table row cells


def _tidy(text: str) -> str:
    """Collapse whitespace, and close the gaps text joining leaves at punctuation.

    get_text() puts a space between every piece of a node, so "<b>raw</b>,"
    came back as "raw ," and a footnote marker as "Text [1] .".
    """
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([.,;:!?)\]])", r"\1", text)
    return re.sub(r"([(\[])\s+", r"\1", text)


def _text(node) -> str:
    """Visible text of a node, with all whitespace collapsed to single spaces."""
    return _tidy(node.get_text(" ", strip=True))


# Tags that start a block of their own; anything else inside a container is
# inline, and runs of it are one paragraph.
BLOCKS = HEADINGS + ("p", "ul", "ol", "blockquote", "pre", "table", "hr", "dl") + CONTAINERS


def to_blocks(md_text: str, dropped: list[str] | None = None) -> list[Block]:
    # sane_lists matters more than it looks: without it an ordered list that
    # follows a bulleted one is merged into the same <ul>, so numbering is
    # silently lost. Converted PDFs mix the two constantly.
    html = markdown(md_text, extensions=["tables", "fenced_code", "sane_lists", "footnotes"])
    soup = BeautifulSoup(html, "html.parser")
    _keep_links(soup)
    _footnotes(soup)
    out: list[Block] = []
    for node in list(soup.children):
        _walk(node, out, depth=0)
    if dropped is not None:
        dropped.extend(_ledger(soup, out))
    return out


def _words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[^\W_]+", text)]


def _ledger(soup, blocks: list[Block]) -> list[str]:
    """Words of the document that no block carries to the page.

    Every word the parsed document holds is counted, and every word the
    blocks will write is taken off that count; what is left was dropped
    somewhere between the two. It should be nothing. When it is not, saying
    so is far better than a page that quietly leaves part of the text out,
    which is how a list inside a quotation went missing for so long.
    """
    have = Counter(_words(soup.get_text(" ")))
    for b in blocks:
        have.subtract(_words(" ".join([b.text, b.marker] + b.cells)))
    return sorted(w for w, n in have.items() if n > 0)


def _keep_links(soup) -> None:
    """Fold each link's destination into its text, as "text (url)".

    Handwriting cannot hyperlink, and get_text() keeps a link's words while
    silently dropping where they pointed, so "[the docs](https://x)" used to
    reach the page as just "the docs". Only real destinations are folded in:
    an anchor within the document says nothing useful on paper, and a bare URL
    whose text already is the URL would only repeat itself.
    """
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href.startswith(("http://", "https://", "mailto:")):
            continue
        plain = href[7:] if href.startswith("mailto:") else href
        text = a.get_text(" ", strip=True)
        if not text or text in (href, plain):
            continue
        a.string = f"{text} ({plain})"


def _footnotes(soup) -> None:
    """Write footnote markers the way a hand would: "[1]", not a bare "1".

    The footnotes extension sets a reference as a superscript number, which
    get_text() flattens into the word before it ("Text1."), and gives each
    note a return arrow the hand has no glyph for.
    """
    for sup in soup.select("sup[id^=fnref]"):
        sup.replace_with(NavigableString(f"[{sup.get_text(strip=True)}]"))
    for back in soup.select("a.footnote-backref"):
        back.decompose()
    for hr in soup.select("div.footnote > hr"):
        hr.decompose()                         # a rule is drawn for the heading below
    for box in soup.select("div.footnote"):
        heading = soup.new_tag("h3")
        heading.string = "Notes"
        box.insert(0, heading)


def _paragraph(node, out: list[Block], kind: str = "para") -> None:
    """A paragraph, split at hard line breaks.

    A line break inside a paragraph is deliberate (an address, a poem, the
    lines of a letter's sign-off), so each piece is written on a line of its
    own. All but the last are "line" blocks, which take no gap after them.
    """
    pieces: list[list] = []
    current: list = []
    for child in node.children:
        if getattr(child, "name", None) == "br":
            pieces.append(current)
            current = []
        else:
            current.append(child)
    pieces.append(current)
    texts = [_tidy(" ".join(
        (c.get_text(" ", strip=True) if hasattr(c, "get_text") else str(c))
        for c in piece)) for piece in pieces]
    texts = [t for t in texts if t]
    for i, text in enumerate(texts):
        last = i == len(texts) - 1
        out.append(Block(kind if last or kind != "para" else "line", text))


def _walk(node, out: list[Block], depth: int) -> None:
    name = getattr(node, "name", None)
    if name is None:                           # loose text between blocks
        text = str(node).strip()
        if text:                               # e.g. the inside of a raw HTML block
            out.append(Block("para", re.sub(r"\s+", " ", text)))
        return

    if name in HEADINGS:
        text = _text(node)
        if text:
            out.append(Block("heading", text, level=int(name[1])))

    elif name == "p":
        img = node.find("img")
        text = _text(node)
        if img is not None and not text:
            # marker carries the source path: an image that resolves to a real
            # file (a cropped equation) is traced onto the page instead of
            # being stood in for by a drawn box.
            out.append(Block("image", img.get("alt") or "figure",
                             marker=img.get("src") or ""))
        elif text:
            # an image in the middle of a sentence cannot be boxed, but its
            # description can be written where it stood instead of vanishing
            for inline in node.find_all("img"):
                alt = (inline.get("alt") or "").strip()
                inline.replace_with(NavigableString(f" [{alt}] " if alt else " "))
            _paragraph(node, out)

    elif name in ("ul", "ol"):
        _list(node, out, depth, ordered=(name == "ol"))

    elif name == "blockquote":
        # Everything inside a quotation stays inside it: lists are indented a
        # level further in, quotations within quotations are written as quotes,
        # and anything else is written as quoted text. Only paragraphs used
        # to be read, so a list in a quote disappeared without a trace.
        for child in node.children:
            kind = getattr(child, "name", None)
            if kind in ("ul", "ol"):
                _list(child, out, depth + 1, ordered=(kind == "ol"))
            elif kind == "blockquote":
                _walk(child, out, depth)
            elif kind == "p":
                _paragraph(child, out, kind="quote")
            elif kind is not None or str(child).strip():
                text = _text(child) if kind else str(child).strip()
                if text:
                    out.append(Block("quote", re.sub(r"\s+", " ", text)))

    elif name == "pre":
        for line in node.get_text().rstrip("\n").split("\n"):
            out.append(Block("code", line))

    elif name == "hr":
        out.append(Block("rule"))

    elif name == "table":
        for tr in node.find_all("tr"):
            cells = [_text(c) for c in tr.find_all(["td", "th"])]
            if any(cells):
                header = bool(tr.find("th")) and not tr.find("td")
                out.append(Block("table", cells=cells, level=1 if header else 0))

    elif name == "img":
        out.append(Block("image", node.get("alt") or "figure",
                         marker=node.get("src") or ""))

    elif name in CONTAINERS:
        # Inline runs (text, <b>, <a>, <span>) between the blocks inside are
        # gathered into one paragraph each, so "Some <b>raw</b> html" stays a
        # sentence rather than becoming three.
        run: list = []

        def flush() -> None:
            text = _tidy(" ".join(c.get_text(" ", strip=True) if hasattr(c, "get_text")
                                  else str(c) for c in run))
            if text:
                out.append(Block("para", text))
            run.clear()

        for child in node.children:
            if getattr(child, "name", None) in BLOCKS:
                flush()
                _walk(child, out, depth)
            else:
                run.append(child)
        flush()

    elif name in ("dl",):
        for child in node.find_all(["dt", "dd"]):
            text = _text(child)
            if text:
                out.append(Block("item" if child.name == "dd" else "para", text,
                                 level=depth, marker="-" if child.name == "dd" else ""))

    else:
        # Anything else, an unknown or inline tag at block level, is kept as a
        # paragraph of its text rather than silently dropped.
        text = _text(node)
        if text:
            out.append(Block("para", text))


def _list(node, out: list[Block], depth: int, ordered: bool) -> None:
    # an ordered list may start anywhere: "3. third" is item three, not one
    try:
        number = int(node.get("start", 1))
    except (TypeError, ValueError):
        number = 1
    for li in node.find_all("li", recursive=False):
        # Pull nested lists out first so they do not bleed into this item's text,
        # then recurse into them at the next depth.
        nested = li.find_all(["ul", "ol"], recursive=False)
        for sub in nested:
            sub.extract()

        text = _text(li)
        if text:
            marker = "{}.".format(number) if ordered else "-"
            out.append(Block("item", text, level=depth, marker=marker))
        number += 1

        for sub in nested:
            _list(sub, out, depth + 1, ordered=(sub.name == "ol"))
