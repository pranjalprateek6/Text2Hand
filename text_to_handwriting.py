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

# --------------------------------------------------------------------------- #
# Glyph loading  (prepare once, cache, discover variants)
# --------------------------------------------------------------------------- #
_cache: dict[str, list[Image.Image] | None] = {}


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


def char_advance(ch: str) -> int:
    """Nominal horizontal advance for a character (used for wrapping)."""
    if ch == " ":
        return SPACE_WIDTH
    vs = glyph_variants(ch)
    return vs[0].width if vs else 0


# --------------------------------------------------------------------------- #
# Per-glyph jitter
# --------------------------------------------------------------------------- #
def jittered(im: Image.Image) -> Image.Image:
    """Return a fresh, randomly perturbed copy of a glyph image."""
    g = im
    if SCALE_JITTER:
        s = 1 + random.uniform(-SCALE_JITTER, SCALE_JITTER)
        g = g.resize((max(1, round(g.width * s)), max(1, round(g.height * s))),
                     Image.LANCZOS)
    if ROT_JITTER:
        g = g.rotate(random.uniform(-ROT_JITTER, ROT_JITTER),
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
    global LINE_HEIGHT, SPACE_WIDTH
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


# --------------------------------------------------------------------------- #
# Page rendering
# --------------------------------------------------------------------------- #
class Sheet:
    """A stack of pages you can write glyphs onto; starts a new page on demand."""

    def __init__(self) -> None:
        self.template = Image.open(BG_PATH).convert("RGB")
        self.width, self.height = self.template.size
        self.pages: list[Image.Image] = []
        self._new_page()

    def _new_page(self) -> None:
        page = self.template.copy()
        if PAPER_TEXTURE:
            page = add_paper_texture(page)
        if RULED:
            self._draw_ruling(page)
        self.page = page
        self.pages.append(page)
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

    def newline(self) -> None:
        self.x = MARGIN_L + random.randint(0, MARGIN_JITTER)
        self.baseline += LINE_HEIGHT
        self._drift_phase = 0.0
        if self.baseline + LINE_HEIGHT > self.height - MARGIN_B:
            self._new_page()

    def _drift(self) -> int:
        # a gentle sine drift so the writing line is never laser-straight
        if not LINE_DRIFT:
            return 0
        self._drift_phase += 0.06
        return int(LINE_DRIFT * math.sin(self._drift_phase))

    def put(self, ch: str) -> None:
        vs = glyph_variants(ch)
        if not vs:
            return
        advance = vs[0].width                     # rhythm set by the un-jittered width
        g = jittered(random.choice(vs))

        drop = int(DESCENDER_DROP * g.height) if ch in DESCENDERS else 0
        wobble = random.randint(-BASELINE_WOBBLE, BASELINE_WOBBLE) + self._drift()
        x = self.x + (advance - g.width) // 2     # keep rotation-expansion centred
        y = self.baseline - g.height + drop + wobble

        self.page.paste(g, (x, y), g)
        self.x += advance + random.randint(*KERN_JITTER)


def render(text: str) -> Sheet:
    sheet = Sheet()
    missing: set[str] = set()

    for line in text.split("\n"):
        words = line.split(" ")
        for word in words:
            # track unsupported characters so we can warn instead of crashing
            for ch in word:
                if ch != " " and glyph_variants(ch) is None:
                    missing.add(ch)

            word_w = sum(char_advance(c) for c in word)
            if sheet.x != MARGIN_L and sheet.x + word_w > sheet.right:
                sheet.newline()

            for ch in word:
                sheet.put(ch)
            sheet.x += SPACE_WIDTH                 # space after the word
        sheet.newline()                            # real line break from source

    if missing:
        shown = " ".join(sorted(repr(c) for c in missing))
        print("Skipped {} unsupported character(s): {}".format(len(missing), shown))
    return sheet


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

    derive_metrics()
    sheet = render(text)

    # Final step: skew each finished page a hair, as if it were scanned crooked.
    # The paper (rules included) rotates as one, filling the exposed corners
    # with white -- the scanner-bed look.
    finals = []
    for page in sheet.pages:
        if SCAN_SKEW:
            fill = PAPER_FILL if PAPER_TEXTURE else (255, 255, 255)
            page = page.rotate(random.uniform(-SCAN_SKEW, SCAN_SKEW),
                               resample=Image.BICUBIC, expand=False,
                               fillcolor=fill)
        finals.append(page)

    os.makedirs(OUT_DIR, exist_ok=True)
    for i, page in enumerate(finals, 1):
        page.save(os.path.join(OUT_DIR, "page_{}.png".format(i)))
    pdf = os.path.join(OUT_DIR, "handwriting.pdf")
    finals[0].save(pdf, save_all=True, append_images=finals[1:])

    print("Wrote {} page(s) to '{}/' (line height {}, space {}).".format(
        len(sheet.pages), OUT_DIR, LINE_HEIGHT, SPACE_WIDTH))


if __name__ == "__main__":
    main()
