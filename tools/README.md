# tools

Scripts for building and extending the handwriting set.

## Capture

`make_capture_sheets.py` prints the sheets to write on:

```
python tools/make_capture_sheets.py
```

- `paragraph.pdf` plus `letters_min.pdf` is the two-page minimum
- `words_short.pdf` / `words_full.pdf` are larger word sets
- `letters.pdf` has three samples per letter, for real variants

Each cell is a known rectangle holding known content. The guide text is grey and
a pen is dark, so extraction drops the guide by brightness and keeps the ink.

## Extraction

```
python tools/extract_glyphs.py     # letter sheet  -> myfont/<ascii>.png
python tools/extract_words.py      # paragraph     -> wordfont/<n>.png + index.json
```

Both label by position rather than by recognising anything: the sheet holds
known content in a known order, so a shape gets its label from the text. Any
row or line whose count disagrees with the text is skipped rather than guessed
at, because a silent mislabel would be permanent and invisible.

Why the two are different: letters can only be cut out of writing that does not
join up, which is why the letter sheet uses separated boxes. Measuring a real
sample of joined handwriting found only 43% of words separable into letters,
while words themselves separated 42 times out of 42. So whole words come from
ordinary prose and single letters come from boxes.

A word image must not include the punctuation that followed it in the prose,
or the renderer draws that mark a second time from the text: "it" was rendering
as "it." everywhere it appeared. Splitting on gap width alone does not catch
these, because a mark written tight against its word sits closer to it than
two words ever sit to each other. `trim_trailing_mark` measures the shape
instead. Dots and dashes are caught by being small, and a comma, which is as
large as a letter, by hanging below the writing: it starts at 0.74 of the row
band where the lowest trailing letter measured started at 0.46.

## Salvage

```
python tools/salvage_letters.py
```

Recovers a letter the letter sheet did not capture usably, from elsewhere in
the same hand. Cutting one out of a word is only safe where the word splits
into exactly as many shapes as it has letters, so the position is unambiguous,
and the result should still be looked at. `v` and `w` came from `we`, `was`,
`will`, `woman`, `have` and `given` this way, which is why they have more
variants than any letter that was written once in a box.

`x` could not be recovered from a word, because this hand writes it as a single
curl that reads as a `u` in every word containing it. It is taken from the
capital `X` scaled down to x-height instead: still a real shape from the same
hand, and a lowercase letter that reads as a different letter is worse than a
slightly formal one.

`f` looked like the same kind of problem, since it rendered as a `J`. It was
not. The sheet glyph was correct and the renderer was placing it wrong, sitting
its tail on the rule instead of below it, so adding `f` to `DESCENDERS` fixed it
without touching the glyph. Worth checking placement before blaming a glyph.

## Drawn symbols

```
python tools/make_symbols.py
```

Draws the 21 technical symbols that no handwriting sample covers:
`# $ % & * + / < = > @ [ \ ] ^ _ ` { | } ~`, and 16 Greek letters and maths
symbols (alpha, beta, gamma, delta, epsilon, theta, lambda, mu, pi, sigma,
element-of, summation, partial, asterisk operator, square root, infinity) so
equations in converted papers stop being skipped. Glyph files are named by
codepoint, so non-ASCII works exactly like ASCII: alpha is `myfont/945.png`.
These are approximations in a hand style rather than real handwriting, so they
read as neat rather than personal. Photograph the character and save it as
`myfont/<codepoint>.png` to replace one.

There used to be a `make_punctuation.py` here that composed `"` `'` `:` `;` out
of the period and comma, for a font that lacked them. The current set has all
four captured directly, so the script would have overwritten real glyphs with
synthesised ones and has been removed.
