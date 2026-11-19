#!/usr/bin/env python3
"""ref_scroll.py -- Python reference frames for scroll_tiles (Chapter 2.5).

Usage: ref_scroll.py OUTDIR
Writes OUTDIR/ref_scroll_<label>.ppm for the five frames tb_frame_scroll.v dumps.

The map is 64x32 tiles of 8x8 pixels, 512x256 logical pixels in all. The
320x240 screen shows the window that starts at (scroll X, scroll Y): screen
pixel (sx, sy) shows map pixel ((sx + scroll X) mod 512, (sy + scroll Y) mod
256). Tile graphics come from rtl/common/tiles.hex (Chapter 2.3), with any
tiles the testbench redraws through the write port replaced. Every logical
pixel is drawn as a 2x2 block of real 640x480 pixels.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

import numpy as np  # noqa: E402
from ppm import write_ppm  # noqa: E402
from ref_tiles import MAX, load_tiles  # noqa: E402

MAP_COLS, MAP_ROWS = 64, 32
MAP_W, MAP_H = MAP_COLS * 8, MAP_ROWS * 8   # 512 x 256 logical pixels
SCREEN_W, SCREEN_H = 320, 240
# tb_frame_scroll.v: PAL = {9'o776, 9'o264, 9'o521, 9'o013}, entry 0 last
PALETTE = [(0, 1, 3), (5, 2, 1), (2, 6, 4), (7, 7, 6)]


def word_to_pixels(w):
    """One 16-bit tile row -> eight 2-bit pixels, leftmost (bits 15:14) first."""
    return [(w >> (14 - 2 * c)) & 3 for c in range(8)]


def map_image(cells, pix):
    """cells: 2,048 tile numbers, row by row (row*64 + col). Returns the
    512x256 map as 2-bit color indices."""
    idx = np.zeros((MAP_H, MAP_W), dtype=np.uint8)
    for i, t in enumerate(cells):
        row, col = divmod(i, MAP_COLS)
        idx[row * 8:row * 8 + 8, col * 8:col * 8 + 8] = pix[t & 63]
    return idx


def window(idx, scroll_x, scroll_y):
    """The 320x240 view of the map at (scroll_x, scroll_y), with wraparound."""
    ys = (np.arange(SCREEN_H) + scroll_y) % MAP_H
    xs = (np.arange(SCREEN_W) + scroll_x) % MAP_W
    return idx[np.ix_(ys, xs)]


def to_frame(logical_rgb):
    """320x240x3 logical image -> 640x480x3 frame (each pixel 2x2)."""
    return logical_rgb.repeat(2, axis=0).repeat(2, axis=1).astype(np.uint16)


def colorize(idx, palette):
    return np.array(palette, dtype=np.uint16)[idx]


# ---- test data: must match tb_frame_scroll.v, keep them in step ----
TILE7 = [0xFF00, 0xE400, 0x9000, 0x4000, 0x001B, 0x0006, 0x0001, 0x5A0F]


def test_cells():
    return [(i % MAP_COLS + 9 * (i // MAP_COLS)) % 64 for i in range(MAP_COLS * MAP_ROWS)]


def test_tiles():
    pix = load_tiles().astype(np.uint8)
    pix[7] = [word_to_pixels(w) for w in TILE7]
    return pix


SCENES = {
    "origin": (0, 0),
    "fine": (3, 5),
    "wrapx": (300, 17),
    "wrapy": (101, 200),
    "corner": (511, 255),
}


def render(scroll_x, scroll_y):
    idx = window(map_image(test_cells(), test_tiles()), scroll_x, scroll_y)
    return to_frame(colorize(idx, PALETTE))


FRAMES = {label: (lambda s=s: render(*s)) for label, s in SCENES.items()}


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    for label, draw in FRAMES.items():
        write_ppm(os.path.join(outdir, f"ref_scroll_{label}.ppm"), draw(), MAX)


if __name__ == "__main__":
    main()
