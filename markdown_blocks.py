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
from dataclasses import dataclass, field

from bs4 import BeautifulSoup
from markdown import markdown

HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass
class Block:
    """One drawable thing. `kind` decides how the renderer lays it out."""
    kind: str                                  # heading para item quote code rule image table
    text: str = ""
    level: int = 0                             # heading level, or list nesting depth
    marker: str = ""                           # bullet or number drawn before a list item
    cells: list[str] = field(default_factory=list)   # table row cells


def _text(node) -> str:
    """Visible text of a node, with all whitespace collapsed to single spaces."""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def to_blocks(md_text: str) -> list[Block]:
    # sane_lists matters more than it looks: without it an ordered list that
    # follows a bulleted one is merged into the same <ul>, so numbering is
    # silently lost. Converted PDFs mix the two constantly.
    html = markdown(md_text, extensions=["tables", "fenced_code", "sane_lists"])
    soup = BeautifulSoup(html, "html.parser")
    _keep_links(soup)
    out: list[Block] = []
    for node in list(soup.children):
        _walk(node, out, depth=0)
    return out


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


def _walk(node, out: list[Block], depth: int) -> None:
    name = getattr(node, "name", None)
    if name is None:                           # loose text between blocks
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
            out.append(Block("para", text))

    elif name in ("ul", "ol"):
        _list(node, out, depth, ordered=(name == "ol"))

    elif name == "blockquote":
        for child in node.find_all("p"):
            text = _text(child)
            if text:
                out.append(Block("quote", text))

    elif name == "pre":
        for line in node.get_text().rstrip("\n").split("\n"):
            out.append(Block("code", line))

    elif name == "hr":
        out.append(Block("rule"))

    elif name == "table":
        for tr in node.find_all("tr"):
            cells = [_text(c) for c in tr.find_all(["td", "th"])]
            if any(cells):
                out.append(Block("table", cells=cells))

    elif name == "img":
        out.append(Block("image", node.get("alt") or "figure",
                         marker=node.get("src") or ""))


def _list(node, out: list[Block], depth: int, ordered: bool) -> None:
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
