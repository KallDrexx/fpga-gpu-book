#!/usr/bin/env python3
"""ref_shaded.py -- test stream and reference frames for shaded triangles (Chapter 6.3).

Usage (from the repo root):
    .venv/bin/python sim/py/ref_shaded.py --stream sim   once: writes the stream the testbench plays
    .venv/bin/python sim/py/ref_shaded.py OUTDIR         writes OUTDIR/ref_shaded_f0.ppm, f1, f2, f3

--stream writes (commit both files):
    sim/shaded_stream.hex    what the testbench sends, one 16-bit hex entry per line (the entries of
                             sim/display_lists_stream.hex, Chapter 5.7, without the HUD window):
                               00NN  one byte, inside the open frame
                               A000  open an SPI frame: wait for READY, then CS falls
                               A001  close the frame: CS rises
                               3000  wait for the start of the next video frame (beam at 0, 0)
                               2YYY  wait for the start of line YYY (hex) of the video frame in progress
                               4000  arm the frame dumpers: the next frame start begins frame f0
                               1NNN  fence: read frames until NNN (hex) more reply bytes have arrived
                               FFFF  end of the stream
    sim/shaded_replies.hex   18 bytes: the two GET_INFO replies the testbench must read

The story. Setup: both framebuffers cleared, 8 palette ramps of 32 shades (256 SET_PALETTE), and the first
picture, drawn with eleven DRAW_TRI_SHADED packets into the back buffer and flipped:
    a big one with corners off the screen, and on top of it a triangle whose three corners have intensities 0, 15.5
    and 31 (a smooth gradient),
    a quad of two triangles that share an edge and its two vertex intensities (no seam may show),
    a flat triangle (all corners the same intensity: one color), a clockwise one, a thin sliver, a degenerate one (nothing), one with intensities past 31 (clamped), one whose
    ramp byte has bits 7:3 set (ignored), and one with equal depths and different intensities.
Display list 0 holds a second picture (a CLEAR, two shaded triangles, a FLIP): a list may hold DRAW_TRI_SHADED.
    k=0  nothing; the dumpers are armed
    k=1  (frame f0 shows the first picture)  RUN_LIST 0
    k=2  (frame f1 shows the list's picture) WRITE_LIST_WORD patches one vertex intensity of the first triangle in
         the list, then RUN_LIST 0
    k=3  (frame f2 shows the patched picture) a DRAW_TRI_SHADED with 21 payload bytes, a real triangle one byte short (BAD_LEN, it
         must draw nothing), an unknown opcode 0x61 (BAD_OP), one more valid small triangle, FLIP, GET_INFO: flags 06
    k=4  (frame f3 shows the buffer that was drawn in during k=3: the picture of f1 and the small triangle; a short triangle
         that was drawn after all would show)
The first GET_INFO (flags 00, after eleven valid shaded triangles) says capabilities 07FF.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model, raster_model

import numpy as np  # noqa: E402

import gpu_model as g  # noqa: E402
from ppm import write_ppm  # noqa: E402

OPEN, CLOSE, FRAME_START, ARM, FENCE, END, LINE = 0xA000, 0xA001, 0x3000, 0x4000, 0x1000, 0xFFFF, 0x2000
LABELS = ["f0", "f1", "f2", "f3"]
PATCH_LINE = 20

RAMPS = {0: ((24, 24, 48), (255, 255, 255)), 1: ((40, 0, 0), (255, 90, 90)), 2: ((0, 40, 0), (120, 255, 120)),
         3: ((0, 0, 48), (110, 140, 255)), 4: ((48, 40, 0), (255, 240, 60)), 5: ((0, 40, 40), (120, 255, 255)),
         6: ((40, 0, 40), (255, 110, 255)), 7: ((50, 20, 0), (255, 160, 40))}


def palette():
    return b"".join(b"".join(g.ramp_palette(r, dark, light)) for r, (dark, light) in RAMPS.items())


def scene_a():
    """The first picture: a list of (ramp, three vertices (x, y, z, i)). Drawn in this order."""
    s = [
        (4, [(-60, 100, 500, 0), (120, -40, 65535, 248), (140, 300, 20000, 100)]),     # corners off the screen, clockwise
        (2, [(30, 20, 1000, 0), (150, 60, 30000, 124), (60, 200, 60000, 248)]),         # the gradient triangle, on top of it
        (1, [(180, 20, 100, 0), (300, 20, 40000, 248), (300, 110, 9000, 124)]),         # a quad: first half ...
        (1, [(180, 20, 100, 0), (300, 110, 9000, 124), (180, 110, 20000, 248)]),        # ... second half, shares (180,20) - (300,110)
        (5, [(200, 130, 5, 160), (300, 130, 6, 160), (250, 220, 7, 160)]),              # flat: all intensities 20 shades
        (3, [(100, 150, 10, 0), (40, 230, 20000, 248), (130, 235, 40000, 40)]),         # another winding
        (6, [(10, 232, 0, 0), (310, 238, 65535, 248), (160, 234, 100, 124)]),           # a thin sliver
        (2, [(250, 60, 0, 0), (250, 60, 0, 255), (250, 60, 0, 0)]),                     # degenerate: nothing
        (7, [(180, 150, 0, 255), (196, 150, 0, 200), (180, 190, 0, 100)]),              # 255 and 200 are past 31 shades: clamped
        (8 + 0, [(120, 100, 0, 8), (170, 100, 4000, 248), (120, 140, 4000, 248)]),      # ramp byte 8: bits 7:3 ignored, ramp 0
        (6, [(60, 20, 50000, 0), (100, 20, 50000, 248), (60, 60, 50000, 124)]),         # equal depths
    ]
    return s


def scene_b():
    return [
        (3, [(40, 40, 100, 0), (280, 60, 20000, 248), (160, 200, 60000, 100)]),
        (5, [(20, 220, 0, 248), (300, 220, 0, 0), (160, 120, 0, 124)]),
    ]


def tri_packets(scene):
    return b"".join(g.draw_tri_shaded(*v, ramp) for ramp, v in scene)


def build():
    """Returns (stream words, the replies, {label: screen index bytes}, model)."""
    m = g.Gpu()
    items, replies, shots = [], bytearray(), {}
    state = {"armed": False}

    def frame(data, line=None):
        if line is not None:
            items.append(LINE | line)
        data = bytes(data)
        items.extend([OPEN] + list(data) + [CLOSE])
        replies.extend(m.feed(data))

    def start_frame():
        items.append(FRAME_START)
        m.vblank()
        if state["armed"]:
            shots[LABELS[len(shots)]] = m.screen()

    # the buffers: both cleared
    frame(g.clear(0) + g.flip() + g.clear(0))
    start_frame()
    pal = palette()
    for i in range(0, len(pal), 6 * 32):
        frame(pal[i:i + 6 * 32])
    frame(g.clear(0))
    for ramp, v in scene_a():
        frame(g.draw_tri_shaded(*v, ramp))
    frame(g.flip() + g.get_info())
    items.append(FENCE | 9)
    assert m.errors == 0
    # the list: CLEAR, two shaded triangles, FLIP
    lb = g.clear(0) + tri_packets(scene_b()) + g.flip()
    for p in g.list_packets(0, lb):
        frame(p)
    # list layout: CLEAR (2 + 1 bytes), then the first triangle: 2 header bytes, 12 coordinate bytes, ramp, 6 z bytes, 3 i bytes
    patch_off = 3 + 2 + 12 + 1 + 6                   # offset of i0, i1 (the word i0 | i1 << 8) of the first triangle
    assert lb[patch_off - 0] == scene_b()[0][1][0][3] and lb[patch_off + 1] == scene_b()[0][1][1][3]

    start_frame()                                    # k = 0
    items += [LINE | PATCH_LINE, ARM]
    state["armed"] = True
    start_frame()                                    # k = 1: frame f0
    frame(g.run_list(0), PATCH_LINE)
    start_frame()                                    # k = 2: frame f1
    frame(g.write_list_word(0, patch_off, 248 | (0 << 8)) + g.run_list(0), PATCH_LINE)   # i0 = 248, i1 = 0 (they were 0 and 248)
    start_frame()                                    # k = 3: frame f2
    short = g.draw_tri_shaded((10, 10, 0, 0), (300, 40, 9000, 248), (100, 230, 60000, 100), 1)[2:23]     # a real triangle, one byte short
    frame(g.packet(g.DRAW_TRI_SHADED, short) + g.packet(0x61, bytes(22))
          + g.draw_tri_shaded((250, 150, 0, 0), (310, 170, 0, 248), (260, 230, 0, 124), 6) + g.flip() + g.get_info(), PATCH_LINE)
    items.append(FENCE | 9)
    start_frame()                                    # k = 4: frame f3 (the picture of f1 and a small triangle on it)
    assert len(shots) == 4, len(shots)

    want = bytes([0x47, 1, 0xFF, 0x07, 0x40, 0x01, 0xF0, 0x00, 0x00,
                  0x47, 1, 0xFF, 0x07, 0x40, 0x01, 0xF0, 0x00, 0x06])
    assert bytes(replies) == want, bytes(replies).hex()
    sh = [shots[l] for l in LABELS]
    assert len({bytes(x) for x in sh}) == 4, "the four frames must differ"
    return items, bytes(replies), shots, m


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--stream":
        items, info, shots, m = build()
        items += [END]
        with open(os.path.join(args[1], "shaded_stream.hex"), "w") as f:
            f.write("".join("%04x\n" % w for w in items))
        with open(os.path.join(args[1], "shaded_replies.hex"), "w") as f:
            f.write("".join("%02x\n" % b for b in info))
        print("wrote %s/shaded_stream.hex (%d entries) and shaded_replies.hex" % (args[1], len(items)))
        return 0
    if len(args) == 1 and not args[0].startswith("-"):
        items, info, shots, m = build()
        pal = np.array([[(c >> 16) & 255, (c >> 8) & 255, c & 255] for c in m.pal], dtype=np.uint16)
        for label, idx in shots.items():
            small = pal[np.frombuffer(idx, dtype=np.uint8).reshape(g.H, g.W)]
            big = small.repeat(2, axis=0).repeat(2, axis=1)
            write_ppm(os.path.join(args[0], "ref_shaded_%s.ppm" % label), big, 255)
        print("wrote %d reference frames to %s" % (len(shots), args[0]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
