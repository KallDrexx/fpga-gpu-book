#!/usr/bin/env python3
"""ref_motion.py -- Python reference frames for bouncing_square (Chapter 1.3).

Usage: ref_motion.py OUTDIR
Writes OUTDIR/ref_motion_<label>.ppm for the four frames tb_frame_motion.v
dumps: free1, free2 (a square in open space) and bounce1, bounce2 (a square
that reaches the bottom-right corner).

Frame k (k = 1, 2) shows the square after k position updates: vga_timing
starts at the top-left pixel, so the first start of vertical blanking comes
before the first vsync, and the first dumped frame follows it.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

from ppm import blank, write_ppm  # noqa: E402

W, H, MAX = 640, 480, 7      # Go Board: 3 bits per channel
SIZE, DX, DY = 32, 3, 2      # must match tb_frame_motion.v
FREE = (100, 80)
BOUNCE = (602, 444)


def axis_step(pos, forward, step, limit):
    """One frame of motion along one axis.

    pos is the square's left (or top) edge, limit the largest legal value
    (screen size minus square size). Returns (new_pos, new_forward).
    Reaching or passing an edge puts the square exactly on it and reverses.
    """
    if forward:
        if pos + step >= limit:
            return limit, False
        return pos + step, True
    if pos <= step:
        return 0, True
    return pos - step, False


def position(start, frames):
    """Top-left corner of the square after `frames` updates."""
    x, y = start
    right = down = True
    for _ in range(frames):
        x, right = axis_step(x, right, DX, W - SIZE)
        y, down = axis_step(y, down, DY, H - SIZE)
    return x, y


def square_frame(start, frames):
    """A white SIZE x SIZE square on black, where the hardware should draw it."""
    x, y = position(start, frames)
    img = blank(W, H)
    img[y:y + SIZE, x:x + SIZE] = MAX
    return img


FRAMES = {
    "free1":   lambda: square_frame(FREE, 1),
    "free2":   lambda: square_frame(FREE, 2),
    "bounce1": lambda: square_frame(BOUNCE, 1),
    "bounce2": lambda: square_frame(BOUNCE, 2),
}


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)

    # The step check the chapter asks for, stated directly: between the two
    # free frames the square moves by exactly (DX, DY).
    (x1, y1), (x2, y2) = position(FREE, 1), position(FREE, 2)
    assert (x2 - x1, y2 - y1) == (DX, DY), "reference model is broken"

    for label, draw in FRAMES.items():
        write_ppm(os.path.join(outdir, f"ref_motion_{label}.ppm"), draw(), MAX)


if __name__ == "__main__":
    main()
