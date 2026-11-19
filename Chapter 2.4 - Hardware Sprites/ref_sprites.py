#!/usr/bin/env python3
"""ref_sprites.py -- Python reference frames for sprite_engine (Chapter 2.4).

Usage: ref_sprites.py OUTDIR
Writes OUTDIR/ref_sprites_<label>.ppm for the five frames tb_frame_sprites.v dumps.

The background is Chapter 2.3's tile layer (ref_tiles.py and
rtl/common/tiles.hex) with cell i holding tile i mod 64. Four 16x16 sprites
from rtl/common/sprites.hex are drawn on top, in logical 320x240 pixels:
16 images of 16 rows, one 32-bit word per row, leftmost pixel in bits 31:30.
Pixel value 0 is transparent, 1-3 pick SPAL1-SPAL3. Sprite 0 has the highest
priority, sprite 3 the lowest, and every sprite is in front of the tiles.
Sprites are clipped at all four screen edges. Every logical pixel is drawn
as a 2x2 block of real 640x480 pixels.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

import numpy as np  # noqa: E402
from ppm import write_ppm  # noqa: E402
from ref_tiles import COLS, ROWS, MAX, PALETTE, load_tiles, read_hex  # noqa: E402

W, H, SIZE = 320, 240, 16
# tb_frame_sprites.v: SPAL1 = 9'o777, SPAL2 = 9'o704, SPAL3 = 9'o770
SPRITE_PALETTE = {1: (7, 7, 7), 2: (7, 0, 4), 3: (7, 7, 0)}

HERE = os.path.dirname(os.path.abspath(__file__))
SPRITE_FILE = os.path.join(HERE, "..", "..", "rtl", "common", "sprites.hex")


def load_sprites(path=SPRITE_FILE):
    """Returns pix[image, row, col], the 2-bit value of every sprite pixel."""
    words = read_hex(path)
    assert len(words) == 16 * SIZE, f"{path}: expected 256 words, got {len(words)}"
    w = np.array(words, dtype=np.uint32).reshape(16, SIZE, 1)
    shifts = np.array([30 - 2 * c for c in range(SIZE)], dtype=np.uint32)
    return ((w >> shifts) & 3).astype(np.uint8)


def background():
    """The tile layer at logical resolution: cell i holds tile i mod 64."""
    pix = load_tiles()
    idx = np.zeros((ROWS * 8, COLS * 8), dtype=np.uint16)
    for i in range(COLS * ROWS):
        row, col = divmod(i, COLS)
        idx[row * 8:row * 8 + 8, col * 8:col * 8 + 8] = pix[i % 64]
    return PALETTE[idx]


def render(sprites):
    """sprites: four (x, y, image, enabled) tuples, sprite 0 first."""
    img = background()
    spix = load_sprites()
    # Draw the lowest priority first so sprite 0 ends up on top.
    for sx, sy, n, en in reversed(sprites):
        if not en:
            continue
        for r in range(SIZE):
            for c in range(SIZE):
                v = spix[n, r, c]
                px, py = sx + c, sy + r
                if v and 0 <= px < W and 0 <= py < H:
                    img[py, px] = SPRITE_PALETTE[v]
    return img.repeat(2, axis=0).repeat(2, axis=1)


# ---- scenes: must match tb_frame_sprites.v, keep them in step ----
# (x, y, image, enabled) for sprites 0-3.
SCENES = {
    "corner":    [(0, 0, 5, 1), (304, 224, 6, 1), (-6, 100, 7, 1), (311, 50, 8, 1)],
    "offscreen": [(150, -9, 9, 1), (60, 233, 10, 1), (-15, -15, 11, 1), (319, 239, 12, 1)],
    "overlap":   [(100, 100, 13, 1), (106, 104, 14, 1), (96, 108, 15, 1), (110, 96, 0, 1)],
    "hidden":    [(160, 100, 3, 1), (150, 200, 0, 0), (-16, 50, 5, 1), (320, 60, 6, 1)],
    "abut":      [(200, 240, 7, 1), (100, -16, 8, 1), (40, 40, 1, 1), (56, 40, 4, 1)],
}

FRAMES = {label: (lambda s=s: render(s)) for label, s in SCENES.items()}


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    for label, draw in FRAMES.items():
        write_ppm(os.path.join(outdir, f"ref_sprites_{label}.ppm"), draw(), MAX)


if __name__ == "__main__":
    main()
