#!/usr/bin/env python3
"""ref_flip_sync.py -- test stream and expected results for tb_flip_sync (Chapter 4.5).

Usage (from the repo root, once; commit the four files it writes):
    .venv/bin/python sim/py/ref_flip_sync.py sim

Writes:
    sim/flipsync_stream.hex   what the testbench sends, one entry per line:
                                00NN  a byte to send over the UART
                                1NNN  fence: wait until NNN (hex) reply bytes
                                      have arrived in all
                                2NNN  wait for the FLIP request, then hold
                                      i_rst high for NNN clocks
                                5NNN  time the next FLIP so that its request
                                      toggles at the start of beam line NNN
                                6NNN  time the next FLIP so that its request
                                      toggles NNN - 1024 pixel clocks after
                                      the start of line 480 (the start of
                                      vertical blanking)
                                FFFF  end of the stream
    sim/flipsync_replies.hex  every reply byte the GPU must send, in order
    sim/flipsync_labels.txt   one line per FB_CRC reply: what it covers
    sim/flipsync_sigs.hex     one 32-bit signature per picture: line k is the
                              signature of the picture on the screen after k
                              FLIPs (line 0: the power-up picture)

A picture's signature is what the testbench computes from the pixels that
leave the scanout pipeline in one frame: the sum, over the 307,200 visible
pixels numbered p = 0, 1, 2, ... in scan order, of (24-bit color) * (2p + 1),
modulo 2**32. A frame made of two pictures matches none of the signatures.

Both buffers start as Chapter 4.1's test image. The palette is never changed,
so a picture's colors depend only on the indices drawn into its buffer.
"""
import os
import random
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model

import numpy as np  # noqa: E402

import gpu_model as g  # noqa: E402
from ref_scanout import image, palette  # noqa: E402

SEED = 4545
# Part 1: FLIP requests at these beam lines (None: as soon as the engine is
# free). Lines 500 and 524 are in vertical blanking, after the start-of-blanking
# swap point. MALFORMED: the FLIPs that get two malformed FLIPs in front.
BEAM_LINES = [None, 240, 479, 500, 524]
MALFORMED = (2, 4)
RESET_AT = 3             # this FLIP gets an i_rst pulse while it waits for the swap
# Part 2: request toggles timed this many pixel clocks from the start of line
# 480. The testbench measures how long your design takes from the first byte
# of FLIP to the toggle, so these land within a clock of the target. With a
# start-of-blanking swap point the request makes it up to about -2 (the
# synchronizer's two clocks) and misses it from there on.
RACE_OFFSETS = [-30, -3, -2, -1, 0]
RESET, LINE, BEAM, END = 0x2000, 0x5000, 0x6000, 0xFFFF
BEAM_BASE = 1024         # 6NNN means NNN - BEAM_BASE pixel clocks after the start of line 480
FB_W = 640 * 480


def signature(fb, pal):
    """The testbench's signature of a frame showing buffer `fb`."""
    rgb = pal[np.frombuffer(bytes(fb), dtype=np.uint8).reshape(g.H, g.W)].astype(np.uint64)
    rgb = (rgb[..., 0] << np.uint64(16)) | (rgb[..., 1] << np.uint64(8)) | rgb[..., 2]
    big = rgb.repeat(2, axis=0).repeat(2, axis=1).reshape(-1)
    weights = np.arange(FB_W, dtype=np.uint64) * np.uint64(2) + np.uint64(1)
    return int((big * weights).sum() & np.uint64(0xFFFFFFFF))


class Stream:
    def __init__(self, model):
        self.model = model
        self.items = []
        self.replies = bytearray()
        self.labels = []
        self.sigs = []

    def send(self, data):
        self.items.extend(data)
        before = self.model.flips
        self.replies += self.model.feed(data)
        if self.model.flips != before:
            self.sigs.append(signature(self.model.front_fb, self.pal))

    def crc(self, label):
        self.send(g.fb_crc())
        self.labels.append(label)
        self.items.append(0x1000 + len(self.replies))     # fence

    def line(self, y):
        self.items.append(LINE + y)

    def beam(self, offset):
        self.items.append(BEAM + BEAM_BASE + offset)


def draw_picture(s, rng, k):
    """Draw picture k into the back buffer (a different one every time)."""
    style = k % 3
    if style == 0:
        s.send(g.clear(0x20 + k))
    elif style == 1:
        s.send(g.fill_rect(0, 0, 319, 239, 0x10 + 5 * k))
    # style 2: draw over the picture that was on the screen two FLIPs ago
    x, y = rng.randrange(0, 250), rng.randrange(0, 180)
    s.send(g.fill_rect(x, y, x + rng.randrange(20, 70), y + rng.randrange(20, 60), 0x80 + k))
    s.send(g.draw_line(rng.randrange(320), rng.randrange(240), rng.randrange(320), rng.randrange(240), 0xC0 + k))
    s.send(g.fill_rect(300 - 3 * k, 5, 301 - 3 * k, 235, 0xF0 - k))   # a vertical bar: a mid-frame swap shears it


def after_flip(s, rng, k):
    """Commands queued right behind a FLIP: they must wait for the swap and
    then land in the new back buffer, which the FB_CRC covers."""
    s.send(g.plot(rng.randrange(320), rng.randrange(240), 0x55))
    s.send(g.fill_rect(10, 10, 12, 12, 0x66))
    s.crc(f"flip {k}: the new back buffer (the old picture) with two commands drawn after the FLIP")


def build(model, pal):
    rng = random.Random(SEED)
    s = Stream(model)
    s.pal = pal
    s.sigs.append(signature(model.front_fb, pal))
    # FB_CRC of the untouched back buffer.
    s.crc("the power-up back buffer (the 4.1 test image)")
    k = 0
    # Part 1: FLIPs requested at different places in the frame.
    for y in BEAM_LINES:
        k += 1
        draw_picture(s, rng, k)
        where = "as soon as the engine is free" if y is None else f"request at line {y}"
        s.crc(f"picture {k} in the back buffer, before its FLIP ({where})")
        if k in MALFORMED:
            s.send(g.packet(g.FLIP, b"\x00"))          # wrong lengths: ignored, no swap, no stall
            s.send(g.packet(g.FLIP, b"\x01\x02"))
            s.send(g.plot(rng.randrange(320), rng.randrange(240), 0x77))
            s.crc("after two malformed FLIPs (length 1 and 2): nothing flipped, the PLOT went to the same back buffer")
        if y is not None:
            s.line(y)
        s.send(g.flip())
        if k == RESET_AT:
            s.items.append(RESET + 10)                  # the engine must still wait for the swap
        after_flip(s, rng, k)
    # Part 2: FLIPs whose request toggles within a few pixel clocks of the
    # start of vertical blanking, before and after it.
    for off in RACE_OFFSETS:
        k += 1
        draw_picture(s, rng, k)
        s.crc(f"picture {k} in the back buffer, before its FLIP (request {off:+d} pixel clocks from the start of blanking)")
        s.beam(off)
        s.send(g.flip())
        after_flip(s, rng, k)
    s.items.append(END)
    return s, k


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR    (normally: sim)")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    pal = palette()
    pal0 = [(int(r) << 16) | (int(gg) << 8) | int(b) for r, gg, b in pal]
    model = g.Gpu(fb=image().reshape(-1).tobytes(), pal=pal0)
    s, k = build(model, pal)
    assert len(set(s.sigs)) == len(s.sigs) == k + 1, "pictures must all differ"

    def write(name, lines, comment):
        with open(os.path.join(outdir, name), "w") as f:
            f.write(f"// {comment} (made by sim/py/ref_flip_sync.py)\n")
            f.write("\n".join(lines) + "\n")

    nbytes = sum(1 for v in s.items if v < 0x100)
    write("flipsync_stream.hex", [f"{v:04x}" for v in s.items],
          "tb_flip_sync stream: 00NN byte, 1NNN fence, 2NNN reset during FLIP, 5NNN FLIP at line NNN, 6NNN FLIP at line 480 + NNN-1024 clocks, FFFF end")
    write("flipsync_replies.hex", [f"{v:02x}" for v in s.replies], "expected reply bytes, in order")
    write("flipsync_sigs.hex", [f"{v:08x}" for v in s.sigs], "picture signature after k FLIPs, k = 0, 1, 2, ...")
    with open(os.path.join(outdir, "flipsync_labels.txt"), "w") as f:
        f.write("".join(f"{lab[:120]}\n" for lab in s.labels))
    print(f"ref_flip_sync: {nbytes} bytes, {k} FLIPs ({len(BEAM_LINES)} across the frame, {len(RACE_OFFSETS)} around the start of blanking), "
          f"{len(s.replies) // 4} FB_CRC replies -> {outdir}/flipsync_*")


if __name__ == "__main__":
    main()
