#!/usr/bin/env python3
"""ref_text.py -- Python reference frames for text_mode (Chapter 2.2).

Usage: ref_text.py OUTDIR
Writes OUTDIR/ref_text_<label>.ppm for the two frames tb_frame_text.v dumps.

The screen is 40x30 character cells of 8x8 logical pixels (320x240); every
logical pixel is drawn as a 2x2 block of real 640x480 pixels. The glyphs come
from rtl/common/font8x8.hex, the same file the Verilog font ROM loads.
A code with bit 7 set draws glyph (code & 0x7F) with the colors swapped.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

import numpy as np  # noqa: E402
from ppm import write_ppm  # noqa: E402

COLS, ROWS, MAX = 40, 30, 7
FG = (7, 6, 4)   # tb_frame_text.v: FG = 9'o764
BG = (0, 1, 3)   #                  BG = 9'o013

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_FILE = os.path.join(HERE, "..", "..", "rtl", "common", "font8x8.hex")


def read_hex(path):
    """Read a $readmemh file: whitespace-separated hex bytes, // comments."""
    values = []
    with open(path) as f:
        for line in f:
            values += [int(tok, 16) for tok in line.split("//")[0].split()]
    return values


def load_font(path=FONT_FILE):
    data = read_hex(path)
    assert len(data) == 128 * 8, f"{path}: expected 1024 bytes, got {len(data)}"
    return np.array(data, dtype=np.uint8).reshape(128, 8)


def render(cells, font):
    """cells: 1,200 codes, row by row. Returns the 480x640x3 frame."""
    # bits[c, row, col] = 1 where glyph c has a foreground pixel; bit 7 is col 0.
    bits = np.unpackbits(font, axis=1).reshape(128, 8, 8)
    img = np.zeros((ROWS * 8, COLS * 8, 3), dtype=np.uint16)
    for i, code in enumerate(cells):
        row, col = divmod(i, COLS)
        on = bits[code & 0x7F].astype(bool)
        if code & 0x80:
            on = ~on
        cell = np.where(on[:, :, None], FG, BG)
        img[row * 8:row * 8 + 8, col * 8:col * 8 + 8] = cell
    return img.repeat(2, axis=0).repeat(2, axis=1)


# ---- screens: must match tb_frame_text.v, keep them in step ----

def put(cells, row, col, text, attr=0):
    for k, ch in enumerate(text):
        cells[row * COLS + col + k] = ord(ch) | attr


def string_screen():
    cells = [0x20] * (COLS * ROWS)
    put(cells, 0, 0, "#"); put(cells, 0, 39, "#")
    put(cells, 29, 0, "#"); put(cells, 29, 39, "#")
    put(cells, 0, 2, "The quick brown fox jumps over")
    for i in range(95):
        cells[2 * COLS + i] = 0x20 + i
    put(cells, 8, 11, "Hello, FPGA world!")
    put(cells, 12, 11, " TEXT MODE 40x30 ", 0x80)
    put(cells, 15, 0, "@"); put(cells, 15, 39, "W")
    put(cells, 29, 2, "MW__WM {edges} |0123456789|")
    put(cells, 28, 37, "end", 0x80)
    return cells


def codes_screen():
    return [i % 256 for i in range(COLS * ROWS)]


FRAMES = {
    "string": lambda: render(string_screen(), load_font()),
    "codes": lambda: render(codes_screen(), load_font()),
}


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    for label, draw in FRAMES.items():
        write_ppm(os.path.join(outdir, f"ref_text_{label}.ppm"), draw(), MAX)


if __name__ == "__main__":
    main()
