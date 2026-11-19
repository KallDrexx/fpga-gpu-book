#!/usr/bin/env python3
"""ref_flip.py -- Python reference frames for tb_frame_flip (Chapter 4.5).

Usage:
    ref_flip.py OUTDIR    write OUTDIR/ref_flip_<label>.ppm for the frames
                          tb_frame_flip.v dumps

Both buffers start as Chapter 4.1's test image. Picture A is drawn into the
back buffer and flipped onto the screen; picture B is then drawn into the
other buffer, which still holds the test image underneath, and shown with a
second FLIP; a third FLIP shows picture A again, unchanged. tb_frame_flip
sends the same commands (the lists are in PICTURE_A and PICTURE_B), and the
model here runs them.

  drawn    the screen after the first FLIP and after B has been drawn into the
           back buffer: picture A, with nothing of B on it
  flipped  after the second FLIP: picture B (the test image with extras)
  again    after the third FLIP: picture A again
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model

import numpy as np  # noqa: E402

import gpu_model as g  # noqa: E402
from ppm import write_ppm  # noqa: E402
from ref_scanout import MAX, image, palette, show  # noqa: E402

PICTURE_A = [
    g.clear(0x30),
    g.fill_rect(40, 30, 279, 209, 0x80),
    g.draw_line(0, 0, 319, 239, 0x01),
    g.draw_line(319, 0, 0, 239, 0x01),
]
PICTURE_B = [
    g.fill_rect(100, 80, 219, 159, 0xC8),
    g.draw_line(0, 120, 319, 120, 0xF0),
    g.plot(160, 120, 0x02),
]


def frames():
    pal0 = [(int(r) << 16) | (int(gg) << 8) | int(b) for r, gg, b in palette()]
    model = g.Gpu(fb=image().reshape(-1).tobytes(), pal=pal0)
    def shown():
        img = np.frombuffer(bytes(model.front_fb), dtype=np.uint8).reshape(g.H, g.W)
        return show(img, palette())

    out = {}
    model.feed(b"".join(PICTURE_A) + g.flip() + b"".join(PICTURE_B))
    out["drawn"] = shown()
    model.feed(g.flip())
    out["flipped"] = shown()
    model.feed(g.flip())
    out["again"] = shown()
    return out


_cache = {}


def _frame(label):
    if not _cache:
        _cache.update(frames())
    return _cache[label]


FRAMES = {label: (lambda label=label: _frame(label)) for label in ("drawn", "flipped", "again")}


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR")
    os.makedirs(sys.argv[1], exist_ok=True)
    for label, draw in FRAMES.items():
        write_ppm(os.path.join(sys.argv[1], f"ref_flip_{label}.ppm"), draw(), MAX)


if __name__ == "__main__":
    main()
