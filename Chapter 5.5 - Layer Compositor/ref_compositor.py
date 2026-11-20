#!/usr/bin/env python3
"""ref_compositor.py -- test stream and reference frames for the layer compositor (Chapter 5.5).

Usage (from the repo root):
    .venv/bin/python sim/py/ref_compositor.py --stream sim   once: writes the stream the testbench plays
    .venv/bin/python sim/py/ref_compositor.py OUTDIR         writes OUTDIR/ref_compositor_f0.ppm ... f7.ppm

--stream writes (commit both files):
    sim/compositor_stream.hex    what the testbench sends, one 16-bit hex entry per line
                                 (the same entries as sim/layers_stream.hex, Chapter 5.3):
                                   00NN  one byte, inside the open frame
                                   A000  open an SPI frame: wait for READY, then CS falls
                                   A001  close the frame: CS rises
                                   3000  wait for the start of the next video frame (beam at 0, 0)
                                   2YYY  wait for the start of line YYY (hex) of the video frame in progress
                                   4000  arm the frame dumpers: the next frame start begins frame f0
                                   1NNN  fence: read frames until NNN (hex) more reply bytes have arrived
                                   FFFF  end of the stream
    sim/compositor_replies.hex   the 9 bytes of the GET_INFO reply the testbench must read

The story, told to the GPU the way a host program would. Setup: palette, a framebuffer
picture with holes (index 0), two tile layers whose tiles have item priorities (map entry bits
11:10: layer 1 by bands of 4 columns, layer 0 by bands of 4 rows), five sprite images and 42
sprites whose rows have item priorities (flags bits 4:3). Then one frame at a time. Commands for
frame fK are sent in the middle of the frame before it (line 100) and reach the shadow registers;
the GPU latches them at vertical blanking. A step may also send commands in the blanking of that
frame (line 500), AFTER the latch: the shadowed ones (priority, backdrop, enables, sprites) must
show one frame later than the middle ones.

    f0  power-up settings: the order of Chapters 5.3 and 5.4 (framebuffer, layer 0, layer 1,
        sprites). The item priorities are all there but the four layer priorities differ, so
        they must change nothing
    f1  SET_PRIORITY fb 3, l0 2, l1 1, sp 0: the framebuffer in front (its index-0 holes
        show what is behind), sprites at the back; items still ignored
    f2  SET_PRIORITY fb 2, l0 2, l1 0, sp 2: three sources share a layer priority, so the item
        priorities and then the "later source wins" rule decide; SET_BACKDROP 77 shows through
        where every source is transparent
    f3  default priorities again, FB_ENABLE 0: backdrop everywhere nothing else is opaque.
        A FB_ENABLE 1 sent in blanking must NOT show yet
    f4  the framebuffer is back; SET_PRIORITY fb 0, l0 1, l1 2, sp 2: sprites and layer 1 tie,
        so a tile hides a sprite whose item priority is lower
    f5  both layers scroll and all 42 sprites are rewritten with other item priorities
    f6  SET_PRIORITY 2, 2, 2, 2: everything ties, the items rule; a SET_BACKDROP 200 sent in
        blanking must NOT show yet
    f7  the backdrop 200 shows; FB_ENABLE with bits 7:1 set and bit 0 clear hides the framebuffer;
        SET_PRIORITY fb 3, l0 3, l1 1, sp 2 with SET_TILE_LAYER 2: the two sources with the top
        layer priority are both disabled, so they must take no part (sprites over layer 1)

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
import ref_layers as L  # noqa: E402  (palette, tile graphics and the stream words of Chapter 5.3)
import ref_sprite_table as T  # noqa: E402  (sprite images of Chapter 5.4)
from ppm import write_ppm  # noqa: E402

OPEN, CLOSE, FRAME_START, ARM, FENCE, END = L.OPEN, L.CLOSE, L.FRAME_START, L.ARM, L.FENCE, L.END
LINE, MID_LINE, BLANK_LINE = L.LINE, L.MID_LINE, L.BLANK_LINE
ON, FX, FY = g.SPRITE_ON, g.SPRITE_FLIP_X, g.SPRITE_FLIP_Y


# ---------------------------------------------------------------- the picture
def fb_commands():
    """Stripes and diagonals (Chapter 5.3), then windows of index 0: a hole in the middle, a
    horizontal band and a vertical band. Index 0 in the framebuffer is transparent."""
    cmds = L.fb_commands()                                   # includes the hole at (150,100)-(169,139)
    cmds += [g.fill_rect(0, 64, 319, 79, 0), g.fill_rect(228, 0, 243, 239, 0), g.draw_line(0, 200, 319, 150, 0)]
    return cmds


def comp_map(layer):
    """Half of Chapter 5.3's tiles (the rest are tile 0, empty). Item priority of a tile (map entry bits
    11:10): layer 1 (column // 4) % 4, layer 0 (row // 4) % 4."""
    ents = L.tile_map(layer)
    out = []
    for i, e in enumerate(ents):
        row, col = divmod(i, 64)
        keep = (row * 5 + col * 3 + layer) % 2 == 0
        pri = (col // 4) % 4 if layer == 1 else (row // 4) % 4
        out.append(e | pri << g.TILE_PRI_SHIFT if keep else 0)
    return out


def grid(item):
    """The 32 grid sprites, 8 per row, 4 rows; sprite 8r + i, item priority item(r)."""
    t = []
    for r in range(4):
        for i in range(8):
            fl = ON | (FX if i % 2 else 0) | (FY if (i // 2) % 2 else 0) | g.sprite_pri(item(r))
            t.append((8 + 38 * i, 12 + 56 * r + (i % 3) * 3, (i + r) % 4, (i + 3 * r) % 8, fl))
    return t


def extras(swap):
    """Entries 32 to 41. 32: image 3 with palette 1 inside the framebuffer hole (some pixels become
    index 0 and must still be opaque). 40 and 41 overlap; the lower index is in front whatever the
    item priorities (0 and 3, swapped when swap is set)."""
    a, b = (3, 0) if swap else (0, 3)
    return [(148, 108, 3, 1, ON | g.sprite_pri(1)),
            (60, 220, 1, 0, ON), (260, 40, 2, 4, ON | g.sprite_pri(2)), (100, 100, 0, 2, ON | FX), (220, 120, 2, 1, ON | g.sprite_pri(3)),
            (140, 20, 3, 5, ON), (30, 150, 0, 6, ON | g.sprite_pri(1)), (290, 200, 1, 7, ON | g.sprite_pri(2)),
            (150, 200, 1, 0, ON | g.sprite_pri(a)),
            (158, 204, 2, 3, ON | g.sprite_pri(b))]


def sprite_table(swap):
    """swap False: sprite items 0-3 by grid row; swap True: 3-0."""
    return grid((lambda r: 3 - r) if swap else (lambda r: r)) + extras(swap)


def asset_packets():
    return T.asset_packets()


def setup_frames():
    pal = L.palette()
    gfx = L.tile_graphics()
    f = [[g.set_tile_layer(0), g.set_scroll(0, 0, 0), g.set_scroll(1, 0, 0), g.sprite_ctrl(False)]]
    f += [[g.set_palette(i, pal[i]) for i in range(256)]]
    f += [fb_commands()]
    f += [[p] for p in g.tile_packets(gfx)]
    f += [[p] for p in g.map_packets(0, comp_map(0))]
    f += [[p] for p in g.map_packets(1, comp_map(1))]
    f += [[p] for p in asset_packets()]
    tab = sprite_table(False)
    f += [[g.set_sprite(i, *e) for i, e in enumerate(tab[a:a + 14], a)] for a in range(0, len(tab), 14)]
    f += [[g.sprite_ctrl(True), g.set_scroll(0, 11, 5), g.set_scroll(1, 3, 40), g.set_tile_layer(3), g.flip()]]
    out = []
    for pk in f:
        if pk:
            out += L.group(pk)
    return out


# What each step sends: (label, commands in the middle of the previous frame, commands in its blanking).
def swapped_sprites():
    return [g.set_sprite(i, *e) for i, e in enumerate(sprite_table(True))]


STEPS = [
    ("f1", [g.set_priority(fb=3, l0=2, l1=1, sp=0)], []),
    ("f2", [g.set_priority(fb=2, l0=2, l1=0, sp=2), g.set_backdrop(77)], []),
    ("f3", [g.set_priority(), g.fb_enable(False)], [g.fb_enable(True)]),
    ("f4", [g.set_priority(fb=0, l0=1, l1=2, sp=2)], []),
    ("f5", [g.set_scroll(0, 200, 5), g.set_scroll(1, 180, 40)] + swapped_sprites(), []),
    ("f6", [g.set_priority(fb=2, l0=2, l1=2, sp=2)], [g.set_backdrop(200)]),
    ("f7", [g.packet(g.FB_ENABLE, bytes([0xFE])), g.set_priority(fb=3, l0=3, l1=1, sp=2), g.set_tile_layer(2)], []),
]
LABELS = ["f0"] + [s[0] for s in STEPS]


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
        for data in L.group(mid):
            frame(data)
        if blank:
            items.append(LINE | BLANK_LINE)
        m.vblank()                  # the latch at line 480 sits between the two groups of commands
        shots[label] = m.screen()
        for data in L.group(blank):
            frame(data)
    items += [FRAME_START, LINE | MID_LINE]
    frame(g.get_info())
    return items, bytes(replies), shots, m


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--stream":
        items, info, shots, m = build()
        assert info == bytes([0x47, 1, 0x7F, 0x02, 0x40, 0x01, 0xF0, 0x00, 0x00]), "an error flag is set"
        for y in range(g.H):                                    # no line may lose a sprite to the 16-per-line limit
            assert len(m.sprite_lines(y)) < 16
        assert len(set(shots.values())) == len(shots), "two frames are identical: a step tests nothing"
        items += [FENCE | 9, END]
        with open(os.path.join(args[1], "compositor_stream.hex"), "w") as f:
            f.write("".join("%04x\n" % w for w in items))
        with open(os.path.join(args[1], "compositor_replies.hex"), "w") as f:
            f.write("".join("%02x\n" % b for b in info))
        print("wrote %s/compositor_stream.hex (%d entries) and compositor_replies.hex" % (args[1], len(items)))
        return 0
    if len(args) == 1 and not args[0].startswith("-"):
        items, info, shots, m = build()
        pal = np.array([[(c >> 16) & 255, (c >> 8) & 255, c & 255] for c in m.pal], dtype=np.uint16)
        for label, idx in shots.items():
            small = pal[np.frombuffer(idx, dtype=np.uint8).reshape(g.H, g.W)]
            big = small.repeat(2, axis=0).repeat(2, axis=1)
            write_ppm(os.path.join(args[0], "ref_compositor_%s.ppm" % label), big, 255)
        print("wrote %d reference frames to %s" % (len(shots), args[0]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
