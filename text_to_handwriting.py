"""
Text-to-Handwriting  --  realism pass.

Renders a text file as an image that looks hand-written, using the per-glyph
images in ./myfont/ (named by ASCII code, e.g. 65.png == 'A').

Why it looks like handwriting and not a font
---------------------------------------------
A font pastes the identical 'e' every time; a person never writes the same
letter twice. This engine breaks that uniformity:

  * Variants  -- if several images exist for one character (65.png, 65_1.png,
    65a.png ...) one is chosen at random for each occurrence.
  * Jitter    -- every single glyph gets its own random rotation, scale,
    baseline wobble, letter-spacing and ink darkness, so repeats never match.
  * Baseline  -- glyphs sit on a writing line (not top-aligned), with a slow
    per-line drift; g j p q y drop their tails below the line.
  * Alpha     -- an alpha mask is derived from ink darkness, so glyphs
    composite cleanly. Rotation and tight spacing no longer paint white
    boxes over neighbouring letters.

It also fixes the things that made the original unusable: it saves the output,
skips (instead of crashing on) unsupported characters, keeps real line breaks,
word-wraps, and spills onto new pages.

Usage
-----
    python text_to_handwriting.py [input.txt]

Output
------
    out/page_1.png, out/page_2.png, ...   and   out/handwriting.pdf
"""

from __future__ import annotations

import glob
import math
import os
import random
import sys

from PIL import Image, ImageChops, ImageDraw

# --------------------------------------------------------------------------- #
# Config -- tweak these to taste
# --------------------------------------------------------------------------- #
FONT_DIR = "myfont"                     # folder of glyph images
WORD_DIR = "wordfont"                   # whole-word images, used in preference to letters
USE_WORDS = True                        # False renders everything letter by letter
WORD_SCALE: float | None = None          # None derives it from the glyphs at runtime
BG_PATH = os.path.join(FONT_DIR, "bg.png")
OUT_DIR = "out"
DEFAULT_INPUT = "dummy.txt"

# Page margins (px)
MARGIN_L, MARGIN_T, MARGIN_R, MARGIN_B = 150, 180, 150, 200

# Metrics. Left as None => derived automatically from your glyph images, so the
# engine adapts to whatever scale your handwriting was scanned at. Override with
# a number to force a value.
LINE_HEIGHT: int | None = None          # vertical distance between writing lines
SPACE_WIDTH: int | None = None          # width of a space character

# --- Realism knobs. Set any to 0 to switch that effect off. -----------------
ROT_JITTER = 2.2                        # max rotation per glyph, +/- degrees
SCALE_JITTER = 0.05                     # max size change per glyph, +/- fraction
BASELINE_WOBBLE = 6                     # max vertical wobble per glyph, +/- px
LINE_DRIFT = 8                          # slow baseline drift across a line, px
KERN_JITTER = (-3, 4)                   # extra space between letters (min, max) px
MARGIN_JITTER = 6                        # ragged left margin, px of random indent
INK_MIN, INK_MAX = 0.78, 1.0            # per-glyph opacity (pen-pressure) range

DESCENDERS = set("gjpqy")               # tails that drop below the writing line
DESCENDER_DROP = 0.25                   # fraction of glyph height pushed down
RAISED = set("\"'*^`")                  # marks that hang high (quotes, apostrophe, * ^ `)
RAISE_FRAC = 0.20                       # how far up, as a fraction of the line height
CENTERED = set("+=<>~")                 # math symbols centred on the x-height axis
X_HEIGHT = 0                            # derived from the glyphs at runtime

INK_THRESHOLD = 240                     # luminance >= this is paper (transparent)
SEED: int | None = None                 # set an int for repeatable output

# --- Paper & scan realism. The rule lines are "printed" on the page, so they
#     skew together with the writing -- that's what sells a scanned sheet. -----
RULED = True                            # draw notebook rule lines
RULE_COLOR = (170, 195, 225)            # faint blue horizontal rule
RULE_WIDTH = 2
RULE_OFFSET = 8                         # rule sits this many px below the baseline
MARGIN_RULE = True                      # vertical margin line down the left
MARGIN_RULE_COLOR = (222, 150, 150)     # faint red margin line
SCAN_SKEW = 0.7                         # whole-page tilt, +/- degrees (0 = off)

# --- Ink & paper -----------------------------------------------------------
INK_COLOR: tuple[int, int, int] | None = (20, 26, 66)   # blue-black pen; None keeps the scan color
PAPER_TEXTURE = True                    # subtle off-white tint + grain + mottle
PAPER_TINT: tuple[int, int, int] | None = (252, 250, 244)  # warm off-white base (None = pure white)
PAPER_NOISE = 22                        # paper grain/mottle strength (gaussian sigma)
PAPER_GRAIN_ALPHA = 0.05                # fine per-pixel grain (0 = off)
PAPER_MOTTLE_ALPHA = 0.06               # soft large-scale mottle (0 = off)
PAPER_FILL = (244, 242, 236)            # corner fill for the scan skew (should match the paper)

# --- Markdown block layout --------------------------------------------------
# Handwriting has no bold and no type sizes, so structure is expressed the way
# a person writes it: headings centred and underlined, lists indented.
INDENT_STEP = 90                        # px of indent per list nesting level
HEADING_SCALE = {1: 1.30, 2: 1.15, 3: 1.05}     # glyph scale by heading level
# Gaps are in whole ruled lines. Anything fractional would walk the writing off
# the ruling, since the rules are a fixed grid and never move.
HEADING_GAP_BEFORE = 1                  # blank lines before a heading
HEADING_GAP_AFTER = 0                   # the underline already separates it
PARA_GAP = 1
UNDERLINE_DROP = 12                     # px below the baseline
UNDERLINE_WIDTH = 4

# --- Fatigue and corrections ------------------------------------------------
# A hand tires down a page: the writing grows a little and gets less tidy. And
# a page with no crossings-out at all is itself a tell, since real handwritten
# work has them.
FATIGUE = True
FATIGUE_JITTER = 0.7                    # extra rotation and wobble by the page foot
FATIGUE_GROWTH = 0.05                   # how much letters swell by the page foot
CORRECTIONS = False                     # off by default: it writes real mistakes
CORRECTION_RATE = 0.012                 # chance per eligible word

# --- Tables -----------------------------------------------------------------
TABLES_RULED = True                     # draw a ruled grid; False lists the rows
TABLE_PAD = 16                          # px of padding inside each cell
TABLE_MIN_COL = 110                     # a column never squeezes below this
FIGURE_LINES = 3                        # height of a figure box, in ruled lines

# --------------------------------------------------------------------------- #
# Glyph loading  (prepare once, cache, discover variants)
# --------------------------------------------------------------------------- #
_cache: dict[str, list[Image.Image] | None] = {}

# Optional progress sink, set for the duration of a render_pages() call. Page
# count is not known up front (pagination is discovered as text flows), so the
# only honest progress signal is "a new page was started".
_progress = None


def _report(stage: str, count: int = 0) -> None:
    if _progress is not None:
        try:
            _progress(stage, count)
        except Exception:
            pass                              # progress must never break a render


def _prepare(path: str) -> Image.Image:
    """Load a glyph, turn its light background transparent, trim to the ink."""
    im = Image.open(path).convert("RGBA")
    gray = im.convert("L")
    # Paper (light) -> alpha 0; ink (dark) -> alpha up to 255. This keeps the
    # natural anti-aliased edges of the pen stroke instead of a hard cut-out.
    alpha = gray.point(lambda p: 0 if p >= INK_THRESHOLD else 255 - p)
    im.putalpha(alpha)
    bbox = alpha.getbbox()                # crop to actual ink so spacing is tight
    if bbox:
        im = im.crop(bbox)
    if INK_COLOR is not None:
        # Recolor the stroke to a single pen color, keeping alpha (which still
        # encodes stroke darkness), so faint strokes read as lighter ink.
        tinted = Image.new("RGBA", im.size, tuple(INK_COLOR) + (0,))
        tinted.putalpha(im.getchannel("A"))
        im = tinted
    return im


def glyph_variants(ch: str) -> list[Image.Image] | None:
    """All prepared images for a character (real variants if present), or None."""
    if ch in _cache:
        return _cache[ch]

    scode = str(ord(ch))
    variants: list[Image.Image] = []
    for path in sorted(glob.glob(os.path.join(FONT_DIR, scode + "*.png"))):
        stem = os.path.splitext(os.path.basename(path))[0]
        rest = stem[len(scode):]
        # accept "65", "65_1", "65a"  -- reject "650" (a different code)
        if rest == "" or not rest[0].isdigit():
            variants.append(_prepare(path))

    _cache[ch] = variants or None
    return _cache[ch]


# --------------------------------------------------------------------------- #
# Whole-word images
# --------------------------------------------------------------------------- #
# A word written in one stroke keeps its joins, which letters pasted side by
# side cannot reproduce. Where an image exists it is used in preference to
# assembling the word, and everything else still falls back to letters.
_words: dict[str, tuple[Image.Image, int]] | None = None


def _load_words() -> dict[str, tuple[Image.Image, int]]:
    """Word image plus its baseline offset, measured from the top of the crop."""
    global _words
    if _words is not None:
        return _words
    _words = {}
    index = os.path.join(WORD_DIR, "index.json")
    if not USE_WORDS or not os.path.exists(index):
        return _words

    import json
    with open(index, encoding="utf-8") as fh:
        listing = json.load(fh)

    for label, name in listing.items():
        path = os.path.join(WORD_DIR, name)
        if not os.path.exists(path):
            continue
        im = Image.open(path).convert("RGBA")
        alpha = im.convert("L").point(lambda p: 0 if p >= INK_THRESHOLD else 255 - p)
        im.putalpha(alpha)
        box = alpha.getbbox()
        if not box:
            continue
        im = im.crop(box)
        if INK_COLOR is not None:
            tinted = Image.new("RGBA", im.size, tuple(INK_COLOR) + (0,))
            tinted.putalpha(im.getchannel("A"))
            im = tinted
        _words[label] = (im, _baseline_of(im))
    return _words


def _baseline_of(im: Image.Image) -> int:
    """Where the word sits on the line, as an offset from the top of its image.

    A word cannot simply be bottom-aligned: a descender in "guy" would shove the
    whole word upward. Most letters do rest on the line, so the commonest column
    bottom is the baseline and the descenders show up as outliers below it.
    """
    alpha = im.getchannel("A")
    width, height = alpha.size
    pixels = alpha.load()

    # Ink per row. Above the line every letter contributes; below it only the
    # descenders do, so the profile falls off a cliff at the baseline. Taking
    # the commonest column bottom instead fails on a short word like "of",
    # where the descender owns more columns than the letter resting on the line.
    rows = [sum(1 for x in range(width) if pixels[x, y] > 40) for y in range(height)]
    if not any(rows):
        return height

    # The baseline is the steepest fall in that profile, not a fixed fraction of
    # it. In a two-letter word like "of" the single descender still holds 35% of
    # the peak all the way down, so any threshold either misses it or cuts real
    # letters off. A word with no descender simply drops hardest at its foot,
    # which lands the baseline there, as it should.
    window = max(2, height // 20)
    best_y, best_drop = height, -1.0
    for y in range(height // 3, height):
        above = rows[max(0, y - window):y]
        below = rows[y:y + window]
        if not above or not below:
            continue
        drop = sum(above) / len(above) - sum(below) / len(below)
        if drop > best_drop:
            best_drop, best_y = drop, y
    return best_y


def split_token(token: str) -> tuple[str, str, str]:
    """Separate a word from any punctuation stuck to it, as in `shop.` or `(a)`."""
    marks = ".,!?;:\"'()[]"
    head = 0
    while head < len(token) and token[head] in marks:
        head += 1
    tail = len(token)
    while tail > head and token[tail - 1] in marks:
        tail -= 1
    return token[:head], token[head:tail], token[tail:]


def word_image(word: str) -> tuple[Image.Image, int] | None:
    return _load_words().get(word)


def measure(word: str, scale: float = 1.0) -> int:
    """Width of a word before jitter, used to decide where lines break."""
    head, core, tail = split_token(word)
    hit = word_image(core) if core else None
    if hit:
        width = int(hit[0].width * WORD_SCALE)
        width += sum(char_advance(c) for c in head + tail)
        return int(width * scale)
    return int(sum(char_advance(c) for c in word) * scale)


def wrap(words: list[str], width: int, scale: float = 1.0) -> list[list[str]]:
    """Group words into lines that fit inside `width`."""
    lines: list[list[str]] = []
    current: list[str] = []
    used = 0
    space = int(SPACE_WIDTH * scale)
    for word in words:
        ww = measure(word, scale)
        add = ww if not current else space + ww
        if current and used + add > width:
            lines.append(current)
            current, used = [word], ww
        else:
            current.append(word)
            used += add
    if current:
        lines.append(current)
    return lines


def char_advance(ch: str) -> int:
    """Nominal horizontal advance for a character (used for wrapping)."""
    if ch == " ":
        return SPACE_WIDTH
    vs = glyph_variants(ch)
    return vs[0].width if vs else 0


# --------------------------------------------------------------------------- #
# Per-glyph jitter
# --------------------------------------------------------------------------- #
def jittered(im: Image.Image, scale: float = 1.0, boost: float = 1.0) -> Image.Image:
    """Return a fresh, randomly perturbed copy of a glyph image.

    `scale` is the block-level size (headings draw larger). It is folded into
    the random size jitter so the glyph is only resampled once.
    """
    g = im
    s = scale * (1 + random.uniform(-SCALE_JITTER, SCALE_JITTER)) if SCALE_JITTER else scale
    if s != 1.0:
        g = g.resize((max(1, round(g.width * s)), max(1, round(g.height * s))),
                     Image.LANCZOS)
    if ROT_JITTER:
        rot = ROT_JITTER * boost
        g = g.rotate(random.uniform(-rot, rot),
                     resample=Image.BICUBIC, expand=True)
    if INK_MIN < 1.0:
        factor = random.uniform(INK_MIN, INK_MAX)
        if factor < 1.0:
            a = g.getchannel("A").point(lambda v: int(v * factor))
            g = g.copy()
            g.putalpha(a)
    return g


def add_paper_texture(page: Image.Image) -> Image.Image:
    """Give the flat page a faint off-white tint plus grain and soft mottle."""
    w, h = page.size
    if PAPER_TINT:
        page = ImageChops.multiply(page, Image.new("RGB", (w, h), PAPER_TINT))
    if PAPER_GRAIN_ALPHA:                 # fine per-pixel grain
        grain = Image.effect_noise((w, h), PAPER_NOISE).convert("RGB")
        page = Image.blend(page, grain, PAPER_GRAIN_ALPHA)
    if PAPER_MOTTLE_ALPHA:                # soft, cloudy large-scale variation
        sw, sh = max(1, w // 12), max(1, h // 12)
        mottle = Image.effect_noise((sw, sh), PAPER_NOISE)
        mottle = mottle.resize((w, h), Image.BILINEAR).convert("RGB")
        page = Image.blend(page, mottle, PAPER_MOTTLE_ALPHA)
    return page


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def derive_metrics() -> None:
    """Fill in LINE_HEIGHT / SPACE_WIDTH from the glyph images if not set."""
    global LINE_HEIGHT, SPACE_WIDTH, X_HEIGHT
    heights, widths = [], []
    for code in range(33, 127):
        vs = glyph_variants(chr(code))
        if vs:
            heights += [v.height for v in vs]
            widths += [v.width for v in vs]
    if not heights:
        raise SystemExit("No glyph images found in '{}'.".format(FONT_DIR))

    heights.sort()
    widths.sort()
    tall = heights[int(len(heights) * 0.95)]        # ~tallest, ignoring outliers
    med_w = widths[len(widths) // 2]

    if LINE_HEIGHT is None:
        # room for an ascender above the line and a descender below it, + gap
        LINE_HEIGHT = int(tall * (1 + DESCENDER_DROP) * 1.15)
    if SPACE_WIDTH is None:
        SPACE_WIDTH = max(1, int(med_w * 0.72))

    # x-height, from letters that have neither ascender nor descender
    xs = []
    for code in (97, 99, 101, 109, 110, 111, 114, 115, 117, 118, 119, 120, 122):
        vs = glyph_variants(chr(code))
        if vs:
            xs += [v.height for v in vs]
    X_HEIGHT = sorted(xs)[len(xs) // 2] if xs else int(tall * 0.5)

    # Word images and letter glyphs come from different sheets, written at
    # different sizes, so match them on x-height. For a word with no ascender
    # or descender the baseline offset is the x-height.
    global WORD_SCALE
    if WORD_SCALE is None:
        flat = ("on", "one", "or", "no", "so", "we", "as", "is", "in", "are",
                "was", "us", "an", "a", "our", "see", "some", "were", "new")
        heights = sorted(w[1] for w in (word_image(f) for f in flat) if w)
        WORD_SCALE = (X_HEIGHT / heights[len(heights) // 2]) if heights else 1.0


# --------------------------------------------------------------------------- #
# Page rendering
# --------------------------------------------------------------------------- #
class Sheet:
    """A stack of pages you can write glyphs onto; starts a new page on demand."""

    def __init__(self) -> None:
        self.template = Image.open(BG_PATH).convert("RGB")
        self.width, self.height = self.template.size
        self.pages: list[Image.Image] = []
        self.missing: list[str] = []          # characters with no glyph
        self.scale = 1.0                      # block-level glyph size
        self._new_page()

    def _new_page(self) -> None:
        page = self.template.copy()
        if PAPER_TEXTURE:
            page = add_paper_texture(page)
        if RULED:
            self._draw_ruling(page)
        self.page = page
        self.pages.append(page)
        _report("page", len(self.pages))
        self.x = MARGIN_L
        self.baseline = MARGIN_T + LINE_HEIGHT
        self._drift_phase = 0.0

    def _draw_ruling(self, page: Image.Image) -> None:
        """Pre-print faint notebook rules (and a margin line) onto the page."""
        draw = ImageDraw.Draw(page)
        x0, x1 = 60, self.width - 60
        y = MARGIN_T + LINE_HEIGHT + RULE_OFFSET      # aligned to the writing lines
        while y <= self.height - MARGIN_B + RULE_OFFSET:
            draw.line([(x0, y), (x1, y)], fill=RULE_COLOR, width=RULE_WIDTH)
            y += LINE_HEIGHT
        if MARGIN_RULE:
            mx = MARGIN_L - 30
            draw.line([(mx, MARGIN_T - 40), (mx, self.height - MARGIN_B + 40)],
                      fill=MARGIN_RULE_COLOR, width=RULE_WIDTH)

    @property
    def right(self) -> int:
        return self.width - MARGIN_R

    def newline(self, left: int | None = None, advance: float = 1.0) -> None:
        # Always a whole number of ruled lines: a person writing on ruled paper
        # moves down to the next rule, never to a fraction of one.
        self.x = (MARGIN_L if left is None else left) + random.randint(0, MARGIN_JITTER)
        self.baseline += LINE_HEIGHT * max(1, round(advance))
        self._drift_phase = 0.0
        if self.baseline + LINE_HEIGHT > self.height - MARGIN_B:
            self._new_page()

    def gap(self, lines: float) -> None:
        """Leave whole blank ruled lines between blocks."""
        self.baseline += LINE_HEIGHT * max(0, round(lines))
        if self.baseline + LINE_HEIGHT > self.height - MARGIN_B:
            self._new_page()

    def snap_to_rule(self) -> None:
        """Put the baseline back on the ruling, moving forward only.

        Figures and tables are sized in pixels rather than lines, so they leave
        the baseline between rules. Everything after them would inherit that
        offset and never recover.
        """
        first = MARGIN_T + LINE_HEIGHT
        steps = math.ceil((self.baseline - first) / LINE_HEIGHT - 1e-6)
        self.baseline = first + max(0, steps) * LINE_HEIGHT
        if self.baseline + LINE_HEIGHT > self.height - MARGIN_B:
            self._new_page()

    def _pen(self) -> tuple[int, int, int]:
        return tuple(INK_COLOR) if INK_COLOR else (25, 30, 60)

    def _pen_stroke(self, x0: float, y: float, x1: float, width: int) -> None:
        """A wobbly horizontal pen stroke, used for underlines and rules."""
        if x1 <= x0:
            return
        steps = max(2, int((x1 - x0) / 70))
        pts = [(x0 + (x1 - x0) * i / steps, y + random.uniform(-2.0, 2.0))
               for i in range(steps + 1)]
        ImageDraw.Draw(self.page).line(pts, fill=self._pen(), width=width, joint="curve")

    def underline(self, x0: float, x1: float) -> None:
        self._pen_stroke(x0, self.baseline + UNDERLINE_DROP, x1, UNDERLINE_WIDTH)

    def hand_rule(self) -> None:
        """A horizontal rule, drawn as a pen stroke across the text column."""
        self._pen_stroke(MARGIN_L + 20, self.baseline - X_HEIGHT // 2,
                         self.right - 20, UNDERLINE_WIDTH)
        self.newline()

    def figure_box(self, label: str) -> None:
        """A hand-drawn box standing in for an image, with its caption inside.

        Sized in whole ruled lines, so the box lines up with the ruling and its
        caption sits on a rule like every other line on the page.
        """
        if self.baseline + LINE_HEIGHT * FIGURE_LINES > self.height - MARGIN_B:
            self._new_page()

        start = self.baseline
        top = start - X_HEIGHT - TABLE_PAD
        bottom = start + LINE_HEIGHT * FIGURE_LINES
        x0, x1 = MARGIN_L + 20, self.right - 20
        w = max(2, UNDERLINE_WIDTH - 1)
        self._pen_stroke(x0, top, x1, w)
        self._pen_stroke(x0, bottom, x1, w)
        for x in (x0, x1):                       # verticals, drawn the same wobbly way
            steps = max(2, int((bottom - top) / 70))
            pts = [(x + random.uniform(-2.0, 2.0), top + (bottom - top) * i / steps)
                   for i in range(steps + 1)]
            ImageDraw.Draw(self.page).line(pts, fill=self._pen(), width=w, joint="curve")

        self.baseline = start + LINE_HEIGHT * (FIGURE_LINES // 2)   # middle rule
        self.put_line(label.split(), MARGIN_L, self.right - MARGIN_L, align="center")

        self.baseline = bottom
        self.gap(1)

    def vrule(self, x: float, y0: float, y1: float, width: int) -> None:
        """A wobbly vertical pen stroke, for table column dividers."""
        steps = max(2, int((y1 - y0) / 70))
        pts = [(x + random.uniform(-2.0, 2.0), y0 + (y1 - y0) * i / steps)
               for i in range(steps + 1)]
        ImageDraw.Draw(self.page).line(pts, fill=self._pen(), width=width, joint="curve")

    def put_word(self, token: str, scale: float = 1.0) -> bool:
        """Draw a whole captured word if one exists. False means use letters.

        The image is placed by its baseline, not its bottom edge, so a
        descender hangs below the line instead of pushing the word up it.
        """
        head, core, tail = split_token(token)
        hit = word_image(core) if core else None
        if hit is None:
            return False

        for ch in head:                       # opening quote or bracket
            self.put(ch)

        image, base = hit
        tired = self._fatigue()
        size = WORD_SCALE * scale * (1 + FATIGUE_GROWTH * tired)
        if abs(size - 1.0) > 0.01:
            image = image.resize((max(1, round(image.width * size)),
                                  max(1, round(image.height * size))), Image.LANCZOS)
            base = int(base * size)

        # The same jitter a single glyph gets, applied to the word as one piece.
        if ROT_JITTER:
            angle = random.uniform(-ROT_JITTER, ROT_JITTER) * 0.6 * (1 + FATIGUE_JITTER * tired)
            grown = image.rotate(angle, resample=Image.BICUBIC, expand=True)
            base += (grown.height - image.height) // 2
            image = grown
        if INK_MIN < 1.0:
            factor = random.uniform(INK_MIN, INK_MAX)
            if factor < 1.0:
                image = image.copy()
                image.putalpha(image.getchannel("A").point(lambda v: int(v * factor)))

        sway = max(1, int(BASELINE_WOBBLE * (1 + FATIGUE_JITTER * tired)))
        y = self.baseline - base + random.randint(-sway, sway) + self._drift()
        self.page.paste(image, (int(self.x), int(y)), image)
        self.x += image.width + random.randint(*KERN_JITTER)

        for ch in tail:                       # comma, full stop, closing bracket
            self.put(ch)
        return True

    def put_words(self, words: list[str], x: int, scale: float = 1.0) -> None:
        """Write words starting at x without moving to the next line."""
        self.scale = scale
        space = int(SPACE_WIDTH * scale)
        self.x = x
        for i, word in enumerate(words):
            for ch in word:
                self.put(ch)
            if i < len(words) - 1:
                self.x += space
        self.scale = 1.0

    def draw_table(self, grid: list[list[str]], widths: list[int]) -> None:
        """Draw a hand-ruled table: cells wrap inside their column."""
        pad = TABLE_PAD
        col_w = [w + 2 * pad for w in widths]
        rule = max(2, UNDERLINE_WIDTH - 1)
        edges = [MARGIN_L]
        for w in col_w:
            edges.append(edges[-1] + w)

        for row in grid:
            cells = [wrap(cell.split(), widths[i]) or [[]] for i, cell in enumerate(row)]
            tall = max(len(c) for c in cells)
            height = tall * LINE_HEIGHT

            if self.baseline + height > self.height - MARGIN_B:
                self._new_page()

            top = self.baseline - X_HEIGHT - pad // 2
            first = self.baseline
            for line_no in range(tall):
                self.baseline = first + line_no * LINE_HEIGHT
                for i, lines in enumerate(cells):
                    if line_no < len(lines):
                        self.put_words(lines[line_no], edges[i] + pad)
            self.baseline = first + (tall - 1) * LINE_HEIGHT
            bottom = self.baseline + pad

            self._pen_stroke(edges[0], top, edges[-1], rule)        # rule above the row
            for x in edges:
                self.vrule(x, top, bottom, rule)
            # whole lines, so rows keep sitting on the page ruling
            self.baseline = first + tall * LINE_HEIGHT
            if self.baseline + LINE_HEIGHT > self.height - MARGIN_B:
                self._new_page()

        self._pen_stroke(edges[0], self.baseline - LINE_HEIGHT + pad,
                         edges[-1], rule)                           # close the last row
        self.snap_to_rule()

    def put_line(self, words: list[str], left: int, width: int,
                 align: str = "left", underline: bool = False,
                 scale: float = 1.0) -> None:
        """Draw one already-wrapped line, then move to the next."""
        self.scale = scale
        space = int(SPACE_WIDTH * scale)
        line_w = sum(measure(w, scale) for w in words) + space * max(0, len(words) - 1)
        x0 = left + max(0, (width - line_w) // 2) if align == "center" else left

        self.x = x0 + random.randint(0, MARGIN_JITTER)
        start = self.x
        slack = width - line_w                   # room a correction may borrow
        for i, word in enumerate(words):
            # Start the word wrong, cross it out, write it again. Only where
            # there is spare width, so a correction can never push past the margin.
            if (CORRECTIONS and len(word) > 3 and align == "left"
                    and random.random() < CORRECTION_RATE):
                stub = word[:random.randint(2, min(4, len(word) - 1))]
                cost = measure(stub, scale) + space
                if cost < slack:
                    slack -= cost
                    mark = self.x
                    for ch in stub:
                        self.put(ch)
                    self.strike(mark, self.x)
                    self.x += space // 2
            if not self.put_word(word, scale):
                for ch in word:
                    self.put(ch)
            if i < len(words) - 1:
                self.x += space
        if underline:
            self.underline(start, self.x)
        self.scale = 1.0
        self.newline(left, advance=max(1.0, scale))

    def _drift(self) -> int:
        # a gentle sine drift so the writing line is never laser-straight
        if not LINE_DRIFT:
            return 0
        self._drift_phase += 0.06
        return int(LINE_DRIFT * math.sin(self._drift_phase))

    def _fatigue(self) -> float:
        """0 at the top of the page, 1 at the foot."""
        if not FATIGUE:
            return 0.0
        top = MARGIN_T + LINE_HEIGHT
        bottom = self.height - MARGIN_B
        return min(1.0, max(0.0, (self.baseline - top) / max(1, bottom - top)))

    def strike(self, x0: float, x1: float) -> None:
        """Cross out what was just written."""
        self._pen_stroke(x0, self.baseline - X_HEIGHT // 2, x1,
                         max(2, UNDERLINE_WIDTH - 1))

    def put(self, ch: str) -> None:
        vs = glyph_variants(ch)
        if not vs:
            return
        tired = self._fatigue()
        boost = 1 + FATIGUE_JITTER * tired
        scale = self.scale * (1 + FATIGUE_GROWTH * tired)

        advance = int(vs[0].width * scale)        # rhythm set by the un-jittered width
        g = jittered(random.choice(vs), scale, boost)

        sway = max(1, int(BASELINE_WOBBLE * boost))
        wobble = random.randint(-sway, sway) + self._drift()
        x = self.x + (advance - g.width) // 2     # keep rotation-expansion centred
        if ch in CENTERED:                        # math symbols float on the x-height axis
            y = self.baseline - X_HEIGHT // 2 - g.height // 2 + wobble
        else:
            drop = int(DESCENDER_DROP * g.height) if ch in DESCENDERS else 0
            lift = int(RAISE_FRAC * LINE_HEIGHT) if ch in RAISED else 0
            y = self.baseline - g.height + drop - lift + wobble

        self.page.paste(g, (x, y), g)
        self.x += advance + random.randint(*KERN_JITTER)


def render(text: str) -> Sheet:
    sheet = Sheet()
    missing: set[str] = set()

    column = sheet.right - MARGIN_L
    for line in text.split("\n"):
        # track unsupported characters so we can warn instead of crashing
        for ch in line:
            if ch != " " and glyph_variants(ch) is None:
                missing.add(ch)

        words = line.split()
        if not words:
            sheet.newline()                        # blank line from the source
            continue
        for wrapped in wrap(words, column):
            sheet.put_line(wrapped, MARGIN_L, column)

    sheet.missing = sorted(missing)
    if missing:
        # ascii(), not repr(): a Windows console is cp1252, and printing a
        # skipped character it cannot encode would raise and kill the render
        # after all the drawing was already done.
        shown = " ".join(ascii(c) for c in sheet.missing)
        print("Skipped {} unsupported character(s): {}".format(len(missing), shown))
    return sheet


def table_layout(rows: list[list[str]], available: int) -> tuple[list[list[str]], list[int]]:
    """Pad rows to a rectangle and size columns to fit the text column."""
    cols = max(len(r) for r in rows)
    grid = [list(r) + [""] * (cols - len(r)) for r in rows]

    # measure() is the un-jittered width, but drawing adds kerning jitter and
    # fatigue growth per glyph, so a column sized to the measurement alone ends
    # up slightly too narrow and its text crosses the divider.
    slack = 1.12
    natural = [int(max((measure(row[i]) for row in grid), default=1) * slack)
               for i in range(cols)]
    # a column can never be narrower than its longest unbreakable word
    floors = [int(max((measure(w) for row in grid for w in row[i].split()), default=1) * slack)
              for i in range(cols)]

    inner = available - 2 * TABLE_PAD * cols
    total = sum(natural) or 1
    if total > inner:                       # squeeze proportionally, but not past the floors
        shrink = inner / total
        return grid, [max(TABLE_MIN_COL, floors[i], int(n * shrink))
                      for i, n in enumerate(natural)]
    return grid, [max(1, n) for n in natural]


def render_markdown(md_text: str) -> Sheet:
    """Render Markdown, expressing its structure in handwriting conventions.

    Headings are centred and underlined rather than bold, lists are indented
    with a drawn marker, and figures become a labelled box. Tables degrade to
    indented rows for now; ruling real tables is a later job.
    """
    from markdown_blocks import to_blocks

    sheet = Sheet()
    missing: set[str] = set()
    column = sheet.right - MARGIN_L

    def track(text: str) -> None:
        for ch in text:
            if ch != " " and glyph_variants(ch) is None:
                missing.add(ch)

    def flow(words: list[str], left: int, width: int, scale: float = 1.0) -> None:
        for line in wrap(words, width, scale):
            sheet.put_line(line, left, width, scale=scale)

    blocks = to_blocks(md_text)
    previous = ""
    index = 0
    while index < len(blocks):
        block = blocks[index]

        # Consecutive table rows belong to one grid, so gather them up first.
        if block.kind == "table" and TABLES_RULED:
            rows = []
            while index < len(blocks) and blocks[index].kind == "table":
                rows.append(blocks[index].cells)
                index += 1
            for row in rows:
                track(" ".join(row))
            sheet.gap(0.3)
            grid, widths = table_layout(rows, column)
            sheet.draw_table(grid, widths)
            sheet.gap(0.4)
            previous = "table"
            continue
        index += 1

        # Runs of list items, table rows and code lines sit tight against each
        # other, but the run as a whole needs separating from what follows.
        if previous in ("item", "table", "code") and block.kind != previous:
            sheet.gap(PARA_GAP)
        previous = block.kind

        if block.kind == "rule":
            sheet.hand_rule()
            sheet.gap(0.3)
            continue

        if block.kind == "image":
            track(block.text)
            sheet.figure_box(block.text or "figure")
            continue

        if block.kind == "heading":
            level = min(max(block.level, 1), 3)
            scale = HEADING_SCALE.get(level, 1.0)
            track(block.text)
            sheet.gap(HEADING_GAP_BEFORE)
            align = "center" if level == 1 else "left"
            for line in wrap(block.text.split(), column, scale):
                sheet.put_line(line, MARGIN_L, column, align=align,
                               underline=True, scale=scale)
            sheet.gap(HEADING_GAP_AFTER)
            continue

        if block.kind == "item":
            track(block.text + block.marker)
            indent = MARGIN_L + INDENT_STEP * block.level
            hang = indent + measure(block.marker) + SPACE_WIDTH
            lines = wrap(block.text.split(), sheet.right - hang)
            # marker sits on the first line, wrapped lines align under the text
            sheet.put_line([block.marker] + lines[0], indent, sheet.right - indent)
            for line in lines[1:]:
                sheet.put_line(line, hang, sheet.right - hang)
            continue

        if block.kind == "quote":
            track(block.text)
            left = MARGIN_L + INDENT_STEP
            flow(block.text.split(), left, sheet.right - left)
            sheet.gap(PARA_GAP)
            continue

        if block.kind == "code":
            track(block.text)
            left = MARGIN_L + INDENT_STEP
            if block.text.strip():
                sheet.put_line(block.text.split(), left, sheet.right - left)
            else:
                sheet.newline()
            continue

        if block.kind == "table":
            row = "   ".join(block.cells)
            track(row)
            left = MARGIN_L + INDENT_STEP // 2
            flow(row.split(), left, sheet.right - left)
            continue

        track(block.text)                              # paragraph
        flow(block.text.split(), MARGIN_L, column)
        sheet.gap(PARA_GAP)

    sheet.missing = sorted(missing)
    if missing:
        # ascii(), not repr(): a Windows console is cp1252, and printing a
        # skipped character it cannot encode would raise and kill the render
        # after all the drawing was already done.
        shown = " ".join(ascii(c) for c in sheet.missing)
        print("Skipped {} unsupported character(s): {}".format(len(missing), shown))
    return sheet


def render_pages(text: str, as_markdown: bool = False,
                 on_progress=None) -> tuple[list[Image.Image], list[str]]:
    """Render text and return the finished page images plus any skipped chars.

    This is the entry point for anything embedding the renderer (the web app
    uses it). main() is just a file-in, file-out wrapper around it.

    The skew is applied last so the paper, rules included, rotates as one and
    the exposed corners fill with the paper colour: the scanner-bed look.
    """
    global _progress
    _progress = on_progress
    try:
        derive_metrics()
        sheet = render_markdown(text) if as_markdown else render(text)
    finally:
        _progress = None

    _report_finish = on_progress
    if _report_finish:
        _report_finish("skew", len(sheet.pages))

    finals = []
    for page in sheet.pages:
        if SCAN_SKEW:
            fill = PAPER_FILL if PAPER_TEXTURE else (255, 255, 255)
            page = page.rotate(random.uniform(-SCAN_SKEW, SCAN_SKEW),
                               resample=Image.BICUBIC, expand=False,
                               fillcolor=fill)
        finals.append(page)
    return finals, sheet.missing


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> None:
    if SEED is not None:
        random.seed(SEED)

    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (IndexError, FileNotFoundError):
        print("Could not read '{}'. Using default '{}'.".format(path, DEFAULT_INPUT))
        with open(DEFAULT_INPUT, "r", encoding="utf-8") as fh:
            text = fh.read()

    # A .md file is rendered as structured Markdown; anything else as plain text.
    finals, _ = render_pages(text, as_markdown=path.lower().endswith(".md"))

    os.makedirs(OUT_DIR, exist_ok=True)
    for i, page in enumerate(finals, 1):
        page.save(os.path.join(OUT_DIR, "page_{}.png".format(i)))
    pdf = os.path.join(OUT_DIR, "handwriting.pdf")
    finals[0].save(pdf, save_all=True, append_images=finals[1:])

    print("Wrote {} page(s) to '{}/' (line height {}, space {}).".format(
        len(finals), OUT_DIR, LINE_HEIGHT, SPACE_WIDTH))


if __name__ == "__main__":
    main()
