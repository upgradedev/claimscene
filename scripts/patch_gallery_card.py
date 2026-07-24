#!/usr/bin/env python3
"""Patch small text regions of a gallery card PNG in place, leaving every
other pixel byte-identical to the source.

Why a patch instead of a from-scratch re-render: the 7 committed
``demo/assets/*.png`` gallery cards were produced by a one-off local tool
whose exact typeface stack is no longer known (their generator script was
never committed). Re-typesetting a whole card from scratch in CI (open-source
fonts only -- see below) risks a visibly "foreign" result across ~30 lines
of hand-transcribed text. A surgical patch touches only the stale field(s)
and keeps 100% of the original pixels everywhere else, which is the right
tool for the recurring case this project actually has: a stat (like a test
count) goes stale and needs updating.

Font: DejaVu Sans Mono Bold (``fonts-dejavu-core`` on Debian/Ubuntu). This is
a deliberate, verified choice, not a fallback of convenience: a side-by-side
render of untouched card text against Consolas Bold and DejaVu Sans Mono
Bold showed DejaVu matching the real card's glyph proportions and spacing
more closely than Consolas -- and Consolas is a proprietary Microsoft font
that cannot be redistributed in this public repo/CI image regardless. Using
an MIT/Bitstream-Vera-licensed font also keeps this script's output
reproducible on any contributor's machine or CI runner, with no dependency
on fonts that only exist locally on one Windows box.

Usage:
    python scripts/patch_gallery_card.py demo/assets/src/*.patch.json

Spec JSON shape (see demo/assets/src/*.patch.json for real examples):
{
  "base_image": "demo/assets/claimscene-07-architecture.png",
  "output_image": "demo/assets/claimscene-07-architecture.png",
  "font": "DejaVuSansMono-Bold",
  "fill_color": [15, 22, 20],
  "text_color": [232, 163, 61],
  "patches": [
    {
      "note": "backend test count -- keep in sync with CI's pytest summary",
      "erase_box": [85, 568, 136, 598],
      "new_text": "289",
      "anchor_left_x": 92,
      "ink_top_y": 574,
      "ink_bottom_y": 593
    }
  ]
}

Each patch's ``erase_box`` and ink anchors were measured once, directly from
the shipped PNG's pixels (amber-glyph bounding boxes), not eyeballed -- see
the PR description for the measurement method. Re-derive them the same way
(scan for the card's amber RGB against its dark box-fill RGB) if a future
edit needs a region this spec doesn't already cover.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Known-good system paths, checked first so CI (which apt-get installs
# fonts-dejavu-core) never depends on an optional Python package.
FONT_SYSTEM_PATHS = {
    "DejaVuSansMono-Bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    ),
}


def find_font(name: str) -> str:
    for path in FONT_SYSTEM_PATHS.get(name, ()):
        if os.path.exists(path):
            return path
    # Best-effort local-dry-run fallback: matplotlib bundles its own copy of
    # the same font, useful for iterating without installing system fonts.
    try:
        import matplotlib
        path = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data",
                             "fonts", "ttf", f"{name}.ttf")
        if os.path.exists(path):
            return path
    except ImportError:
        pass
    raise SystemExit(
        f"Font '{name}' not found. On Debian/Ubuntu (incl. CI): "
        f"sudo apt-get install -y fonts-dejavu-core")


def find_font_size(draw: ImageDraw.ImageDraw, text: str, font_path: str,
                    target_h: int, lo: int = 10, hi: int = 96) -> int:
    """Scan font sizes and return the one whose rendered ink-height is
    closest to ``target_h`` pixels. The search range is tiny (<100 sizes),
    so a linear scan is simpler than a binary search and just as fast."""
    best_size, best_diff = lo, 10 ** 9
    for size in range(lo, hi):
        font = ImageFont.truetype(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        h = bbox[3] - bbox[1]
        diff = abs(h - target_h)
        if diff < best_diff:
            best_diff, best_size = diff, size
    return best_size


def apply_patch_spec(spec: dict, spec_path: str) -> str:
    base_path = os.path.join(REPO_ROOT, spec["base_image"])
    out_path = os.path.join(REPO_ROOT, spec.get("output_image", spec["base_image"]))
    font_path = find_font(spec.get("font", "DejaVuSansMono-Bold"))
    fill_color = tuple(spec.get("fill_color", [15, 22, 20]))
    text_color = tuple(spec.get("text_color", [232, 163, 61]))

    img = Image.open(base_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    for patch in spec["patches"]:
        draw.rectangle(tuple(patch["erase_box"]), fill=fill_color)

        target_h = patch["ink_bottom_y"] - patch["ink_top_y"]
        size = find_font_size(draw, patch["new_text"], font_path, target_h)
        font = ImageFont.truetype(font_path, size)
        bbox = draw.textbbox((0, 0), patch["new_text"], font=font)
        draw_x = patch["anchor_left_x"] - bbox[0]
        draw_y = patch["ink_top_y"] - bbox[1]
        draw.text((draw_x, draw_y), patch["new_text"], font=font, fill=text_color)
        print(f"  [{os.path.basename(spec_path)}] drew {patch['new_text']!r} "
              f"at ({draw_x},{draw_y}) font_size={size}")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path)
    return out_path


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("specs", nargs="+", help="path(s) to *.patch.json spec files")
    args = parser.parse_args(argv)

    for spec_path in args.specs:
        with open(spec_path, encoding="utf-8") as fh:
            spec = json.load(fh)
        out = apply_patch_spec(spec, spec_path)
        print(f"patched -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
