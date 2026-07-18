# tools

Generators for the glyphs that were not in the original handwriting sample.
Both write straight into `myfont/`, overwriting the files they own. They are
deterministic, so re-running them reproduces the committed glyphs exactly.

Run from anywhere in the repo:

```
python tools/make_punctuation.py
python tools/make_symbols.py
```

## make_punctuation.py

Builds `"` `'` `:` `;` by **composing marks that already exist** in the
handwriting: a colon is two periods, a semicolon is a period above a comma, a
double quote is two commas, and an apostrophe is a single comma. Because these
reuse real strokes, they match the rest of the handwriting exactly.

Owns `myfont/34.png`, `39.png`, `58.png`, `59.png`.

## make_symbols.py

Draws the 21 technical symbols that have no natural building block in the
sample: `# $ % & * + / < = > @ [ \ ] ^ _ ` { | } ~`. These are approximations
in a hand style, not tracings of real handwriting, so they read as neat rather
than personal. To make any of them genuinely yours, photograph the symbol and
drop it in as `myfont/<ascii-code>.png`; the renderer will use it with no code
change and this script simply stops owning that glyph.

Owns `myfont/{35,36,37,38,42,43,47,60,61,62,64,91,92,93,94,95,96,123,124,125,126}.png`.

## A note on vertical placement

Neither script positions glyphs on the line. The renderer trims every glyph to
its ink, so padding is discarded; height comes from the character sets in
`text_to_handwriting.py`:

- `CENTERED` (`+ = < > ~`) sit on the x-height axis
- `RAISED` (`" ' * ^` and backtick) hang high on the line
- `DESCENDERS` (`g j p q y`) drop their tails below it
- everything else bottom-aligns to the writing line
