# Text2Hand

Turn typed text, Markdown or a PDF into pages that look hand written on ruled notebook paper.

Text2Hand draws text using a folder of scanned glyph images, one per character, and adds the small imperfections that make writing look human instead of like a font. It runs as a command line tool or a small web app.

## Demo

![Sample output](samples/example.png)

Everything on that page, including the ruled paper, the table grid and the figure box, was drawn by the renderer from [samples/demo.md](samples/demo.md).

## What it does

- **Renders plain text, Markdown, or a PDF** into page images and a PDF
- **Keeps document structure**: headings, nested lists, quotes, rules, figures and tables
- **Reads PDFs** through a pluggable converter, local by default, with the extracted Markdown shown for review before rendering
- **Web app** with a page-by-page viewer, per-page and whole-document downloads, and live progress on long jobs
- **Full printable ASCII**, so punctuation and technical symbols do not silently vanish

## How it works

Markdown is the contract between reading a document and drawing it. Any input format only has to reach Markdown, and the renderer never learns a second input grammar:

```
PDF  ─┐
DOCX ─┼─►  converter  ─►  Markdown  ─►  block renderer  ─►  pages + PDF
HTML ─┘                      ▲
                             │
                    you can edit it here
```

That middle step is the point. Extraction is never perfect, and because the intermediate is readable, a wrong heading is a ten second fix rather than a dead end.

## Why it looks handwritten and not like a font

A font pastes an identical letter every time. A person never writes the same letter twice. Text2Hand breaks that uniformity:

- **Variants.** If several images exist for one character, one is chosen at random for each occurrence.
- **Jitter.** Every glyph gets its own random rotation, scale, baseline wobble, letter spacing and ink darkness, so repeats never match.
- **Baseline.** Glyphs sit on a writing line with a slow drift along each line. The letters g, j, p, q and y drop their tails below the line, quotes and apostrophes hang high, and math symbols sit on the x-height axis.
- **Clean compositing.** An alpha mask is built from ink darkness, so rotation and tight spacing never paint white boxes over neighbouring letters.
- **Ink and paper.** Strokes are recoloured to a blue-black pen, and the page carries a faint off-white tint with subtle grain and mottle instead of flat white.
- **Fatigue.** Letters grow slightly and get less tidy toward the foot of each page, because a hand tires.
- **Corrections.** Optionally starts a word wrong, crosses it out and writes it again, because a page with no crossings-out anywhere is itself a giveaway.
- **Paper and scan.** Faint ruled lines and a margin line are printed on the page, then the whole sheet is tilted a little as if it were scanned crooked.

It also saves its output, keeps real line breaks, word wraps, spills onto new pages, and skips unsupported characters with a warning instead of crashing.

## Requirements

Python 3.9 or newer. Developed and tested on 3.14.

```
pip install -r requirements.txt
```

Pillow and Flask are the only hard requirements. Markdown and beautifulsoup4 are needed for Markdown input, and `pymupdf4llm` for PDF input. The app still runs without the PDF extra; the Convert button just reports that the converter is missing.

## Web app

```
python app.py
```

Then open http://127.0.0.1:5000.

Type or paste text, tick the options you want, and hit **Generate**. Converting and rendering both run on a worker thread and report progress on their button (`Reading page 3 of 19...`, `Writing page 7...`), so neither hangs waiting on a request.

The result panel is a viewer, not a wall of images:

- one page at a time with a `3 / 12` counter, prev and next buttons, and **arrow key** paging
- a thumbnail strip for jumping straight to a page
- downloads for **this page** as PNG or PDF, **all pages** as one PDF, or a **ZIP** of everything

Rendering runs locally on the machine hosting the app, and the page loads no webfont, so it works offline. Cloud PDF converters are the one exception and are clearly marked.

## PDF input

Upload a PDF, pick a converter, optionally give a page range like `1-10`, and hit **Convert**. The PDF becomes Markdown *in the text box* rather than going straight to handwriting, so you can fix what the extractor got wrong. Review it, then Generate.

- **Local (PyMuPDF)** is the default and the file never leaves your machine.
- **Local OCR (Tesseract)** reads scanned PDFs, also without uploading anything. Needs `pip install pytesseract` plus the Tesseract binary (`winget install UB-Mannheim.TesseractOCR`, `apt install tesseract-ocr`, or `brew install tesseract`).
- **Cloud (LlamaParse, Mistral OCR)** are opt-in, need their own API key in the environment, and upload the file to that provider. They are disabled in the UI until installed and keyed.

A PDF with no text layer is detected and reported, and the app tells you to switch to OCR. OCR rasterises each page and reads it with Tesseract, then reflows the hard-wrapped result back into paragraphs and stitches words that were split across a line break. It runs at roughly a second or two per page, and reports which page it is reading as it goes. Typographic characters the handwriting has no glyph for (curly quotes, dashes, ellipses, ligatures, currency and maths symbols) are folded to ASCII, and accented letters are reduced to their base letter, so `café` renders as `cafe` instead of losing characters. OCR of real documents throws these off constantly.

## Markdown

Tick **Markdown** in the web app, or pass a `.md` file on the command line. Structure is drawn the way a person writing by hand would express it, since handwriting has no bold and no type sizes:

| Markdown | Drawn as |
|---|---|
| `# Heading` | centred, underlined, larger |
| `## Heading`, `### Heading` | underlined, progressively smaller |
| `- item` | indented with a drawn marker, one indent per nesting level |
| `1. item` | the same, numbered |
| `> quote` | indented from the margin |
| `---` | a drawn pen rule |
| `![alt](img)` | a hand-drawn box captioned with the alt text |
| table | a hand-ruled grid, cells wrapping inside their column |

Inline bold and italic are dropped on purpose. Converted PDFs barely use them (33 bold spans in 16,000 lines of a real converted report) and handwriting has no bold to map them onto.

## Command line

Put your text in `dummy.txt`, or pass a file path. A `.md` file is rendered as Markdown, anything else as plain text:

```
python text_to_handwriting.py
python text_to_handwriting.py my_essay.txt
python text_to_handwriting.py notes.md
```

Output goes to the `out` folder:

- `out/page_1.png`, `out/page_2.png`, ... one image per page
- `out/handwriting.pdf` the whole document

## Limits

Handwriting is far less dense than print, so a modest PDF becomes a lot of pages. Roughly 850 characters fill one handwritten page.

- Conversion stops at **50 PDF pages**, and says so when it truncates. OCR shares that limit.
- Rendering stops at **60,000 characters**, about 70 pages. Over that, convert a smaller page range.
- After converting, the app reports the character count and an estimated page count, and warns before you hit either limit rather than after.

## Make it your own handwriting

The `myfont` folder holds one PNG per character, named by its ASCII code. For example `65.png` is `A`, `97.png` is `a`, and `46.png` is a full stop. `bg.png` is the blank page background.

To use your own handwriting, replace these images with cropped photos of your own letters, named by the matching ASCII code. You can add variants for extra realism by adding files such as `65_1.png` and `65_2.png`, and the engine picks among them at random.

The bundled font covers the full printable ASCII set. Characters with no matching image are skipped and listed at the end, so a missing glyph never stops a run.

## Glyph tools

Not every character came from the original handwriting sample. Two generators in `tools/` fill the gaps. Both are deterministic, so re-running them reproduces the committed glyphs exactly:

```
python tools/make_punctuation.py
python tools/make_symbols.py
```

`make_punctuation.py` **composes** `"` `'` `:` `;` out of marks that already exist in the sample. A colon is two periods, a semicolon is a period above a comma, a double quote is two commas, and an apostrophe is a single comma. Because they reuse real strokes, they match the rest of the handwriting exactly.

`make_symbols.py` **draws** the 21 technical symbols with no natural building block, such as `# $ % & @ / \ { }`. These are approximations in a hand style, so they read as neat rather than personal.

To replace any generated glyph with a real one, photograph the character and save it as `myfont/<ascii-code>.png`. See [tools/README.md](tools/README.md) for what each script owns.

## Configuration

All of the controls are plain constants near the top of `text_to_handwriting.py`:

| Setting | Effect |
|---|---|
| `ROT_JITTER`, `SCALE_JITTER`, `BASELINE_WOBBLE`, `KERN_JITTER` | how messy the writing looks |
| `RULED`, `MARGIN_RULE`, `RULE_COLOR` | the notebook lines |
| `INK_COLOR` | pen colour, or `None` to keep the original scan colour |
| `PAPER_TEXTURE`, `PAPER_TINT`, `PAPER_NOISE` | the paper look |
| `SCAN_SKEW` | page tilt in degrees, `0` for a flat scan |
| `DESCENDERS`, `RAISED`, `CENTERED` | which characters drop below the line, hang high, or sit on the math axis |
| `HEADING_SCALE`, `INDENT_STEP` | Markdown heading size and list indent |
| `TABLES_RULED` | draw tables as a ruled grid, or `False` to list the rows |
| `FATIGUE` | writing grows and loosens down each page |
| `CORRECTIONS` | occasionally write a word wrong, cross it out and rewrite it. Off by default |
| `SEED` | set an integer for repeatable output |

## Tests

`tests/` holds the Markdown fixtures used to check the renderer: [fixture.md](tests/fixture.md) covers headings, nested lists, quotes, rules and figures, and [table.md](tests/table.md) covers table layout.

```
python text_to_handwriting.py tests/fixture.md
python text_to_handwriting.py tests/table.md
```

## Project layout

```
text_to_handwriting.py    the renderer, usable as a library or a CLI
markdown_blocks.py        Markdown to drawable blocks
converters.py             document to Markdown adapters, local and cloud
app.py                    Flask web app wrapping the renderer
templates/, static/       the web app front end
myfont/                   glyph images (one per ASCII code) and bg.png
tools/                    generators for the composed and drawn glyphs
tests/                    Markdown fixtures
samples/                  the demo shown above
dummy.txt                 default sample input text
Text To Handwriting.py    the original minimal version, kept for reference
```

## Known limitations

- **OCR quality is OCR quality.** Tesseract reads scanned pages well but picks up page furniture (navigation, headers) and mangles footnote markers. That is what the review step is for.
- **Cloud converters are untested.** The adapters are written and the UI disables them with a reason, but they have not been run against a live key.
- **Running headers and footers leak in.** A repeated "Company Confidential" becomes body text. This is what the review step is for.
- **Drawn symbols are not your handwriting.** The 21 technical symbols are approximations, unlike the composed punctuation.
- **Full-res pages are large.** Roughly 9 MB each, so a ZIP of a long document gets big. The PDF is far smaller.

## Notes

This project is meant for personal and educational use, such as making personalised notes, cards and practice pages. Please use it responsibly.
