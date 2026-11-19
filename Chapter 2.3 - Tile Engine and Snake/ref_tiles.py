#!/usr/bin/env python3
"""ref_tiles.py -- Python reference frames for tile_engine (Chapter 2.3).

Usage: ref_tiles.py OUTDIR
Writes OUTDIR/ref_tiles_<label>.ppm for the two frames tb_frame_tiles.v dumps.

The screen is a 40x30 map of 8x8 tiles (320x240 logical pixels); every
logical pixel is drawn as a 2x2 block of real 640x480 pixels. Tile graphics
come from rtl/common/tiles.hex, the same file the Verilog tile ROM loads:
64 tiles of 8 rows, one 16-bit word per row, leftmost pixel in bits 15:14.
Each 2-bit pixel selects one of four palette colors.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

import numpy as np  # noqa: E402
from ppm import write_ppm  # noqa: E402

COLS, ROWS, MAX = 40, 30, 7
# tb_frame_tiles.v: PAL0 = 9'o013, PAL1 = 9'o521, PAL2 = 9'o264, PAL3 = 9'o776
PALETTE = np.array([(0, 1, 3), (5, 2, 1), (2, 6, 4), (7, 7, 6)], dtype=np.uint16)

HERE = os.path.dirname(os.path.abspath(__file__))
TILE_FILE = os.path.join(HERE, "..", "..", "rtl", "common", "tiles.hex")


def read_hex(path):
    """Read a $readmemh file: whitespace-separated hex words, // comments."""
    values = []
    with open(path) as f:
        for line in f:
            values += [int(tok, 16) for tok in line.split("//")[0].split()]
    return values


def load_tiles(path=TILE_FILE):
    """Returns pix[tile, row, col], the 2-bit color index of every pixel."""
    words = read_hex(path)
    assert len(words) == 64 * 8, f"{path}: expected 512 words, got {len(words)}"
    w = np.array(words, dtype=np.uint16).reshape(64, 8, 1)
    shifts = np.array([14 - 2 * c for c in range(8)], dtype=np.uint16)
    return (w >> shifts) & 3


def render(cells, pix):
    """cells: 1,200 tile numbers (0-63), row by row. Returns the 480x640x3 frame."""
    idx = np.zeros((ROWS * 8, COLS * 8), dtype=np.uint16)
    for i, t in enumerate(cells):
        row, col = divmod(i, COLS)
        idx[row * 8:row * 8 + 8, col * 8:col * 8 + 8] = pix[t]
    img = PALETTE[idx]
    return img.repeat(2, axis=0).repeat(2, axis=1)


# ---- screens: must match tb_frame_tiles.v, keep them in step ----
EMPTY, WALL, BODY, HEAD, FOOD = range(5)


def all_screen():
    return [i % 64 for i in range(COLS * ROWS)]


def scene_screen():
    cells = [EMPTY] * (COLS * ROWS)

    def put(col, row, t):
        cells[row * COLS + col] = t

    for r in range(ROWS):
        for c in range(COLS):
            if r in (0, ROWS - 1) or c in (0, COLS - 1):
                put(c, r, WALL)
    for i in range(16):
        put(2 + i, 2, 0x30 + i)
    for c in range(5, 21):
        put(c, 14, BODY)
    for r in range(15, 21):
        put(20, r, BODY)
    for c in range(21, 31):
        put(c, 20, BODY)
    put(31, 20, HEAD)
    put(35, 20, FOOD)
    put(1, 1, FOOD)
    put(38, 28, FOOD)
    return cells


FRAMES = {
    "all": lambda: render(all_screen(), load_tiles()),
    "scene": lambda: render(scene_screen(), load_tiles()),
}


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    for label, draw in FRAMES.items():
        write_ppm(os.path.join(outdir, f"ref_tiles_{label}.ppm"), draw(), MAX)


if __name__ == "__main__":
    main()
