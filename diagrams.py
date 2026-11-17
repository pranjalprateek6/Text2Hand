"""Hand-drawn tree diagrams for ```diagram blocks.

A diagram is written as an indented outline, one node per line:

    ```diagram
    [20 | 40]
      [5 | 10]
      [25 | 30]
      [50 | 60]
    leaves ->
    ```

    [a | b | c]     a node holding several keys, drawn as a divided box
    20(B)  NIL  X   any other text is a single box with that label
    < child         a left child, > child a right one (binary trees); a node
                    with only one child is drawn off to that side
    ... !red        outline the node in red (or !black), as for red-black trees
    leaves ->       link the bottom row with arrows, left to right
    leaves <->      the same in both directions
    =>              on a line of its own: what follows is a second tree drawn
                    to the right, with an arrow between (before and after)

Everything is drawn as pen strokes that wobble, over- and undershoot at the
ends and change pressure; a box is four strokes whose corners do not quite
meet. Labels are written with the hand's own glyphs, and the whole diagram is
in the pen the text is written in unless a node asks for a colour.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

SS = 2                    # supersampling for smooth strokes
SCALE = 1.55              # diagram unit, in x-heights
RED = (150, 28, 24)
BLACK = (26, 26, 29)


class DiagramError(ValueError):
    """The block could not be read as a diagram; it is written out as text instead."""


@dataclass
class Node:
    keys: list[str]
    colour: str | None = None
    side: str | None = None               # "<" or ">" under a binary parent
    children: list[Node] = field(default_factory=list)
    # set by layout
    cx: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    anchors: list[float] = field(default_factory=list)


@dataclass
class Tree:
    root: Node
    leaves: str | None = None             # "->" or "<->"


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def parse(text: str) -> list[Tree]:
    trees: list[Tree] = []
    stack: list[tuple[int, Node]] = []
    current: Tree | None = None
    for raw in text.split("\n"):
        if not raw.strip():
            continue
        line = raw.rstrip()
        body = line.strip()
        if body == "=>":
            current, stack = None, []
            continue
        m = re.fullmatch(r"leaves\s*(<?->)", body)
        if m:
            if current is None:
                raise DiagramError("'leaves' before any node")
            current.leaves = m.group(1)
            continue
        indent = len(line) - len(line.lstrip())
        side = None
        if body[0] in "<>" and len(body) > 1 and body[1] == " ":
            side, body = body[0], body[2:].strip()
        colour = None
        cm = re.search(r"\s!(red|black)$", body)
        if cm:
            colour, body = cm.group(1), body[:cm.start()].strip()
        if body.startswith("[") and body.endswith("]"):
            keys = [k.strip() for k in body[1:-1].split("|")]
            if not all(keys):
                raise DiagramError(f"empty key in {body!r}")
        else:
            keys = [body]
        node = Node(keys, colour, side)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            if current is not None:
                raise DiagramError("a second root without '=>' before it")
            current = Tree(node)
            trees.append(current)
        else:
            stack[-1][1].children.append(node)
        stack.append((indent, node))
    if not trees:
        raise DiagramError("no nodes")
    return trees


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
class Pen:
    def __init__(self, t2h, ink: tuple[int, int, int], rng: random.Random):
        self.t = t2h
        self.ink = ink
        self.u = t2h.X_HEIGHT * SCALE
        self.w = max(3, round(self.u * 0.13))
        self.rng = rng
        self.ops: list = []

    def colour(self, name: str | None) -> tuple[int, int, int]:
        return {"red": RED, "black": BLACK}.get(name or "", self.ink)

    # ---- strokes -------------------------------------------------------------
    def stroke(self, pts, colour=None, overshoot=True):
        self.ops.append(("stroke", [tuple(p) for p in pts], colour or self.ink, overshoot))

    def arrow(self, p0, p1, colour=None, head=None):
        self.stroke([p0, p1], colour)
        head = head or self.u * 0.5
        ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        for side in (-1, 1):
            a = ang + math.pi + side * self.rng.uniform(0.42, 0.55)
            hl = head * self.rng.uniform(0.85, 1.1)
            self.stroke([(p1[0] + hl * math.cos(a), p1[1] + hl * math.sin(a)), p1], colour, False)

    def box(self, x, y, w, h, colour=None):
        j = self.u * 0.17
        c = [(x + self.rng.uniform(-j, j), y + self.rng.uniform(-j, j)),
             (x + w + self.rng.uniform(-j, j), y + self.rng.uniform(-j, j)),
             (x + w + self.rng.uniform(-j, j), y + h + self.rng.uniform(-j, j)),
             (x + self.rng.uniform(-j, j), y + h + self.rng.uniform(-j, j))]
        for a, b in ((0, 1), (1, 2), (2, 3), (3, 0)):
            self.stroke([c[a], c[b]], colour)

    # ---- handwriting -----------------------------------------------------------
    def measure(self, s: str, size: float = 1.1) -> float:
        total = 0.0
        for ch in s:
            if ch == " ":
                total += self.t.SPACE_WIDTH * size
                continue
            g = self.t.glyph_variants(ch)
            if g:
                total += g[0].width * size + self.u * 0.08
        return total

    def text(self, s: str, cx: float, base: float, colour=None, size: float = 1.1) -> None:
        x = cx - self.measure(s, size) / 2
        for ch in s:
            if ch == " ":
                x += self.t.SPACE_WIDTH * size
                continue
            vs = self.t.glyph_variants(ch)
            if not vs:
                continue
            a = self.rng.choice(vs).getchannel("A")
            k = size * self.rng.uniform(0.94, 1.06)
            a = a.resize((max(1, round(a.width * k)), max(1, round(a.height * k))), Image.Resampling.LANCZOS)
            a = a.rotate(self.rng.uniform(-4, 4), resample=Image.Resampling.BICUBIC, expand=True)
            drop = int(a.height * 0.2) if ch in "()[]|" else self.t._drop_below_line(ch, a.height)
            y = base - a.height + drop + self.rng.uniform(-self.u * .06, self.u * .06)
            self.ops.append(("ink", a, x, y, colour or self.ink))
            x += a.width + self.u * self.rng.uniform(0.05, 0.12)

    # ---- rendering -------------------------------------------------------------
    def render(self) -> Image.Image:
        xs: list[float] = []
        ys: list[float] = []
        for op in self.ops:
            if op[0] == "stroke":
                for x, y in op[1]:
                    xs.append(x)
                    ys.append(y)
            else:
                _, a, x, y, _ = op
                xs += [x, x + a.width]
                ys += [y, y + a.height]
        m = self.u
        ox, oy = min(xs) - m, min(ys) - m
        W, H = int(max(xs) - ox + m), int(max(ys) - oy + m)
        layers: dict[tuple, Image.Image] = {}
        for op in self.ops:
            if op[0] == "stroke":
                _, pts, colour, overshoot = op
                layer = layers.setdefault(colour, Image.new("L", (W * SS, H * SS), 0))
                self._stroke(ImageDraw.Draw(layer), [((x - ox) * SS, (y - oy) * SS) for x, y in pts],
                             self.w * SS, overshoot)
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for colour, layer in layers.items():
            ink = Image.new("RGBA", (W, H), tuple(colour) + (0,))
            ink.putalpha(layer.resize((W, H), Image.Resampling.LANCZOS))
            out = Image.alpha_composite(out, ink)
        for op in self.ops:
            if op[0] == "ink":
                _, a, x, y, colour = op
                ink = Image.new("RGBA", a.size, tuple(colour) + (0,))
                ink.putalpha(a)
                piece = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                piece.paste(ink, (int(x - ox), int(y - oy)))
                out = Image.alpha_composite(out, piece)
        return out

    def _stroke(self, d, pts, width, overshoot):
        rng = self.rng
        dense: list[tuple[float, float]] = []
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            n = max(2, int(math.hypot(x1 - x0, y1 - y0) / (6 * SS)))
            dense += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n)]
        dense.append(pts[-1])
        if overshoot and len(dense) > 2:
            for end, nxt in ((0, 1), (-1, -2)):
                ex, ey = dense[end]
                nx, ny = dense[nxt]
                L = math.hypot(ex - nx, ey - ny) or 1
                k = rng.uniform(-0.6, 1.8) * self.u * 0.12 * SS / L
                dense[end] = (ex + (ex - nx) * k, ey + (ey - ny) * k)
        total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(dense, dense[1:])) or 1
        amp = min(self.u * 0.16 * SS, total * 0.02) * rng.uniform(0.5, 1.2)
        phase, freq = rng.uniform(0, 6.28), rng.uniform(0.6, 1.6)
        walk, s = 0.0, 0.0
        out = []
        for i, (x, y) in enumerate(dense):
            if i:
                s += math.hypot(x - dense[i - 1][0], y - dense[i - 1][1])
            j, k = min(i + 1, len(dense) - 1), max(i - 1, 0)
            dx, dy = dense[j][0] - dense[k][0], dense[j][1] - dense[k][1]
            L = math.hypot(dx, dy) or 1
            walk = walk * 0.9 + rng.gauss(0, 0.35 * SS)
            off = amp * math.sin(phase + freq * 6.28 * s / total) + walk
            out.append((x - dy / L * off, y + dx / L * off))
        p0, p1 = rng.uniform(0.85, 1.05), rng.uniform(0.8, 1.1)
        for i in range(len(out) - 1):
            t = i / max(1, len(out) - 1)
            r = width / 2 * (p0 + (p1 - p0) * t)
            (ax, ay), (bx, by) = out[i], out[i + 1]
            d.line([(ax, ay), (bx, by)], fill=255, width=max(1, int(2 * r)))
            d.ellipse([bx - r, by - r, bx + r, by + r], fill=255)


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
def _size(p: Pen, n: Node) -> None:
    pad = p.u * 0.55
    n.h = p.u * 2.0
    n.w = sum(max(p.measure(k), p.u * 0.9) + 2 * pad for k in n.keys)
    for c in n.children:
        _size(p, c)


def _span(p: Pen, n: Node, gap: float) -> float:
    """Width of a node's subtree. Binary children (< >) keep their sides, a
    missing one leaving room as if a sibling stood there."""
    if not n.children:
        return n.w
    if any(c.side for c in n.children):
        left = next((c for c in n.children if c.side == "<"), None)
        right = next((c for c in n.children if c.side == ">"), None)
        lw = _span(p, left, gap) if left else 0.0
        rw = _span(p, right, gap) if right else 0.0
        half = max(lw, rw, n.w)
        return 2 * half + gap
    return max(n.w, sum(_span(p, c, gap) for c in n.children) + gap * (len(n.children) - 1))


def _place(p: Pen, n: Node, left: float, top: float, gap: float, step: float) -> None:
    span = _span(p, n, gap)
    n.cx, n.y = left + span / 2, top
    if not n.children:
        return
    if any(c.side for c in n.children):
        half = (span - gap) / 2
        for c in n.children:
            cs = _span(p, c, gap)
            slot = left if c.side == "<" else left + half + gap
            _place(p, c, slot + (half - cs) / 2, top + step, gap, step)
        return
    total = sum(_span(p, c, gap) for c in n.children) + gap * (len(n.children) - 1)
    x = n.cx - total / 2
    for c in n.children:
        cs = _span(p, c, gap)
        _place(p, c, x, top + step, gap, step)
        x += cs + gap


def _draw_node(p: Pen, n: Node) -> None:
    colour = p.colour(n.colour)
    pad = p.u * 0.55
    widths = [max(p.measure(k), p.u * 0.9) + 2 * pad for k in n.keys]
    x = n.cx - n.w / 2
    p.box(x, n.y, n.w, n.h, colour)
    if n.colour:                          # gone over twice, as when colouring a node in
        p.box(x - p.u * 0.06, n.y - p.u * 0.06, n.w + p.u * 0.12, n.h + p.u * 0.12, colour)
    n.anchors = [x]
    for i, (k, cw) in enumerate(zip(n.keys, widths)):
        p.text(k, x + cw / 2, n.y + n.h * 0.72)
        x += cw
        if i < len(n.keys) - 1:
            j = p.u * 0.06
            p.stroke([(x + p.rng.uniform(-j, j), n.y + p.rng.uniform(0, 2 * j)),
                      (x + p.rng.uniform(-j, j), n.y + n.h - p.rng.uniform(0, 2 * j))], colour)
        n.anchors.append(x)
    for i, c in enumerate(n.children):
        _draw_node(p, c)
        sx = n.cx
        if len(n.children) == len(n.keys) + 1 and len(n.keys) > 1:
            # one child per gap between keys: the pointer leaves from that gap
            sx = n.anchors[i] + (p.u * 0.35 if i == 0 else -p.u * 0.35 if i == len(n.keys) else 0)
        p.arrow((sx, n.y + n.h + p.u * 0.12), (c.cx, c.y - p.u * 0.15))


def _leaves(n: Node) -> list[Node]:
    return [n] if not n.children else [x for c in n.children for x in _leaves(c)]


def draw(text: str, t2h, ink: tuple[int, int, int], seed: int | None = None) -> Image.Image:
    """Lay out and draw a diagram block; raises DiagramError if it cannot be read."""
    trees = parse(text)
    rng = random.Random(seed if seed is not None else random.random())
    p = Pen(t2h, ink, rng)
    gap, step = p.u * 1.6, p.u * 3.6
    left = 0.0
    for i, tree in enumerate(trees):
        _size(p, tree.root)
        tgap = gap * 1.6 if tree.leaves else gap       # room for the links between leaves
        _place(p, tree.root, left, 0.0, tgap, step)
        _draw_node(p, tree.root)
        span = _span(p, tree.root, tgap)
        if tree.leaves:
            leaves = _leaves(tree.root)
            for a, b in zip(leaves, leaves[1:]):
                y = a.y + a.h * 0.5
                x0, x1 = a.cx + a.w / 2 + p.u * 0.2, b.cx - b.w / 2 - p.u * 0.2
                p.arrow((x0, y), (x1, y), head=p.u * 0.4)
                if tree.leaves == "<->":
                    p.arrow((x1, y + p.u * 0.02), (x0, y), head=p.u * 0.4)
        left += span
        if i < len(trees) - 1:            # before => after
            p.arrow((left + p.u * 1.2, step * 0.9), (left + p.u * 4.2, step * 0.9), head=p.u * 0.6)
            left += p.u * 5.4
    return p.render()
