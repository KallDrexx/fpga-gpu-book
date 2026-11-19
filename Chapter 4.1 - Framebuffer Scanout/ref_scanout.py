#!/usr/bin/env python3
"""ref_scanout.py -- test image and Python reference frames for fb_scanout (Chapter 4.1).

Usage:
    ref_scanout.py --hex DIR    write DIR/fb_test.hex and DIR/pal_test.hex
                                (run once: .venv/bin/python sim/py/ref_scanout.py --hex rtl/common)
    ref_scanout.py OUTDIR       write OUTDIR/ref_scanout_<label>.ppm for the
                                frames tb_frame_scanout.v dumps

The framebuffer is 320x240 bytes, address y*320 + x, one palette index per
pixel. The palette is 256 RGB888 entries. Every logical pixel is shown as a
2x2 block of the 640x480 output. The test image is drawn by image() and
palette() below; the hex files are just those arrays written out, so the
Verilog and this reference cannot disagree about the source.

What the image is built to catch:
  * The background index changes by 1 per x step and by 3 per y step, so a
    pixel shown one place too early or late in x or y, a wrong stride, or a
    missing line repeat changes almost every pixel.
  * A 1-pixel border and four different corner markers show the first and
    last row and column, flips and address wrap (the last byte, 76,799, is
    the bottom-right corner).
  * A 1-pixel checkerboard shows clean 2x2 blocks on the monitor.
  * A 16x16 swatch shows all 256 palette entries; every ramp entry (16-255)
    has R, G and B different from each other, so a swapped channel changes
    the color.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

import numpy as np  # noqa: E402

from ppm import write_ppm  # noqa: E402

W, H = 320, 240          # logical framebuffer
SCALE = 2                # shown 2x on 640x480
MAX = 255                # 8 bits per channel

# Entries 0-15: named colors. 16-255: a ramp used for the background.
NAMED = [
    0x000000, 0xFFFFFF, 0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00, 0x00FFFF, 0xFF00FF,
    0x808080, 0xFF8000, 0x000080, 0x008000, 0x800000, 0x80FF80, 0xC0C0FF, 0x402010,
]
BLACK, WHITE, RED, GREEN, BLUE, YELLOW = 0, 1, 2, 3, 4, 5


def palette():
    """(256, 3) uint8 array of (r, g, b)."""
    pal = np.zeros((256, 3), dtype=np.uint8)
    for i, c in enumerate(NAMED):
        pal[i] = ((c >> 16) & 255, (c >> 8) & 255, c & 255)
    for i in range(16, 256):
        t = i - 16                                   # 0..239
        pal[i] = (t + 8, 255 - abs(2 * t - 239) // 2, 255 - t)   # r != b always
    return pal


def image():
    """(240, 320) uint8 array of palette indices."""
    y, x = np.mgrid[0:H, 0:W]
    img = (16 + (x + 3 * y) % 240).astype(np.uint8)     # background ramp

    # 1-pixel checkerboard, 64x32 at (16, 16)
    cy, cx = np.mgrid[16:48, 16:80]
    img[16:48, 16:80] = np.where((cx ^ cy) & 1, WHITE, BLACK)

    # The 16 named colors as 8x32 bars at (16, 200)
    for i in range(16):
        img[200:232, 16 + 8 * i:24 + 8 * i] = i

    # All 256 entries: a 16x16 grid of 6x6 squares at (208, 128)
    for i in range(256):
        r, c = divmod(i, 16)
        img[128 + 6 * r:134 + 6 * r, 208 + 6 * c:214 + 6 * c] = i

    # 1-pixel white border, then 8x8 corner markers just inside it
    img[0, :] = img[H - 1, :] = WHITE
    img[:, 0] = img[:, W - 1] = WHITE
    img[1:9, 1:9] = RED                 # top left
    img[1:9, W - 9:W - 1] = GREEN       # top right
    img[H - 9:H - 1, 1:9] = BLUE        # bottom left
    img[H - 9:H - 1, W - 9:W - 1] = YELLOW  # bottom right
    return img


# Writes tb_frame_scanout.v makes through the draw (clk_sys) port during the
# vertical blanking after the first dumped frame. Keep the two in step.
PAL_EDITS = {1: 0xFF8000, 254: 0x0080FF, 255: 0x80FF00}


def fb_edits():
    """List of (address, index) the testbench writes, in order."""
    ed = []
    for (x, y) in ((0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)):
        ed.append((y * W + x, 254))
    for x in range(40, 280):              # horizontal line on row 120
        ed.append((120 * W + x, 255))
    for y in range(20, 220):              # vertical line in column 161
        ed.append((y * W + 161, 255))
    return ed


def show(img, pal):
    """Logical image + palette -> the 640x480 RGB frame scanout produces."""
    rgb = pal[img].astype(np.uint16)                    # (240, 320, 3)
    return rgb.repeat(SCALE, axis=0).repeat(SCALE, axis=1)


def frame_image():
    """The preloaded image, unchanged."""
    return show(image(), palette())


def frame_edit():
    """The image after the testbench's draw-port and palette writes."""
    img, pal = image(), palette()
    flat = img.reshape(-1)
    for a, v in fb_edits():
        flat[a] = v
    for i, c in PAL_EDITS.items():
        pal[i] = ((c >> 16) & 255, (c >> 8) & 255, c & 255)
    return show(img, pal)


FRAMES = {"image": frame_image, "edit": frame_edit}


def write_hex(outdir):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "fb_test.hex"), "w") as f:
        f.write("// Chapter 4.1 test image: 320x240 palette indices, address y*320+x\n")
        f.write("\n".join(f"{v:02x}" for v in image().reshape(-1)) + "\n")
    with open(os.path.join(outdir, "pal_test.hex"), "w") as f:
        f.write("// Chapter 4.1 test palette: 256 entries, RRGGBB\n")
        f.write("\n".join(f"{r:02x}{g:02x}{b:02x}" for r, g, b in palette()) + "\n")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--hex":
        write_hex(sys.argv[2])
        return
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR  |  {sys.argv[0]} --hex DIR")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    for label, draw in FRAMES.items():
        write_ppm(os.path.join(outdir, f"ref_scanout_{label}.ppm"), draw(), MAX)


if __name__ == "__main__":
    main()
