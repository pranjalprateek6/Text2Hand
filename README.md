# Text2Hand

Convert digital text into realistic handwriting on ruled notebook paper.

Text2Hand renders a plain text file as page images that look hand written. It uses a folder of scanned glyph images (one per character) and adds the small imperfections that make writing look human instead of like a font.

## Demo

![Sample output](samples/example.png)

## Why it looks handwritten and not like a font

A font pastes an identical letter every time. A person never writes the same letter twice. Text2Hand breaks that uniformity:

- **Variants.** If several images exist for one character, one is chosen at random for each occurrence.
- **Jitter.** Every glyph gets its own random rotation, scale, baseline wobble, letter spacing and ink darkness, so repeats never match.
- **Baseline.** Glyphs sit on a writing line with a slow drift along each line. The letters g, j, p, q and y drop their tails below the line.
- **Clean compositing.** An alpha mask is built from ink darkness, so rotation and tight spacing never paint white boxes over neighboring letters.
- **Ink and paper.** Strokes are recolored to a blue-black pen, and the page carries a faint off-white tint with subtle grain and mottle instead of flat white.
- **Paper and scan.** Faint ruled lines and a margin line are printed on the page, then the whole sheet is tilted a little as if it were scanned crooked.

It also saves its output, keeps real line breaks, word wraps, spills onto new pages, and skips unsupported characters with a warning instead of crashing.

## Requirements

- Python 3.8 or newer
- Pillow

Install the dependency:

```
pip install Pillow
```

## Usage

Put your text in `dummy.txt`, or pass a file path:

```
python text_to_handwriting.py
python text_to_handwriting.py my_essay.txt
```

Output is written to the `out` folder:

- `out/page_1.png`, `out/page_2.png`, ... one image per page
- `out/handwriting.pdf` the full document

## Make it your own handwriting

The `myfont` folder holds one PNG per character, named by its ASCII code. For example `65.png` is `A`, `97.png` is `a`, and `46.png` is a full stop. `bg.png` is the blank page background.

To use your own handwriting, replace these images with cropped photos of your own letters, named by the matching ASCII code. You can add variants for extra realism by adding files such as `65_1.png` and `65_2.png`, and the engine will pick among them at random.

The bundled font covers the full printable ASCII set: letters, digits, and symbols (`. , ! ? ' " ( ) - : ; / \ | # $ % & * + = < > @ [ ] { } ^ _ ~` and backtick). Characters that have no matching image are skipped and listed at the end, so a missing glyph never stops the run.

## Glyph tools

Not every character came from the original handwriting sample. Two generators in `tools/` fill the gaps. Both are deterministic, so re-running them reproduces the committed glyphs exactly:

```
python tools/make_punctuation.py
python tools/make_symbols.py
```

`make_punctuation.py` **composes** `"` `'` `:` `;` out of marks that already exist in the sample. A colon is two periods, a semicolon is a period above a comma, a double quote is two commas, and an apostrophe is a single comma. Because they reuse real strokes, they match the rest of the handwriting exactly.

`make_symbols.py` **draws** the 21 technical symbols that have no natural building block, such as `# $ % & @ / \ { }`. These are approximations in a hand style, so they read as neat rather than personal.

To replace any generated glyph with a real one, photograph the character and save it as `myfont/<ascii-code>.png`. The renderer picks it up with no code change. See `tools/README.md` for what each script owns.

## Configuration

All of the realism controls are plain constants near the top of `text_to_handwriting.py`. Some you might change:

- `ROT_JITTER`, `SCALE_JITTER`, `BASELINE_WOBBLE`, `KERN_JITTER` control how messy the writing looks
- `RULED`, `MARGIN_RULE`, `RULE_COLOR` toggle and color the notebook lines
- `INK_COLOR` sets the pen color, or `None` to keep the original scan color
- `PAPER_TEXTURE`, `PAPER_TINT`, `PAPER_NOISE` control the paper look
- `DESCENDERS`, `RAISED`, `CENTERED` set which characters drop below the line, hang high, or sit on the math axis
- `SCAN_SKEW` sets the page tilt in degrees. Set it to `0` for a flat scan
- `SEED` can be set to an integer for repeatable output

## Project layout

```
text_to_handwriting.py    the renderer
myfont/                   glyph images (one per ASCII code) and bg.png
tools/                    generators for the composed and drawn glyphs
dummy.txt                 default sample input text
samples/                  the example output shown above
Text To Handwriting.py    the original minimal version, kept for reference
```

## Notes

This project is meant for personal and educational use, such as making personalized notes, cards and practice pages. Please use it responsibly.
