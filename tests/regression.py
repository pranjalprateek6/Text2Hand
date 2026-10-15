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
import converters as _conv  # noqa: F811  (a repeated alias is harmless)
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

# --- Markdown images cannot reach outside the asset folder -----------------
# On a server the Markdown is someone else's text. An image path must not be
# able to name any file on the host, a UNC share, or a URL.
_root = _tf.mkdtemp(prefix="t2h_assets_")
os.makedirs(os.path.join(_root, "eq_abc"))
Image.new("L", (40, 20), 0).save(os.path.join(_root, "eq_abc", "eq1_1.png"))
_outside = os.path.join(_tf.mkdtemp(prefix="t2h_secret_"), "secret.png")
Image.new("L", (40, 20), 0).save(_outside)
_saved_root, t2h.IMAGE_ROOT = t2h.IMAGE_ROOT, _root
try:
    _refused = [s for s in (_outside, _outside.replace("\\", "/"), "../" + os.path.basename(_outside),
                            "eq_abc/../../secret.png", r"\\host\share\x.png", "//host/share/x.png",
                            "http://example.com/x.png", "C:x.png", "file:///etc/passwd")
                if t2h.resolve_image(s) is not None]
    check("image paths outside the asset folder are refused", not _refused, str(_refused))
    check("an image inside the asset folder resolves",
          t2h.resolve_image("eq_abc/eq1_1.png") is not None)
finally:
    t2h.IMAGE_ROOT = _saved_root
    _sh.rmtree(_root, ignore_errors=True)
    _sh.rmtree(os.path.dirname(_outside), ignore_errors=True)

# --- the server refuses what it should, as JSON the studio can show -------
_client = app.app.test_client()
import io as _io
_r = _client.post("/api/convert", data={"file": (_io.BytesIO(b"not a pdf at all"), "x.pdf")},
                  content_type="multipart/form-data")
check("upload without PDF magic is refused", _r.status_code == 400 and _r.is_json,
      f"{_r.status_code}")
_limit = app.app.config["MAX_CONTENT_LENGTH"]
app.app.config["MAX_CONTENT_LENGTH"] = 1024
try:
    _r = _client.post("/api/convert", data={"file": (_io.BytesIO(b"%PDF-" + b"0" * 4096), "x.pdf")},
                      content_type="multipart/form-data")
    check("oversized upload is refused as JSON", _r.status_code == 413 and _r.is_json,
          f"{_r.status_code}")
finally:
    app.app.config["MAX_CONTENT_LENGTH"] = _limit

with app.JOBS_LOCK:
    _fake = [f"fake{i}" for i in range(app.MAX_ACTIVE_JOBS)]
    for _k in _fake:
        app.JOBS[_k] = {"state": "running", "message": "", "error": None}
try:
    _r = _client.post("/api/render", json={"text": "hello"})
    check("a full job queue answers 429", _r.status_code == 429 and _r.is_json, f"{_r.status_code}")
finally:
    with app.JOBS_LOCK:
        for _k in _fake:
            app.JOBS.pop(_k, None)

import converters as _cv
import pymupdf as _pm
_dpi = _cv._safe_dpi(_pm.Rect(0, 0, 3600, 3600), 300)        # a 50 inch poster
check("a large PDF page is rasterised under the pixel cap",
      (50 * _dpi) ** 2 <= _cv.MAX_RASTER_PIXELS, f"{_dpi} dpi, {(50 * _dpi) ** 2:,} px")
try:
    _cv._safe_dpi(_pm.Rect(0, 0, 14400, 14400), 300)          # 200 inches, PDF's maximum
    check("an absurd PDF page is refused", False, "was accepted")
except ValueError:
    check("an absurd PDF page is refused", True)
check("an ordinary page keeps full resolution",
      _cv._safe_dpi(_pm.Rect(0, 0, 595, 842), 300) == 300)

# --- writing stays inside the right margin ---------------------------------
# Fatigue grows the hand down the page and every glyph adds kerning jitter, so
# lines wrapped against the bare measurement used to run ~90px past the margin.
# A long URL has no space to break at and ran clean off the paper.
_skew, t2h.SCAN_SKEW = t2h.SCAN_SKEW, 0
try:
    _text = ("A line of ordinary prose that wraps several times across the page. " * 60
             + "\n\nhttps://example.com/a/very/long/path/that/never/ends/and/keeps/"
               "going/on/and/on/forever/index.html")
    _over = -9999
    for _p in t2h.render_pages(_text)[0]:
        _a = _np.asarray(_p.convert("L"))
        _cols = _np.where((_a[:, :_a.shape[1] - 60] < 110).any(axis=0))[0]
        _over = max(_over, int(_cols.max()) - (_a.shape[1] - t2h.MARGIN_R))
    check("no ink runs past the right margin", _over <= 20, f"furthest {_over}px past")
finally:
    t2h.SCAN_SKEW = _skew

# --- word images follow the hand size --------------------------------------
_ws = {}
for _mm in (2.5, 3.5):
    t2h.X_HEIGHT_MM = _mm
    t2h.derive_metrics()
    _ws[_mm] = t2h.WORD_SCALE
t2h.X_HEIGHT_MM = 3.0
t2h.derive_metrics()
check("word images rescale with the hand size", _ws[2.5] < t2h.WORD_SCALE < _ws[3.5],
      f"{_ws[2.5]:.3f} < {t2h.WORD_SCALE:.3f} < {_ws[3.5]:.3f}")

# --- the renderer finds its glyphs from any working directory ---------------
import subprocess as _sp
_out = _sp.run([sys.executable, "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); import text_to_handwriting as t;"
                " print(bool(t.glyph_variants('a')))", ROOT],
               cwd=_tf.gettempdir(), capture_output=True, text=True)
check("glyphs load from another working directory", _out.stdout.strip() == "True",
      _out.stdout.strip() or _out.stderr.strip()[-120:])

# --- typed text is normalised without changing what it says -----------------
_n = converters.normalize("don’t café “quoted” x² H₂O 25°C a·b")
check("typed typography folds to drawable text",
      _n == "don't cafe \"quoted\" x^2 H_2O 25 deg C a*b", _n)


def _app_render(payload):
    """Run one render through the real endpoint and wait for it."""
    import time as _time
    r = _client.post("/api/render", json=payload)
    if r.status_code != 202:
        return None, r.get_json()
    jid = r.get_json()["job"]
    for _ in range(600):
        job = _client.get(f"/api/job/{jid}").get_json()
        if job["state"] != "running":
            return job, None
        _time.sleep(0.1)
    return None, "timed out"


_job, _err = _app_render({"text": "It’s a café, isn’t it?", "size": "large"})
check("app render of pasted typography skips nothing",
      _job is not None and _job["state"] == "done" and not _job["missing"],
      str(_err or (_job and (_job.get("missing") or _job.get("error")))))
check("a large render leaves the normal page size alone",
      app.page_size() == app.page_size("normal") and app.page_size("large") < app.page_size(),
      f"normal {app.page_size()}, large {app.page_size('large')}")
if _job and _job["state"] == "done":
    _pdf = _client.get(f"/download/{_job['id']}/handwriting.pdf")
    with _pm.open(stream=_pdf.data, filetype="pdf") as _doc:
        _w, _h = _doc[0].rect.width, _doc[0].rect.height
    check("exported PDF pages are A4", abs(_w - 595) < 6 and abs(_h - 842) < 8,
          f"{_w:.0f} x {_h:.0f} pt")
    _single = _client.get(f"/download/{_job['id']}/page_1.pdf")
    with _pm.open(stream=_single.data, filetype="pdf") as _doc:
        _w = _doc[0].rect.width
    check("single-page PDF is A4 too", abs(_w - 595) < 6, f"{_w:.0f} pt wide")
    # full-size pages are kept as JPEG; a PNG is made only when asked for
    _png = _client.get(f"/download/{_job['id']}/page_1.png")
    check("page PNG export builds on demand",
          _png.status_code == 200 and _png.data[:8] == b"\x89PNG\r\n\x1a\n",
          f"{_png.status_code}, {len(_png.data):,} bytes")
    import zipfile as _zf
    _z = _client.get(f"/download/{_job['id']}/pages.zip")
    with _zf.ZipFile(_io.BytesIO(_z.data)) as _zip:
        _names = _zip.namelist()
    check("zip holds every page and the PDF",
          "page_1.jpg" in _names and "handwriting.pdf" in _names, str(_names))

# --- through the app, equations keep working with relative asset paths -----
if _PAPER:
    import time as _time
    with open(_PAPER, "rb") as _fh:
        _r = _client.post("/api/convert", data={"file": (_fh, "paper.pdf"), "pages": "5"},
                          content_type="multipart/form-data")
    _jid = _r.get_json()["job"]
    for _ in range(1200):
        _job = _client.get(f"/api/job/{_jid}").get_json()
        if _job["state"] != "running":
            break
        _time.sleep(0.1)
    _md = _job.get("markdown", "")
    check("a read reports what it found, for the checklist",
          (_job.get("found") or {}).get("equations", 0) >= 1 and _job.get("converter_label"),
          str(_job.get("found")) + " " + str(_job.get("converter_label")))
    _srcs = _re.findall(r"!\[[^\]]*\]\(([^)]+)\)", _md)
    check("converted Markdown names crops by relative path only",
          _srcs and all(s.startswith("eq_") and ":" not in s for s in _srcs),
          str(_srcs[:2]) or _job.get("error", ""))
    _rend, _err = _app_render({"text": _md, "markdown": True, "skew": False})
    _ok = _rend is not None and _rend["state"] == "done"
    if _ok:
        _pg = _client.get(f"/preview/{_rend['id']}/2.jpg")
        with Image.open(_io.BytesIO(_pg.data)) as _im:
            _dark = int((_np.asarray(_im.convert("L")) < 120).sum())
    check("app traces the crops onto the page", _ok and _dark > 15_000,
          f"{_dark:,} dark px in the preview" if _ok else str(_err))
else:
    skip("app equation round trip", "million-step paper not found")

# the studio shows pages as they finish, so a job publishes its id and how
# many pages are ready, counted from page 1
_job, _err = _app_render({"text": "A page that fills and spills over. " * 160})
check("render job publishes ready pages",
      _job is not None and _job.get("ready") == _job.get("pages") and _job["pages"] > 1,
      f"ready {_job and _job.get('ready')} of {_job and _job.get('pages')}" if _job else str(_err))

# --- captured words write in the same ink as the letters --------------------
def _level(im):
    a = _np.asarray(im.getchannel("A"), dtype=float)
    return a[a > 40].mean()


t2h.derive_metrics()
_letters = _np.mean([_level(t2h.glyph_variants(c)[0]) for c in "aceimnorsuvwxz"])
_faintest = min(_level(im) for im, _ in t2h._load_words().values())
check("no captured word is far fainter than the letters", _faintest > 0.9 * _letters,
      f"faintest word {_faintest:.0f} vs letters {_letters:.0f}")

# --- lines do not all drift in the same shape ------------------------------
_sh_ = t2h.Sheet()
_shapes = set()
for _ in range(6):
    _sh_._start_line()
    _shapes.add((round(_sh_._drift_phase, 3), round(_sh_._drift_step, 4), round(_sh_._slope, 5)))
_sh_.close()
check("each line gets its own drift and slope", len(_shapes) == 6, f"{len(_shapes)} of 6 distinct")

# --- Cancel stops the render, rather than letting it finish ----------------
# The renderer swallowed every exception from its progress hook, the
# cancellation included, so a cancelled job used to draw every remaining page.
import time as _tm
_r = _client.post("/api/render", json={"text": "A long document to cancel part way through. " * 1800})
_jid = _r.get_json()["job"]
_tm.sleep(0.8)
_client.post(f"/api/job/{_jid}/cancel")
while _client.get(f"/api/job/{_jid}").get_json()["state"] == "running":
    _tm.sleep(0.05)
_cj = _client.get(f"/api/job/{_jid}").get_json()
check("cancel stops the render part way", _cj["state"] == "cancelled" and (_cj.get("ready") or 0) < 30,
      f"{_cj['state']} after {_cj.get('ready')} of ~57 pages")
check("a cancelled render leaves no folder behind",
      not (_cj.get("id") and (app.RENDER_ROOT / _cj["id"]).exists()))

# --- rendering in worker processes ------------------------------------------
# A spawned worker re-imports the main module, so those checks run as their own
# guarded script; its PASS/FAIL lines are folded into this suite.
_pool = _sp.run([sys.executable, os.path.join(ROOT, "tests", "pool_check.py")],
                capture_output=True, text=True, timeout=600)
for _line in _pool.stdout.splitlines():
    _m = _re.match(r"\s+(PASS|FAIL)\s+(.*?)(?:\s+\[(.*)\])?$", _line)
    if _m:
        check("pool: " + _m.group(2), _m.group(1) == "PASS", _m.group(3) or "")
if _pool.returncode and "FAIL" not in _pool.stdout:
    check("pool checks ran", False, (_pool.stderr or _pool.stdout)[-300:])

# --- Markdown that used to vanish on the way to the page --------------------
def _kinds(md):
    return [(b.kind, b.text, b.marker, b.level) for b in markdown_blocks.to_blocks(md)]


_q = _kinds("> Intro\n>\n> - one\n> - two\n")
check("a list inside a quote is kept", ("item", "one", "-", 1) in _q, str(_q))
_h5 = _kinds("<div>Some <b>raw</b>, html</div>\n")
check("raw HTML blocks keep their text", _h5 == [("para", "Some raw, html", "", 0)], str(_h5))
_ol = _kinds("3. third\n4. fourth\n")
check("an ordered list keeps its starting number", [m for _, _, m, _ in _ol] == ["3.", "4."], str(_ol))
_im = _kinds("See ![a chart](c.png) here.\n")
check("an inline image keeps its description", _im[0][1] == "See [a chart] here.", str(_im))
_fn = _kinds("Text[^1].\n\n[^1]: The note.\n")
check("footnotes are written as notes", ("item", "The note.", "1.", 0) in _fn
      and _fn[0][1] == "Text [1].", str(_fn))
_br = _kinds("12 Rose Lane  \nBristol\n")
check("hard line breaks keep their lines", [k for k, *_ in _br] == ["line", "para"], str(_br))
_th = [b.level for b in markdown_blocks.to_blocks("| A | B |\n|---|---|\n| 1 | 2 |\n")]
check("table header rows are marked", _th == [1, 0], str(_th))
_pg, _ms = t2h.render_pages("| A | B |\n|---|---|\n| 1 | 2 |\n\n```\ndef f():\n    return 1\n```\n",
                            as_markdown=True)
check("a table with a header and indented code render", len(_pg) == 1 and not _ms)

# --- OCR keeps real hyphens at a line end ---------------------------------
_u = converters._unwrap("It is a well-\nknown result, accord-\ning to plan; it is known well.")
check("OCR keeps compound hyphens and rejoins split words",
      "well-known" in _u and "according" in _u, _u)

# --- a photo of a filled-in capture sheet reads back -----------------------
sys.path.insert(0, os.path.join(ROOT, "tests"))
import capture_check as _cap

for _name, _ok, _detail in _cap.run():
    check("capture: " + _name, _ok, _detail)

# --- paper styles and sizes ----------------------------------------------
_job, _err = _app_render({"text": "Squared paper on a Letter page.", "paper": "grid",
                          "sheet": "letter", "skew": False})
if _job and _job["state"] == "done":
    _pdf = _client.get(f"/download/{_job['id']}/handwriting.pdf")
    with _pm.open(stream=_pdf.data, filetype="pdf") as _doc:
        _w, _h = _doc[0].rect.width, _doc[0].rect.height
        _meta = _doc.metadata
    check("a Letter render exports Letter pages", abs(_w - 612) < 6 and abs(_h - 792) < 8,
          f"{_w:.0f} x {_h:.0f} pt")
    check("exported PDFs say they were made by Text2Hand",
          "Text2Hand" in (_meta.get("creator") or "") and "Text2Hand" in (_meta.get("subject") or ""),
          str({k: _meta.get(k) for k in ("creator", "producer", "subject")}))
    _pv = _client.get(f"/preview/{_job['id']}/1.jpg")
    with Image.open(_io.BytesIO(_pv.data)) as _im:
        _g = _np.asarray(_im.convert("L"))[300:600, 200:700]
    # squared paper has as many light vertical lines as horizontal ones
    _cols = int((_np.abs(_np.diff(_g.astype(int), axis=1)) > 6).mean(axis=0).max() > 0.5)
    check("squared paper prints a grid", _cols == 1, "vertical rules found" if _cols else "none")
else:
    check("a Letter render exports Letter pages", False, str(_err))
check("page limits are measured per page size", app.page_size("normal", "letter") != app.page_size(),
      f"A4 {app.page_size()}, Letter {app.page_size('normal', 'letter')}")

# --- headers, caching, and per-visitor limits ------------------------------
_st = _client.get("/studio")
_csp = _st.headers.get("Content-Security-Policy", "")
check("pages carry a content security policy", "script-src 'self'" in _csp
      and "frame-ancestors 'none'" in _csp and _st.headers.get("X-Content-Type-Options") == "nosniff")
_body = _st.get_data(as_text=True)
check("the studio has no inline script for the policy to block",
      not _re.search(r"<script(?![^>]*\bsrc=)[^>]*>", _body))
_js = _re.search(r'src="(/static/app\.js\?v=\d+)"', _body)
check("static links carry a version", bool(_js), "app.js?v=..." if _js else "unversioned")
if _js:
    _sr = _client.get(_js.group(1))
    check("versioned static files cache for a year",
          "max-age=31536000" in _sr.headers.get("Cache-Control", ""), _sr.headers.get("Cache-Control"))
with app.JOBS_LOCK:
    _mine = [f"mine{i}" for i in range(app.MAX_JOBS_PER_CLIENT)]
    for _k in _mine:
        app.JOBS[_k] = {"state": "running", "message": "", "error": None, "_client": "127.0.0.1"}
try:
    _r1 = _client.post("/api/render", json={"text": "hello"})
    _r2 = _client.post("/api/render", json={"text": "hello"},
                       environ_base={"REMOTE_ADDR": "10.9.8.7"})
    check("one visitor cannot take every slot",
          _r1.status_code == 429 and "already have" in _r1.get_json()["error"], f"{_r1.status_code}")
    check("another visitor still gets one", _r2.status_code == 202, f"{_r2.status_code}")
    if _r2.status_code == 202:
        _jj = _client.get(f"/api/job/{_r2.get_json()['job']}").get_json()
        check("a job never reveals who started it", "_client" not in _jj, str(sorted(_jj)))
finally:
    with app.JOBS_LOCK:
        for _k in _mine:
            app.JOBS.pop(_k, None)

# --- the seeded reference page still looks the same --------------------------
_gold = _sp.run([sys.executable, os.path.join(ROOT, "tests", "golden_check.py")],
                capture_output=True, text=True, timeout=300)
_gm = _re.search(r"(PASS|FAIL)\s+(.*?)\s+\[(.*)\]", _gold.stdout)
check("rendering matches the reference picture", bool(_gm) and _gm.group(1) == "PASS",
      _gm.group(3) if _gm else (_gold.stderr or _gold.stdout)[-300:])

# --- transparent pages: ink alone, for laying over a design -----------------
_job, _err = _app_render({"text": "Ink with no paper under it.", "paper": "none"})
if _job and _job["state"] == "done":
    _png = _client.get(f"/download/{_job['id']}/page_1.png")
    with Image.open(_io.BytesIO(_png.data)) as _im:
        _mode = _im.mode
        _alpha = _np.asarray(_im.convert("RGBA"))[..., 3]
    check("a transparent render downloads as a PNG with alpha",
          _mode == "RGBA" and _alpha[10, 10] == 0 and _alpha.max() > 200,
          f"{_mode}, corner alpha {_alpha[10, 10]}")
    _pv = _client.get(f"/preview/{_job['id']}/1.jpg")
    with Image.open(_io.BytesIO(_pv.data)) as _im:
        _corner = _im.convert("L").getpixel((5, 5))
    check("its preview is the ink on white", _corner > 245, f"corner {_corner}")
    _z = _client.get(f"/download/{_job['id']}/pages.zip")
    with _zf.ZipFile(_io.BytesIO(_z.data)) as _zip:
        _zn = _zip.namelist()
    check("its zip holds the transparent pages", "page_1.png" in _zn and "handwriting.pdf" in _zn,
          str(_zn))
    _single = _client.get(f"/download/{_job['id']}/page_1.pdf")
    check("its single-page PDF still builds", _single.status_code == 200
          and _single.data.startswith(b"%PDF-"), str(_single.status_code))
else:
    check("a transparent render downloads as a PNG with alpha", False, str(_err))

# --- the studio's structure for assistive technology ------------------------
_sb = _client.get("/studio").get_data(as_text=True)
check("the studio has a main landmark, a heading and a labelled editor",
      "<main" in _sb and "<h1" in _sb and 'id="text" spellcheck="false" aria-label=' in _sb)
check("the studio offers a one-click PDF and a Fit control",
      'id="dlMain"' in _sb and 'id="zoomFit"' in _sb)
check("the studio knows how long renders are kept, for the library's expiry line",
      f'data-render-ttl="{app.RENDER_TTL}"' in _sb)
check("the studio carries its first-run example note",
      'id="welcome"' in _sb and 'id="welcomeStart"' in _sb)

# --- nothing in the Markdown goes missing without a word about it ------------
_dl = []
markdown_blocks.to_blocks(open("tests/fixture.md", encoding="utf-8").read(), _dl)
check("the Markdown fixture reaches the page whole", _dl == [], str(_dl))
from bs4 import BeautifulSoup as _BS
_lost = markdown_blocks._ledger(_BS("<p>kept words</p><p>lost words</p>", "html.parser"),
                                [markdown_blocks.Block("para", "kept words")])
check("words no block carries are reported", _lost == ["lost", "words"], str(_lost))
_job, _err = _app_render({"text": "# Fine\n\nAll of this is written.", "markdown": True})
check("a render reports its dropped words", _job is not None and _job.get("dropped") == [],
      str(_job and _job.get("dropped")) if _job else str(_err))

# --- a document keeps its handwriting from render to render ----------------
def _gray(job, n):
    with Image.open(_io.BytesIO(_client.get(f"/preview/{job['id']}/{n}.jpg").data)) as _im:
        return _np.asarray(_im.convert("L"), dtype=_np.int16)


_seedtext = "The same text in the same hand, every time it is written. " * 110
_base = {"text": _seedtext, "seed": 4242, "texture": False}
_s1, _ = _app_render(_base)
_app_render({"text": "Something else in between, in red.", "seed": 7, "ink": "red", "texture": False})
_s2, _ = _app_render(_base)
_s3, _ = _app_render({**_base, "seed": 4243})
_s4, _ = _app_render({**_base, "text": _seedtext + " One more word."})
if all(j and j["state"] == "done" for j in (_s1, _s2, _s3, _s4)):
    _n = _s1["pages"]
    _same = max(int(_np.abs(_gray(_s1, i) - _gray(_s2, i)).max()) for i in range(1, _n + 1))
    check("the same seed writes the same pages", _same == 0 and _s1["seed"] == 4242,
          f"largest pixel difference {_same} over {_n} pages")
    _other = float(_np.abs(_gray(_s1, 1) - _gray(_s3, 1)).mean())
    check("a new seed is a new hand", _other > 5, f"mean difference {_other:.1f}")
    _early = max(float(_np.abs(_gray(_s1, i) - _gray(_s4, i)).mean()) for i in range(1, _n))
    check("an edit at the end leaves earlier pages alone", _early == 0, f"{_early:.2f}")
else:
    check("the same seed writes the same pages", False, "a render failed")
_ns, _ = _app_render({"text": "No seed given."})
check("a render without a seed is given one", _ns is not None and isinstance(_ns.get("seed"), int))

# --- PDF reading speaks plainly and chooses for itself ----------------------
_av = converters.available()
check("converter choices are named in plain words",
      [c["name"] for c in _av][0] == "auto"
      and not any("pip " in c["label"] + c["reason"] or "set " in c["reason"] for c in _av),
      "; ".join(f"{c['label']} ({c['reason']})" for c in _av))
_tmp_pdf = os.path.join(_tf.mkdtemp(prefix="t2h_pdf_"), "text.pdf")
_doc = _pm.open()
_pg = _doc.new_page()
_pg.insert_text((72, 72), "A text PDF with a real text layer.", fontsize=12)
_doc.save(_tmp_pdf)
_doc.close()
check("Automatic reads a text PDF as text", converters.choose(_tmp_pdf) == "pymupdf")
with open(_tmp_pdf, "rb") as _fh:
    _r = _client.post("/api/convert", data={"file": (_fh, "text.pdf")},
                      content_type="multipart/form-data")
_cj = _r.get_json()["job"]
for _ in range(300):
    _cv_job = _client.get(f"/api/job/{_cj}").get_json()
    if _cv_job["state"] != "running":
        break
    _tm.sleep(0.1)
check("a PDF read with no choice made uses Automatic",
      _cv_job["state"] == "done" and _cv_job.get("converter") == "pymupdf"
      and _cv_job.get("converter_label") == "Text PDF" and "found" in _cv_job,
      str({k: _cv_job.get(k) for k in ("state", "converter", "converter_label", "error")}))

_h = _client.get("/healthz")
check("health check answers", _h.status_code == 200 and _h.get_json()["ok"], str(_h.get_json()))

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed"
      + (f", {len(skipped)} skipped" if skipped else ""))
if bad:
    print("failed: " + ", ".join(bad))
sys.exit(1 if bad else 0)
