#!/usr/bin/env python3
"""ref_streaming.py -- test stream and reference frames for streamed tile maps (Chapter 5.6).

Usage (from the repo root):
    .venv/bin/python sim/py/ref_streaming.py --stream sim   once: writes the stream the testbench plays
    .venv/bin/python sim/py/ref_streaming.py OUTDIR         writes OUTDIR/ref_streaming_f0.ppm ... fd.ppm

--stream writes (commit both files):
    sim/streaming_stream.hex   what the testbench sends, one 16-bit hex entry per line
                               (the entries of sim/layers_stream.hex, Chapter 5.3):
                                 00NN  one byte, inside the open frame
                                 A000  open an SPI frame: wait for READY, then CS falls
                                 A001  close the frame: CS rises
                                 3000  wait for the start of the next video frame (beam at 0, 0)
                                 2YYY  wait for the start of line YYY (hex) of the video frame in progress
                                 4000  arm the frame dumpers: the next frame start begins frame f0
                                 1NNN  fence: read frames until NNN (hex) more reply bytes have arrived
                                 FFFF  end of the stream
    sim/streaming_replies.hex  18 bytes: the two GET_INFO replies the testbench must read

The test level is 256 tiles wide (2,048 pixels) and 96 tall for layer 0, and 96 x 64 for layer 1.
Scrolling through it at a game's 2 pixels per frame would take 864 frames, far too many to
simulate, so the test takes big steps: 13 steps of about 157 pixels (20 tiles) in X, close to
the 23 tiles a 64-entry ring allows per frame, and up to 128 in Y; layer 1 moves a quarter as
fast in X and wraps in Y. Layer 0's ring of columns is rewritten three times over and its ring
of rows half; every step streams up to 20 columns and some rows, and all 14 frames are
compared. Frames fc and fd scroll back to the left and up. Layer 0 is loaded column by column
and layer 1 row by row, so both commands are used on both layers.

Frame labels are f0 to f9, then fa to fd (a hex digit, so file names stay one character).
Each step's writes and its two SET_SCROLLs are sent in the middle of the frame before it
(line 100). The StreamedLayer planner (host/level_stream.py) decides which columns and rows
to write and asserts that none of them overwrites a tile of the frame on screen.

The frames are what host/gpu_model.py says (Gpu.screen() after Gpu.vblank()), and the script also
checks them against a second computation straight from the level function with no map or ring
in between, so the host planner and the model cannot agree on a wrong picture.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model, raster_model, level_stream

import numpy as np  # noqa: E402

import gpu_model as g  # noqa: E402
import ref_layers as rl  # noqa: E402
from level_stream import StreamedLayer, total_bytes  # noqa: E402
from ppm import write_ppm  # noqa: E402

OPEN, CLOSE, FRAME_START, ARM, FENCE, END = rl.OPEN, rl.CLOSE, rl.FRAME_START, rl.ARM, rl.FENCE, rl.END
LINE = rl.LINE
MID_LINE = int(os.environ.get("STREAM_MID_LINE", "100"))   # a legal variant: send the steps later (up to about line 200: the largest takes 270 lines of SPI)
N_TILES = 64                                               # tiles in the test (the first 64 of ref_layers)
W0, H0 = 256, 96                                           # layer 0 level, in tiles
W1, H1 = 96, 64                                            # layer 1 level


# ---------------------------------------------------------------- the levels
def _hash(c, r, seed):
    x = (c * 73856093) ^ (r * 19349663) ^ (seed * 83492791)
    x = (x * 2654435761) & 0xFFFFFFFF
    x ^= x >> 15
    return (x * 2246822519) & 0xFFFFFFFF


def level0(c, r):
    """Layer 0: every cell of the 256 x 96 level different. About one in seven is empty."""
    if not (0 <= c < W0 and 0 <= r < H0):
        return 0
    v = _hash(c, r, 1) >> 4
    tile = 0 if v % 7 == 0 else 1 + v % 63
    return g.map_entry(tile, bool(v & 0x100), bool(v & 0x200))


def level1(c, r):
    """Layer 1: 96 x 64, about 60% empty, so layer 0 shows through."""
    if not (0 <= c < W1 and 0 <= r < H1):
        return 0
    v = _hash(c, r, 2) >> 4
    tile = 0 if v % 5 < 3 else 1 + (v >> 3) % 63
    return g.map_entry(tile, bool(v & 0x100), bool(v & 0x200))


# the path: (layer 0 x, y) per frame; layer 1 follows at x / 4 and 3 * y / 2 (it wraps in Y: 64 rows)
PATH = [(0, 0), (157, 0), (314, 40), (471, 85), (628, 130), (785, 175), (942, 220), (1099, 265),
        (1256, 310), (1413, 355), (1570, 400), (1728, 528), (1536, 400), (1380, 240)]
LABELS = ["f%x" % i for i in range(len(PATH))]


def scroll1(sx, sy):
    return sx // 4, sy * 3 // 2


def tile_set():
    """The first 64 tiles of Chapter 5.3's tile graphics."""
    return rl.tile_graphics()[:N_TILES * 64]


def junk(k=0):
    return [g.map_entry(0x21 + (i + k) % 31, bool(i & 1), bool(i & 2)) for i in range(64)]


def setup_frames(layers):
    pal = rl.palette()
    f = [[g.set_tile_layer(0), g.set_scroll(0, 0, 0), g.set_scroll(1, 0, 0)]]
    f += [[g.set_palette(i, pal[i]) for i in range(256)]]
    f += [rl.fb_commands()]
    f += [[p] for p in g.tile_packets(tile_set())]
    s0, s1 = layers
    f += [[p] for p in s0.load(0, 0)]                # layer 0: 64 columns
    s1.load(*scroll1(0, 0))                          # layer 1: the same ring, written as 64 rows
    f += [[s1.row(r)] for r in range(64)]
    # packets that must change nothing: a layer or an index that does not exist (no error flag)
    f += [[g.write_tile_column(2, 0, junk(0)), g.write_tile_column(3, 0, junk(1))],
          [g.write_tile_column(0, 64, junk(2))],
          [g.write_tile_column(1, 200, junk(3))],
          [g.write_tile_row(2, 0, junk(4)), g.write_tile_row(3, 1, junk(5))],
          [g.write_tile_row(0, 64, junk(6))],
          [g.write_tile_row(1, 130, junk(7))]]
    # wrong lengths: skipped whole, BAD_LEN; and a reserved opcode, BAD_OP
    f += [[g.packet(g.WRITE_TILE_COLUMN, bytes([0, 1]) + b"".join(e.to_bytes(2, "little") for e in junk(8)[:63]))],   # 128 bytes: 129 short of 130
          [g.packet(g.WRITE_TILE_ROW, bytes([0, 2]) + b"".join(e.to_bytes(2, "little") for e in junk(9)) + b"\x55")],   # 131
          [g.packet(g.WRITE_TILE_COLUMN, bytes([0, 3]) + b"".join(e.to_bytes(2, "little") for e in junk(10)[:63]) + b"\x55")],   # 129
          [g.packet(g.WRITE_TILE_ROW), g.packet(g.WRITE_TILE_COLUMN, bytes([0]))],
          [g.packet(0x42, bytes(130))]]
    return f


# ---------------------------------------------------------------- the picture straight from the levels
def truth(m, sx, sy):
    """One frame of palette indices computed from the level functions alone: no map, no ring, no model."""
    ys, xs = np.mgrid[0:g.H, 0:g.W]
    fb = np.frombuffer(bytes(m.front_fb), dtype=np.uint8).reshape(g.H, g.W)
    gfx = np.frombuffer(tile_set(), dtype=np.uint8).reshape(N_TILES, 8, 8)
    out = fb.copy()
    for level, (px, py), wrap in ((level0, (sx, sy), False), (level1, scroll1(sx, sy), True)):
        wx, wy = px + xs, py + ys
        lay = np.zeros((g.H, g.W), dtype=np.uint8)
        for ty in range(((py + g.H - 1) >> 3) - (py >> 3) + 1):
            for tx in range(((px + g.W - 1) >> 3) - (px >> 3) + 1):
                tc, tr = (px >> 3) + tx, (py >> 3) + ty
                e = level(tc, tr % 64 if wrap else tr)
                t = e & 255
                if t == 0:
                    continue
                for yy in range(8):
                    for xx in range(8):
                        sxp, syp = tc * 8 + xx - px, tr * 8 + yy - py
                        if 0 <= sxp < g.W and 0 <= syp < g.H:
                            gx = 7 - xx if e & 0x100 else xx
                            gy = 7 - yy if e & 0x200 else yy
                            lay[syp, sxp] = gfx[t, gy, gx]
        out = np.where(lay != 0, lay, out)
    return out.astype(np.uint8).tobytes()


# ---------------------------------------------------------------- stream and frames
def build(check=True):
    """Returns (stream words, the replies, {label: screen index bytes}, model, byte counts per step)."""
    m = g.Gpu()
    s0 = StreamedLayer(0, level0, W0, H0)
    s1 = StreamedLayer(1, level1, W1, H1, wrap_y=True)
    items, shots, replies, sizes = [], {}, bytearray(), []

    def frame(data):
        items.extend([OPEN] + list(data) + [CLOSE])
        replies.extend(m.feed(data, sof=True))

    for data in [d for pk in setup_frames([s0, s1]) for d in rl.group(pk)]:
        frame(data)
    frame(g.get_info())                      # BAD_LEN | BAD_OP
    items.append(FENCE | 9)
    frame(g.clear_errors(0x0F))
    frame(b"".join([g.set_scroll(0, 0, 0), g.set_scroll(1, 0, 0), g.set_tile_layer(3), g.flip()]))
    m.vblank()
    items += [FRAME_START, LINE | MID_LINE, ARM]
    shots[LABELS[0]] = m.screen()
    for label, (sx, sy) in zip(LABELS[1:], PATH[1:]):
        x1, y1 = scroll1(sx, sy)
        pk = s0.plan(sx, sy) + s1.plan(x1, y1)
        sizes.append(total_bytes(pk) + 14)
        items += [FRAME_START, LINE | MID_LINE]
        for data in rl.group(pk + [g.set_scroll(0, sx, sy), g.set_scroll(1, x1, y1)]):
            frame(data)
        m.vblank()
        shots[label] = m.screen()
    items += [FRAME_START, LINE | MID_LINE]
    frame(g.get_info())
    if check:
        for label, (sx, sy) in zip(LABELS, PATH):
            assert shots[label] == truth(m, sx, sy), "model and level disagree in " + label
    return items, bytes(replies), shots, m, sizes


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--stream":
        items, info, shots, m, sizes = build()
        assert info == bytes([0x47, 1, 0xFF, 0x02, 0x40, 0x01, 0xF0, 0x00, 0x06,
                              0x47, 1, 0xFF, 0x02, 0x40, 0x01, 0xF0, 0x00, 0x00]), "unexpected error flags: " + info.hex()
        items += [FENCE | 9, END]
        with open(os.path.join(args[1], "streaming_stream.hex"), "w") as f:
            f.write("".join("%04x\n" % w for w in items))
        with open(os.path.join(args[1], "streaming_replies.hex"), "w") as f:
            f.write("".join("%02x\n" % b for b in info))
        print("wrote %s/streaming_stream.hex (%d entries) and streaming_replies.hex; bytes per step %s" %
              (args[1], len(items), sizes))
        return 0
    if len(args) == 1 and not args[0].startswith("-"):
        items, info, shots, m, sizes = build()
        pal = np.array([[(c >> 16) & 255, (c >> 8) & 255, c & 255] for c in m.pal], dtype=np.uint16)
        for label, idx in shots.items():
            small = pal[np.frombuffer(idx, dtype=np.uint8).reshape(g.H, g.W)]
            big = small.repeat(2, axis=0).repeat(2, axis=1)
            write_ppm(os.path.join(args[0], "ref_streaming_%s.ppm" % label), big, 255)
        print("wrote %d reference frames to %s" % (len(shots), args[0]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
