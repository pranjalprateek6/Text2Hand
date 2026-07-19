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

## Salvage

```
python tools/salvage_letters.py
```

Recovers a letter that never made it onto the letter sheet by cutting it out of
a word that contains it. Only safe where the word splits into exactly as many
shapes as it has letters, so the position is unambiguous, and the result should
still be looked at. `v` and `w` came from `we`, `was`, `will`, `woman`, `have`
and `given` this way, which is why they have more variants than any letter that
was written once in a box.

## Drawn symbols

```
python tools/make_symbols.py
```

Draws the 21 technical symbols that no handwriting sample covers:
`# $ % & * + / < = > @ [ \ ] ^ _ ` { | } ~`. These are approximations in a hand
style rather than real handwriting, so they read as neat rather than personal.
Photograph the character and save it as `myfont/<ascii-code>.png` to replace one.

There used to be a `make_punctuation.py` here that composed `"` `'` `:` `;` out
of the period and comma, for a font that lacked them. The current set has all
four captured directly, so the script would have overwritten real glyphs with
synthesised ones and has been removed.
