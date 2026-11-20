#!/usr/bin/env python3
"""ref_layers.py -- test stream and reference frames for the tile layers (Chapter 5.3).

Usage (from the repo root):
    .venv/bin/python sim/py/ref_layers.py --stream sim   once: writes the stream the testbench plays
    .venv/bin/python sim/py/ref_layers.py OUTDIR         writes OUTDIR/ref_layers_f0.ppm ... f9.ppm

--stream writes (commit both files):
    sim/layers_stream.hex   what the testbench sends, one 16-bit hex entry per line:
                              00NN  one byte, inside the open frame
                              A000  open an SPI frame: wait for READY, then CS falls
                              A001  close the frame: CS rises
                              3000  wait for the start of the next video frame (beam at 0, 0)
                              2YYY  wait for the start of line YYY (hex) of the video frame in progress
                              4000  arm the frame dumpers: the next frame start begins frame f0
                              1NNN  fence: read frames until NNN (hex) more reply bytes have arrived
                              FFFF  end of the stream
    sim/layers_replies.hex  the 9 bytes of the GET_INFO reply the testbench must read

The test is a story told to the GPU through the link, like a host program would:
set up, show frame f0, then change the scroll and the layer enables one video
frame at a time. Each change is sent in the middle of the frame before the one
it is meant for (line 100), so a design that applies a scroll value at once
instead of at vertical blanking tears the frame on screen and fails. Frame f8
also edits the tile graphics and one map row during blanking, after the latch.

The frames are what host/gpu_model.py says: Gpu.screen() after Gpu.vblank().
All graphics come from the functions below, so the Verilog test and this
reference cannot disagree about them.
"""
import os
import struct
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model, raster_model

import numpy as np  # noqa: E402

import gpu_model as g  # noqa: E402
from ppm import write_ppm  # noqa: E402

OPEN, CLOSE, FRAME_START, ARM, FENCE, END = 0xA000, 0xA001, 0x3000, 0x4000, 0x1000, 0xFFFF
LINE = 0x2000
MID_LINE, BLANK_LINE = 100, 500          # where the steps send their commands


# ---------------------------------------------------------------- the test data
class Lcg:
    """A small deterministic random source (the same numbers on every machine)."""

    def __init__(self, seed):
        self.s = seed

    def next(self):
        self.s = (self.s * 1103515245 + 12345) & 0x7FFFFFFF
        return self.s >> 8


def palette():
    """256 colors as 0xRRGGBB: entry 0 dark grey, the rest pseudo-random."""
    r = Lcg(77)
    return [0x202020] + [r.next() & 0xFFFFFF for _ in range(255)]


def tile_graphics():
    """The 16,384 bytes of tile graphics. Every pixel's index depends on the
    tile and on x and y separately, so a flip, a one-pixel shift or a wrong
    tile changes the color. Holes (index 0) let what is behind show through:
    tile 0 is empty, tiles with number % 4 == 3 are a checkerboard, % 8 == 5
    have a transparent left half, % 16 == 14 are a frame with a hole in the middle."""
    gfx = bytearray(g.TILE_BYTES)
    for t in range(256):
        for y in range(8):
            for x in range(8):
                c = 1 + (t * 37 + y * 11 + x * 5 + ((x * y) & 7) * 3) % 255
                if t == 0 or (t % 4 == 3 and (x + y) % 2 == 0) or (t % 8 == 5 and x < 4) \
                        or (t % 16 == 14 and 0 < x < 7 and 0 < y < 7):
                    c = 0
                gfx[t * 64 + y * 8 + x] = c
    return gfx


def tile_map(layer):
    """4,096 map entries. Layer 1 is half empty (tile 0) so layer 0 shows through it."""
    r = Lcg(1000 + layer)
    out = []
    for _ in range(g.MAP_ENTRIES):
        v = r.next()
        tile = (v >> 4) & 255
        if layer == 1 and v & 1:
            tile = 0
        out.append(g.map_entry(tile, bool(v & 0x1000), bool(v & 0x2000)))
    return out


def fb_commands():
    """The framebuffer picture the layers cover: stripes and diagonal lines."""
    cmds = [g.clear(1)]
    for k in range(20):
        cmds.append(g.fill_rect(k * 16, 0, k * 16 + 15, 239, 1 + (k * 29) % 255))
    for k in range(0, 240, 24):
        cmds.append(g.draw_line(0, k, 319, 239 - k, 250 - k // 24))
    cmds.append(g.fill_rect(150, 100, 169, 139, 0))      # index 0 in the framebuffer is opaque black-ish
    return cmds


def edit_row_entries():
    """Frame f8: layer 1, map row 5 gets new entries."""
    r = Lcg(5)
    return [g.map_entry((r.next() >> 3) & 255, False, bool(r.next() & 1)) for _ in range(64)]


def edit_tile_bytes():
    """Frame f8: tile 200 gets a new picture."""
    return bytes(1 + (i * 7 + 3) % 255 for i in range(64))


# What each step sends. (label, commands in the middle of the previous frame, commands in its blanking)
STEPS = [
    ("f1", [g.set_scroll(0, 7, 13)], []),
    ("f2", [g.set_scroll(1, 50, 50), g.set_scroll(1, 301, 0)], []),          # the first one is replaced before blanking
    ("f3", [g.set_scroll(0, -1, -1), g.set_scroll(1, 301, 299)], []),        # 511 and 511; x and y wrap on the screen
    ("f4", [g.set_scroll(0, 1, 1), g.set_scroll(0, 264, 264), g.set_scroll(1, 511, 511)], []),
    ("f5", [g.set_tile_layer(1), g.set_scroll(0, 100, 50), g.set_scroll(1, 5, 6)], []),
    ("f6", [g.set_tile_layer(2), g.set_scroll(0, 1, 1)], []),
    ("f7", [g.set_tile_layer(0)], []),
    ("f8", [g.set_tile_layer(3), g.set_scroll(0, 320, 240)],
     [g.write_tile_map(1, 5 * 64, edit_row_entries()[:g.MAP_CHUNK]), g.upload_tiles(200 * 64, edit_tile_bytes())]),
    ("f9", [g.set_scroll(0, 509 + 1024, 3 - 512), g.set_scroll(1, 2, 510 + 1024)], []),
]
LABELS = ["f0"] + [s[0] for s in STEPS]


def group(packets, limit=257):
    """Pack whole packets into SPI frames of at most 257 bytes."""
    frames, cur = [], b""
    for p in packets:
        if cur and len(cur) + len(p) > limit:
            frames.append(cur)
            cur = b""
        cur += p
    if cur:
        frames.append(cur)
    return frames


def setup_frames():
    pal = palette()
    gfx = tile_graphics()
    f = [[g.set_tile_layer(0), g.set_scroll(0, 0, 0), g.set_scroll(1, 0, 0)]]
    f += [[g.set_palette(i, pal[i]) for i in range(256)]]
    f += [fb_commands()]
    f += [[p] for p in g.tile_packets(gfx)]
    f += [[p] for p in g.map_packets(0, tile_map(0))]
    f += [[p] for p in g.map_packets(1, tile_map(1))]
    # packets that must change nothing outside their range, and set no error flag
    f += [[g.set_scroll(2, 77, 77), g.write_tile_map(2, 0, [0x0155, 0x0166, 0x0177, 0x0188]),
           g.write_tile_map(1, 4094, [g.map_entry(0x21), g.map_entry(0x22, True, True), 0x0399, 0x03AA]),
           g.upload_tiles(16380, bytes([0x31, 0x32, 0x33, 0x34, 0xEE, 0xEE, 0xEE, 0xEE]))]]
    f += [[g.set_scroll(0, 0, 0), g.set_scroll(1, 0, 0), g.set_tile_layer(3), g.flip()]]
    out = []
    for pk in f:
        out += group(pk)
    return out


# ---------------------------------------------------------------- stream and frames
def build():
    """Returns (stream words, the GET_INFO reply, {label: screen index bytes}, model)."""
    m = g.Gpu()
    items, shots, replies = [], {}, bytearray()

    def frame(data):
        items.extend([OPEN] + list(data) + [CLOSE])
        replies.extend(m.feed(data, sof=True))

    for data in setup_frames():
        frame(data)
    m.vblank()
    items += [FRAME_START, LINE | MID_LINE, ARM]
    shots["f0"] = m.screen()
    for label, mid, blank in STEPS:
        items += [FRAME_START, LINE | MID_LINE]
        for data in group(mid):
            frame(data)
        if blank:
            items.append(LINE | BLANK_LINE)
            for data in group(blank):
                frame(data)
        m.vblank()
        shots[label] = m.screen()
    items += [FRAME_START, LINE | MID_LINE]
    frame(g.get_info())
    return items, bytes(replies), shots, m


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--stream":
        items, info, shots, m = build()
        assert info == bytes([0x47, 1, 0x1F, 0x02, 0x40, 0x01, 0xF0, 0x00, 0x00]), "an error flag is set"
        items += [FENCE | 9, END]
        with open(os.path.join(args[1], "layers_stream.hex"), "w") as f:
            f.write("".join("%04x\n" % w for w in items))
        with open(os.path.join(args[1], "layers_replies.hex"), "w") as f:
            f.write("".join("%02x\n" % b for b in info))
        print("wrote %s/layers_stream.hex (%d entries) and layers_replies.hex" % (args[1], len(items)))
        return 0
    if len(args) == 1 and not args[0].startswith("-"):
        items, info, shots, m = build()
        pal = np.array([[(c >> 16) & 255, (c >> 8) & 255, c & 255] for c in m.pal], dtype=np.uint16)
        for label, idx in shots.items():
            small = pal[np.frombuffer(idx, dtype=np.uint8).reshape(g.H, g.W)]
            big = small.repeat(2, axis=0).repeat(2, axis=1)
            write_ppm(os.path.join(args[0], "ref_layers_%s.ppm" % label), big, 255)
        print("wrote %d reference frames to %s" % (len(shots), args[0]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
