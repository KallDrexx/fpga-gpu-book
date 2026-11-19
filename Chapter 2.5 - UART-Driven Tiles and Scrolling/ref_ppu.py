#!/usr/bin/env python3
"""ref_ppu.py -- Python reference frames for uart_ppu (Chapter 2.5).

Usage: ref_ppu.py OUTDIR
Writes OUTDIR/ref_ppu_<label>.ppm for the three frames tb_frame_ppu.v dumps.

The background is the scrolling 64x32 tile layer of ref_scroll.py, with the
palette, map and one redrawn tile that the testbench loads over the serial
line. On top are Chapter 2.4's four 16x16 sprites (ref_sprites.py), placed in
screen coordinates: they do not scroll with the map. Palette entries 0-3 color
the tiles, entries 5-7 are sprite colors 1-3.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

import numpy as np  # noqa: E402
from ppm import write_ppm  # noqa: E402
from ref_tiles import MAX, load_tiles  # noqa: E402
from ref_sprites import SIZE, load_sprites  # noqa: E402
from ref_scroll import (MAP_COLS, MAP_ROWS, SCREEN_W, SCREEN_H, colorize,  # noqa: E402
                        map_image, to_frame, window, word_to_pixels)


def octal_rgb(v):
    """9-bit {r, g, b} -> (r, g, b)."""
    return ((v >> 6) & 7, (v >> 3) & 7, v & 7)


# ---- test data: must match tb_frame_ppu.v, keep them in step ----
PALETTE = [octal_rgb(v) for v in (0o001, 0o531, 0o247, 0o675, 0o000, 0o777, 0o704, 0o770)]
TILE5 = [0xC003, 0x3FFC, 0x2008, 0x2E08, 0x2008, 0x2008, 0x3FFC, 0x0001]


def cells():
    return [(3 * (i % MAP_COLS) + 5 * (i // MAP_COLS) + 1) % 64
            for i in range(MAP_COLS * MAP_ROWS)]


def tiles():
    pix = load_tiles().astype(np.uint8)
    pix[5] = [word_to_pixels(w) for w in TILE5]
    return pix


# (x, y, image, enabled) for sprites 0-3, and (scroll X, scroll Y)
SCENE_A = ([(-6, 100, 0, 1), (150, 230, 1, 1), (300, 10, 3, 1), (60, 60, 4, 0)], (300, 200))
SCENE_C = ([(52, 56, 0, 1), (150, 230, 1, 1), (300, 10, 3, 1), (60, 60, 4, 1)], (37, 250))


def render(scene):
    sprites, (scroll_x, scroll_y) = scene
    img = colorize(window(map_image(cells(), tiles()), scroll_x, scroll_y), PALETTE[:4])
    spix = load_sprites()
    for sx, sy, n, en in reversed(sprites):   # sprite 0 ends up on top
        if not en:
            continue
        for r in range(SIZE):
            for c in range(SIZE):
                v = spix[n, r, c]
                px, py = sx + c, sy + r
                if v and 0 <= px < SCREEN_W and 0 <= py < SCREEN_H:
                    img[py, px] = PALETTE[4 + v]
    return to_frame(img)


# Frame b is drawn while the new positions arrive: it must still show scene A.
FRAMES = {
    "a": lambda: render(SCENE_A),
    "b": lambda: render(SCENE_A),
    "c": lambda: render(SCENE_C),
}


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    for label, draw in FRAMES.items():
        write_ppm(os.path.join(outdir, f"ref_ppu_{label}.ppm"), draw(), MAX)


if __name__ == "__main__":
    main()
