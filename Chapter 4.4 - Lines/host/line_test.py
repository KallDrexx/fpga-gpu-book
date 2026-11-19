#!/usr/bin/env python3
"""line_test.py -- Chapter 4.4 hardware check: a starburst and random DRAW_LINEs, then FB_CRC.

Usage:
    .venv/bin/python host/line_test.py PORT [--count 500] [--seed N]
                                            [--baud 115200] [--rtscts] [--keep]

PORT is the USB-serial adapter on Pmod JA (e.g. /dev/ttyUSB0). The script

  1. sets a palette, clears the screen and draws a starburst: 70 rays from
     the centre to every 16th pixel of the border, one colour per octant,
     then an FB_CRC,
  2. unless --keep is given, sends COUNT random DRAW_LINEs: short and long,
     steep and shallow, with exact ties, partly and wholly off screen, and
     at the int16 limits, with an FB_CRC after every 100 of them,
  3. compares every FB_CRC reply with host/gpu_model.py's CRC at the same
     point, and names the first block that differs.

With --keep it stops after the starburst, so you can look at it.
--rtscts turns on hardware flow control, for an adapter whose CTS input is
wired to the board's uart_cts pin. This test doesn't need it.
Exit status 0 on PASS, 1 on FAIL.
"""
import argparse
import math
import os
import random
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import serial  # noqa: E402  (pyserial)

import gpu_model as g  # noqa: E402

BLOCK = 100          # random lines per FB_CRC
# Background, then one colour per octant of the starburst (counter-clockwise
# from +x on the screen, where y grows downward), then white.
PALETTE = (0x101828, 0xE04040, 0xE0A040, 0xE0E040, 0x40E040, 0x40E0E0, 0x4080E0, 0x8040E0, 0xE040E0, 0xF0F0F0)


def starburst():
    """(x0, y0, x1, y1, color) for rays from the centre to the border."""
    cx, cy = 160, 120
    ends = [(x, 0) for x in range(0, 320, 16)] + [(319, y) for y in range(0, 240, 16)] + \
           [(x, 239) for x in range(319, 0, -16)] + [(0, y) for y in range(239, 0, -16)]
    out = []
    for x, y in ends:
        angle = math.degrees(math.atan2(cy - y, x - cx)) % 360    # y up: counter-clockwise
        out.append((cx, cy, x, y, 1 + int(angle // 45)))
    return out


def random_line(rng):
    """One random line; returns (x0, y0, x1, y1)."""
    kind = rng.choice(["short"] * 4 + ["long", "long", "cross", "tie", "tie", "extreme"])
    if kind == "short":
        x0, y0 = rng.randrange(-10, 330), rng.randrange(-10, 250)
        x1, y1 = x0 + rng.randrange(-30, 31), y0 + rng.randrange(-30, 31)
    elif kind == "long":
        x0, y0, x1, y1 = rng.randrange(320), rng.randrange(240), rng.randrange(320), rng.randrange(240)
    elif kind == "cross":
        x0, y0, x1, y1 = rng.randrange(-400, 720), rng.randrange(-300, 540), rng.randrange(-400, 720), rng.randrange(-300, 540)
    elif kind == "tie":                       # |dx| = 2*odd*|dy| or the other way round
        k = rng.choice([1, 2, 3, 5, 10, 30])
        a, b = 2 * k * rng.choice([1, 3]), k
        if rng.random() < 0.5:
            a, b = b, a
        x0, y0 = rng.randrange(-20, 340), rng.randrange(-20, 260)
        x1, y1 = x0 + a * rng.choice([-1, 1]), y0 + b * rng.choice([-1, 1])
    else:                                     # int16 limits: long lines through the screen
        x0, y0 = rng.choice([(-32768, rng.randrange(-200, 440)), (32767, rng.randrange(-200, 440)),
                             (rng.randrange(-200, 520), -32768), (rng.randrange(-200, 520), 32767)])
        x1, y1 = 2 * 160 - x0 + rng.randrange(-100, 101), 2 * 120 - y0 + rng.randrange(-100, 101)
    clamp = lambda v: max(-32768, min(32767, v))  # noqa: E731
    return clamp(x0), clamp(y0), clamp(x1), clamp(y1)


def drawing(count, seed):
    """The test's packets as one byte string, the number of FB_CRCs in it,
    and some statistics."""
    rng = random.Random(seed)
    out = bytearray()
    for i, rgb in enumerate(PALETTE):
        out += g.set_palette(i, rgb)
    out += g.clear(0)
    for ln in starburst():
        out += g.draw_line(*ln)
    out += g.fb_crc()
    ncrc = 1
    stats = {"tie": 0, "steep": 0, "offscreen": 0, "points": 0}
    for i in range(count):
        x0, y0, x1, y1 = random_line(rng)
        pts = list(g.line_points(x0, y0, x1, y1))
        stats["points"] += len(pts)
        stats["steep"] += abs(y1 - y0) > abs(x1 - x0)
        stats["offscreen"] += not any(0 <= x < g.W and 0 <= y < g.H for x, y in pts)
        stats["tie"] += pts != list(g.line_points(x1, y1, x0, y0))[::-1]
        out += g.draw_line(x0, y0, x1, y1, rng.randrange(1, 256) if rng.random() < 0.3 else rng.randrange(1, 10))
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
    ap.add_argument("--keep", action="store_true", help="draw only the starburst")
    args = ap.parse_args(argv)
    if args.count < 1:
        ap.error("--count must be at least 1")
    count = 0 if args.keep else args.count
    seed = args.seed if args.seed is not None else random.randrange(1 << 30)

    stream, ncrc, stats = drawing(count, seed)
    model = g.Gpu()                                # CLEAR makes its start state irrelevant
    expect = model.feed(stream)

    with serial.Serial(args.port, args.baud, timeout=2.0, rtscts=args.rtscts) as ser:
        ser.reset_input_buffer()
        t0 = time.time()
        ser.timeout = 2.0 + len(stream) * 10.0 / args.baud
        ser.write(stream)
        reply = ser.read(4 * ncrc)
        dt = time.time() - t0

    print(f"sent {len(stream)} bytes: a starburst and {count} random DRAW_LINEs (seed {seed}; "
          f"{stats['tie']} with a tie, {stats['steep']} steep, {stats['offscreen']} entirely off screen, "
          f"{model.line_points} points stepped, {model.line_pixels} on screen) in {dt:.2f} s")
    if len(reply) != 4 * ncrc:
        print(f"FAIL: line_test: got {len(reply)} of {4 * ncrc} reply bytes. Check the wiring, the baud "
              f"rate and that the design is loaded; press BTN0 to reset the parser and try again.")
        return 1
    bad = None
    for k in range(ncrc):
        got = g.parse_crc_reply(reply[4 * k: 4 * k + 4])
        want = g.parse_crc_reply(expect[4 * k: 4 * k + 4])
        what = "after the starburst" if k == 0 else f"after line {min(k * BLOCK, count):4d}"
        print(f"  {what:<19}: hardware CRC {got:08x}, Python CRC {want:08x}" + ("" if got == want else "   <-- differs"))
        if got != want and bad is None:
            bad = "the starburst" if k == 0 else f"random lines {(k - 1) * BLOCK + 1}-{min(k * BLOCK, count)}"
    if bad is None:
        print("PASS: line_test")
        return 0
    print(f"FAIL: line_test (first difference in {bad}; rerun with --seed {seed} to repeat this exact drawing)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
