#!/usr/bin/env python3
"""ref_depth.py -- test streams and reference frames for the depth buffer (Chapter 6.4).

Usage (from the repo root):
    .venv/bin/python sim/py/ref_depth.py --stream sim   once: the stream for tb_frame_depth
    .venv/bin/python sim/py/ref_depth.py --stats sim    once: the stream and the expected values for tb_depth_stats
    .venv/bin/python sim/py/ref_depth.py OUTDIR         writes OUTDIR/ref_depth_f0.ppm ... f5.ppm

--stream writes (commit both files):
    sim/depth_stream.hex    what tb_frame_depth sends; the entries are those of sim/shaded_stream.hex (Chapter 6.3)
    sim/depth_replies.hex   18 bytes: the two GET_INFO replies the testbench must read
--stats writes (commit both files):
    sim/depth_stats_stream.hex   bytes for tb_depth_stats (00NN send, 1000 check a GET_STATS reply, 1001 the same as
                                 the previous reply, FFFF end)
    sim/depth_stats_expect.hex   per check four 32-bit words: pixels, written, lowest and highest allowed clocks

The story (frame fN shows the picture drawn while frame f(N-1) was on the screen; every picture starts with a clear):
    f0  two intersecting shaded triangles over a background, depth test and write on, drawn A then B: the visible
        part of each is decided pixel by pixel, the intersection line is where the depths cross
    f1  the same two triangles drawn B then A: the same picture, except at pixels where the two depths are exactly
        equal (the first one drawn stays); this script checks that and lists the pixels
    f2  four panels, each with three triangles X, Y, Z drawn with different SET_DEPTH modes: both on; X tests only
        (writes no depth); X writes only (no test); X neither but Y writes only (no test): what each mode leaves behind
    f3  clearing: CLEAR_ALL with a colour and depth 45000, a triangle at 46000 that it refuses, a near triangle,
        CLEAR_DEPTH, a far triangle that is only visible
        because the depth was cleared, CLEAR_DEPTH to 30000, a triangle at 20000 that passes and one at 40000 that fails
    f4  back-to-back triangles on the same pixels: a stack of 16 identical-footprint triangles with scrambled depths
        (several equal), a stack of tiny three-pixel triangles, DRAW_TRI (flat) ignoring depth, a shaded triangle over virgin depth
    f5  depth persists: the host sends four bad packets (BAD_LEN x3, BAD_OP) that must change nothing, then runs a list
        (CLEAR, two triangles at z = 60000, GET_STATS which a list may not hold, FLIP): CLEAR leaves the depth alone, the
        triangles are refused where f4 left a depth <= 60000, equality included
The first GET_INFO (flags 00) says capabilities 0FFF; the second (flags 06 = BAD_LEN + BAD_OP) comes after the bad packets.
"""
import os
import struct
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model, raster_model

import numpy as np  # noqa: E402

import gpu_model as g  # noqa: E402
import raster_model as rm  # noqa: E402
from ppm import write_ppm  # noqa: E402

OPEN, CLOSE, FRAME_START, ARM, FENCE, END, LINE = 0xA000, 0xA001, 0x3000, 0x4000, 0x1000, 0xFFFF, 0x2000
LABELS = ["f0", "f1", "f2", "f3", "f4", "f5"]
PATCH_LINE = 20
W, H = g.W, g.H

RAMPS = {0: ((24, 24, 48), (255, 255, 255)), 1: ((40, 0, 0), (255, 90, 90)), 2: ((0, 40, 0), (120, 255, 120)),
         3: ((0, 0, 48), (110, 140, 255)), 4: ((48, 40, 0), (255, 240, 60)), 5: ((0, 40, 40), (120, 255, 255)),
         6: ((40, 0, 40), (255, 110, 255)), 7: ((50, 20, 0), (255, 160, 40))}

FAR = 65535
TEST, WRITE = 1, 2


def palette():
    return b"".join(b"".join(g.ramp_palette(r, dark, light)) for r, (dark, light) in RAMPS.items())


def mode(m):
    return g.set_depth(bool(m & TEST), bool(m & WRITE))


def tri(v, ramp):
    return g.draw_tri_shaded(*v, ramp)


# ---------------------------------------------------------------- the pictures
BG = [(0, [(-10, -10, 62000, 40), (330, -10, 62000, 120), (-10, 250, 62000, 200)]),
      (0, [(330, -10, 62000, 120), (330, 250, 62000, 248), (-10, 250, 62000, 200)])]
T1 = (2, [(20, 30, 8000, 0), (290, 60, 58000, 248), (80, 215, 8000, 124)])
T2 = (4, [(300, 40, 8000, 248), (30, 70, 58000, 0), (260, 220, 40000, 124)])
U1 = (5, [(230, 5, 30000, 248), (315, 5, 30000, 100), (240, 35, 30000, 0)])        # U1 and U2 have the same constant depth:
U2 = (6, [(240, 5, 30000, 100), (315, 35, 30000, 248), (260, 30, 30000, 0)])       # where they overlap the depths are equal


ORDER_A = [T1, T2, U1, U2]
ORDER_B = [T2, T1, U2, U1]


def pic_pair(order):
    out = [g.clear_all(9), mode(TEST | WRITE)] + [tri(v, r) for r, v in BG]
    out += [tri(v, r) for r, v in order]
    return out


def paint(order):
    """An independent painter's check for f0 and f1: BG, then the triangles in order with the depth test and write
    on, straight from raster_model (z < stored, equal fails). Returns {(x, y): colour}."""
    zb, col = {}, {}
    for ramp, v in list(BG) + list(order):
        for x, y, z, i in rm.shade_pixels(v[0], v[1], v[2], W, H):
            if z < zb.get((x, y), 65535):
                zb[(x, y)] = z
                col[(x, y)] = ramp * 32 + i
    return col, zb


def pic_modes():
    out = [g.clear_all(9)]
    for p, (mx, my) in enumerate([(3, 3), (1, 3), (2, 3), (0, 2)]):
        x0 = 8 + 78 * p
        X = (1, [(x0 + 10, 30, 20000, 0), (x0 + 60, 30, 20000, 248), (x0 + 10, 120, 20000, 124)])
        Y = (2, [(x0 + 2, 20, 40000, 248), (x0 + 68, 20, 40000, 248), (x0 + 2, 200, 40000, 60)])
        Z = (3, [(x0 + 5, 100, 30000, 0), (x0 + 66, 100, 30000, 248), (x0 + 5, 220, 30000, 124)])
        out += [mode(mx), tri(X[1], X[0]), mode(my), tri(Y[1], Y[0]), mode(TEST | WRITE), tri(Z[1], Z[0])]
    return out


def pic_clears():
    out = [g.clear_all(9, 45000), mode(TEST | WRITE),                                       # 45000 = AFC8: byte order matters
           tri([(5, 208, 46000, 248), (90, 232, 46000, 248), (5, 236, 46000, 248)], 7),         # E: behind the clear value, refused
           tri([(20, 20, 1000, 0), (200, 30, 1000, 248), (30, 200, 1000, 124)], 1),            # N: near, left
           g.clear_depth(FAR),
           tri([(100, 60, 50000, 248), (300, 80, 50000, 0), (120, 230, 50000, 124)], 2),        # F: far, overdraws N
           g.clear_depth(30000),
           tri([(200, 150, 20000, 0), (310, 150, 20000, 248), (310, 235, 20000, 124)], 3),      # G: passes
           tri([(190, 140, 40000, 248), (315, 140, 40000, 248), (250, 238, 40000, 248)], 4)]    # H: refused everywhere
    return out


STACK_Z = [40000, 35000, 36000, 20000, 20000, 20001, 19999, 50000, 19999, 19998, 30000, 19998, 19997, 40000, 19997, 65535]
PIX_POS = [(100, 100), (100, 100), (100, 100), (104, 100), (100, 100), (104, 100), (104, 100), (100, 100),
           (104, 100), (104, 100), (100, 100), (100, 100)]
PIX_Z = [900, 800, 800, 700, 600, 600, 500, 400, 450, 300, 300, 299]


def pic_stacks():
    out = [g.clear_all(0), mode(TEST | WRITE)]
    for k, z in enumerate(STACK_Z):
        out.append(tri([(20, 20, z, 100 + 9 * k), (70, 20, z, 248 - 4 * k), (20, 70, z, 50 + 10 * k)], k % 8))
    for k, ((x, y), z) in enumerate(zip(PIX_POS, PIX_Z)):
        out.append(tri([(x, y, z, 248), (x + 2, y, z, 248), (x, y + 2, z, 248)], 1 + k % 7))
    out.append(g.draw_tri(30, 30, 90, 40, 40, 90, 12))                                           # flat: no depth test, no write
    out.append(tri([(25, 25, 19000, 248), (95, 25, 19000, 248), (25, 95, 19000, 248)], 5))       # passes only if the flat one wrote nothing
    out.append(g.draw_tri(200, 20, 250, 20, 200, 70, 40))                                         # flat on virgin depth
    out.append(tri([(190, 10, 60000, 200), (260, 10, 60000, 200), (190, 80, 60000, 200)], 3))    # over it: depth still 65535
    return out


def pic_list_body():
    return (g.clear(0)
            + tri([(10, 120, 60000, 0), (150, 120, 60000, 248), (10, 238, 60000, 124)], 6)       # f4's pictures are not under it
            + tri([(10, 10, 60000, 248), (120, 10, 60000, 248), (10, 120, 60000, 248)], 5)       # over the stacks: refused where f4 drew
            + tri([(190, 10, 60000, 248), (260, 10, 60000, 248), (190, 80, 60000, 248)], 7)      # equal depth to f4's: refused
            + g.get_stats()                                                                     # not allowed in a list
            + g.flip())


def bad_packets():
    return [g.packet(g.SET_DEPTH, bytes(2)), g.packet(g.CLEAR_DEPTH, bytes([0xFF, 0xFF, 0x00])),
            g.packet(g.CLEAR_ALL, bytes(4)), g.packet(0x66, bytes(3))]


# ---------------------------------------------------------------- the checks the script makes on its own model
def planes(scene):
    """{(x, y): (z, shade)} of one shaded triangle, straight from raster_model."""
    ramp, v = scene
    return {(x, y): (z, i) for x, y, z, i in rm.shade_pixels(v[0], v[1], v[2], W, H)}


def tie_pixels(scenes):
    """Pixels where the smallest depth among the scenes covering them occurs twice."""
    seen = {}
    for sc in scenes:
        for p, (z, i) in planes(sc).items():
            seen.setdefault(p, []).append(z)
    return {p for p, zs in seen.items() if zs.count(min(zs)) > 1}


def build():
    """Returns (stream words, the replies, {label: screen index bytes}, model, notes)."""
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

    def picture(pk, first_line=None, tail=b""):
        line = first_line
        for p in pk:
            frame(p, line)
            line = None
        frame(g.flip() + tail)

    frame(g.clear(0) + g.flip() + g.clear(0))
    start_frame()
    pal = palette()
    for i in range(0, len(pal), 6 * 32):
        frame(pal[i:i + 6 * 32])
    picture(pic_pair(ORDER_A), tail=g.get_info())     # picture of f0, flipped now
    items.append(FENCE | 9)
    assert m.errors == 0
    start_frame()                                      # k = 0
    items += [LINE | PATCH_LINE, ARM]
    state["armed"] = True
    start_frame()                                      # k = 1: f0 on the screen; draw the picture of f1
    picture(pic_pair(ORDER_B), PATCH_LINE)
    pic_f1_model = bytes(m.fb)
    start_frame()                                      # k = 2: f1; draw f2
    picture(pic_modes(), PATCH_LINE)
    start_frame()                                      # k = 3: f2; draw f3
    picture(pic_clears(), PATCH_LINE)
    start_frame()                                      # k = 4: f3; draw f4, define the list that makes f5
    picture(pic_stacks(), PATCH_LINE)
    for p in g.list_packets(0, pic_list_body()):
        frame(p)
    start_frame()                                      # k = 5: f4; the bad packets, then the list
    for n, p in enumerate(bad_packets()):
        frame(p, PATCH_LINE if n == 0 else None)
    frame(g.run_list(0) + g.get_info())
    items.append(FENCE | 9)
    start_frame()                                      # k = 6: f5 on the screen
    assert len(shots) == 6, len(shots)

    want = bytes([0x47, 1, 0xFF, 0x0F, 0x40, 0x01, 0xF0, 0x00, 0x00,
                  0x47, 1, 0xFF, 0x0F, 0x40, 0x01, 0xF0, 0x00, 0x06])
    assert bytes(replies) == want, bytes(replies).hex()
    sh = [shots[x] for x in LABELS]
    notes = {}
    ties = tie_pixels([T1, T2, U1, U2])
    # f0 and f1 against the independent painter, in their drawing orders
    for label, order in (("f0", ORDER_A), ("f1", ORDER_B)):
        col, zb = paint(order)
        shot = shots[label]
        for n in range(W * H):
            x, y = n % W, n // W
            want = col.get((x, y), 0)
            assert shot[n] == want or (want == 0 and shot[n] == 0), (label, x, y, shot[n], want)
    # f0 against f1: equal except where the nearest depths tie (the pixel drawn first stays)
    diff = [n for n in range(W * H) if sh[0][n] != sh[1][n]]
    assert diff and set(diff) <= {y * W + x for x, y in ties}, "f0 and f1 must differ, and only where depths tie"
    notes["ties"] = len(ties)
    notes["f0_f1_diff"] = len(diff)
    pa, pb = planes(T1), planes(T2)
    notes["pair"] = (sum(1 for p in pa if p in pb and pa[p][0] < pb[p][0]), sum(1 for p in pa if p in pb and pb[p][0] < pa[p][0]))
    # the tiny triangles must cover at least one pixel each (otherwise the test says nothing)
    for (x, y) in set(PIX_POS):
        n = len(rm.tri_pixels((x, y), (x + 2, y), (x, y + 2), W, H))
        assert n >= 1, "a tiny triangle covers nothing"
    notes["pix_cover"] = len(rm.tri_pixels((100, 100), (102, 100), (100, 102), W, H))
    return items, bytes(replies), shots, m, notes


# ---------------------------------------------------------------- the counters' stream
def box_and_rows(v0, v1, v2):
    tri_ = rm.normalize(v0, v1, v2)
    if tri_ is None:
        return 0, 0
    bb = rm.bbox(tri_, W, H)
    if bb is None:
        return 0, 0
    return (bb[2] - bb[0] + 1) * (bb[3] - bb[1] + 1), bb[3] - bb[1] + 1


def build_stats():
    m = g.Gpu()
    items, rows = [], []
    acc = {"hi": 0}                       # upper bound on the clocks since the last CLEAR_STATS

    def send(data):
        data = bytes(data)
        m.feed(data)
        items.extend(data)

    def flat(*a):
        send(g.draw_tri(*a))
        v = [(a[0], a[1], 0), (a[2], a[3], 0), (a[4], a[5], 0)]
        box, nrows = box_and_rows(*[(p[0], p[1]) for p in v])
        acc["hi"] += box + 2 * nrows + 60 + 8

    def shaded(v, ramp):
        send(g.draw_tri_shaded(*v, ramp))
        box, nrows = box_and_rows(*[(p[0], p[1]) for p in v])
        acc["hi"] += 2 * box + 1000 + 8

    def check(same=False):
        send(g.get_stats())
        if same:
            items.append(0x1001)
            return
        items.append(0x1000)
        cov, wr = m.stat_cov, m.stat_wr
        rows.append((cov, wr, cov, acc["hi"]))

    def clear_stats():
        send(g.clear_stats())
        acc["hi"] = 0

    clear_stats()
    check()                                            # nothing drawn: all zero (clocks exactly 0)
    send(g.clear_all(0, FAR) + g.set_depth(True, True))
    shaded([(20, 20, 1000, 0), (120, 30, 1000, 248), (30, 120, 1000, 124)], 1)
    shaded([(40, 40, 5000, 0), (140, 50, 5000, 248), (50, 140, 5000, 124)], 2)       # partly behind: some pixels refused
    shaded([(10, 100, 30000, 0), (100, 110, 30000, 248), (20, 200, 30000, 124)], 3)
    flat(150, 20, 250, 30, 160, 100, 7)
    flat(-50, -50, 60, -40, -40, 60, 8)                                              # corner off the screen
    send(g.set_depth(False, False))
    shaded([(160, 150, 100, 0), (300, 160, 100, 248), (170, 230, 100, 124)], 4)      # test off: all written
    check()
    check(same=True)                                                                 # GET_STATS changes nothing
    send(g.fill_rect(0, 0, 50, 50, 3) + g.plot(5, 5, 9) + g.clear(2) + g.draw_line(0, 0, 100, 80, 4)
         + g.clear_depth(1234) + g.set_depth(True, True) + g.clear_all(1, 99) + g.nop())
    check(same=True)                                                                 # 2D drawing and clears are not counted
    shaded([(10, 10, 5, 0), (11, 10, 5, 0), (10, 11, 5, 0)], 1)                      # a very small one
    shaded([(500, 500, 5, 0), (600, 500, 5, 0), (500, 600, 5, 0)], 1)                # off the screen: no pixels
    shaded([(60, 60, 9, 0), (60, 60, 9, 200), (60, 60, 9, 0)], 1)                    # degenerate
    flat(70, 70, 70, 70, 70, 70, 2)
    shaded([(0, 0, 0, 0), (319, 0, 65535, 248), (0, 239, 30000, 100)], 5)            # a big one
    check()                                                                          # cumulative
    send(g.clear_depth(FAR))
    clear_stats()
    shaded([(30, 30, 60000, 0), (94, 30, 60000, 248), (30, 94, 60000, 100)], 6)      # one 64x64 triangle after the reset
    check()
    send(g.clear_depth(FAR))
    shaded([(30, 30, 60000, 0), (94, 30, 60000, 248), (30, 94, 60000, 100)], 6)
    shaded([(30, 30, 60000, 0), (94, 30, 60000, 248), (30, 94, 60000, 100)], 6)      # again: all refused (equal)
    check()
    return items, rows


def hexwords(path, words, width):
    with open(path, "w") as f:
        f.write("".join(("%0*x\n" % (width, w)) for w in words))


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--stream":
        items, info, shots, m, notes = build()
        items += [END]
        hexwords(os.path.join(args[1], "depth_stream.hex"), items, 4)
        hexwords(os.path.join(args[1], "depth_replies.hex"), info, 2)
        print("wrote %s/depth_stream.hex (%d entries) and depth_replies.hex" % (args[1], len(items)))
        print("T1/T2 overlap: T1 nearer on %d pixels, T2 nearer on %d; %d pixels have equal nearest depths; f0 and f1 differ in %d"
              % (notes["pair"] + (notes["ties"], notes["f0_f1_diff"])))
        return 0
    if len(args) == 2 and args[0] == "--stats":
        items, rows = build_stats()
        hexwords(os.path.join(args[1], "depth_stats_stream.hex"), items + [END], 4)
        hexwords(os.path.join(args[1], "depth_stats_expect.hex"), [v for r in rows for v in r], 8)
        print("wrote %s/depth_stats_stream.hex (%d bytes) and depth_stats_expect.hex (%d rows)" % (args[1], len(items), len(rows)))
        for r in rows:
            print("  pixels %d written %d clocks %d..%d" % r)
        return 0
    if len(args) == 1 and not args[0].startswith("-"):
        items, info, shots, m, notes = build()
        pal = np.array([[(c >> 16) & 255, (c >> 8) & 255, c & 255] for c in m.pal], dtype=np.uint16)
        for label, idx in shots.items():
            small = pal[np.frombuffer(idx, dtype=np.uint8).reshape(g.H, g.W)]
            big = small.repeat(2, axis=0).repeat(2, axis=1)
            write_ppm(os.path.join(args[0], "ref_depth_%s.ppm" % label), big, 255)
        print("wrote %d reference frames to %s" % (len(shots), args[0]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
