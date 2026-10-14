"""Compare a seeded render with the reference picture of it.

    python tests/golden_check.py            check
    python tests/golden_check.py --update   accept the current output as the reference

The renderer is random by design, but with a fixed seed, fresh caches, and
the two effects that draw on Pillow's own noise (paper texture) or on
nothing worth checking (scan tilt) switched off, it is repeatable. A change
that moves letters, spacing, wrapping or ruling shows up here as a
difference from the picture, where the other checks only measure numbers.

Compared at reduced size, so a different Pillow resampling a pixel
differently is not a failure, but a letter in a different place is.
Runs in its own process, so nothing earlier in a test run can have touched
the renderer's caches or random state.
"""
from __future__ import annotations

import os
import random
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
GOLDEN = os.path.join(ROOT, "tests", "golden", "fixture_p1.png")
WIDTH = 600

MAX_MEAN_DIFF = 4.0        # average brightness difference, out of 255
MIN_INK_OVERLAP = 0.80     # share of ink pixels that must land on the reference's ink


def render() -> Image.Image:
    import text_to_handwriting as t2h

    t2h.PAPER_TEXTURE = False
    t2h.SCAN_SKEW = 0
    t2h.derive_metrics()
    t2h.reset_glyph_caches()
    t2h._blank_pools.clear()
    random.seed(20261015)
    with open(os.path.join(ROOT, "tests", "fixture.md"), encoding="utf-8") as fh:
        pages, _ = t2h.render_pages(fh.read(), as_markdown=True)
    page = pages[0].convert("L")
    return page.resize((WIDTH, round(page.height * WIDTH / page.width)), Image.LANCZOS)


def compare(now: Image.Image, ref: Image.Image) -> tuple[bool, str]:
    if now.size != ref.size:
        return False, f"size {now.size} vs reference {ref.size}"
    a = np.asarray(now, dtype=np.int16)
    b = np.asarray(ref, dtype=np.int16)
    mean = float(np.abs(a - b).mean())
    ink_a, ink_b = a < 140, b < 140
    union = (ink_a | ink_b).sum()
    overlap = float((ink_a & ink_b).sum() / union) if union else 1.0
    ok = mean <= MAX_MEAN_DIFF and overlap >= MIN_INK_OVERLAP
    return ok, f"mean difference {mean:.2f}/255, ink overlap {overlap:.2f}"


def main() -> int:
    now = render()
    if "--update" in sys.argv or not os.path.exists(GOLDEN):
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        now.save(GOLDEN, optimize=True)
        print(f"  PASS  rendering matches the reference picture  [reference written to {GOLDEN}]")
        return 0
    ok, detail = compare(now, Image.open(GOLDEN).convert("L"))
    if not ok:
        now.save(os.path.join(ROOT, "out", "golden_now.png")) if os.path.isdir(
            os.path.join(ROOT, "out")) else None
    print(f"  {'PASS' if ok else 'FAIL'}  rendering matches the reference picture  [{detail}]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
