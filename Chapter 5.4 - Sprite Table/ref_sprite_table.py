#!/usr/bin/env python3
"""ref_sprite_table.py -- test stream and reference frames for the sprite table (Chapter 5.4).

Usage (from the repo root):
    .venv/bin/python sim/py/ref_sprite_table.py --stream sim   once: writes the stream the testbench plays
    .venv/bin/python sim/py/ref_sprite_table.py OUTDIR         writes OUTDIR/ref_sprite_table_f0.ppm ... f7.ppm

--stream writes (commit both files):
    sim/sprite_table_stream.hex    what the testbench sends, one 16-bit hex entry per line
                                   (the same entries as sim/layers_stream.hex, Chapter 5.3):
                                     00NN  one byte, inside the open frame
                                     A000  open an SPI frame: wait for READY, then CS falls
                                     A001  close the frame: CS rises
                                     3000  wait for the start of the next video frame (beam at 0, 0)
                                     2YYY  wait for the start of line YYY (hex) of the video frame in progress
                                     4000  arm the frame dumpers: the next frame start begins frame f0
                                     1NNN  fence: read frames until NNN (hex) more reply bytes have arrived
                                     FFFF  end of the stream
    sim/sprite_table_replies.hex   the 9 bytes of the GET_INFO reply the testbench must read

The story, told to the GPU the way a host program would. Setup: palette, a
framebuffer picture, two tile layers, five sprite images, scene A in the table.
Then one frame at a time. Commands for frame fK are sent in the middle of the frame
before it (line 100) and reach the table's shadow; the GPU latches the shadow at
vertical blanking. A step may also send commands in the blanking of that frame
(line 500), AFTER the latch: they must show one frame later than the middle ones.

    f0  scene A: sprites at every edge, partly off the screen, flips, palettes,
        transparency, overlaps, a disabled sprite, images that cannot be drawn
    f1  MOVE_SPRITE rewrites (also off the screen and back), one sprite written
        twice, SET_SPRITE (one with bits 7:4 of ASSET and PALETTE set, which are
        ignored); a MOVE sent in blanking that must NOT show yet
    f2  nothing new is sent: the blanking MOVE of f1 shows now
    f3  scene B: all 64 entries rewritten; 20 sprites on one line (one disabled,
        one off the screen, one with a missing asset), a staircase of sprites
        with more than 16 on many lines
    f4  SPRITE_CTRL 0xFE (bit 0 clear): no sprites (a SPRITE_CTRL 1 sent in blanking)
    f5  sprites again; tile layers scroll under them; 20 sprites moved
    f6  32 sprites moved by MOVE_SPRITE
    f7  SET_SPRITE / MOVE_SPRITE with an id of 64 or more change nothing,
        SPRITE_CTRL with other bits set keeps the sprites on

The frames are what host/gpu_model.py says: Gpu.screen() after Gpu.vblank().
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model, raster_model

import numpy as np  # noqa: E402

import gpu_model as g  # noqa: E402
import ref_layers as L  # noqa: E402  (palette, tile graphics, maps and the stream words of Chapter 5.3)
from ppm import write_ppm  # noqa: E402

OPEN, CLOSE, FRAME_START, ARM, FENCE, END = L.OPEN, L.CLOSE, L.FRAME_START, L.ARM, L.FENCE, L.END
LINE, MID_LINE, BLANK_LINE = L.LINE, L.MID_LINE, L.BLANK_LINE
ON, FX, FY = g.SPRITE_ON, g.SPRITE_FLIP_X, g.SPRITE_FLIP_Y


# ---------------------------------------------------------------- sprite images
def sprite_image(k):
    """256 bytes, row by row. Each pixel depends on x and y separately, so a flip,
    a shift or a wrong image changes colors. Holes (index 0) are transparent.
      0: scattered holes   1: a frame, hole in the middle   2: solid, one corner cut
      3: high indices (232-255): with a palette offset they wrap past 255, some to 0
    """
    out = bytearray(256)
    for y in range(16):
        for x in range(16):
            v = 1 + (y * 17 + x * 5 + k * 53) % 200
            if k == 0 and (x + 2 * y) % 6 == 0:
                v = 0
            if k == 1 and 3 <= x <= 12 and 3 <= y <= 12:
                v = 0
            if k == 2 and x < 2 and y < 2:
                v = 0
            if k == 3:
                v = 232 + (x + y) % 24
                if (x * y) % 11 == 0:
                    v = 0
            out[y * 16 + x] = v
    return bytes(out)


EIGHT = bytes(1 + (i * 3) % 200 for i in range(64))      # asset 4: 8 x 8, cannot be a sprite


def asset_packets():
    pk = []
    for k in range(4):
        pk += g.upload_packets(k, 16, 16, sprite_image(k))
    pk += g.upload_packets(4, 8, 8, EIGHT)               # asset 5 is never uploaded
    return pk


# ---------------------------------------------------------------- scenes
def scene_a():
    """(x, y, asset, palette, flags) for table entries 0...; entries not listed stay all-zero (disabled)."""
    return [
        (0, 0, 0, 0, ON), (-8, -8, 1, 0, ON), (312, -5, 2, 0, ON), (-3, 233, 0, 0, ON), (310, 230, 2, 0, ON),
        (-16, 100, 0, 0, ON), (320, 100, 0, 0, ON), (150, -16, 0, 0, ON), (150, 239, 1, 0, ON),
        (100, 50, 0, 0, ON | FX), (120, 50, 0, 0, ON | FY), (140, 50, 0, 0, ON | FX | FY), (160, 50, 0, 0, ON),
        (200, 60, 1, 0, ON), (208, 64, 2, 0, ON), (204, 70, 0, 0, ON), (206, 66, 3, 1, ON),
        (50, 120, 3, 1, ON), (70, 120, 3, 3, ON), (90, 120, 3, 15, ON),
        (110, 120, 4, 0, ON), (130, 120, 5, 0, ON), (150, 120, 0, 0, 0),
        (319, 60, 0, 0, ON), (-15, 60, 0, 0, ON), (0, 224, 1, 0, ON), (304, 0, 1, 2, ON),
        (160, 150, 1, 2, ON | FX | FY),
    ]


def scene_b():
    """All 64 entries. G1: entries 0-19 in one row at y = 40 (entry 7 disabled, entry 3 off the left
    edge, entry 11 names the missing asset 5); G2: 20-36, 17 overlapping sprites at y = 100; G3: 37-56, a staircase from y = 150 down;
    G4: 57-63 at y = 160, among the staircase."""
    t = []
    for i in range(20):
        x = -100 if i == 3 else i * 16
        t.append((x, 40, 5 if i == 11 else i % 4, i % 3, 0 if i == 7 else ON))
    for i in range(20, 37):
        t.append((20 + (i - 20) * 10, 100, (i * 2) % 3, 0, ON))
    for i in range(37, 57):
        j = i - 37
        t.append((10 + j * 14, 150 + j, (i % 4), i % 4, ON | (FX if i % 2 else 0) | (FY if i % 3 == 0 else 0)))
    for i in range(57, 64):
        t.append((20 + (i - 57) * 40, 160, 2 if i % 2 else 0, 0, ON))
    assert len(t) == 64
    return t


def moves_f1():
    """(sprite, x, y) in the order sent in the middle of f0's frame."""
    return [(0, 300, 200), (1, -1, -1), (5, 100, 150), (7, 150, -15), (9, 10, 10), (9, 100, 52), (11, 140, 52),
            (13, 208, 62), (3, -3, 232), (4, 312, 232), (8, 150, 238)]


def moves_f5():
    """The staircase (entries 37-56) one pixel to the right."""
    tab = scene_b()
    return [(i, tab[i][0] + 1, tab[i][1]) for i in range(37, 57)]


def moves_f6():
    """32 sprites to pseudo-random places, partly off screen."""
    r = L.Lcg(54)
    return [(i, (r.next() % 360) - 24, (r.next() % 280) - 24) for i in range(32)]


def moves(pairs):
    return [g.move_sprite(i, x, y) for i, x, y in pairs]


# What each step sends: (label, commands in the middle of the previous frame, commands in its blanking).
STEPS = [
    ("f1", moves(moves_f1()) + [g.set_sprite(22, 150, 120, 0x30, 0x40, ON), g.set_sprite(20, 110, 120, 3, 2, ON)],
     [g.move_sprite(0, 20, 20)]),
    ("f2", [], []),
    ("f3", [g.set_sprite(i, *e) for i, e in enumerate(scene_b())], []),
    ("f4", [g.packet(g.SPRITE_CTRL, bytes([0xFE]))], [g.sprite_ctrl(True)]),   # bit 0 clear, bits 7:1 set: off
    ("f5", [g.set_scroll(0, 33, 17), g.set_scroll(1, 100, 9)] + moves(moves_f5()), []),
    ("f6", moves(moves_f6()), []),
    ("f7", [g.set_sprite(64, 5, 5, 0), g.move_sprite(64, 0, 0), g.set_sprite(255, 9, 9, 1), g.move_sprite(200, 7, 7),
            g.packet(g.SPRITE_CTRL, bytes([0xFF])), g.move_sprite(63, 280, 100)], []),
]
LABELS = ["f0"] + [s[0] for s in STEPS]


def sparse_map(layer):
    """Chapter 5.3's map with most tiles removed (entry 0 = tile 0, empty), so the picture stays readable."""
    ents = L.tile_map(layer)
    return [e if (i // 64 * 7 + i % 64 * 3 + layer) % 5 == 0 else 0 for i, e in enumerate(ents)]


def setup_frames():
    pal = L.palette()
    gfx = L.tile_graphics()
    f = [[g.set_tile_layer(0), g.set_scroll(0, 0, 0), g.set_scroll(1, 0, 0), g.sprite_ctrl(False)]]
    f += [[g.set_palette(i, pal[i]) for i in range(256)]]
    f += [L.fb_commands()]
    f += [[p] for p in g.tile_packets(gfx)]
    f += [[p] for p in g.map_packets(0, sparse_map(0))]
    f += [[p] for p in g.map_packets(1, sparse_map(1))]
    f += [[p] for p in asset_packets()]
    f += [[g.set_sprite(i, *e) for i, e in enumerate(scene_a())] + [g.sprite_ctrl(True)]]
    f += [[g.set_scroll(0, 11, 5), g.set_scroll(1, 3, 40), g.set_tile_layer(3), g.flip()]]
    out = []
    for pk in f:
        out += L.group(pk)
    return out


# ---------------------------------------------------------------- stream and frames
def raw_hits(m, y):
    """How many enabled entries of the live table cover line y (before the limit of 16)."""
    n = 0
    for i in range(g.N_SPRITES):
        ey = int.from_bytes(m.live_sprites[i * 8 + 2:i * 8 + 4], "little", signed=True)
        if m.live_sprites[i * 8 + 6] & ON and ey <= y < ey + 16:
            n += 1
    return n


def build():
    """Returns (stream words, the GET_INFO reply, {label: screen index bytes}, model)."""
    m = g.Gpu()
    items, shots, replies, crowd = [], {}, bytearray(), {}
    build.crowd = crowd

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
        for data in L.group(mid):
            frame(data)
        if blank:
            items.append(LINE | BLANK_LINE)
        m.vblank()                  # the latch at line 480 sits between the two groups of commands
        shots[label] = m.screen()
        crowd[label] = (max(len(m.sprite_lines(y)) for y in range(g.H)),
                        sum(1 for y in range(g.H) if len(m.sprite_lines(y)) == 16 and raw_hits(m, y) > 16))
        for data in L.group(blank):
            frame(data)
    items += [FRAME_START, LINE | MID_LINE]
    frame(g.get_info())
    return items, bytes(replies), shots, m


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--stream":
        items, info, shots, m = build()
        assert info == bytes([0x47, 1, 0x3F, 0x02, 0x40, 0x01, 0xF0, 0x00, 0x00]), "an error flag is set"
        # the story must exercise what it claims to
        assert build.crowd["f3"][0] == 16 and build.crowd["f3"][1] >= 10, build.crowd   # lines that lose sprites
        items += [FENCE | 9, END]
        with open(os.path.join(args[1], "sprite_table_stream.hex"), "w") as f:
            f.write("".join("%04x\n" % w for w in items))
        with open(os.path.join(args[1], "sprite_table_replies.hex"), "w") as f:
            f.write("".join("%02x\n" % b for b in info))
        print("wrote %s/sprite_table_stream.hex (%d entries) and sprite_table_replies.hex" % (args[1], len(items)))
        return 0
    if len(args) == 1 and not args[0].startswith("-"):
        items, info, shots, m = build()
        pal = np.array([[(c >> 16) & 255, (c >> 8) & 255, c & 255] for c in m.pal], dtype=np.uint16)
        for label, idx in shots.items():
            small = pal[np.frombuffer(idx, dtype=np.uint8).reshape(g.H, g.W)]
            big = small.repeat(2, axis=0).repeat(2, axis=1)
            write_ppm(os.path.join(args[0], "ref_sprite_table_%s.ppm" % label), big, 255)
        print("wrote %d reference frames to %s" % (len(shots), args[0]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
