#!/usr/bin/env python3
"""ref_patterns.py -- Python reference images for test_pattern (Chapter 1.2).

Usage: ref_patterns.py OUTDIR
Writes OUTDIR/ref_patterns_<label>.ppm for each label the testbench dumps.

Every ref_<name>.py in later chapters follows the same shape: one function per
frame that returns an (height, width, 3) array, and a FRAMES table mapping the
label used in the file name to that function.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

import numpy as np  # noqa: E402

from ppm import blank, write_ppm  # noqa: E402

W, H, MAX = 640, 480, 7  # Go Board: 3 bits per channel

# Coordinate grids: X[y, x] == x and Y[y, x] == y for every pixel.
Y, X = np.mgrid[0:H, 0:W]


def checker():
    """32x32 squares, white in the top-left corner."""
    img = blank(W, H)
    white = ((X // 32) % 2) == ((Y // 32) % 2)
    img[white] = (MAX, MAX, MAX)
    return img


def gradient():
    """Red steps 0..7 left to right, green 0..7 top to bottom, blue = 7 - red."""
    img = blank(W, H)
    img[..., 0] = X // (W // 8)
    img[..., 1] = Y // (H // 8)
    img[..., 2] = MAX - X // (W // 8)
    return img


def border():
    """A white 1-pixel frame on the outermost rows and columns, black inside."""
    img = blank(W, H)
    img[0, :] = img[H - 1, :] = MAX
    img[:, 0] = img[:, W - 1] = MAX
    return img


def bars():
    """Chapter 1.1's eight color bars: white, yellow, cyan, green, magenta, red, blue, black."""
    rgb = [(1, 1, 1), (1, 1, 0), (0, 1, 1), (0, 1, 0), (1, 0, 1), (1, 0, 0), (0, 0, 1), (0, 0, 0)]
    img = blank(W, H)
    for i, c in enumerate(rgb):
        img[:, i * 80:(i + 1) * 80] = tuple(MAX * v for v in c)
    return img


FRAMES = {"checker": checker, "gradient": gradient, "border": border, "bars": bars}


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    for label, draw in FRAMES.items():
        write_ppm(os.path.join(outdir, f"ref_patterns_{label}.ppm"), draw(), MAX)


if __name__ == "__main__":
    main()
