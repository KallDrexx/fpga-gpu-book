#!/usr/bin/env python3
"""ref_cmd.py -- test stream and expected results for tb_uart_gpu (Chapter 4.2).

Usage (from the repo root, once; commit the four files it writes):
    .venv/bin/python sim/py/ref_cmd.py sim

Writes:
    sim/cmd_stream.hex   what the testbench sends, one entry per line:
                           00NN  a byte to send over the UART
                           1NNN  fence: wait until NNN (hex) reply bytes have
                                 arrived in all, the replies to every FB_CRC
                                 sent so far
                           2000  from here on, send without looking at o_ready
                           2001  from here on, wait for o_ready before each byte
                           FFFF  end of the stream
    sim/cmd_replies.hex  every reply byte the GPU must send, in order
    sim/cmd_fb.hex       the framebuffer at the end (76,800 lines, address order)
    sim/cmd_pal.hex      the palette at the end (256 lines, RRGGBB)

The framebuffer and palette start as Chapter 4.1's test image
(rtl/common/fb_test.hex, pal_test.hex), and the expected results come from
host/gpu_model.py, the same model the host scripts use.
"""
import os
import random
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model

import gpu_model as g  # noqa: E402
from ref_scanout import image, palette  # noqa: E402

IGNORE_READY, HONOR_READY, END = 0x2000, 0x2001, 0xFFFF


class Stream:
    """The testbench's stream, fed through the model as it is built."""

    def __init__(self, model):
        self.model = model
        self.items = []              # bytes and directives, in order
        self.replies = bytearray()   # what the GPU must answer, in order
        self.nbytes = 0

    def send(self, data):
        self.items.extend(data)
        self.replies += self.model.feed(data)
        self.nbytes += len(data)

    def directive(self, d):
        self.items.append(d)

    def fence(self):
        self.items.append(0x1000 + len(self.replies))


def build(model):
    rng = random.Random(4242)
    s = Stream(model)
    s.directive(HONOR_READY)

    # 1. The CRC of the preloaded 4.1 test image. Its pixels all differ, so
    #    this catches a traversal in the wrong order, not just a wrong CRC.
    s.send(g.fb_crc())
    s.fence()

    # 2. NOPs. A NOP's payload must be skipped: these hold whole packets that
    #    would plot or clear if the parser looked inside.
    s.send(g.nop())
    s.send(g.nop(g.plot(5, 6, 0xEE)))
    s.send(g.nop(g.clear(0x11)))
    s.send(g.nop(bytes(range(200))))

    # 3. Palette writes. R, G, B all different so a swapped byte order shows.
    for i, rgb in ((0, 0x123456), (1, 0xA1B2C3), (128, 0x0F1E2D), (255, 0xFEDCBA), (17, 0x00FF80)):
        s.send(g.set_palette(i, rgb))

    # 4. Unknown opcodes, with payloads that hide a valid packet.
    s.send(g.packet(0x80, g.plot(7, 8, 0xEE)))
    s.send(g.packet(0xFF, b""))
    s.send(g.packet(0xC3, g.clear(0x22)))
    s.send(g.packet(0xEE, g.fb_crc() + g.fb_crc()))

    # 5. Known opcodes with the wrong length: read to the end, then ignored.
    s.send(g.packet(g.PLOT, g.plot(9, 9, 0xEE)[2:6]))            # 4 bytes, not 5
    s.send(g.packet(g.PLOT, g.plot(9, 9, 0xEE)[2:] + b"\x03"))   # 6 bytes
    s.send(g.packet(g.CLEAR, b""))                                # 0, not 1
    s.send(g.packet(g.CLEAR, b"\x33\x44"))                        # 2
    s.send(g.packet(g.FB_CRC, b"\x00"))                           # must not reply
    s.send(g.packet(g.SET_PALETTE, b"\x05\xAA\xBB"))              # 3, not 4
    s.send(g.packet(g.SET_PALETTE, b"\x05\xAA\xBB\xCC\xDD"))      # 5

    # 6. PLOT: corners, near-corners, and off-screen points (all ignored).
    #    x and y differ everywhere, so swapping them shows.
    for (x, y, c) in ((0, 0, 0xE0), (319, 0, 0xE1), (0, 239, 0xE2), (319, 239, 0xE3),
                      (1, 0, 0xE4), (0, 1, 0xE5), (318, 239, 0xE6), (319, 238, 0xE7),
                      (100, 37, 0xE8), (37, 100, 0xE9), (200, 3, 0xEA), (160, 120, 0xEB)):
        s.send(g.plot(x, y, c))
    for (x, y) in ((320, 0), (-1, 0), (0, 240), (0, -1), (-1, -1), (320, 240),
                   (330, 10), (10, 250), (512 + 10, 20), (20, 256 + 20), (1024 + 5, 5),
                   (5, 512 + 5), (-320, 5), (5, -240), (-32768, 10), (10, -32768),
                   (32767, 10), (10, 32767), (65536 // 2 - 1, -1), (-1, 239), (319, -1)):
        s.send(g.plot(x, y, 0xEE))
    s.send(g.fb_crc())
    s.fence()

    # 7. Back-pressure without flow control. The FIFO is empty (the fence
    #    saw the last reply). A CLEAR takes 76,800 clocks, about 384 byte
    #    times at the test baud, while ~470 more bytes arrive without the
    #    testbench looking at o_ready. A 512-byte FIFO holds them all.
    s.directive(IGNORE_READY)
    s.send(g.clear(0x5A))
    for _ in range(66):
        s.send(g.plot(rng.randrange(g.W), rng.randrange(g.H), rng.randrange(256)))
    s.send(g.fb_crc())
    # ...and the same behind an FB_CRC: the plots after it must not change
    # the CRC it reports.
    for _ in range(60):
        s.send(g.plot(rng.randrange(g.W), rng.randrange(g.H), rng.randrange(256)))
    s.send(g.fb_crc())
    s.fence()

    # 8. Back-pressure with flow control: four CLEARs (300,000+ clocks) and
    #    then a long stream, sent as fast as o_ready allows.
    s.directive(HONOR_READY)
    for c in (0x01, 0x02, 0x03, 0x00):
        s.send(g.clear(c))
    for i in range(1000):
        if rng.random() < 0.1:
            x, y = rng.choice(((rng.randrange(-400, 0), rng.randrange(-50, 300)),
                               (rng.randrange(320, 700), rng.randrange(-50, 300)),
                               (rng.randrange(-50, 400), rng.randrange(-300, 0)),
                               (rng.randrange(-50, 400), rng.randrange(240, 600))))
        else:
            x, y = rng.randrange(g.W), rng.randrange(g.H)
        s.send(g.plot(x, y, rng.randrange(256)))
        if i % 97 == 0:
            s.send(g.nop(bytes([g.PLOT, 5])))
        if i % 131 == 0:
            s.send(g.packet(0x80 + rng.randrange(128), bytes(rng.randrange(256) for _ in range(rng.randrange(12)))))
        if i == 500:
            s.send(g.set_palette(200, 0x405060))
            s.send(g.fb_crc())
    s.send(g.fb_crc())
    s.fence()
    s.directive(END)
    return s


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR    (normally: sim)")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    pal0 = [(int(r) << 16) | (int(gg) << 8) | int(b) for r, gg, b in palette()]
    model = g.Gpu(fb=image().reshape(-1).tobytes(), pal=pal0)
    s = build(model)
    replies = s.replies

    def write(name, lines, comment):
        with open(os.path.join(outdir, name), "w") as f:
            f.write(f"// {comment} (made by sim/py/ref_cmd.py)\n")
            f.write("\n".join(lines) + "\n")

    write("cmd_stream.hex", [f"{v:04x}" for v in s.items],
          "tb_uart_gpu stream: 00NN byte, 1NNN fence (NNN reply bytes), 2000 ignore o_ready, 2001 honor o_ready, FFFF end")
    write("cmd_replies.hex", [f"{v:02x}" for v in replies], "expected reply bytes, in order")
    write("cmd_fb.hex", [f"{v:02x}" for v in model.fb], "expected framebuffer, address y*320+x")
    write("cmd_pal.hex", [f"{v:06x}" for v in model.pal], "expected palette, RRGGBB")
    print(f"ref_cmd: {s.nbytes} bytes, {len(replies) // 4} FB_CRC replies, "
          f"{model.ignored} packets ignored -> {outdir}/cmd_*.hex")


if __name__ == "__main__":
    main()
