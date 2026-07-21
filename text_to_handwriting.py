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
from concurrent.futures import ThreadPoolExecutor

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
MARGIN_L, MARGIN_T, MARGIN_R, MARGIN_B = 150, 100, 150, 100

# Metrics. Left as None => derived automatically from your glyph images, so the
# engine adapts to whatever scale your handwriting was scanned at. Override with
# a number to force a value.
LINE_HEIGHT: int | None = None          # vertical distance between writing lines
SPACE_WIDTH: int | None = None          # width of a space character

# Handwriting is specified in millimetres, the way ruled paper is, and turned
# into pixels from the page width. Set any of these to None to fall back to
# deriving it from the glyphs instead.
PAGE_WIDTH_MM = 210.0                   # what the sheet is taken to be, A4 wide
X_HEIGHT_MM: float | None = 3.0         # height of a c e m n o
LINE_HEIGHT_MM: float | None = 8.4      # baseline to baseline; 33 lines on A4
WORD_GAP_MM: float | None = 4.0         # space between words

GLYPH_SCALE = 1.0                       # set from X_HEIGHT_MM when metrics derive
SPACE_RATIO = 0.85                      # only used when WORD_GAP_MM is None

# --- Realism knobs. Set any to 0 to switch that effect off. -----------------
ROT_JITTER = 2.2                        # max rotation per glyph, +/- degrees
SCALE_JITTER = 0.05                     # max size change per glyph, +/- fraction
BASELINE_WOBBLE = 6                     # max vertical wobble per glyph, +/- px
LINE_DRIFT = 8                          # slow baseline drift across a line, px
KERN_JITTER = (-3, 4)                   # extra space between letters (min, max) px
MARGIN_JITTER = 6                        # ragged left margin, px of random indent
INK_MIN, INK_MAX = 0.78, 1.0            # per-glyph opacity (pen-pressure) range

# Letters with an x-height body and a tail under it. These are placed by the
# body, not by a fraction of their height: see _drop_below_line. The Greek
# letters follow the same anatomy: gamma and mu carry their bodies on the line
# with the tail below, exactly like g and p.
DESCENDERS = set("gjpqyγμηρχ")
# f belongs with them in spirit, but its body reaches well above the x-height,
# so the body rule would bury it. It keeps the fractional drop. Left out of both
# sets it was aligned like an x-height letter, which sat its tail on the rule
# and made every f read as a t.
TAILED_ASCENDERS = set("fβφψζξ")       # rise past the x-height and hang a tail
# Marks whose tail crosses the writing line rather than resting on it. Without
# this a comma sits entirely above the rule and reads as a 9.
HANGING = set(",;")
DESCENDER_DROP = 0.25                   # fraction of glyph height pushed down
HANG_FRAC = 0.62                        # same, for HANGING marks
RAISED = set("\"'*^`")                  # marks that hang high (quotes, apostrophe, * ^ `)
# Quotes were written once, in the closing shape: thick at the top, tapering
# down to the left. That is right for the mark that ends a quotation and wrong
# for the one that opens it, and using the one glyph for both had every
# quotation leaning the same way at both ends. An opening quote is the same
# stroke mirrored, so it is flipped rather than captured again.
MIRRORED_OPENING = set("\"'")
# What can stand before an opening quote: nothing, a space, or a bracket. After
# a letter or a full stop, a quote is closing.
OPENS_AFTER = set(" \t([{-—")
RAISE_FRAC = 0.20                       # how far up, as a fraction of the line height
CENTERED = set("+=<>~-∈∗∞")             # centred on the x-height axis; a hyphen
                                        # left on the baseline reads as _
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

# Cropped display equations arrive as images rendered from the PDF, and are
# traced onto the page as ink rather than boxed, the way a person copies an
# equation carefully into notes. The typeset strokes are thinner than a pen,
# so the mask is thickened, and the type's x-height is mapped onto the hand's.
EQ_DPI = 300                            # what converters render the crops at
EQ_SOURCE_X_PT = 4.5                    # x-height of 10pt Computer Modern, in points
EQ_MAX_WIDTH = 0.86                     # of the writing column

# --------------------------------------------------------------------------- #
# Glyph loading  (prepare once, cache, discover variants)
# --------------------------------------------------------------------------- #
_cache: dict[str, list[Image.Image] | None] = {}

# The Sheet currently being written, for cleanup when a render is abandoned.
_live_sheet = None

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
    if GLYPH_SCALE != 1.0:                # resize once here, so all metrics follow
        im = im.resize((max(1, round(im.width * GLYPH_SCALE)),
                        max(1, round(im.height * GLYPH_SCALE))), Image.LANCZOS)
    if INK_COLOR is not None:
        # Recolor the stroke to a single pen color, keeping alpha (which still
        # encodes stroke darkness), so faint strokes read as lighter ink.
        tinted = Image.new("RGBA", im.size, tuple(INK_COLOR) + (0,))
        tinted.putalpha(im.getchannel("A"))
        im = tinted
    return im


# Greek letters that are the same drawing as a Latin one. Omicron is o, and
# most Greek capitals are indistinguishable from Latin capitals in any hand,
# so they borrow the captured letters instead of being drawn imitations.
GLYPH_ALIASES = {
    "ο": "o",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
}


def glyph_variants(ch: str) -> list[Image.Image] | None:
    """All prepared images for a character (real variants if present), or None."""
    if ch in _cache:
        return _cache[ch]

    scode = str(ord(GLYPH_ALIASES.get(ch, ch)))
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
JITTER_POOL = 4                         # pre-jittered copies kept per glyph bucket
JITTER_POOL_MAX = 6000                  # total pooled images before the pool resets
_SCALE_STEP = 0.025                     # scale bucket width for the pool key
_TIRED_AT = 1.35                        # boost above this counts as the tired bucket

_jitter_pool: dict[tuple, list[Image.Image]] = {}
_jitter_pooled = 0                      # images currently held, to bound memory


def reset_glyph_caches() -> None:
    """Drop every cached glyph, the prepared words, and the jitter pool.

    The pool holds glyphs that were already tinted with the current ink, so
    anything changing how a glyph is prepared, a new pen colour or a new glyph
    scale, has to clear the pool too. Clearing only the glyph cache would leave
    the pool handing out the old ink for as long as the process lived.
    """
    global _words, _jitter_pooled
    _cache.clear()
    _jitter_pool.clear()
    _word_pool.clear()
    _jitter_pooled = 0
    _words = None


def pool_key(ch: str, variant: int, flipped: bool,
             scale: float, boost: float) -> tuple:
    """Bucket a glyph's jitter inputs so repeated letters can share their work.

    Fatigue moves scale and rotation continuously down the page, so no two
    calls ever asked for exactly the same thing and every glyph was resampled
    and rotated from scratch. The movement is what matters, not its precision:
    the angle inside the range is random anyway, so rounding the range itself
    to a few steps cannot be seen. Bucketing turns a unique request per glyph
    into a handful per character, which is what makes a pool possible at all.
    """
    return (ch, variant, flipped,
            round(scale / _SCALE_STEP), boost >= _TIRED_AT)


def jittered(im: Image.Image, scale: float = 1.0, boost: float = 1.0,
             key: tuple | None = None) -> Image.Image:
    """Return a randomly perturbed copy of a glyph image.

    With a `key`, the first few perturbations for that bucket are kept and
    reused instead of recomputed. A letter is drawn thousands of times in a
    document but only ever looks like a few of itself: keeping four, on top of
    the several photographs of each letter already on file, gives a dozen or
    more distinct renderings per character. Real handwriting repeats itself
    more than that.
    """
    global _jitter_pooled
    if key is None:
        return _jitter(im, scale, boost)

    pool = _jitter_pool.get(key)
    if pool is None:
        if _jitter_pooled >= JITTER_POOL_MAX:      # bounded, so a long document
            _jitter_pool.clear()                   # cannot grow the pools forever
            _word_pool.clear()
            _jitter_pooled = 0
        pool = _jitter_pool[key] = []
    if len(pool) < JITTER_POOL:
        g = _jitter(im, scale, boost)
        pool.append(g)
        _jitter_pooled += 1
        return g
    return random.choice(pool)


def _skew_matrix(w: int, h: int, angle: float) -> list[float]:
    """Pillow's own rotate-about-centre matrix, mapping output back to input."""
    rad = -math.radians(angle % 360.0)
    cos, sin = round(math.cos(rad), 15), round(math.sin(rad), 15)
    cx, cy = w / 2.0, h / 2.0
    m = [cos, sin, 0.0, -sin, cos, 0.0]
    m[2] = cos * -cx + sin * -cy + cx
    m[5] = -sin * -cx + cos * -cy + cy
    return m


def _skew_bands(page: Image.Image, angle: float, fill, bands: int) -> list:
    """Split a page rotation into independent horizontal band jobs.

    A rotation does not have to be done in one piece: every output pixel is a
    function of the same matrix, so a band can be computed on its own provided
    it still samples the whole source and carries its own origin in the
    matrix's constant terms. Splitting lets a short document put every core on
    one page instead of leaving all but one idle.
    """
    w, h = page.size
    a, b, c, d, e, f = _skew_matrix(w, h, angle)
    step = -(-h // bands)                      # ceiling, so the last band is short

    def make(y0: int):
        bh = min(step, h - y0)
        m = (a, b, c + b * y0, d, e, f + e * y0)
        return lambda: page.transform((w, bh), Image.AFFINE, m,
                                      Image.BICUBIC, fillcolor=fill)

    return [(y0, make(y0)) for y0 in range(0, h, step)]


def _skew_page(page: Image.Image, angle: float, fill, pool) -> Image.Image:
    """Rotate one page, its bands computed across the pool."""
    out = Image.new(page.mode, page.size)
    jobs = _skew_bands(page, angle, fill, _workers())
    for (y0, _), strip in zip(jobs, pool.map(lambda j: j[1](), jobs)):
        out.paste(strip, (0, y0))
    return out


def _workers() -> int:
    """Threads to use for the page-sized image work.

    Capped rather than taken from the core count alone: the server renders
    several documents at once, and a machine-wide pool per request would have
    them fighting each other for the same cores.
    """
    return max(1, min(8, (os.cpu_count() or 2)))


WORD_POOL = 6                           # pre-jittered copies kept per word bucket
_word_pool: dict[tuple, list[tuple[Image.Image, int]]] = {}


def _jitter_word(im: Image.Image, base: int, size: float, boost: float,
                 key: tuple) -> tuple[Image.Image, int]:
    """Resize, rotate and ink a whole captured word, reusing earlier copies.

    A word image is far larger than a glyph, so rotating one costs perhaps
    thirty times as much, and with glyph jitter pooled this became the single
    most expensive thing in the render. Prose leans hard on a small set of
    words, so keeping a few jittered copies of each pays off immediately. The
    baseline offset travels with the image because rotation grows the canvas
    and moves where the word sits on the line.
    """
    global _jitter_pooled
    pool = _word_pool.get(key)
    if pool is None:
        pool = _word_pool[key] = []
    if len(pool) >= WORD_POOL:
        return random.choice(pool)

    if abs(size - 1.0) > 0.01:
        im = im.resize((max(1, round(im.width * size)),
                        max(1, round(im.height * size))), Image.LANCZOS)
        base = int(base * size)
    # The same jitter a single glyph gets, applied to the word as one piece.
    if ROT_JITTER:
        angle = random.uniform(-ROT_JITTER, ROT_JITTER) * 0.6 * boost
        grown = im.rotate(angle, resample=Image.BICUBIC, expand=True)
        base += (grown.height - im.height) // 2
        im = grown
    if INK_MIN < 1.0:
        factor = random.uniform(INK_MIN, INK_MAX)
        if factor < 1.0:
            im = im.copy()
            im.putalpha(im.getchannel("A").point(lambda v: int(v * factor)))

    pool.append((im, base))
    _jitter_pooled += 1
    return im, base


def _jitter(im: Image.Image, scale: float = 1.0, boost: float = 1.0) -> Image.Image:
    """Perturb a glyph once.

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


_NOISE_PAD = 48                         # slack in a cached field, for offset cuts
_TEXTURE_POOL = 4                       # pre-mixed grain layers kept per page size
_noise_fields: dict[tuple, Image.Image] = {}
_tint_layers: dict[tuple, Image.Image] = {}
_texture_layers: dict[tuple, list[Image.Image]] = {}


def _noise_window(w: int, h: int, sigma: int, rgb: bool, rng) -> Image.Image:
    """A noise patch of (w, h), cut at a random offset from one cached field.

    effect_noise fills a whole page with gaussian noise on every call, which
    made paper texture a third of the render. The field is generated once per
    size, a little larger than the page, and each page cuts its own window
    from a random offset. Every page still gets grain of its own; only the
    generating is shared.
    """
    key = (w, h, sigma, rgb)
    field = _noise_fields.get(key)
    if field is None:
        field = Image.effect_noise((w + _NOISE_PAD, h + _NOISE_PAD), sigma)
        if rgb:                           # converted once, not once per page
            field = field.convert("RGB")
        _noise_fields[key] = field
    ox = rng.randint(0, _NOISE_PAD)
    oy = rng.randint(0, _NOISE_PAD)
    return field.crop((ox, oy, ox + w, oy + h))


def _texture_layer(w: int, h: int, rng) -> tuple[Image.Image, float]:
    """One pre-mixed grain-and-mottle layer, and the weight to blend it at.

    Applying grain and then mottle means two passes over a 30 MB page. Mixing
    the two by their own weights first gives a single layer and a single
    blend, and the arithmetic works out to exactly the same picture: either
    way the result is (1-a)(1-b) of the page, a(1-b) of the grain and b of the
    mottle. The mixed layers are kept and reused, since only their grain
    offset ever differed.
    """
    a, b = PAPER_GRAIN_ALPHA, PAPER_MOTTLE_ALPHA
    gw, mw = a * (1 - b), b
    total = gw + mw
    if not total:
        return None, 0.0

    key = (w, h, PAPER_NOISE, a, b)
    pool = _texture_layers.setdefault(key, [])
    if len(pool) >= _TEXTURE_POOL:
        return rng.choice(pool), total

    grain = (_noise_window(w, h, PAPER_NOISE, True, rng) if a
             else Image.new("RGB", (w, h), (128, 128, 128)))
    if b:
        sw, sh = max(1, w // 12), max(1, h // 12)
        mottle = _noise_window(sw, sh, PAPER_NOISE, False, rng)
        mottle = mottle.resize((w, h), Image.BILINEAR).convert("RGB")
        layer = Image.blend(grain, mottle, mw / total)
    else:
        layer = grain
    pool.append(layer)
    return layer, total


def add_paper_texture(page: Image.Image, rng=random) -> Image.Image:
    """Give the flat page a faint off-white tint plus grain and soft mottle."""
    w, h = page.size
    if PAPER_TINT:
        # Multiplying a flat colour over the same template gives the same
        # picture every time, so the tinted sheet is built once and copied.
        key = (w, h, PAPER_TINT, page.tobytes()[:64])
        base = _tint_layers.get(key)
        if base is None:
            flat = Image.new("RGB", (w, h), PAPER_TINT)
            base = _tint_layers[key] = ImageChops.multiply(page, flat)
        page = base.copy()
    layer, weight = _texture_layer(w, h, rng)
    if layer is not None:
        page = Image.blend(page, layer, weight)
    return page


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def _raw_x_height() -> int:
    """x-height of the glyph files themselves, before GLYPH_SCALE is applied."""
    found = []
    for code in (97, 99, 101, 109, 110, 111, 114, 115, 117, 118, 119, 120, 122):
        for path in sorted(glob.glob(os.path.join(FONT_DIR, "{}*.png".format(code)))):
            rest = os.path.splitext(os.path.basename(path))[0][len(str(code)):]
            if rest and rest[0].isdigit():
                continue
            grey = Image.open(path).convert("L")
            box = grey.point(lambda p: 0 if p >= INK_THRESHOLD else 255 - p).getbbox()
            if box:
                found.append(box[3] - box[1])
    return sorted(found)[len(found) // 2] if found else 1


def px_per_mm() -> float:
    with Image.open(BG_PATH) as page:
        return page.width / PAGE_WIDTH_MM


def derive_metrics() -> None:
    """Fill in LINE_HEIGHT / SPACE_WIDTH from the glyph images if not set."""
    global LINE_HEIGHT, SPACE_WIDTH, X_HEIGHT, GLYPH_SCALE

    # Size the glyphs before any of them are cached, since GLYPH_SCALE is
    # applied as they load.
    if X_HEIGHT_MM:
        wanted = GLYPH_SCALE
        GLYPH_SCALE = (X_HEIGHT_MM * px_per_mm()) / _raw_x_height()
        if abs(wanted - GLYPH_SCALE) > 1e-6:
            reset_glyph_caches()

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
        if LINE_HEIGHT_MM:
            LINE_HEIGHT = max(1, round(LINE_HEIGHT_MM * px_per_mm()))
        else:
            # room for an ascender above the line and a descender below it, + gap
            LINE_HEIGHT = int(tall * (1 + DESCENDER_DROP) * 1.15)

    # x-height, from letters that have neither ascender nor descender
    xs = []
    for code in (97, 99, 101, 109, 110, 111, 114, 115, 117, 118, 119, 120, 122):
        vs = glyph_variants(chr(code))
        if vs:
            xs += [v.height for v in vs]
    X_HEIGHT = sorted(xs)[len(xs) // 2] if xs else int(tall * 0.5)

    # The word gap reads relative to letter size, not to letter width, so it is
    # set from the x-height. Deriving it from the median glyph width instead
    # made it drift whenever the mix of wide and narrow letters changed.
    if SPACE_WIDTH is None:
        SPACE_WIDTH = (max(1, round(WORD_GAP_MM * px_per_mm())) if WORD_GAP_MM
                       else max(1, int(X_HEIGHT * SPACE_RATIO)))

    # Word images and letter glyphs come from different sheets, written at
    # different sizes, so match them on x-height. For a word with no ascender
    # or descender the baseline offset is the x-height.
    global WORD_SCALE
    if WORD_SCALE is None:
        flat = ("on", "one", "or", "no", "so", "we", "as", "is", "in", "are",
                "was", "us", "an", "a", "our", "see", "some", "were", "new")
        # Use the image height, not the baseline offset. With no ascender or
        # descender the two ought to be the same, but the baseline is found by
        # the steepest fall in ink, which sits a little above the true foot of
        # the letters. Measuring from it made every word render ~12% too large.
        heights = sorted(w[0].height for w in (word_image(f) for f in flat) if w)
        WORD_SCALE = (X_HEIGHT / heights[len(heights) // 2]) if heights else 1.0


# --------------------------------------------------------------------------- #
# Page rendering
# --------------------------------------------------------------------------- #
class Sheet:
    """A stack of pages you can write glyphs onto; starts a new page on demand."""

    # Textured blank sheets kept to copy. Each is a 30 MB page held for the
    # whole render, and the gain flattens after the first few: two already
    # avoid nearly all the retexturing, and a bigger pool measured both heavier
    # and slower, since the memory traffic is what this is bound by.
    PAGE_POOL = 3

    def __init__(self, on_page=None) -> None:
        # Remembered at module level so stream_pages can clean up a sheet it
        # never got handed: a render cancelled mid-page raises out of render()
        # before the Sheet is returned, and the skew pool's threads would
        # otherwise outlive the render that made them.
        global _live_sheet
        _live_sheet = self
        self.template = Image.open(BG_PATH).convert("RGB")
        self.width, self.height = self.template.size
        # With an on_page sink a finished page is handed over and forgotten, so
        # only the one being written is held. Without one they pile up here,
        # which is what the command line and the tests want.
        self.on_page = on_page
        self.pages: list[Image.Image] = []
        self.count = 0                        # pages finished, kept or not
        self.missing: list[str] = []          # characters with no glyph
        self.scale = 1.0                      # block-level glyph size
        self._last = ""                       # last character written, for quote direction
        self._in_quote = False                # inside a quotation, for quote direction
        self._blanks: list[Image.Image] = []   # prepared sheets, copied per page
        self.page = None
        self._skew_pool = None
        self._new_page()

    def close(self) -> None:
        self._blanks.clear()
        if self._skew_pool is not None:
            self._skew_pool.shutdown(wait=True)
            self._skew_pool = None

    def finish(self) -> None:
        """Hand over the page still being written. Call once, at the end."""
        self._emit()
        self.close()

    def _emit(self) -> None:
        """Skew the page just finished and pass it on, then let go of it."""
        page, self.page = self.page, None
        if page is None:
            return
        if SCAN_SKEW:
            if self._skew_pool is None:
                self._skew_pool = ThreadPoolExecutor(max_workers=_workers())
            fill = PAPER_FILL if PAPER_TEXTURE else (255, 255, 255)
            # Drawn here, one page at a time in page order, so the sequence of
            # angles does not depend on thread scheduling.
            angle = random.uniform(-SCAN_SKEW, SCAN_SKEW)
            # One page at a time means banding it: every core works on this
            # sheet rather than each core taking a sheet of its own.
            page = _skew_page(page, angle, fill, self._skew_pool)
        self.count += 1
        if self.on_page is not None:
            self.on_page(self.count, page)
        else:
            self.pages.append(page)

    def _new_page(self) -> None:
        self._emit()                          # the previous page is done with
        # A page is 30 MB, and texturing one means a multiply and two blends
        # across every pixel of it. That, not the writing, is the bulk of a
        # render, and threads cannot fix it because the limit is memory
        # bandwidth rather than arithmetic. The recipe is identical for every
        # page and only the grain offset differs, so a few sheets are prepared
        # and the rest are copies of those. Copying 30 MB costs a fraction of
        # retexturing it, and grain that repeats every sixth sheet is not
        # something a reader can find.
        if len(self._blanks) < self.PAGE_POOL:
            blank = self.template.copy()
            if PAPER_TEXTURE:
                blank = add_paper_texture(blank)
            if RULED:
                self._draw_ruling(blank)
            self._blanks.append(blank)
        else:
            blank = random.choice(self._blanks)
        self.page = blank.copy()               # the sheet is kept clean to reuse
        _report("page", self.count + 1)
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
        self._last = ""                       # a new line opens a quote, like a space
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

    def paste_equation(self, path: str) -> bool:
        """Trace a cropped equation image onto the page as ink.

        Returns False if the file is unusable, so the caller can fall back to
        the figure box. The crop is typeset print: its alpha comes from ink
        darkness exactly like a glyph's, the strokes are thickened toward pen
        weight, and the whole thing is tilted a fraction of a degree so it
        sits with the writing rather than on top of it.
        """
        from PIL import ImageFilter

        try:
            grey = Image.open(path).convert("L")
        except OSError:
            return False
        alpha = grey.point(lambda p: 0 if p >= 200 else 255 - p)
        box = alpha.getbbox()
        if not box:
            return False
        alpha = alpha.crop(box).filter(ImageFilter.MaxFilter(3))

        # map the type's x-height onto the hand's, then cap to the column
        scale = X_HEIGHT / (EQ_DPI / 72.0 * EQ_SOURCE_X_PT)
        column = self.right - MARGIN_L
        width = alpha.width * scale
        if width > column * EQ_MAX_WIDTH:
            scale *= column * EQ_MAX_WIDTH / width
        alpha = alpha.resize((max(1, round(alpha.width * scale)),
                              max(1, round(alpha.height * scale))), Image.LANCZOS)

        ink = Image.new("RGBA", alpha.size, tuple(INK_COLOR or (20, 26, 66)) + (0,))
        ink.putalpha(alpha)
        ink = ink.rotate(random.uniform(-0.8, 0.8), resample=Image.BICUBIC, expand=True)

        lines = max(1, math.ceil(ink.height / LINE_HEIGHT))
        if self.baseline + LINE_HEIGHT * lines > self.height - MARGIN_B:
            self._new_page()

        # centred in the column, vertically centred on the lines it occupies
        x = MARGIN_L + (column - ink.width) // 2 + random.randint(-12, 12)
        top = self.baseline - X_HEIGHT
        y = top + (LINE_HEIGHT * lines - ink.height) // 2
        self.page.paste(ink, (int(x), int(y)), ink)
        self.baseline += LINE_HEIGHT * lines
        self.gap(1)
        return True

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
        boost = 1 + FATIGUE_JITTER * tired
        image, base = _jitter_word(image, base, size, boost,
                                   (core, round(size / _SCALE_STEP),
                                    boost >= _TIRED_AT))

        sway = max(1, int(BASELINE_WOBBLE * (1 + FATIGUE_JITTER * tired)))
        y = self.baseline - base + random.randint(-sway, sway) + self._drift()
        self.page.paste(image, (int(self.x), int(y)), image)
        self.x += image.width + random.randint(*KERN_JITTER)
        self._last = core[-1]                 # a whole word skips put(), so record it here

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
                self._last = " "
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
                    self._last = " "
            if not self.put_word(word, scale):
                for ch in word:
                    self.put(ch)
            if i < len(words) - 1:
                self.x += space
                self._last = " "
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
        vi = random.randrange(len(vs))
        chosen = vs[vi]
        flipped = False
        if ch in MIRRORED_OPENING:
            if self._opening():
                chosen = chosen.transpose(Image.FLIP_LEFT_RIGHT)
                flipped = True
            if ch == '"':
                self._in_quote = not self._in_quote
        g = jittered(chosen, scale, boost,
                     pool_key(ch, vi, flipped, scale, boost))

        sway = max(1, int(BASELINE_WOBBLE * boost))
        wobble = random.randint(-sway, sway) + self._drift()
        x = self.x + (advance - g.width) // 2     # keep rotation-expansion centred
        if ch in CENTERED:                        # math symbols float on the x-height axis
            y = self.baseline - X_HEIGHT // 2 - g.height // 2 + wobble
        else:
            drop = _drop_below_line(ch, g.height)
            lift = int(RAISE_FRAC * LINE_HEIGHT) if ch in RAISED else 0
            y = self.baseline - g.height + drop - lift + wobble

        self.page.paste(g, (x, y), g)
        self.x += advance + random.randint(*KERN_JITTER)
        self._last = ch

    def _opening(self) -> bool:
        """Whether a quote written here opens rather than closes a quotation.

        Judged first from what was written immediately before it, which is what
        a person reading the line has to go on too: after a letter or a full
        stop, a quote closes.

        A space before it does not settle the question on its own. Extracted
        Markdown pads an inline code span, so a quoted snippet arrives as
        " `move = <move>` " with a space inside both quotes, and reading the
        space alone made every quote on the line an opening one. Where the
        character before is that ambiguous, whether a quotation is already open
        decides it.
        """
        if self._last != "" and self._last not in OPENS_AFTER:
            return False
        return not self._in_quote


def _drop_below_line(ch: str, height: int) -> int:
    """How far a glyph hangs below the writing line.

    One fraction of glyph height for every tailed character was wrong, because
    a tail is not a fixed share of the letter it hangs off. Letters with a long
    tail kept too much of themselves above the rule: p sat with its bowl above
    the x-height band and read as a capital P. What is actually constant is the
    body resting on the line, so for those the drop is whatever is left over
    once the body is placed.
    """
    if ch in DESCENDERS:                  # x-height bowl, tail underneath it
        return max(0, height - X_HEIGHT)
    if ch in TAILED_ASCENDERS:            # body rises past the x-height as well
        return int(DESCENDER_DROP * height)
    if ch in HANGING:                     # tail crosses the line instead of sitting on it
        return int(HANG_FRAC * height)
    return 0


def render(text: str, on_page=None) -> Sheet:
    sheet = Sheet(on_page)
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


def chars_per_page() -> int:
    """Roughly how many characters fill one page with the current glyphs.

    Callers use this to estimate output length before committing to a render.
    It has to be derived rather than fixed, because it depends entirely on how
    large the handwriting is: swapping in a bigger hand halved it.
    """
    derive_metrics()
    with Image.open(BG_PATH) as page:
        width, height = page.size

    lines = max(1, (height - MARGIN_T - MARGIN_B) // LINE_HEIGHT)
    widths = [v[0].width for v in (glyph_variants(chr(c)) for c in range(33, 127)) if v]
    if not widths:
        return lines * 40
    widths.sort()
    typical = widths[len(widths) // 2] + sum(KERN_JITTER) / 2
    # about one space every six characters in ordinary prose
    per_char = typical * (6 / 7) + SPACE_WIDTH * (1 / 7)
    packed = lines * max(1, (width - MARGIN_L - MARGIN_R) // per_char)

    # That is the ceiling, with every line full to the margin. Real prose wraps
    # raggedly and leaves blank lines between blocks, so it never gets there.
    # Measured on two long documents: 0.62 and 0.69 of the ceiling.
    return int(packed * 0.65)


def render_markdown(md_text: str, on_page=None) -> Sheet:
    """Render Markdown, expressing its structure in handwriting conventions.

    Headings are centred and underlined rather than bold, lists are indented
    with a drawn marker, and figures become a labelled box. Tables degrade to
    indented rows for now; ruling real tables is a later job.
    """
    from markdown_blocks import to_blocks

    sheet = Sheet(on_page)
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
        # Quotations do not run across blocks, so a stray one cannot flip the
        # direction of every quote in the rest of the document.
        sheet._in_quote = False

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
            # An image whose source is a real file gets traced onto the page;
            # anything else keeps the drawn stand-in box.
            src = block.marker
            if not (src and os.path.exists(src) and sheet.paste_equation(src)):
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


def stream_pages(text: str, on_page, as_markdown: bool = False,
                 on_progress=None) -> tuple[int, list[str]]:
    """Render text, handing each finished page to `on_page(number, image)`.

    The page is skewed before it is handed over, so what arrives is final, and
    the renderer keeps no reference to it afterwards. A caller that writes the
    page out and lets go holds one page instead of the whole document: a
    thirty page render used to carry 1.7 GB of pixels for no reason other than
    that the pages were all returned together at the end.

    Returns how many pages were made and any characters with no glyph.
    """
    global _progress
    _progress = on_progress
    try:
        derive_metrics()
        sheet = render_markdown(text, on_page) if as_markdown else render(text, on_page)
        sheet.finish()
    finally:
        _progress = None
        if _live_sheet is not None:       # close() is safe to repeat
            _live_sheet.close()
    return sheet.count, sheet.missing


def render_pages(text: str, as_markdown: bool = False,
                 on_progress=None) -> tuple[list[Image.Image], list[str]]:
    """Render text and return the finished page images plus any skipped chars.

    Every page is held until the last one is done, which is convenient and
    costs a page of memory each. Anything rendering a long document should use
    stream_pages and write each page as it arrives.

    The skew is applied to each page as it is finished, so the paper, rules
    included, rotates as one and the exposed corners fill with the paper
    colour: the scanner-bed look.
    """
    pages: list[Image.Image] = []
    _, missing = stream_pages(text, lambda _n, page: pages.append(page),
                              as_markdown=as_markdown, on_progress=on_progress)
    return pages, missing


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
