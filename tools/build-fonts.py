#!/usr/bin/env python3
"""
Build the webfont subsets that ship in app/fonts/.

Why this exists:
  The site used to ship inter-variable.ttf (875 KB) and
  space-grotesk-variable.ttf (134 KB). Neither contains a single Hebrew or
  Arabic glyph, so ~95% of the Hebrew/Arabic UI was rendered in whatever the
  visitor's OS falls back to — and 72% of the page weight bought nothing.

  These subsets cover the scripts the site actually renders:
    heebo-latin.woff2    Latin + punctuation            (Heebo covers both)
    heebo-hebrew.woff2   Hebrew points/letters + punc   (preloaded — RTL default)
    arabic.woff2         Arabic + presentation forms    (Noto Sans Arabic)

Sources (both SIL Open Font License 1.1):
  Heebo            https://fonts.google.com/specimen/Heebo        (Hebrew + Latin)
  Noto Sans Arabic https://fonts.google.com/noto/specimen/Noto+Sans+Arabic
  Raw TTFs are kept in assets/fonts/ so the subsets can be rebuilt.

Usage:  python tools/build-fonts.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "assets", "fonts")
OUT = os.path.join(ROOT, "app", "fonts")

# Shared punctuation/arrows the UI and AI-generated text need in every script.
COMMON = "U+00A0,U+2000-206F,U+20AA,U+20AC,U+2190-21BB,U+2212"

SUBSETS = [
    # (source ttf, output wght2, unicode ranges, axis limits)
    # Heebo carries both Hebrew and Latin; the UI uses weights 300-700.
    ("Heebo[wght].ttf", "heebo-latin.woff2",
     f"U+0000-00FF,U+0100-017F,{COMMON}", {"wght": (300, 800)}),
    ("Heebo[wght].ttf", "heebo-hebrew.woff2",
     f"U+0590-05FF,U+FB1D-FB4F,{COMMON}", {"wght": (300, 800)}),
    # The source is 845 KB: pinning the width axis and keeping only the letters
    # feeds actually use (no presentation-forms block — shaping comes from the
    # OpenType features, and precomposed presentation forms are never emitted
    # by news text) brings the subset down to ~60 KB.
    ("NotoSansArabic[wdth,wght].ttf", "arabic.woff2",
     f"U+0600-06FF,U+0750-077F,{COMMON}", {"wght": (300, 800), "wdth": 100}),
]

# Arabic shaping features (init/medi/fina/isol/lig…) must survive subsetting.
FEATURES = "*"


def _instantiate(src_path, axes):
    """Pin/limit variable axes before subsetting — smaller files, same look."""
    from fontTools import ttLib
    from fontTools.varLib import instancer
    font = ttLib.TTFont(src_path)
    font = instancer.instantiateVariableFont(font, axes, inplace=False, updateFontNames=False)
    tmp = src_path + ".instanced.ttf"
    font.save(tmp)
    return tmp


def main():
    os.makedirs(OUT, exist_ok=True)
    for src, dst, unicodes, axes in SUBSETS:
        src_path = os.path.join(SRC, src)
        if not os.path.exists(src_path):
            print(f"!! missing source font: {src_path}", file=sys.stderr)
            return 1
        out_path = os.path.join(OUT, dst)
        target = _instantiate(src_path, axes) if axes else src_path
        cmd = [
            sys.executable, "-m", "fontTools.subset", target,
            f"--output-file={out_path}",
            f"--unicodes={unicodes}",
            "--flavor=woff2",
            f"--layout-features={FEATURES}",
            "--desubroutinize",
            "--name-IDs=1,2,3,4,5,6",
            "--no-hinting",
        ]
        subprocess.run(cmd, check=True)
        if target != src_path:
            os.remove(target)
        kb = os.path.getsize(out_path) / 1024
        print(f"  {dst:22s} {kb:7.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
