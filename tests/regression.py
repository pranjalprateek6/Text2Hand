"""Regression checks for the renderer, the converters and the app's limits.

Run it from anywhere:

    python tests/regression.py

Exits non-zero if anything fails, so it can gate a commit.

Some checks need a real paper, and papers are too big and too much someone
else's to keep in the repository. Those are skipped, not failed, when the file
is not there. Point the suite at a folder of them with:

    T2H_FIXTURES=/path/to/papers python tests/regression.py

Wanted, by name: "attention.pdf" (Attention Is All You Need) and "SOLVING A
MILLION-STEP LLM TASK WITH ZERO ERRORS.pdf". Without them the suite still
covers everything that does not need a PDF.
"""
import glob
import os
import re as _re
import shutil as _sh
import sys
import tempfile as _tf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# the renderer resolves myfont/ and the page template relative to the cwd
os.chdir(ROOT)

from PIL import Image
import markdown_blocks
import text_to_handwriting as t2h

results = []
skipped = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def skip(name, why):
    """A check that could not run, which is not the same as one that failed."""
    skipped.append(name)
    print(f"  SKIP  {name}  [{why}]")


def fixture(*names):
    """Locate an optional paper, or None. See the module docstring.

    T2H_FIXTURES, when set, is the only place searched: an explicit override
    that falls through to the default folders is not an override, and it made
    a "run as CI would" rehearsal quietly pick papers out of ~/Downloads.
    """
    override = os.environ.get("T2H_FIXTURES")
    roots = [override] if override is not None else [
        os.path.join(ROOT, "tests", "fixtures"),
        os.path.join(os.path.expanduser("~"), "Downloads")]
    for root in filter(None, roots):
        for name in names:
            path = os.path.join(root, name)
            if os.path.exists(path):
                return path
    return None


t2h.derive_metrics()
mm = t2h.px_per_mm()

# 1. typography lands where it was set
xh = t2h.X_HEIGHT / mm
lh = t2h.LINE_HEIGHT / mm
gap = t2h.SPACE_WIDTH / mm
check("x-height 3 mm", abs(xh - 3.0) < 0.15, f"{xh:.2f} mm")
check("line height 8.4 mm", abs(lh - 8.4) < 0.15, f"{lh:.2f} mm")
check("word gap 4 mm", abs(gap - 4.0) < 0.15, f"{gap:.2f} mm")

# 2. 33 ruled lines on the page, and every one of them written on. Both are
# replicated from the renderer rather than derived, so the check tracks the
# real break test instead of an arithmetic guess about it.
with Image.open(t2h.BG_PATH) as bg:
    H = bg.height
LH, MT, MB = t2h.LINE_HEIGHT, t2h.MARGIN_T, t2h.MARGIN_B
y, rules = MT + LH + t2h.RULE_OFFSET, 0
while y <= H - MB + t2h.RULE_OFFSET:
    rules += 1
    y += LH
b, written = MT + LH, 1
while b + LH <= H - MB:
    b += LH
    written += 1
check("33 ruled lines printed", rules == 33, f"{rules} rules")
check("every rule written on", written == rules, f"{written} written of {rules}")

# 3. f is a descender, and its glyph really hangs below the others
check("f declared as tailed", "f" in t2h.DESCENDERS | t2h.TAILED_ASCENDERS)
fg = t2h.glyph_variants("f")
og = t2h.glyph_variants("o")
check("f glyph taller than x-height", fg and og and fg[0].height > og[0].height * 1.8,
      f"f {fg[0].height}px vs o {og[0].height}px")

# 3b. descenders are placed by their body: the part above the line is the
# x-height, whatever the tail is doing. This is what stopped p reading as P.
for ch in "gjpqy":
    g = t2h.glyph_variants(ch)[0]
    above = g.height - t2h._drop_below_line(ch, g.height)
    check(f"{ch} body sits on the line", above == t2h.X_HEIGHT,
          f"{above}px above vs x-height {t2h.X_HEIGHT}")

# 3c. f keeps its ascender above the line rather than being buried by that rule
fg = t2h.glyph_variants("f")[0]
f_above = fg.height - t2h._drop_below_line("f", fg.height)
check("f keeps its ascender", f_above > t2h.X_HEIGHT * 1.4, f"{f_above}px above")

# 3d. a comma crosses the line instead of floating above it as a 9
cg = t2h.glyph_variants(",")[0]
c_drop = t2h._drop_below_line(",", cg.height)
check("comma hangs below the line", 0 < c_drop < cg.height, f"{c_drop} of {cg.height}px below")
check("comma hangs less deep than p", c_drop <= t2h._drop_below_line("p", t2h.glyph_variants("p")[0].height) + 6,
      f"comma {c_drop} vs p {t2h._drop_below_line('p', t2h.glyph_variants('p')[0].height)}")

# 3e. a full stop still rests on the line
dg = t2h.glyph_variants(".")[0]
check("full stop rests on the line", t2h._drop_below_line(".", dg.height) == 0)

# 4. x is no longer the u-shaped curl: it should be about as wide as tall
xg = t2h.glyph_variants("x")
ratio = xg[0].width / xg[0].height
check("x roughly square (crossed form)", 0.7 < ratio < 1.4, f"w/h {ratio:.2f}")

# 5. every printable ASCII character has a glyph
missing = [chr(c) for c in range(33, 127) if not t2h.glyph_variants(chr(c))]
check("no missing ASCII glyphs", not missing, f"missing {missing}" if missing else "94/94")

# 6. v and w present with variants
for letter, want in (("v", 2), ("w", 4)):
    n = len(glob.glob(os.path.join(t2h.FONT_DIR, f"{ord(letter)}*.png")))
    check(f"{letter} has {want} variants", n == want, f"{n} files")

# 7. plain text renders
pages, miss = t2h.render_pages("The quick brown fox jumps over the lazy dog.\n"
                               "Pack my box with five dozen liquor jugs.")
check("plain text renders", len(pages) == 1 and not miss, f"{len(pages)} page")

# 8. markdown structure renders across pages
md = open("tests/fixture.md", encoding="utf-8").read()
blocks = markdown_blocks.to_blocks(md)
kinds = {b.kind for b in blocks}
check("markdown parses to blocks", len(blocks) > 5, f"{len(blocks)} blocks, kinds {sorted(kinds)}")
pages, _ = t2h.render_pages(md, as_markdown=True)
check("markdown renders", len(pages) >= 1, f"{len(pages)} pages")

# 9. tables render
tmd = open("tests/table.md", encoding="utf-8").read()
pages, _ = t2h.render_pages(tmd, as_markdown=True)
check("table renders", len(pages) >= 1, f"{len(pages)} pages")

# 10. word images still in play
check("word rendering on", t2h.USE_WORDS)
w = t2h.word_image("the")
check("word image found for 'the'", w is not None)

# 10b. no word image carries punctuation that the renderer will draw again
import json
from collections import deque

import numpy as np


def _blobs(mask, min_blob=8):
    h, w = mask.shape
    seen = np.zeros_like(mask, bool)
    out = []
    for y in range(h):
        for x in range(w):
            if mask[y, x] and not seen[y, x]:
                q = deque([(y, x)])
                seen[y, x] = True
                xs, ys, n = [x], [y], 0
                while q:
                    cy, cx = q.popleft()
                    n += 1
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            ny, nx = cy + dy, cx + dx
                            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                                seen[ny, nx] = True
                                q.append((ny, nx))
                                xs.append(nx)
                                ys.append(ny)
                if n >= min_blob:
                    out.append([min(xs), min(ys), max(xs), max(ys), n])
    return sorted(out, key=lambda b: b[0])


widx = json.load(open("wordfont/index.json", encoding="utf-8"))
dirty = []
for word, fn in widx.items():
    grey = np.asarray(Image.open(os.path.join("wordfont", fn)).convert("L"))
    bs = _blobs(grey < 150)
    if len(bs) < 2:
        continue
    last, prev = bs[-1], bs[-2]
    gap, H = last[0] - prev[2], grey.shape[0]
    ht, ink = last[3] - last[1], last[4]
    if gap > 12 and ((ink < 250 and ht < 0.20 * H) or (last[1] > 0.55 * H and ink < 500)):
        dirty.append(word)
check("no word image carries punctuation", not dirty,
      f"{len(widx)} clean" if not dirty else f"stray marks in {dirty}")

# 10c. quotation marks and related punctuation all reduce to ASCII
import converters

probe = ("“curly” ‘single’ « angle » ‹ x › "
         "5′ 6″ ‐hyphen 1⁄4 —dash…")
reduced = converters.normalize(probe)
leftover = sorted({c for c in reduced if ord(c) > 126})
check("quotes and punctuation normalize to ASCII", not leftover,
      "clean" if not leftover else f"left {[hex(ord(c)) for c in leftover]}")

# 10d. drawn symbols are on the same scale as the captured hand. < was 0.41 of
# the x-height, small enough that "<move>" read as "move>".
raw_x = t2h._raw_x_height()


def _ink_h(ch):
    grey = np.asarray(Image.open(os.path.join("myfont", f"{ord(ch)}.png")).convert("L"))
    ys = np.where((grey < 150).any(axis=1))[0]
    return (ys[-1] - ys[0] + 1) if len(ys) else 0


tiny = [ch for ch in "<>+" if _ink_h(ch) / raw_x < 0.7]
check("angle brackets and plus are letter-sized", not tiny,
      f"< is {_ink_h('<')/raw_x:.0%} of x-height" if not tiny else f"too small: {tiny}")
short = [ch for ch in "/|[]{}" if _ink_h(ch) / raw_x < 1.2]
check("brackets taller than the letters", not short,
      f"[ is {_ink_h('[')/raw_x:.0%} of x-height" if not short else f"too small: {short}")

# 10e. a quote leans by direction: the captured glyph closes, mirrored opens
sheet = t2h.Sheet()
# (preceding char, already inside a quotation, should open)
cases = [("", False, True), (" ", False, True), ("(", False, True), ("[", False, True),
         ("a", False, False), (".", False, False), ("!", False, False), (")", False, False),
         # a space before it does not settle it: extracted Markdown pads a code
         # span, so a closing quote can follow a space too
         (" ", True, False), ("", True, False), ("a", True, False)]
wrong = []
for prev, inside, want in cases:
    sheet._last, sheet._in_quote = prev, inside
    if sheet._opening() != want:
        wrong.append(f"after {prev!r} inside={inside}: wanted {want}")
check("quote direction reads from context", not wrong,
      f"{len(cases)} cases" if not wrong else "; ".join(wrong))

# and the two directions must actually differ on the page
sheet._last = ""
opened = t2h.glyph_variants('"')[0].transpose(Image.FLIP_LEFT_RIGHT)
closed = t2h.glyph_variants('"')[0]
check("opening quote is not the closing one",
      list(opened.getdata()) != list(closed.getdata()), "mirrored")

# 10f. Greek and maths glyphs exist, place correctly, and render clean
GREEK = "αβγδεθλμπσφψ∈∑∂∗√∞"
no_glyph = [ch for ch in GREEK if not t2h.glyph_variants(ch)]
check("greek and maths glyphs exist", not no_glyph,
      f"{len(GREEK)} glyphs" if not no_glyph else f"missing {no_glyph}")
check("gamma and mu hang their tails", set("γμ") <= t2h.DESCENDERS)
check("beta, phi, psi rise and hang", set("βφψ") <= t2h.TAILED_ASCENDERS)
check("element/asterisk/infinity centred", set("∈∗∞") <= t2h.CENTERED)
_, greek_missing = t2h.render_pages("rate α where x ∈ S and √n, θ λ μ σ ∑ ∂ ∞")
check("greek line renders with nothing skipped", not greek_missing,
      "clean" if not greek_missing else f"skipped {greek_missing}")
import converters as _conv  # noqa: repeated alias is harmless
_note_md = "some α and β with かな"
_left = {ch for ch in _note_md if ord(ch) > 126 and not __import__("os").path.exists(
    __import__("os").path.join("myfont", f"{ord(ch)}.png"))}
# ascii() because the Windows console is cp1252 and cannot print these
check("converter warning ignores drawable chars", _left == set("かな"),
      "warns only for CJK" if _left == set("かな") else f"would warn about {ascii(sorted(_left))}")

# 10g. display equations are excised to images, tagged, and traced back in
_PAPER = fixture("SOLVING A MILLION-STEP LLM TASK WITH ZERO ERRORS.pdf",
                 os.path.join("kinda work",
                              "SOLVING A MILLION-STEP LLM TASK WITH ZERO ERRORS.pdf"))
if _PAPER:
    _eqdir = os.path.join(_tf.gettempdir(), "t2h_regress_eq")
    _sh.rmtree(_eqdir, ignore_errors=True)
    _res = _conv.to_markdown(_PAPER, "pymupdf", "5", assets_dir=_eqdir)
    _refs = _re.findall(r"!\[(equations? \([^)]*\)[^\]]*|equation)\]", _res.markdown)
    check("five display equations excised", len(_refs) == 5, f"{_refs}")
    check("captions carry the paper's tags", any("(1)-(3)" in r for r in _refs))
    check("no tokens or junk tables left",
          not _re.search(r"T2HEQ\d+Z", _res.markdown)
          and not [l for l in _res.markdown.splitlines() if _re.match(r"^\s*\|", l)])
    _pages, _miss = t2h.render_pages(_res.markdown, as_markdown=True)
    check("equation page renders clean", len(_pages) >= 3 and not _miss,
          f"{len(_pages)} pages")
    # the crops must actually be ON the page: the traced group is far more ink
    # than a figure box outline would be
    import numpy as _np
    _dark = int((_np.asarray(_pages[1].convert("L")) < 120).sum())
    check("equations traced as ink, not boxed", _dark > 200_000, f"{_dark:,} dark px")
else:
    skip("display equation checks", "million-step paper not found")

# 11. multi-page split is sane
cpp = t2h.chars_per_page()
check("chars per page sane", 900 < cpp < 1800, f"{cpp}")
long_text = "word " * 900
pages, _ = t2h.render_pages(long_text)
check("long text spans pages", len(pages) > 1, f"{len(pages)} pages")

# 12. the app's limits still derive
import app
check("app render limit derives", app.render_limit() == app.MAX_OUTPUT_PAGES * app.page_size(),
      f"{app.render_limit():,} chars")

# --- equation tokens must never survive, whole or in pieces ----------------
_att = fixture("attention.pdf")
if _att:
    _gl = glob
    _d = _tf.mkdtemp(prefix="eqguard_")
    _r = _conv.to_markdown(_att, "pymupdf", "1-15", assets_dir=_d)
    _debris = _re.findall(r"T2H\w*|Q\d{5}Z", _r.markdown)
    _crops = [os.path.basename(x) for x in _gl.glob(os.path.join(_d, "*.png"))]
    _placed = _re.findall(r"!\[[^\]]*\]\(([^)]+)\)", _r.markdown)
    _lost = [c for c in _crops if not any(c in q for q in _placed)]
    check("no token debris reaches the text", not _debris, str(_debris))
    check("every excised equation is placed", not _lost,
          f"{len(_crops)} crops, {len(_placed)} placed")
    _sh.rmtree(_d, ignore_errors=True)
else:
    skip("equation token checks", "attention.pdf not found")

# --- full Greek coverage, drawn and aliased --------------------------------
_drawn = "ζηικνξρτυχω" + "ΓΔΘΛΞΠΣΦΨΩ"
_missing_files = [c for c in _drawn
                  if not os.path.exists(os.path.join("myfont", f"{ord(c)}.png"))]
check("new greek glyphs on disk", not _missing_files,
      " ".join(ascii(c) for c in _missing_files) or f"{len(_drawn)} drawn")
_aliased = "οΑΒΕΖΗΙΚΜΝΟΡΤΥΧ"
_dead = [c for c in _aliased if not t2h.glyph_variants(c)]
check("greek aliases resolve to latin glyphs", not _dead,
      " ".join(ascii(c) for c in _dead) or f"{len(_aliased)} aliased")
_pgs, _miss = t2h.render_pages("α β γ δ ε ζ η θ ι κ λ μ ν ξ ο π ρ σ τ υ φ χ ψ ω "
                               "Γ Δ Θ Λ Ξ Π Σ Φ Ψ Ω")
check("full greek line renders clean", not _miss,
      " ".join(ascii(c) for c in _miss) or "nothing skipped")
check("eta rho chi declared descenders", set("ηρχ") <= t2h.DESCENDERS)
check("zeta xi declared tailed ascenders", set("ζξ") <= t2h.TAILED_ASCENDERS)

# --- the E glyph is one piece ----------------------------------------------
# Its capture kept a fragment of the letter above it, which every rendered E
# wore as a breve until someone finally looked at one.
import numpy as _np2
_e = _np2.asarray(Image.open("myfont/69.png").convert("L"))
_rows = _np2.where((_e < 150).sum(axis=1) > 0)[0]
_gaps = (_np2.diff(_rows) > 8).sum() if len(_rows) else 0
check("E glyph is a single component", _gaps == 0, f"{_gaps} gaps")

# --- handwriting sizes really change the glyphs ----------------------------
_heights = {}
for _mm in (2.5, 3.0, 3.5):
    t2h.X_HEIGHT_MM = _mm
    t2h.derive_metrics()
    _heights[_mm] = t2h.glyph_variants("o")[0].height
t2h.X_HEIGHT_MM = 3.0
t2h.derive_metrics()
check("three sizes give three glyph heights",
      _heights[2.5] < _heights[3.0] < _heights[3.5], str(_heights))

# --- paper grain: present, subtle, and allowed to repeat -------------------
# The blank-sheet pool and the cached texture layers mean pages can share
# their paper exactly, like sheets torn from one pad. What matters is that
# the grain exists (texture on has to differ from texture off), that it stays
# far too faint to read as a pattern, and that reuse never leaks ink: a
# pooled sheet that was written on by mistake would show up here first.
#
# Measured with the skew off, because rotating the sheet resamples the grain
# and would smear the comparison. And only ever measured in a region PROVEN
# blank first: two earlier attempts at this check sampled handwriting and
# rules by accident and returned confident nonsense.
import numpy as _np

_texts = ("The page fills more slowly than you expect it to. " * 120)
_skew, _seedpages = t2h.SCAN_SKEW, None
t2h.SCAN_SKEW = 0
try:
    _pgs, _ = t2h.render_pages(_texts)
    _BOX = (400, 18, 720, 98)          # top margin, above the first ruled line
    _strips = [_np.asarray(p.convert("L").crop(_BOX), dtype=_np.int16) for p in _pgs]

    _clean = all(int(s.min()) > 200 for s in _strips)
    check("grain strip is proven blank on every page", _clean,
          f"min brightness {min(int(s.min()) for s in _strips)}")

    if _clean:
        # grain exists: the same region on an untextured render is flat
        t2h.PAPER_TEXTURE = False
        _flat, _ = t2h.render_pages(_texts[:400])
        _fs = _np.asarray(_flat[0].convert("L").crop(_BOX), dtype=_np.int16)
        t2h.PAPER_TEXTURE = True
        check("texture leaves visible grain", float(_strips[0].std()) > float(_fs.std()) + 0.2,
              f"std {float(_strips[0].std()):.2f} textured vs {float(_fs.std()):.2f} flat")

        # and it is subtle: pages may share a sheet outright (diff 0), but no
        # pair may differ by enough to read as a pattern under the writing
        _worst = max(float(_np.abs(a - b).mean())
                     for i, a in enumerate(_strips) for b in _strips[i + 1:])
        check("grain difference between pages stays subtle", _worst < 4.0,
              f"worst pair {_worst:.2f}/255")

        # reuse must hand out CLEAN sheets: blank margins on later pages,
        # where the pool is certainly recycling
        check("recycled sheets carry no ink", all(int(s.min()) > 200 for s in _strips[3:]),
              f"{len(_strips) - 3} recycled pages checked" if len(_strips) > 3 else "few pages")
finally:
    t2h.SCAN_SKEW = _skew
    t2h.PAPER_TEXTURE = True

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed"
      + (f", {len(skipped)} skipped" if skipped else ""))
if bad:
    print("failed: " + ", ".join(bad))
sys.exit(1 if bad else 0)
