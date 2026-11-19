#!/usr/bin/env python3
"""ref_pong.py -- Python reference frames for pong_render (Chapter 2.1).

Usage: ref_pong.py OUTDIR
Writes OUTDIR/ref_pong_<label>.ppm for the four frames tb_frame_pong.v dumps.

The game runs on a 320x240 logical screen; every logical pixel is drawn as a
2x2 block of real 640x480 pixels. Drawing order sets priority: net first,
then paddles, then the ball on top.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

import numpy as np  # noqa: E402
from ppm import write_ppm  # noqa: E402

W, H, MAX = 320, 240, 7          # logical screen; 3 bits per channel
BALL, PAD_W, PAD_H = 4, 4, 32    # pong_render defaults
PAD_L_X, PAD_R_X = 8, 308

BLACK, WHITE = (0, 0, 0), (7, 7, 7)
YELLOW, GREY = (7, 7, 0), (3, 3, 3)

# {label: (ball_x, ball_y, lpad_y, rpad_y)} -- must match tb_frame_pong.v
POSITIONS = {
    "start":   (158, 118, 104, 104),
    "edges":   (0, 0, 0, 208),
    "net":     (157, 5, 207, 1),
    "overlap": (6, 100, 90, 33),
}


def logical_frame(ball_x, ball_y, lpad_y, rpad_y):
    """The 320x240 picture, indexed img[y, x] = (r, g, b)."""
    img = np.zeros((H, W, 3), dtype=np.uint16)
    # Net: two pixels wide in the middle, 8-pixel dashes with 8-pixel gaps.
    for y in range(H):
        if (y // 8) % 2 == 0:
            img[y, W // 2 - 1:W // 2 + 1] = GREY
    img[lpad_y:lpad_y + PAD_H, PAD_L_X:PAD_L_X + PAD_W] = WHITE
    img[rpad_y:rpad_y + PAD_H, PAD_R_X:PAD_R_X + PAD_W] = WHITE
    img[ball_y:ball_y + BALL, ball_x:ball_x + BALL] = YELLOW
    return img


def frame(label):
    """Scale the logical picture up 2x2 to 640x480."""
    img = logical_frame(*POSITIONS[label])
    return img.repeat(2, axis=0).repeat(2, axis=1)


FRAMES = {label: (lambda label=label: frame(label)) for label in POSITIONS}


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    for label, draw in FRAMES.items():
        write_ppm(os.path.join(outdir, f"ref_pong_{label}.ppm"), draw(), MAX)


if __name__ == "__main__":
    main()
