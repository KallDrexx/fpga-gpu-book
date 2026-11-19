#!/usr/bin/env python3
"""ref_rect.py -- test stream and expected results for tb_gpu_rect (Chapter 4.3).

Usage (from the repo root, once; commit the five files it writes):
    .venv/bin/python sim/py/ref_rect.py sim

Writes:
    sim/rect_stream.hex   what the testbench sends, one entry per line, in
                          Chapter 4.2's format plus two new directives:
                            00NN  a byte to send over the UART
                            1NNN  fence: wait until NNN (hex) reply bytes
                                  have arrived in all
                            2000  from here on, send without looking at o_ready
                            2001  from here on, wait for o_ready before each byte
                            2002  start the throughput window: from here,
                                  record the first and last framebuffer write
                            2003  end the throughput window
                            FFFF  end of the stream
    sim/rect_replies.hex  every reply byte the GPU must send, in order
    sim/rect_labels.txt   one line per FB_CRC reply: what it covers, so the
                          testbench can say which group of rectangles failed
    sim/rect_fb.hex       the framebuffer at the end (76,800 lines)
    sim/rect_pal.hex      the palette at the end (256 lines, RRGGBB)

The framebuffer and palette start as Chapter 4.1's test image, as in
ref_cmd.py, and the expected results come from host/gpu_model.py.
"""
import os
import random
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model

import gpu_model as g  # noqa: E402
from ref_scanout import image, palette  # noqa: E402

IGNORE_READY, HONOR_READY, WIN_START, WIN_END, END = 0x2000, 0x2001, 0x2002, 0x2003, 0xFFFF
SEED = 4343
N_RANDOM = 500
GROUP = 50          # random rectangles per FB_CRC


class Stream:
    """The testbench's stream, fed through the model as it is built."""

    def __init__(self, model):
        self.model = model
        self.items = []
        self.replies = bytearray()
        self.labels = []
        self.nbytes = 0
        self.rects = 0

    def send(self, data):
        self.items.extend(data)
        self.replies += self.model.feed(data)
        self.nbytes += len(data)

    def rect(self, x0, y0, x1, y1, c):
        self.send(g.fill_rect(x0, y0, x1, y1, c))
        self.rects += 1

    def crc(self, label):
        self.send(g.fb_crc())
        self.labels.append(label)

    def directive(self, d):
        self.items.append(d)

    def fence(self):
        self.items.append(0x1000 + len(self.replies))


# Edge cases, in groups; each group ends with an FB_CRC. They draw on the
# 4.1 test image (every pixel different), so a pixel written in the wrong
# place always shows. Colors differ from rectangle to rectangle.
EDGE_GROUPS = [
    ("single pixels at the four corners and the centre", [
        (0, 0, 0, 0), (319, 0, 319, 0), (0, 239, 0, 239), (319, 239, 319, 239), (160, 120, 160, 120)]),
    ("small rectangles, corners in all four orders", [
        (10, 20, 13, 22), (33, 22, 30, 20), (50, 42, 53, 40), (73, 40, 70, 42)]),
    ("lines: one pixel wide or high (x0 == x1, y0 == y1)", [
        (100, 5, 100, 60), (110, 60, 110, 5), (5, 100, 90, 100), (90, 110, 5, 110)]),
    ("rectangles touching the right and bottom edges (x1 = 319, y1 = 239)", [
        (300, 50, 319, 52), (250, 237, 260, 239), (318, 236, 319, 239)]),
    ("clipped on the right and bottom: x1 = 320, 330, 32767; y1 = 240, 300", [
        (305, 60, 320, 61), (305, 64, 330, 65), (305, 68, 32767, 69), (200, 230, 210, 240), (215, 235, 225, 300),
        (300, 236, 400, 260)]),
    ("clipped on the left and top: x0 = -1, -20, -32768; y0 = -1, -50", [
        (-1, 80, 3, 81), (-20, 84, 2, 85), (-32768, 88, 1, 89), (120, -1, 122, 2), (126, -50, 128, 1),
        (-10, -10, 4, 4)]),
    ("inverted and clipped", [
        (330, 140, 310, 142), (4, 150, -30, 152), (200, 10, 198, -40), (210, 250, 212, 235)]),
    ("touching an edge from outside, all off screen (nothing drawn)", [
        (320, 0, 320, 239), (-1, 0, -1, 239), (0, 240, 319, 240), (0, -1, 319, -1), (-1, -1, -1, -1),
        (320, 240, 320, 240), (-5, 50, -1, 60), (320, 50, 400, 60), (50, -40, 60, -1), (50, 240, 60, 300)]),
    ("far off screen, and low bits that alias onto it (nothing drawn)", [
        (522, 10, 530, 20), (10, 256 + 10, 20, 256 + 20), (-1000, -1000, -500, -500), (1000, 1000, 2000, 2000),
        (-32768, -32768, -32768, -32768), (32767, 32767, 32767, 32767), (-32768, 0, -1, 239),
        (320, 0, 32767, 239), (0, -32768, 319, -1), (0, 240, 319, 32767), (-640, 10, -321, 20),
        (64 * 1024 // 2 - 2, 5, 32767, 9)]),
    ("spanning the whole screen in one axis", [
        (-5, 170, 330, 171), (240, -5, 241, 250), (-32768, 180, 32767, 180), (250, -32768, 250, 32767)]),
    ("full screen: exactly, inverted, and from -32768 to 32767", [
        (0, 0, 319, 239), (319, 239, 0, 0), (-32768, -32768, 32767, 32767), (32767, 32767, -32768, -32768),
        (-1, -1, 320, 240)]),
]


def random_rect(rng, kind):
    """One random rectangle of the given kind, corners in random order."""
    def span(lo, hi, size):
        a = rng.randrange(lo, hi)
        return a, a + rng.randrange(size + 1)

    if kind == "small":
        x0, x1 = span(-20, 330, 40)
        y0, y1 = span(-20, 250, 30)
    elif kind == "large":
        x0, x1 = rng.randrange(-80, 400), rng.randrange(-80, 400)
        y0, y1 = rng.randrange(-60, 300), rng.randrange(-60, 300)
    elif kind == "offscreen":
        side = rng.randrange(4)
        x0, x1 = span(-100, 400, 80)
        y0, y1 = span(-80, 300, 60)
        if side == 0:
            x0, x1 = span(-400, -1, 300)
            x1 = min(x1, -1)
        elif side == 1:
            x0, x1 = span(320, 700, 300)
        elif side == 2:
            y0, y1 = span(-300, -1, 250)
            y1 = min(y1, -1)
        else:
            y0, y1 = span(240, 600, 300)
    elif kind == "zero":
        x0, x1 = span(-10, 330, 60)
        y0, y1 = span(-10, 250, 60)
        which = rng.randrange(3)
        if which != 1:
            x1 = x0
        if which != 0:
            y1 = y0
    else:  # "extreme": special values and wide ranges, signed corner cases
        special = (-32768, -32767, -1000, -321, -320, -1, 0, 1, 318, 319, 320, 321, 511, 512, 522,
                   639, 640, 1000, 32766, 32767)
        def coord(lim):
            return rng.choice(special) if rng.random() < 0.6 else rng.randrange(-40, lim + 40)
        x0, x1 = coord(320), coord(320)
        y0, y1 = coord(240), coord(240)
    if rng.random() < 0.5:
        x0, x1 = x1, x0
    if rng.random() < 0.5:
        y0, y1 = y1, y0
    return x0, y0, x1, y1


KINDS = ["small"] * 45 + ["large"] * 10 + ["offscreen"] * 20 + ["zero"] * 15 + ["extreme"] * 10


def build(model):
    rng = random.Random(SEED)
    s = Stream(model)
    stats = {"inverted": 0, "offscreen": 0, "negative": 0, "zero": 0, "clipped": 0}
    s.directive(HONOR_READY)

    # 1. The untouched 4.1 test image (checks the CRC unit on its own).
    s.crc("the 4.1 test image before any command (FB_CRC itself)")
    s.fence()

    # 2. Edge cases on top of the test image.
    color = 0x10
    for label, rects in EDGE_GROUPS:
        for r in rects:
            s.rect(*r, color)
            color += 1
        s.crc("edge cases: " + label)
    s.fence()

    # 3. Malformed and hidden FILL_RECTs: none of these may draw.
    full = g.fill_rect(0, 0, 319, 239, 0xEE)
    s.send(g.packet(g.FILL_RECT, full[2:10]))            # 8 payload bytes
    s.send(g.packet(g.FILL_RECT, full[2:] + b"\x00"))    # 10
    s.send(g.packet(g.FILL_RECT, b""))                   # 0
    s.send(g.nop(full))                                  # inside a NOP
    s.send(g.packet(0x85, full))                         # inside an unknown packet
    s.send(g.packet(0x86, b""))
    # ...and the older commands still work around it.
    s.send(g.set_palette(0x21, 0x13579B))
    s.send(g.plot(7, 9, 0xD1))
    s.send(g.plot(-1, 9, 0xD2))
    s.rect(7, 9, 7, 9, 0xD3)                             # over the plot
    s.send(g.plot(8, 9, 0xD4))                           # next to it
    s.crc("FILL_RECT with lengths 8, 10, 0 and inside NOP/unknown packets (must not draw), mixed with PLOT and SET_PALETTE")
    s.fence()

    # 4. Throughput: a full-screen FILL_RECT alone in a window. The
    #    testbench measures the clocks from its first write to its last.
    s.directive(WIN_START)
    s.rect(0, 0, 319, 239, 0x44)
    s.crc("throughput window: a full-screen FILL_RECT")
    s.fence()
    s.directive(WIN_END)

    # 5. Back-pressure without flow control: a full-screen fill (384 byte
    #    times at the test baud), then 34 small rectangles and an FB_CRC sent
    #    straight behind it. The engine must not fetch while it fills.
    s.directive(IGNORE_READY)
    s.rect(-50, -50, 400, 300, 0x45)
    for _ in range(34):
        x, y = rng.randrange(310), rng.randrange(230)
        s.rect(x, y, x + rng.randrange(10), y + rng.randrange(10), rng.randrange(256))
    s.crc("back-pressure: full-screen FILL_RECT with 34 small ones sent right behind it")
    s.fence()
    s.directive(HONOR_READY)

    # 6. 500 random rectangles, with an FB_CRC every 50, and the older
    #    commands mixed in now and then.
    for i in range(N_RANDOM):
        kind = rng.choice(KINDS)
        x0, y0, x1, y1 = random_rect(rng, kind)
        if x0 > x1 or y0 > y1:
            stats["inverted"] += 1
        if min(x0, x1, y0, y1) < 0:
            stats["negative"] += 1
        if x0 == x1 or y0 == y1:
            stats["zero"] += 1
        r = g.clip_rect(x0, y0, x1, y1)
        if r is None:
            stats["offscreen"] += 1
        elif r != (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)):
            stats["clipped"] += 1
        s.rect(x0, y0, x1, y1, rng.randrange(256))
        if i % 50 == 17:
            s.send(g.plot(rng.randrange(-5, 325), rng.randrange(-5, 245), rng.randrange(256)))
        if i % 71 == 3:
            s.send(g.packet(0x80 + rng.randrange(128), bytes(rng.randrange(256) for _ in range(rng.randrange(12)))))
        if i == 250:
            s.send(g.clear(0x07))
            s.send(g.set_palette(0x22, 0x2468AC))
        if i % GROUP == GROUP - 1:
            s.crc(f"random rectangles {i - GROUP + 2}-{i + 1}" + (" (and a CLEAR after 251)" if i - GROUP + 1 <= 250 <= i else ""))
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
            f.write(f"// {comment} (made by sim/py/ref_rect.py)\n")
            f.write("\n".join(lines) + "\n")

    write("rect_stream.hex", [f"{v:04x}" for v in s.items],
          "tb_gpu_rect stream: 00NN byte, 1NNN fence, 2000/2001 ignore/honor o_ready, 2002/2003 throughput window, FFFF end")
    write("rect_replies.hex", [f"{v:02x}" for v in s.replies], "expected reply bytes, in order")
    write("rect_fb.hex", [f"{v:02x}" for v in model.fb], "expected framebuffer, address y*320+x")
    write("rect_pal.hex", [f"{v:06x}" for v in model.pal], "expected palette, RRGGBB")
    with open(os.path.join(outdir, "rect_labels.txt"), "w") as f:
        f.write("".join(f"{lab[:120]}\n" for lab in s.labels))
    print(f"ref_rect: {s.nbytes} bytes, {s.rects} FILL_RECTs ({model.rect_pixels} pixels), "
          f"{len(s.replies) // 4} FB_CRC replies -> {outdir}/rect_*")
    print(f"  random: {N_RANDOM} ({stats['inverted']} inverted, {stats['negative']} with a negative corner, "
          f"{stats['zero']} zero width or height, {stats['offscreen']} entirely off screen, "
          f"{stats['clipped']} clipped)")


if __name__ == "__main__":
    main()
