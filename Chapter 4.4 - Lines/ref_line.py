#!/usr/bin/env python3
"""ref_line.py -- test stream and expected results for tb_gpu_line (Chapter 4.4).

Usage (from the repo root, once; commit the five files it writes):
    .venv/bin/python sim/py/ref_line.py sim

Writes:
    sim/line_stream.hex   what the testbench sends, one entry per line, in
                          Chapter 4.3's format plus one new directive:
                            00NN  a byte to send over the UART
                            1NNN  fence: wait until NNN (hex) reply bytes
                                  have arrived in all
                            2000  from here on, send without looking at o_ready
                            2001  from here on, wait for o_ready before each byte
                            2002  start a throughput window: from here,
                                  record the first and last framebuffer write
                            2003  end the throughput window
                            3NNN  the open window may take at most NNN (hex)
                                  clocks from its first write to its last
                            FFFF  end of the stream
    sim/line_replies.hex  every reply byte the GPU must send, in order
    sim/line_labels.txt   one line per FB_CRC reply: what it covers
    sim/line_fb.hex       the framebuffer at the end (76,800 lines)
    sim/line_pal.hex      the palette at the end (256 lines, RRGGBB)

The framebuffer and palette start as Chapter 4.1's test image, as in
ref_rect.py, and the expected results come from host/gpu_model.py.
"""
import math
import os
import random
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model

import gpu_model as g  # noqa: E402
from ref_scanout import image, palette  # noqa: E402

IGNORE_READY, HONOR_READY, WIN_START, WIN_END, WIN_LIMIT, END = 0x2000, 0x2001, 0x2002, 0x2003, 0x3000, 0xFFFF
SEED = 4444
N_RANDOM = 500
GROUP = 50          # random lines per FB_CRC
WIN_SLACK = 8       # spare clocks allowed per throughput window


def on_screen(x, y):
    return 0 <= x < g.W and 0 <= y < g.H


def visible(x0, y0, x1, y1):
    return sum(on_screen(x, y) for x, y in g.line_points(x0, y0, x1, y1))


def has_tie(x0, y0, x1, y1):
    """True if the line passes exactly half-way between two pixels somewhere,
    so that DRAW_LINE a b and DRAW_LINE b a differ."""
    return list(g.line_points(x0, y0, x1, y1)) != list(g.line_points(x1, y1, x0, y0))[::-1]


class Stream:
    """The testbench's stream, fed through the model as it is built."""

    def __init__(self, model):
        self.model = model
        self.items = []
        self.replies = bytearray()
        self.labels = []
        self.nbytes = 0
        self.lines = 0

    def send(self, data):
        self.items.extend(data)
        self.replies += self.model.feed(data)
        self.nbytes += len(data)

    def line(self, x0, y0, x1, y1, c):
        self.send(g.draw_line(x0, y0, x1, y1, c))
        self.lines += 1

    def crc(self, label):
        self.send(g.fb_crc())
        self.labels.append(label)

    def directive(self, d):
        self.items.append(d)

    def fence(self):
        self.items.append(0x1000 + len(self.replies))


# The eight octants as (sign x, sign y, steep). Each cell of a 4 x 2 grid
# gets one octant, so a wrong octant shows on its own.
OCTANTS = [(sx, sy, steep) for sy in (1, -1) for sx in (1, -1) for steep in (False, True)]
OCT_VECS = [(35, 12), (35, 1), (35, 34), (29, 11), (33, 20), (34, 17), (30, 0)]


def octant_lines(reverse):
    out = []
    for i, (sx, sy, steep) in enumerate(OCTANTS):
        cx, cy = 40 + 80 * (i % 4), 60 + 120 * (i // 4)
        for a, b in OCT_VECS:
            dx, dy = (b, a) if steep else (a, b)
            line = (cx, cy, cx + sx * dx, cy + sy * dy)
            out.append(line[2:] + line[:2] if reverse else line)
    return out


def tie_lines():
    """Lines with exact ties in every octant, each drawn forward and, 3 pixels
    away, backward, so the direction rule shows."""
    out = []
    vecs = [(2, 1), (4, 2), (8, 4), (6, 3), (20, 6), (30, 9), (14, 5)]
    for i, (sx, sy, steep) in enumerate(OCTANTS):
        bx = 4 + 40 * i
        for j, (a, b) in enumerate(vecs):
            dx, dy = (b, a) if steep else (a, b)
            by = 2 + 34 * j
            x0, y0 = bx + (dx if sx < 0 else 0), by + (dy if sy < 0 else 0)
            x1, y1 = x0 + sx * dx, y0 + sy * dy
            ox, oy = (3, 0) if steep else (0, 3)
            assert has_tie(x0, y0, x1, y1), (x0, y0, x1, y1)
            out.append((x0, y0, x1, y1))
            out.append((x1 + ox, y1 + oy, x0 + ox, y0 + oy))
    return out


def starburst():
    """Rays from the centre to every 16th pixel of the screen's border."""
    cx, cy = 160, 120
    ends = [(x, 0) for x in range(0, 320, 16)] + [(319, y) for y in range(0, 240, 16)] + \
           [(x, 239) for x in range(319, 0, -16)] + [(0, y) for y in range(239, 0, -16)]
    return [(cx, cy, x, y) for x, y in ends]


EDGE_GROUPS = [
    ("single points (x0 == x1 and y0 == y1), on and just off the screen", [
        (0, 0, 0, 0), (319, 0, 319, 0), (0, 239, 0, 239), (319, 239, 319, 239), (160, 120, 160, 120),
        (-1, 0, -1, 0), (320, 5, 320, 5), (5, 240, 5, 240), (0, -1, 0, -1), (522, 10, 522, 10)]),
    ("horizontal and vertical lines, both directions", [
        (10, 10, 60, 10), (60, 14, 10, 14), (70, 10, 70, 60), (74, 60, 74, 10), (0, 239, 319, 239),
        (319, 0, 319, 239), (319, 0, 0, 0), (0, 239, 0, 0), (100, 30, 101, 30), (110, 30, 110, 31)]),
    ("45-degree diagonals in all four directions", [
        (20, 80, 60, 120), (120, 120, 80, 80), (140, 120, 180, 80), (240, 80, 200, 120), (0, 0, 239, 239),
        (319, 0, 80, 239)]),
    ("the eight octants, drawn outward from a centre", octant_lines(False)),
    ("the eight octants, drawn inward (the same lines with the endpoints swapped)", octant_lines(True)),
    ("exact ties (dx = 2*dy and others) in every octant, each drawn both ways", tie_lines()),
    ("touching the screen edges, and corner to corner", [
        (0, 5, 0, 60), (319, 200, 319, 150), (5, 0, 300, 0), (300, 239, 5, 239), (0, 0, 319, 239),
        (319, 0, 0, 239), (0, 239, 319, 0), (319, 239, 0, 0), (318, 239, 319, 237), (1, 0, 0, 2)]),
    ("partly off screen: clipped on each side and across corners", [
        (-20, 50, 30, 70), (300, 80, 340, 95), (100, -30, 120, 20), (130, 230, 150, 260),
        (-100, -50, 420, 290), (60, -100, 90, 400), (-5, 10, 10, -5), (330, 200, 290, 250),
        (-1, 100, 0, 100), (319, 100, 320, 101), (200, -1, 200, 0), (210, 239, 211, 240)]),
    ("off screen, nothing drawn: just outside each edge, missing the corners, far away, aliasing", [
        (-1, 0, -1, 239), (320, 0, 320, 239), (0, -1, 319, -1), (0, 240, 319, 240), (-10, 5, 5, -10),
        (-10, 230, 9, 249), (310, -10, 330, 10), (330, 230, 310, 250), (522, 10, 530, 20),
        (10, 266, 20, 276), (-502, 10, -490, 20), (-1000, -1000, -500, -200), (1000, 1000, 2000, 1500),
        (-32768, -32768, -32768, -32700), (32767, 32767, 32700, 32767)]),
    ("long lines at the int16 limits that cross the screen (needs a wide error term)", [
        (-32768, -32768, 32767, 32767), (32767, -32528, -32528, 32767), (-29840, -14880, 30160, 15120),
        (100, -32768, 130, 32767), (-32768, 0, 32767, 239), (32767, 200, -32768, 20),
        (150, 32767, 170, -32768)]),
]


def random_line(rng, kind):
    if kind == "short":
        x0, y0 = rng.randrange(-10, 330), rng.randrange(-10, 250)
        x1, y1 = x0 + rng.randrange(-30, 31), y0 + rng.randrange(-30, 31)
    elif kind == "long":
        x0, y0, x1, y1 = rng.randrange(320), rng.randrange(240), rng.randrange(320), rng.randrange(240)
    elif kind == "cross":
        x0, y0 = rng.randrange(-400, 720), rng.randrange(-300, 540)
        x1, y1 = rng.randrange(-400, 720), rng.randrange(-300, 540)
    elif kind == "tie":
        k = rng.choice([1, 1, 2, 3, 5, 7, 10, 20, 60])
        a, b = 2 * k * rng.choice([1, 3, 5]), k
        if rng.random() < 0.5:
            a, b = b, a
        dx, dy = a * rng.choice([-1, 1]), b * rng.choice([-1, 1])
        x0, y0 = rng.randrange(-20, 340), rng.randrange(-20, 260)
        x1, y1 = x0 + dx, y0 + dy
    elif kind == "axis":
        x0, y0 = rng.randrange(-20, 340), rng.randrange(-20, 260)
        which = rng.randrange(3)
        x1 = x0 if which != 1 else x0 + rng.randrange(-200, 201)
        y1 = y0 if which != 0 else y0 + rng.randrange(-200, 201)
    else:  # extreme: int16 limits and screen-edge values; must stay short to step
        special = (-32768, -1000, -321, -1, 0, 1, 239, 240, 318, 319, 320, 511, 522, 1000, 32767)

        def coord(lim):
            return rng.choice(special) if rng.random() < 0.5 else rng.randrange(-40, lim + 40)
        x0, y0, x1, y1 = coord(320), coord(240), coord(320), coord(240)
    clamp = lambda v: max(-32768, min(32767, v))  # noqa: E731
    return clamp(x0), clamp(y0), clamp(x1), clamp(y1)


KINDS = ["short"] * 40 + ["long"] * 15 + ["cross"] * 15 + ["tie"] * 15 + ["axis"] * 8 + ["extreme"] * 7


def build(model):
    rng = random.Random(SEED)
    s = Stream(model)
    stats = {"tie": 0, "steep": 0, "offscreen": 0, "partial": 0, "point": 0, "steps": 0}
    s.directive(HONOR_READY)

    # 1. The untouched 4.1 test image.
    s.crc("the 4.1 test image before any command (FB_CRC itself)")
    s.fence()

    # 2. Edge cases on top of the test image, one FB_CRC per group.
    color = 0x10
    for label, lines in EDGE_GROUPS:
        for ln in lines:
            s.line(*ln, color)
            color = (color + 1) & 0xFF
        s.crc("edge cases: " + label)
    s.fence()

    # 3. Malformed and hidden DRAW_LINEs (none may draw), and DRAW_LINE
    #    mixed with the older commands, in order.
    full = g.draw_line(0, 0, 319, 239, 0xEE)
    s.send(g.packet(g.DRAW_LINE, full[2:10]))            # 8 payload bytes
    s.send(g.packet(g.DRAW_LINE, full[2:] + b"\x00"))    # 10
    s.send(g.packet(g.DRAW_LINE, b""))                   # 0
    s.send(g.nop(full))                                  # inside a NOP
    s.send(g.packet(0x87, full))                         # inside an unknown packet
    s.send(g.fill_rect(20, 150, 120, 200, 0x31))
    s.line(20, 150, 120, 200, 0x32)                      # over the rectangle
    s.send(g.plot(70, 175, 0x33))                        # on the line
    s.line(70, 160, 70, 190, 0x34)                       # over the plot
    s.send(g.set_palette(0x23, 0xA1B2C3))
    s.send(g.fill_rect(125, 150, 125, 150, 0x35))
    s.crc("DRAW_LINE with lengths 8, 10, 0 and inside NOP/unknown packets (must not draw), mixed with FILL_RECT, PLOT, SET_PALETTE")
    s.fence()

    # 4. Throughput: one line per window. The testbench measures the clocks
    #    from its first write to its last against the 3NNN limit.
    windows = [("shallow, corner to corner", (0, 0, 319, 239)),
               ("steep, drawn upward and left", (300, 239, 200, 0)),
               ("horizontal, drawn right to left", (319, 60, 0, 60)),
               ("starting 1000 pixels off screen", (-1000, 30, 319, 30))]
    for i, (name, ln) in enumerate(windows):
        n = visible(*ln)
        s.directive(WIN_START)
        s.directive(WIN_LIMIT + n + WIN_SLACK)
        s.line(*ln, 0x40 + i)
        s.crc(f"throughput window {i + 1}: {name} ({n} pixels)")
        s.fence()
        s.directive(WIN_END)

    # 5. Back-pressure without flow control: a 65,536-point line (328 byte
    #    times at the test baud), then 30 short lines and an FB_CRC sent
    #    straight behind it. The engine must not fetch while it steps.
    s.directive(IGNORE_READY)
    s.line(-32768, -32768, 32767, 32767, 0x48)
    for _ in range(30):
        x, y = rng.randrange(300), rng.randrange(220)
        s.line(x, y, x + rng.randrange(-15, 16), y + rng.randrange(-15, 16), rng.randrange(256))
    s.crc("back-pressure: a 65,536-point DRAW_LINE with 30 short ones sent right behind it")
    s.fence()
    s.directive(HONOR_READY)

    # 6. A starburst: 70 rays from the centre to the border, every slope.
    s.send(g.clear(0x01))
    for i, ln in enumerate(starburst()):
        s.line(*ln, 0x50 + (i & 15))
    s.crc("a starburst after CLEAR: 70 rays from (160, 120) to every 16th border pixel")
    s.fence()

    # 7. 500 random lines, an FB_CRC every 50, older commands mixed in.
    for i in range(N_RANDOM):
        kind = rng.choice(KINDS)
        x0, y0, x1, y1 = random_line(rng, kind)
        pts = list(g.line_points(x0, y0, x1, y1))
        if len(pts) > 2000:          # keep the run short: only section 2 and 5 step far
            x1, y1 = x0 + (x1 - x0) // 64, y0 + (y1 - y0) // 64
            pts = list(g.line_points(x0, y0, x1, y1))
        n = sum(on_screen(x, y) for x, y in pts)
        stats["steps"] += len(pts)
        stats["tie"] += has_tie(x0, y0, x1, y1)
        stats["steep"] += abs(y1 - y0) > abs(x1 - x0)
        stats["offscreen"] += n == 0
        stats["partial"] += 0 < n < len(pts)
        stats["point"] += x0 == x1 and y0 == y1
        s.line(x0, y0, x1, y1, rng.randrange(256))
        if i % 50 == 17:
            s.send(g.plot(rng.randrange(-5, 325), rng.randrange(-5, 245), rng.randrange(256)))
        if i % 60 == 29:
            x, y = rng.randrange(-20, 320), rng.randrange(-20, 240)
            s.send(g.fill_rect(x, y, x + rng.randrange(40), y + rng.randrange(30), rng.randrange(256)))
        if i % 71 == 3:
            s.send(g.packet(0x80 + rng.randrange(128), bytes(rng.randrange(256) for _ in range(rng.randrange(12)))))
        if i == 250:
            s.send(g.set_palette(0x24, 0x2468AC))
        if i % GROUP == GROUP - 1:
            s.crc(f"random lines {i - GROUP + 2}-{i + 1}")
    s.fence()
    s.directive(END)
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
            f.write(f"// {comment} (made by sim/py/ref_line.py)\n")
            f.write("\n".join(lines) + "\n")

    write("line_stream.hex", [f"{v:04x}" for v in s.items],
          "tb_gpu_line stream: 00NN byte, 1NNN fence, 2000/2001 ignore/honor o_ready, "
          "2002/2003 window start/end, 3NNN window limit, FFFF end")
    write("line_replies.hex", [f"{v:02x}" for v in s.replies], "expected reply bytes, in order")
    write("line_fb.hex", [f"{v:02x}" for v in model.fb], "expected framebuffer, address y*320+x")
    write("line_pal.hex", [f"{v:06x}" for v in model.pal], "expected palette, RRGGBB")
    with open(os.path.join(outdir, "line_labels.txt"), "w") as f:
        f.write("".join(f"{lab[:120]}\n" for lab in s.labels))
    print(f"ref_line: {s.nbytes} bytes, {s.lines} DRAW_LINEs ({model.line_points} points, "
          f"{model.line_pixels} on screen), {len(s.replies) // 4} FB_CRC replies -> {outdir}/line_*")
    print(f"  random: {N_RANDOM} ({stats['tie']} with a tie, {stats['steep']} steep, {stats['point']} single points, "
          f"{stats['partial']} partly and {stats['offscreen']} entirely off screen)")


if __name__ == "__main__":
    main()
