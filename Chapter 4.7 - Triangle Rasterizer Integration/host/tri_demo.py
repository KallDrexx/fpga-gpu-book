#!/usr/bin/env python3
"""tri_demo.py -- Chapter 4.7 hardware check: rotating triangles, FLIP every frame, CRC checked.

Usage:
    .venv/bin/python host/tri_demo.py PORT [--seconds 10] [--baud 115200] [--rtscts]
    .venv/bin/python host/tri_demo.py --model [--frames 8]

PORT is the USB-serial adapter on Pmod JA (e.g. /dev/ttyUSB0). Put board
switch SW0 down (double buffered). Every frame the script draws, into the back
buffer,

  * a big red triangle at the left, turning clockwise,
  * a yellow square at the right, turning the other way. It is two triangles
    with one color that share a diagonal, so a crack along the diagonal would
    show as a dark line, at every angle,
  * a small white triangle on the red one, turning twice as fast the other way,
  * a green triangle sweeping from left to right near the top. It crosses the
    left and right screen edges, so the clipping shows,

then sends FB_CRC (the picture before the swap), FLIP, and a second FB_CRC.
The GPU answers the second one only after the swap, so waiting for it paces
the script to the monitor's 60 Hz, as in Chapter 4.5. Every reply is compared
with host/gpu_model.py.

--model draws the same frames in the Python model only, with no board, and
prints the CRC of each frame: the numbers the hardware must reproduce.

The script prints the frame rate it reached and "PASS: tri_demo" if every
FB_CRC reply matched the model. Exit status 0 on PASS, 1 on FAIL.
"""
import argparse
import math
import os
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gpu_model as g  # noqa: E402

BG, WHITE, RED, YELLOW, GREEN = 0, 1, 2, 3, 4
PALETTE = (0x101828, 0xF0F0F0, 0xE04040, 0xE0E040, 0x40D060)
DEG = 3.0                      # degrees per frame: a turn takes 120 frames, 2 s


def polygon(cx, cy, radius, angle_deg, corners):
    """Corners of a regular polygon as integer (x, y); y grows downward."""
    pts = []
    for k in range(corners):
        a = math.radians(angle_deg + 360.0 * k / corners)
        pts.append((round(cx + radius * math.cos(a)), round(cy + radius * math.sin(a))))
    return pts


def frame_commands(n):
    """The packets that draw picture n into the buffer drawing goes to."""
    a = n * DEG
    t1 = polygon(100, 120, 85, a, 3)
    sq = polygon(235, 120, 60, 45 - a, 4)
    t2 = polygon(100, 120, 36, 90 - 2 * a, 3)
    gx = -30 + (n * 4) % 380                             # -30 .. 349: off both edges
    t3 = polygon(gx, 36, 26, 20 + a, 3)
    return b"".join([
        g.clear(BG),
        g.draw_tri(*t1[0], *t1[1], *t1[2], RED),
        g.draw_tri(*sq[0], *sq[1], *sq[2], YELLOW),       # the square: two triangles
        g.draw_tri(*sq[0], *sq[2], *sq[3], YELLOW),       # sharing the diagonal sq[0]-sq[2]
        g.draw_tri(*t2[0], *t2[1], *t2[2], WHITE),
        g.draw_tri(*t3[0], *t3[1], *t3[2], GREEN),
    ])


def picture_crc(n):
    """FB_CRC of picture n. Every picture starts with CLEAR, so it does not
    depend on what the buffer held before: the model draws it alone. Picture
    -1 is the blank buffer the setup leaves behind."""
    gpu = g.Gpu()
    if n >= 0:
        gpu.feed(frame_commands(n))
    return gpu.crc()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--check-every", type=int, default=10, metavar="K",
                    help="compare the replies of every K-th frame with the model afterwards (1: all; slow)")
    ap.add_argument("--rtscts", action="store_true", help="use the adapter's CTS input")
    ap.add_argument("--model", action="store_true", help="Python model only, no board")
    ap.add_argument("--frames", type=int, default=8, help="with --model: how many frames to print")
    args = ap.parse_args(argv)

    if args.model:
        for n in range(args.frames):
            print(f"frame {n:3d}: CRC {picture_crc(n):08x}")
        return 0
    if not args.port:
        ap.error("PORT is required (or use --model)")

    import serial  # pyserial; imported here so --model needs nothing installed

    replies = []                      # (crc of picture n before the swap, crc of the buffer behind it after)
    with serial.Serial(args.port, args.baud, timeout=2.0, rtscts=args.rtscts) as ser:
        ser.reset_input_buffer()
        # Same state in the hardware and the model, whatever ran before: palette, and both buffers cleared.
        setup = b"".join(g.set_palette(i, c) for i, c in enumerate(PALETTE))
        setup += g.clear(BG) + g.flip() + g.clear(BG) + g.flip()
        ser.write(setup)
        print(f"rotating triangles: SW0 must be down, DRAW_TRI into the back buffer, FLIP after each picture, "
              f"{args.seconds:g} s")
        t0 = time.time()
        while time.time() - t0 < args.seconds:
            n = len(replies)
            ser.write(frame_commands(n) + g.fb_crc() + g.flip() + g.fb_crc())
            have = ser.read(8)
            if len(have) != 8:
                print(f"FAIL: tri_demo: no reply to frame {n}. Check the wiring, the baud rate and that the "
                      f"design is loaded; press BTN0 to reset the parser and try again.")
                return 1
            replies.append((g.parse_crc_reply(have[:4]), g.parse_crc_reply(have[4:])))
        dt = time.time() - t0

    # The model is slow next to the hardware, so it checks afterwards. After a
    # FLIP the buffer behind the picture holds the previous picture, so the
    # second reply is the CRC of picture n-1.
    n_frames = len(replies)
    print(f"{n_frames} frames in {dt:.1f} s: {n_frames / dt:.1f} frames/s; checking every {args.check_every}th against the model")
    bad = checked = 0
    for n in range(0, n_frames, args.check_every):
        want = (picture_crc(n), picture_crc(n - 1))
        checked += 1
        if n % (60 * args.check_every // math.gcd(60, args.check_every)) == 0:
            print(f"  frame {n:4d}: hardware CRC {replies[n][0]:08x}, Python CRC {want[0]:08x}")
        if replies[n] != want:
            bad += 1
            if bad == 1:
                print(f"  frame {n}: hardware {replies[n][0]:08x} {replies[n][1]:08x}, "
                      f"Python {want[0]:08x} {want[1]:08x}")
    if bad:
        print(f"FAIL: tri_demo ({bad} of {checked} checked frames had a wrong FB_CRC)")
        return 1
    print(f"PASS: tri_demo ({checked} frames checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
