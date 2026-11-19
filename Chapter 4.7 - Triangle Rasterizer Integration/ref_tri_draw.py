#!/usr/bin/env python3
"""ref_tri_draw.py -- test stream and expected results for tb_gpu_tri (Chapter 4.7).

Usage (from the repo root, once; commit the six files it writes):
    .venv/bin/python sim/py/ref_tri_draw.py sim

Writes:
    sim/tri_stream.hex    what the testbench sends, one entry per line, in
                          Chapter 4.4's format plus new directives (2004-2006, 4HHH):
                            00NN  a byte to send over the UART
                            1NNN  fence: wait until NNN (hex) reply bytes
                                  have arrived in all
                            2000  from here on, send without looking at o_ready
                            2001  from here on, wait for o_ready before each byte
                            2002  start a throughput window: from here,
                                  record the first and last framebuffer write
                            2003  end the throughput window
                            2004  zero the testbench's write counters
                            2005  check: since 2004, every pixel of the back
                                  buffer was written exactly once, and the
                                  front buffer not at all
                            2006  check: since 2004, no pixel of the back
                                  buffer was written twice, and the front
                                  buffer not at all
                            3NNN  the open window may take at most
                                  4096 * HHH + NNN clocks from its first write to
                                  its last, where 4HHH precedes the 3NNN
                                  (the high part is sent first)
                            FFFF  end of the stream
    sim/tri_replies.hex   every reply byte the GPU must send, in order
    sim/tri_labels.txt    one line per FB_CRC reply: what it covers
    sim/tri_fb0.hex       framebuffer 0 at the end (76,800 lines)
    sim/tri_fb1.hex       framebuffer 1 at the end
    sim/tri_pal.hex       the palette at the end (256 lines, RRGGBB)

Both buffers start as Chapter 4.1's test image, as in ref_flip_sync.py, and the
expected results come from host/gpu_model.py (which uses host/raster_model.py).

The stream, in order:
  1. directed cases: Chapter 4.6's worked triangles, both windings, malformed
     DRAW_TRIs, degenerate and off-screen triangles, one FB_CRC per group
  2. ordering: DRAW_TRI mixed with the older commands, no fences
  3. four throughput windows
  4. back-pressure: a screen-sized triangle with short commands right behind it
  5. six meshes into the counting buffer (2004 ... 2005/2006), with FLIPs between
  6. 300 random DRAW_TRIs with an FB_CRC every 50 and older commands mixed in
"""
import os
import random
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model, raster_model

import gpu_model as g  # noqa: E402
import raster_model as rm  # noqa: E402
from ref_scanout import image, palette  # noqa: E402

(IGNORE_READY, HONOR_READY, WIN_START, WIN_END, COUNT_RESET, COUNT_ONCE, COUNT_AT_MOST,
 WIN_LIMIT, WIN_LIMIT_HI, END) = 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x3000, 0x4000, 0xFFFF
SEED = 4747
N_RANDOM = 300
GROUP = 50          # random triangles per FB_CRC
WIN_SLACK = 16      # spare clocks allowed per throughput window ...
WIN_PER_ROW = 2     # ... plus this many per box row (a row change may cost a clock or two)
I16 = (-32768, 32767)


def clamp16(v):
    return max(-32768, min(32767, v))


def box_size(tri):
    """(pixels, rows) of the triangle's bounding box clamped to the screen ((0, 0) if empty)."""
    bb = rm.bbox(tri, g.W, g.H)
    return (0, 0) if bb is None else ((bb[2] - bb[0] + 1) * (bb[3] - bb[1] + 1), bb[3] - bb[1] + 1)


class Stream:
    """The testbench's stream, fed through the model as it is built."""

    def __init__(self, model):
        self.model = model
        self.items = []
        self.replies = bytearray()
        self.labels = []
        self.nbytes = 0
        self.tris = 0

    def send(self, data):
        self.items.extend(data)
        self.replies += self.model.feed(data)
        self.nbytes += len(data)

    def tri(self, v0, v1, v2, c):
        self.send(g.draw_tri(*v0, *v1, *v2, c))
        self.tris += 1

    def crc(self, label):
        self.send(g.fb_crc())
        self.labels.append(label)

    def directive(self, d):
        self.items.append(d)

    def fence(self):
        self.items.append(0x1000 + len(self.replies))


def scramble(rng, v):
    """The three corners rotated at random, and half the time with two swapped,
    so every triangle arrives in a random order and winding."""
    v = list(v)
    k = rng.randrange(3)
    v = v[k:] + v[:k]
    if rng.random() < 0.5:
        v[1], v[2] = v[2], v[1]
    return tuple(v)


# ------------------------------------------------------------------ meshes
def mesh(rng, xs, ys, jit, jit_boundary=True):
    """A mesh of quads on the lattice xs x ys (vertex coordinates), every quad
    split into two triangles along a random diagonal. Vertices move by up to
    +-jit(i, j) in each axis, which keeps every quad convex when jit is below a
    quarter of the smallest adjacent cell. Returns the triangles, shuffled,
    with random winding and corner order, as a list of (v0, v1, v2, color)."""
    nx, ny = len(xs) - 1, len(ys) - 1
    v = {}
    for i in range(nx + 1):
        for j in range(ny + 1):
            edge = i in (0, nx) or j in (0, ny)
            a = 0 if (edge and not jit_boundary) else jit(i, j)
            v[i, j] = (xs[i] + rng.randint(-a, a), ys[j] + rng.randint(-a, a))
    tris = []
    for i in range(nx):
        for j in range(ny):
            a, b, c, d = v[i, j], v[i + 1, j], v[i + 1, j + 1], v[i, j + 1]
            signs = {rm.area2(p, q, r) > 0 for p, q, r in ((a, b, c), (b, c, d), (c, d, a), (d, a, b))}
            assert len(signs) == 1, "quad not convex"
            quad = [(a, b, c), (a, c, d)] if rng.random() < 0.5 else [(a, b, d), (b, c, d)]
            tris.extend(quad)
    rng.shuffle(tris)
    return [scramble(rng, t) + (rng.randrange(1, 256),) for t in tris]


def lattice(lo, hi, n):
    return [lo + (hi - lo) * i // n for i in range(n + 1)]


def min_cell(vals, i):
    gaps = []
    if i > 0:
        gaps.append(vals[i] - vals[i - 1])
    if i < len(vals) - 1:
        gaps.append(vals[i + 1] - vals[i])
    return min(gaps)


def jitter_for(xs, ys):
    return lambda i, j: min(min_cell(xs, i), min_cell(ys, j)) // 4 - 1


def mesh_coverage(tris):
    """Per-pixel hit counts of a mesh according to the model."""
    cnt = {}
    for v0, v1, v2, _ in tris:
        for p in rm.tri_pixels(v0, v1, v2, g.W, g.H):
            cnt[p] = cnt.get(p, 0) + 1
    return cnt


def build_meshes(rng):
    """The six meshes: (name, triangles, covers the whole screen?)."""
    out = []
    xs, ys = lattice(0, 320, 16), lattice(0, 240, 12)
    out.append(("20x20 squares on the exact screen, no jitter, random diagonals",
                mesh(rng, xs, ys, lambda i, j: 0), True))
    xs, ys = lattice(-24, 344, 11), lattice(-16, 256, 8)
    out.append(("jittered quads extending 24 pixels past every screen edge",
                mesh(rng, xs, ys, jitter_for(xs, ys)), True))
    xs, ys = [-3000, 40, 170, 3300], [-2000, 60, 2200]
    out.append(("huge jittered triangles, most of their area off the screen",
                mesh(rng, xs, ys, lambda i, j: 25), True))
    xs, ys = lattice(112, 208, 10), lattice(90, 150, 7)
    out.append(("a 96 x 60 patch of 9-pixel cells, jittered into slivers (the screen around it untouched)",
                mesh(rng, xs, ys, lambda i, j: 1), False))
    xs, ys = [-32768, -8000, 150, 32767], [-32768, 110, 32767]
    out.append(("an int16-limit mesh: corners at +-32768, a vertex near the screen centre",
                mesh(rng, xs, ys, lambda i, j: 40, jit_boundary=False), True))
    out.append(("two triangles from (-32768, -32768) to (32767, 32767), one diagonal across the screen",
                [scramble(rng, t) + (c,) for t, c in (
                    (((-32768, -32768), (32767, -32768), (32767, 32767)), 0x71),
                    (((-32768, -32768), (32767, 32767), (-32768, 32767)), 0x72))], True))
    return out


# ---------------------------------------------------------------- random
def random_tri(rng, kind):
    X, Y = lambda: rng.randrange(-8, 329), lambda: rng.randrange(-8, 249)  # noqa: E731
    if kind == "small":
        x, y = X(), Y()
        v = [(x, y), (x + rng.randint(-12, 12), y + rng.randint(-12, 12)),
             (x + rng.randint(-12, 12), y + rng.randint(-12, 12))]
    elif kind == "medium":
        x, y = X(), Y()
        v = [(x + rng.randint(-40, 40), y + rng.randint(-40, 40)) for _ in range(3)]
    elif kind == "large":
        v = [(rng.randrange(320), rng.randrange(240)) for _ in range(3)]
    elif kind == "cross":
        v = [(rng.randrange(-400, 720), rng.randrange(-300, 540)) for _ in range(3)]
    elif kind == "sliver":
        x, y = X(), Y()
        v = [(x, y), (x + rng.randint(-2, 2), y + rng.randint(-2, 2)),
             (rng.randrange(-20, 340), rng.randrange(-20, 260))]
    elif kind == "axis":
        x, y = X(), Y()
        w, h = rng.randint(1, 80) * rng.choice((-1, 1)), rng.randint(1, 80) * rng.choice((-1, 1))
        v = [(x, y), (x + w, y), (x, y + h)]
    elif kind == "diag":
        x, y = X(), Y()
        w, h = rng.randint(1, 60) * rng.choice((-1, 1)), rng.randint(1, 60) * rng.choice((-1, 1))
        v = [(x, y), (x + w, y + w * rng.choice((-1, 1))), (x, y + h)]
    elif kind == "degen":
        x, y = X(), Y()
        p1 = (x + rng.randint(-30, 30), y + rng.randint(-30, 30))
        which = rng.randrange(4)
        if which == 0:
            v = [(x, y)] * 3
        elif which == 1:
            v = [(x, y), (x, y), p1]
        elif which == 2:
            k = rng.randint(-3, 4)
            v = [(x, y), p1, (x + k * (p1[0] - x), y + k * (p1[1] - y))]
        else:
            v = [(x, y), (x + rng.randint(1, 60), y), (x + rng.randint(-60, 60), y)]
    else:  # extreme: int16 limits and screen-edge values
        special = (-32768, -1000, -321, -1, 0, 1, 239, 240, 318, 319, 320, 511, 1000, 32767)
        v = [(rng.choice(special) if rng.random() < 0.6 else rng.randrange(-40, 360),
              rng.choice(special) if rng.random() < 0.6 else rng.randrange(-40, 280)) for _ in range(3)]
    return scramble(rng, [(clamp16(a), clamp16(b)) for a, b in v])


KINDS = (["small"] * 75 + ["medium"] * 45 + ["large"] * 25 + ["cross"] * 45 + ["sliver"] * 30 +
         ["axis"] * 30 + ["diag"] * 12 + ["degen"] * 22 + ["extreme"] * 10)


# ----------------------------------------------------------------- stream
def directed_groups():
    """Directed triangles, in groups that end in one FB_CRC each."""
    sq_a = [((0, 0), (0, 4), (4, 0)), ((4, 0), (0, 4), (4, 4))]
    return [
        ("Chapter 4.6's worked triangle (0,0) (0,4) (4,0), both windings, and every corner order", [
            ((0, 0), (0, 4), (4, 0)), ((0, 0), (4, 0), (0, 4)), ((0, 4), (4, 0), (0, 0)),
            ((4, 0), (0, 0), (0, 4)), ((0, 4), (0, 0), (4, 0)), ((4, 0), (0, 4), (0, 0))]),
        ("a 4x4 square from two triangles on a shared diagonal, at the origin, mid-screen and in the far corner", [
            *sq_a, *[tuple((x + 100, y + 70) for x, y in t) for t in sq_a],
            *[tuple((x + 316, y + 236) for x, y in t) for t in sq_a]]),
        ("triangles touching the screen's four edges and corners", [
            ((0, 0), (319, 0), (0, 239)), ((319, 239), (0, 239), (319, 0)), ((0, 100), (30, 120), (0, 140)),
            ((319, 100), (289, 120), (319, 140)), ((100, 0), (120, 25), (140, 0)), ((100, 239), (140, 239), (120, 215)),
            ((0, 0), (0, 0), (1, 0)), ((319, 239), (319, 239), (318, 239))]),
        ("partly off screen: across each edge, each corner, and all four at once", [
            ((-20, 50), (30, 20), (30, 90)), ((300, 50), (350, 20), (350, 90)), ((100, -30), (140, -30), (120, 25)),
            ((100, 270), (140, 270), (120, 215)), ((-30, -30), (40, -30), (-30, 40)), ((350, -30), (280, -30), (350, 40)),
            ((-30, 270), (40, 270), (-30, 200)), ((350, 270), (280, 270), (350, 200)),
            ((-100, -100), (500, 120), (-100, 400))]),
        ("hidden: just off each edge, past a corner with an overlapping bounding box, at x = 522 (nothing drawn)", [
            ((-30, 10), (-1, 10), (-1, 80)), ((320, 10), (350, 10), (320, 80)), ((10, -30), (80, -1), (10, -1)),
            ((10, 240), (80, 240), (10, 270)), ((-40, 250), (-10, 220), (-10, 250)), ((330, -10), (350, 10), (330, 10)),
            ((522, 10), (560, 10), (522, 40)), ((-502, 10), (-470, 10), (-502, 40)),
            ((1000, 1000), (2000, 1500), (1500, 2000)), ((-32768, -32768), (-32768, -32700), (-32700, -32768))]),
        ("degenerate: three equal corners, two equal, zero width, zero height, collinear on a slope (nothing drawn)", [
            ((50, 50), (50, 50), (50, 50)), ((60, 60), (60, 60), (90, 70)), ((70, 70), (70, 100), (70, 130)),
            ((100, 70), (130, 70), (160, 70)), ((10, 150), (40, 160), (100, 180)), ((200, 200), (100, 100), (150, 150)),
            ((-32768, -32768), (0, 0), (32767, 32767))]),
        ("thin slivers and one-pixel triangles", [
            ((20, 190), (21, 190), (20, 191)), ((30, 190), (31, 191), (30, 191)), ((40, 190), (140, 191), (40, 192)),
            ((150, 190), (250, 192), (150, 191)), ((20, 200), (300, 201), (20, 201)), ((20, 210), (21, 230), (22, 210)),
            ((30, 210), (30, 230), (31, 230)), ((100, 215), (260, 216), (261, 217))]),
        ("right triangles with horizontal and vertical edges in all four orientations, and exact 45-degree edges", [
            ((10, 10), (60, 10), (10, 60)), ((120, 10), (70, 10), (120, 60)), ((130, 70), (130, 20), (180, 70)),
            ((240, 70), (240, 20), (190, 20)), ((200, 100), (250, 150), (200, 150)), ((260, 150), (310, 100), (310, 150)),
            ((20, 120), (70, 120), (45, 145)), ((120, 150), (170, 150), (145, 100))]),
        ("corners at the int16 limits whose edge values need 33 bits", [
            ((-32768, -32768), (32767, -32768), (-32768, 32767)), ((32767, 32767), (-32768, 32767), (32767, -32768)),
            ((-32768, 100), (32767, 120), (150, -32768)), ((150, 32767), (32767, -32768), (-32768, 20)),
            ((-32768, -32768), (32767, 32767), (-32768, 32767)), ((-32768, -32768), (32767, -32768), (32767, 32767))]),
    ]


def build(model):
    rng = random.Random(SEED)
    s = Stream(model)
    stats = {"cw": 0, "empty": 0, "partial": 0, "degen": 0}
    s.directive(HONOR_READY)

    # 1. The untouched test image, then the directed groups.
    s.crc("the 4.1 test image before any command (FB_CRC itself)")
    s.fence()
    color = 0x10
    for label, tris in directed_groups():
        for v in tris:
            s.tri(*v, color)
            color = 0x10 + (color - 0x0F) % 200
        s.crc("directed: " + label)
    s.fence()

    # 2. Malformed DRAW_TRIs (none may draw), and DRAW_TRI mixed with the older
    #    commands, in order, with no fence between them.
    full = g.draw_tri(10, 10, 300, 20, 150, 230, 0xEE)
    s.send(g.packet(g.DRAW_TRI, full[2:14]))             # 12 payload bytes
    s.send(g.packet(g.DRAW_TRI, full[2:] + b"\x00"))     # 14
    s.send(g.packet(g.DRAW_TRI, b""))                    # 0
    s.send(g.nop(full))                                  # inside a NOP
    s.send(g.packet(0x87, full))                         # inside an unknown packet
    s.crc("DRAW_TRI with lengths 12, 14, 0 and inside NOP/unknown packets (must not draw)")
    s.send(g.fill_rect(20, 150, 120, 200, 0x31))
    s.tri((20, 150), (120, 200), (20, 200), 0x32)        # over the rectangle
    s.send(g.plot(40, 190, 0x33))                        # on the triangle
    s.tri((30, 160), (30, 195), (60, 195), 0x34)         # over the plot
    s.send(g.draw_line(20, 150, 120, 200, 0x35))         # on the triangle's long edge
    s.send(g.fill_rect(60, 170, 80, 180, 0x36))          # over everything
    s.tri((0, 0), (60, 0), (0, 60), 0x37)                # a second triangle right behind
    s.send(g.set_palette(0x32, 0xA1B2C3))
    s.crc("DRAW_TRI mixed with FILL_RECT, PLOT, DRAW_LINE, SET_PALETTE, in order, no fences")
    s.fence()

    # 3. Throughput: one triangle per window. The testbench measures the clocks
    #    from its first write to its last against the 3NNN limit: the clamped
    #    bounding box plus 2 clocks per box row plus 16: about one box pixel per
    #    clock. Setup before the first write is not counted.
    windows = [("inside the screen, 291 x 221 box", ((10, 10), (300, 20), (150, 230))),
               ("clockwise and mostly off screen: the box is clamped to the screen", ((-1000, -500), (100, 900), (1500, 100))),
               ("a thin sliver, 301 x 5 box", ((10, 100), (310, 104), (10, 104))),
               ("small, 13 x 11 box", ((200, 200), (212, 205), (205, 210)))]
    for i, (name, v) in enumerate(windows):
        tri = rm.normalize(*v)
        n, rows = box_size(tri)
        limit = n + WIN_PER_ROW * rows + WIN_SLACK
        s.directive(WIN_START)
        s.directive(WIN_LIMIT_HI + (limit >> 12))
        s.directive(WIN_LIMIT + (limit & 0xFFF))
        s.tri(*v, 0x40 + i)
        s.crc(f"throughput window {i + 1}: {name} ({n} box pixels)")
        s.fence()
        s.directive(WIN_END)

    # 4. Back-pressure without flow control: a screen-sized triangle (1.5 ms of
    #    rasterizing, 385 byte times at the test baud), then 20 short ones and
    #    an FB_CRC right behind it. The engine must not fetch while it works.
    s.directive(IGNORE_READY)
    s.tri((-32768, -32768), (32767, -32768), (32767, 32767), 0x48)
    s.send(g.fill_rect(0, 0, 319, 239, 0x49))            # would be erased by the next if run early
    for _ in range(20):
        x, y = rng.randrange(300), rng.randrange(220)
        s.tri(*scramble(rng, [(x, y), (x + rng.randrange(1, 20), y + rng.randrange(-15, 16)),
                              (x + rng.randrange(-15, 16), y + rng.randrange(1, 20))]), rng.randrange(256))
    s.crc("back-pressure: a screen-sized DRAW_TRI with a FILL_RECT and 20 short DRAW_TRIs sent right behind it")
    s.fence()
    s.directive(HONOR_READY)

    # 5. Meshes into the counting buffer. A FLIP between them swaps which
    #    buffer is the back buffer, so both get used.
    mesh_stats = []
    for k, (name, tris, full_cover) in enumerate(build_meshes(rng)):
        cnt = mesh_coverage(tris)
        assert max(cnt.values()) == 1, name
        if full_cover:
            assert len(cnt) == g.W * g.H, (name, len(cnt))
        s.directive(COUNT_RESET)
        for v0, v1, v2, c in tris:
            stats["cw"] += rm.area2(v0, v1, v2) < 0
            s.tri(v0, v1, v2, c)
        s.crc(f"mesh {k + 1}: {name} ({len(tris)} triangles, {len(cnt)} pixels)")
        s.fence()
        s.directive(COUNT_ONCE if full_cover else COUNT_AT_MOST)
        mesh_stats.append((len(tris), len(cnt)))
        s.send(g.flip())
        s.crc("after the FLIP behind mesh %d: the other buffer (a fence, and a check of the swap)" % (k + 1))
        s.fence()

    # 6. Random triangles, an FB_CRC every 50, older commands mixed in.
    for i in range(N_RANDOM):
        kind = rng.choice(KINDS)
        v = random_tri(rng, kind)
        n = len(rm.tri_pixels(*v, g.W, g.H))
        stats["cw"] += rm.area2(*v) < 0
        stats["degen"] += rm.area2(*v) == 0
        stats["empty"] += n == 0
        stats["partial"] += n > 0 and any(not (0 <= x < g.W and 0 <= y < g.H) for x, y in v)
        s.tri(*v, rng.randrange(256))
        if i % 40 == 17:
            s.send(g.plot(rng.randrange(-5, 325), rng.randrange(-5, 245), rng.randrange(256)))
        if i % 60 == 29:
            x, y = rng.randrange(-20, 320), rng.randrange(-20, 240)
            s.send(g.fill_rect(x, y, x + rng.randrange(40), y + rng.randrange(30), rng.randrange(256)))
        if i % 70 == 41:
            s.send(g.draw_line(rng.randrange(320), rng.randrange(240), rng.randrange(320), rng.randrange(240),
                               rng.randrange(256)))
        if i % 71 == 3:
            s.send(g.packet(0x80 + rng.randrange(128), bytes(rng.randrange(256) for _ in range(rng.randrange(16)))))
        if i == 200:
            s.send(g.set_palette(0x24, 0x2468AC))
        if i % GROUP == GROUP - 1:
            s.crc(f"random triangles {i - GROUP + 2}-{i + 1}")
            if i in (149, 249):
                s.send(g.flip())
                s.crc(f"after a FLIP behind random triangle {i + 1}")
                s.fence()
    s.fence()
    s.directive(END)
    stats["meshes"] = mesh_stats
    return s, stats


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR    (normally: sim)")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    pal0 = [(int(r) << 16) | (int(gg) << 8) | int(b) for r, gg, b in palette()]
    model = g.Gpu(fb=image().reshape(-1).tobytes(), pal=pal0)
    s, stats = build(model)

    def write(name, lines, comment):
        with open(os.path.join(outdir, name), "w") as f:
            f.write(f"// {comment} (made by sim/py/ref_tri_draw.py)\n")
            f.write("\n".join(lines) + "\n")

    write("tri_stream.hex", [f"{v:04x}" for v in s.items],
          "tb_gpu_tri stream: 00NN byte, 1NNN fence, 20xx directive, 3NNN/4HHH window limit, FFFF end")
    write("tri_replies.hex", [f"{v:02x}" for v in s.replies], "expected reply bytes, in order")
    write("tri_fb0.hex", [f"{v:02x}" for v in model.bufs[0]], "expected framebuffer 0, address y*320+x")
    write("tri_fb1.hex", [f"{v:02x}" for v in model.bufs[1]], "expected framebuffer 1, address y*320+x")
    write("tri_pal.hex", [f"{v:06x}" for v in model.pal], "expected palette, RRGGBB")
    with open(os.path.join(outdir, "tri_labels.txt"), "w") as f:
        f.write("".join(f"{lab[:120]}\n" for lab in s.labels))
    print(f"ref_tri_draw: {s.nbytes} bytes, {s.tris} DRAW_TRIs ({model.tri_drawn} pixels), "
          f"{len(s.replies) // 4} FB_CRC replies, {model.flips} FLIPs -> {outdir}/tri_*")
    print("  meshes (triangles, pixels): " + ", ".join(f"{a}/{b}" for a, b in stats["meshes"]))
    print(f"  {stats['cw']} clockwise in meshes and random; random: {N_RANDOM} "
          f"({stats['degen']} degenerate, {stats['empty']} empty, {stats['partial']} with a corner off screen but drawing)")


if __name__ == "__main__":
    main()
