"""Render the landing page's pictures with the current renderer.

    python tools/make_landing_images.py

The landing page shows real output, so its pictures go stale whenever the
renderer improves: the July versions still showed tables as separate boxes
long after they were drawn as one grid. This makes them again, from fixed
text and fixed seeds, through the same /api/render the studio uses:

  static/sample_letter.jpg   the hero: a short letter on ruled paper
  static/sample_page.jpg     the showcase: a page of study notes
  static/sample_eq.jpg       display equations traced from a real paper
                             (needs attention.pdf; kept as is without it)
  static/og.jpg              the 1200x630 picture shown when the link is shared

The paper grain is random on every run; the writing is not.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("T2H_RENDER_PROCS", "0")
os.environ.setdefault("T2H_RENDER_ROOT", tempfile.mkdtemp(prefix="t2h_landing_"))
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageOps  # noqa: E402

import app  # noqa: E402

STATIC = ROOT / "static"

LETTER = """Dear Maya,

Thank you for the parcel. The tea arrived on the wettest morning of the year, which felt like good timing, and the little notebook is already half full of lists I will never finish.

The garden has given up on tomatoes and taken up slugs instead. Dad says it is a phase. I say it is a lifestyle. Either way the basil survived, so there will be pesto when you visit in May.

I finally framed the photo from the lake, the one where the dog is mostly a blur. It is on the shelf by the window and everyone who visits asks about it.

Write back soon, and tell me everything about the new flat.

With love,
Asha"""

# one paragraph to a line: plain text keeps every line break it is given
NOTES = """# Thermodynamics, week 3

## Entropy

- Entropy measures how many ways a system can be arranged
- In an isolated system it never decreases
    - which is why heat flows from hot to cold
- Units are joules per kelvin

1. Write the energy balance first
2. Then check the sign of the entropy change

> A process is reversible only if the total entropy stays the same.

| Process | Change in S |
|---|---|
| Free expansion | positive |
| Reversible cycle | zero |
| Melting ice | positive |

## Free energy

At constant temperature and pressure, a change happens on its own only if the Gibbs energy falls: G = H - TS, so a big enough rise in entropy can pay for an uphill change in enthalpy.

---

Remember: the Greek letters still need care, like a Carnot efficiency of 1 - Tc / Th, and the sum over states in Z."""


def render(text: str, seed: int, **opts) -> Image.Image:
    """Page 1 of a render through the real endpoint, as a full-size image."""
    client = app.app.test_client()
    started = client.post("/api/render", json={"text": text, "seed": seed, **opts})
    if started.status_code != 202:
        raise SystemExit(f"render refused: {started.get_json()}")
    jid = started.get_json()["job"]
    while True:
        job = client.get(f"/api/job/{jid}").get_json()
        if job["state"] != "running":
            break
        time.sleep(0.1)
    if job["state"] != "done":
        raise SystemExit(f"render {job['state']}: {job.get('error')}")
    data = client.get(f"/download/{job['id']}/page_1.png").data
    return Image.open(io.BytesIO(data)).convert("RGB")


def save_jpeg(im: Image.Image, name: str, width: int, quality: int = 84) -> None:
    im = im.copy()
    im.thumbnail((width, width * 3), Image.LANCZOS)
    im.save(STATIC / name, quality=quality, optimize=True, progressive=True)
    print(f"  {name}: {im.width}x{im.height}, {(STATIC / name).stat().st_size // 1024} KB")


def crop_to_writing(page: Image.Image, keep_below: int = 0, pad: int = 70) -> Image.Image:
    """The page cut to the area that has writing on it, plus a margin."""
    grey = ImageOps.invert(page.convert("L")).point(lambda v: 255 if v > 110 else 0)
    box = grey.getbbox()
    if not box:
        return page
    x0, y0, x1, y1 = box
    return page.crop((0, max(0, y0 - pad), page.width, min(page.height, max(y1, keep_below) + pad)))


def equations() -> Image.Image | None:
    """Display equations from a real paper, traced as ink, or None without it."""
    import converters

    paper = Path.home() / "Downloads" / "attention.pdf"
    if not paper.exists():
        return None
    assets = app.ASSET_ROOT / "eq_landing"
    result = converters.to_markdown(str(paper), "pymupdf", "4", assets_dir=str(assets),
                                    asset_ref="eq_landing")
    images = [line for line in result.markdown.splitlines() if line.startswith("![")]
    if not images:
        return None
    page = render("\n\n".join(images[:2]), 7, markdown=True, skew=False)
    return crop_to_writing(page, pad=50)


def main() -> None:
    print("rendering landing images with the current renderer")
    letter = render(LETTER, 20261017, paper="ruled", ink="blue-black")
    save_jpeg(letter, "sample_letter.jpg", 1100)

    notes = render(NOTES, 4242, markdown=True, paper="ruled", ink="blue-black")
    save_jpeg(notes, "sample_page.jpg", 1100)

    eq = equations()
    if eq is not None:
        save_jpeg(eq, "sample_eq.jpg", 1100)
    else:
        print("  sample_eq.jpg: kept (attention.pdf not found in Downloads)")

    # the shared-link picture: the top of the letter, on its own paper
    top = letter.crop((0, 0, letter.width, round(letter.width * 630 / 1200)))
    og = top.resize((1200, 630), Image.LANCZOS)
    og.save(STATIC / "og.jpg", quality=86, optimize=True, progressive=True)
    print(f"  og.jpg: 1200x630, {(STATIC / 'og.jpg').stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
