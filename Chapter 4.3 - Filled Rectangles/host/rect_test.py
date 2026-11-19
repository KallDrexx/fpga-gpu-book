#!/usr/bin/env python3
"""rect_test.py -- Chapter 4.3 hardware check: random FILL_RECTs, then FB_CRC.

Usage:
    .venv/bin/python host/rect_test.py PORT [--count 500] [--seed N]
                                            [--baud 115200] [--rtscts]

PORT is the USB-serial adapter on Pmod JA (e.g. /dev/ttyUSB0). The script

  1. sets a few palette entries and clears the screen to entry 0,
  2. sends COUNT random FILL_RECTs: on screen, partly and wholly off screen,
     with negative, zero-size and inverted corners, and an FB_CRC after
     every 100 of them and at the end,
  3. compares every FB_CRC reply with host/gpu_model.py's CRC at the same
     point, and names the first block of 100 that differs.

--rtscts turns on hardware flow control, for an adapter whose CTS input is
wired to the board's uart_cts pin. This test doesn't need it.
Exit status 0 on PASS, 1 on FAIL.
"""
import argparse
import os
import random
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import serial  # noqa: E402  (pyserial)

import gpu_model as g  # noqa: E402

BLOCK = 100          # rectangles per FB_CRC


def random_rect(rng):
    """One random rectangle; returns (x0, y0, x1, y1, kind)."""
    kind = rng.choice(["small"] * 4 + ["large", "offscreen", "offscreen", "zero", "extreme"])
    if kind == "small":
        x0 = rng.randrange(-20, 330); x1 = x0 + rng.randrange(41)
        y0 = rng.randrange(-20, 250); y1 = y0 + rng.randrange(31)
    elif kind == "large":
        x0, x1 = rng.randrange(-80, 400), rng.randrange(-80, 400)
        y0, y1 = rng.randrange(-60, 300), rng.randrange(-60, 300)
    elif kind == "offscreen":
        x0, x1 = rng.randrange(-100, 400), rng.randrange(-100, 400)
        y0, y1 = rng.randrange(-80, 300), rng.randrange(-80, 300)
        side = rng.randrange(4)
        if side == 0:
            x0, x1 = rng.randrange(-32768, 0), rng.randrange(-400, 0)
        elif side == 1:
            x0, x1 = rng.randrange(320, 32768), rng.randrange(320, 700)
        elif side == 2:
            y0, y1 = rng.randrange(-32768, 0), rng.randrange(-300, 0)
        else:
            y0, y1 = rng.randrange(240, 32768), rng.randrange(240, 600)
    elif kind == "zero":
        x0 = rng.randrange(-10, 330); y0 = rng.randrange(-10, 250)
        x1 = x0 if rng.random() < 0.7 else x0 + rng.randrange(-60, 61)
        y1 = y0 if x1 != x0 or rng.random() < 0.5 else y0 + rng.randrange(-60, 61)
    else:  # extreme: values at and around the screen edges and the int16 limits
        special = (-32768, -1, 0, 319, 320, 239, 240, 511, 522, 32767)
        x0, x1, y0, y1 = (rng.choice(special) if rng.random() < 0.6 else rng.randrange(-40, 360)
                          for _ in range(4))
    if rng.random() < 0.5:
        x0, x1 = x1, x0
    if rng.random() < 0.5:
        y0, y1 = y1, y0
    return x0, y0, x1, y1, kind


def drawing(count, seed):
    """The test's packets as one byte string, the number of FB_CRCs in it,
    and some statistics."""
    rng = random.Random(seed)
    out = bytearray()
    for i, rgb in enumerate((0x101828, 0xE04040, 0x40E040, 0x4040E0, 0xE0E040, 0x40E0E0, 0xE040E0, 0xF0F0F0)):
        out += g.set_palette(i, rgb)
    out += g.clear(0)
    stats = {"inverted": 0, "negative": 0, "zero": 0, "offscreen": 0}
    ncrc = 0
    for i in range(count):
        x0, y0, x1, y1, kind = random_rect(rng)
        stats["inverted"] += x0 > x1 or y0 > y1
        stats["negative"] += min(x0, y0, x1, y1) < 0
        stats["zero"] += x0 == x1 or y0 == y1
        stats["offscreen"] += g.clip_rect(x0, y0, x1, y1) is None
        out += g.fill_rect(x0, y0, x1, y1, rng.randrange(1, 256) if rng.random() < 0.3 else rng.randrange(1, 8))
        if i % BLOCK == BLOCK - 1 or i == count - 1:
            out += g.fb_crc()
            ncrc += 1
    return bytes(out), ncrc, stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--count", type=int, default=500)
    ap.add_argument("--seed", type=int, default=None, help="default: a new seed each run")
    ap.add_argument("--rtscts", action="store_true", help="use the adapter's CTS input")
    args = ap.parse_args(argv)
    if args.count < 1:
        ap.error("--count must be at least 1")
    seed = args.seed if args.seed is not None else random.randrange(1 << 30)

    stream, ncrc, stats = drawing(args.count, seed)
    model = g.Gpu()                                # CLEAR makes its start state irrelevant
    expect = model.feed(stream)

    with serial.Serial(args.port, args.baud, timeout=2.0, rtscts=args.rtscts) as ser:
        ser.reset_input_buffer()
        t0 = time.time()
        ser.timeout = 2.0 + len(stream) * 10.0 / args.baud
        ser.write(stream)
        reply = ser.read(4 * ncrc)
        dt = time.time() - t0

    print(f"sent {len(stream)} bytes: {args.count} FILL_RECTs (seed {seed}; {stats['inverted']} inverted, "
          f"{stats['negative']} with a negative corner, {stats['zero']} zero width or height, "
          f"{stats['offscreen']} entirely off screen, {model.rect_pixels} pixels) in {dt:.2f} s")
    if len(reply) != 4 * ncrc:
        print(f"FAIL: rect_test: got {len(reply)} of {4 * ncrc} reply bytes. Check the wiring, the baud "
              f"rate and that the design is loaded; press BTN0 to reset the parser and try again.")
        return 1
    bad = None
    for k in range(ncrc):
        got = g.parse_crc_reply(reply[4 * k: 4 * k + 4])
        want = g.parse_crc_reply(expect[4 * k: 4 * k + 4])
        last = min((k + 1) * BLOCK, args.count)
        print(f"  after rectangle {last:4d}: hardware CRC {got:08x}, Python CRC {want:08x}"
              + ("" if got == want else "   <-- differs"))
        if got != want and bad is None:
            bad = (k * BLOCK + 1, last)
    if bad is None:
        print("PASS: rect_test")
        return 0
    print(f"FAIL: rect_test (first difference in rectangles {bad[0]}-{bad[1]}; "
          f"rerun with --seed {seed} to repeat this exact drawing)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
