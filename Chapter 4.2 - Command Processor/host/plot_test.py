#!/usr/bin/env python3
"""plot_test.py -- Chapter 4.2 hardware check: random pixels, then FB_CRC.

Usage:
    .venv/bin/python host/plot_test.py PORT [--count 1000] [--seed N]
                                             [--baud 115200] [--rtscts]

PORT is the USB-serial adapter on Pmod JA (e.g. /dev/ttyUSB0). The script

  1. asks for FB_CRC and reports whether the framebuffer still holds
     Chapter 4.1's test image (it does right after `make prog`),
  2. sets palette entry 0, clears to 0, plots COUNT random on-screen pixels
     and COUNT/10 off-screen ones (which must change nothing),
  3. asks for FB_CRC again and compares it with host/gpu_model.py's CRC of
     the same drawing.

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


def test_image_crc():
    """CRC of Chapter 4.1's test image, or None if ref_scanout isn't found."""
    sys.path.insert(0, os.path.join(HERE, "..", "sim", "py"))
    try:
        from ref_scanout import image
    except ImportError:
        return None
    return g.crc32(image().reshape(-1).tobytes())


def swap32(v):
    return int.from_bytes(v.to_bytes(4, "little"), "big")


def read_crc(ser, what):
    reply = ser.read(4)
    if len(reply) != 4:
        sys.exit(f"FAIL: no FB_CRC reply {what} (got {len(reply)} of 4 bytes). Check the wiring "
                 f"(adapter TXD to JA1, RXD to JA2, GND to JA5), the baud rate, and that the "
                 f"design is loaded. Press BTN0 to reset the parser and try again.")
    return g.parse_crc_reply(reply)


def drawing(count, seed):
    """The packets of the test, as one byte string."""
    rng = random.Random(seed)
    out = bytearray()
    out += g.set_palette(0, 0x101828)            # dark blue background
    out += g.clear(0)
    for i in range(count):
        out += g.plot(rng.randrange(g.W), rng.randrange(g.H), rng.randrange(1, 256))
        if i % 10 == 9:                           # and one that must be ignored
            x, y = rng.choice(((rng.randrange(-1000, 0), rng.randrange(g.H)),
                               (rng.randrange(g.W, 1000), rng.randrange(g.H)),
                               (rng.randrange(g.W), rng.randrange(-1000, 0)),
                               (rng.randrange(g.W), rng.randrange(g.H, 1000))))
            out += g.plot(x, y, 255)
    out += g.fb_crc()
    return bytes(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--count", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=None, help="default: a new seed each run")
    ap.add_argument("--rtscts", action="store_true", help="use the adapter's CTS input")
    args = ap.parse_args(argv)
    seed = args.seed if args.seed is not None else random.randrange(1 << 30)

    stream = drawing(args.count, seed)
    model = g.Gpu()                               # CLEAR makes its start state irrelevant
    expect = g.parse_crc_reply(model.feed(stream))

    byte_time = 10.0 / args.baud
    with serial.Serial(args.port, args.baud, timeout=2.0, rtscts=args.rtscts) as ser:
        ser.reset_input_buffer()

        ser.write(g.fb_crc())
        first = read_crc(ser, "to the first FB_CRC")
        img = test_image_crc()
        if img is None:
            note = "(sim/py/ref_scanout.py not found, cannot compare with the test image)"
        elif first == img:
            note = "= Chapter 4.1's test image, untouched since power-up"
        elif swap32(first) == img:
            note = "= the test image's CRC with its bytes reversed: send the CRC low byte first"
        else:
            note = f"(the test image would be {img:08x}: something has drawn since power-up)"
        print(f"framebuffer CRC before the test: {first:08x} {note}")

        t0 = time.time()
        ser.timeout = 2.0 + len(stream) * byte_time  # the whole stream, then the reply
        ser.write(stream)
        got = read_crc(ser, f"after {args.count} plots")
        dt = time.time() - t0

    print(f"sent {len(stream)} bytes ({args.count} pixels on screen, {args.count // 10} off screen, "
          f"seed {seed}) in {dt:.2f} s")
    print(f"hardware CRC {got:08x}, Python CRC {expect:08x}")
    if got == expect:
        print("PASS: plot_test")
        return 0
    if swap32(got) == expect:
        print("  the bytes are reversed: FB_CRC must send the CRC low byte first")
    print("FAIL: plot_test (rerun with --seed %d to repeat this exact drawing)" % seed)
    return 1


if __name__ == "__main__":
    sys.exit(main())
