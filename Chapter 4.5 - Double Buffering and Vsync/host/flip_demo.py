#!/usr/bin/env python3
"""flip_demo.py -- Chapter 4.5 hardware check: animated rectangles, one buffer or two.

Usage:
    .venv/bin/python host/flip_demo.py PORT [--single] [--seconds 10]
                                       [--baud 115200] [--rtscts]

PORT is the USB-serial adapter on Pmod JA (e.g. /dev/ttyUSB0). Every frame the
script clears the screen, draws a border, a tall bar and a small square that
move a few pixels, and checks the result with FB_CRC against host/gpu_model.py.

  default   double buffered: after each picture it sends FLIP, then an FB_CRC.
            The GPU answers that FB_CRC only after the swap, so waiting for
            the reply paces the script to the monitor's 60 Hz with no timer.
  --single  set board switch SW0 up first. The GPU then draws into the buffer
            that is on the screen, and the script sends no FLIP at all. It
            paces itself with a timer instead (about 60 frames a second, not
            locked to the monitor), so the beam meets the drawing at a
            different place every frame: you see tearing and flicker.

First it clears both buffers (CLEAR, FLIP, CLEAR, FLIP), so the hardware and the
model start from the same state whatever ran before. In --single mode it just
clears the buffer on the screen.

Each frame costs a CLEAR (1.5 ms) and a few rectangles, a few milliseconds
in all, so about a third of every frame time the picture on screen is only
partly drawn if you draw into it.

The script prints the frame rate it reached and "PASS: flip_demo" if every
FB_CRC reply matched the model. Exit status 0 on PASS, 1 on FAIL.
"""
import argparse
import os
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import serial  # noqa: E402  (pyserial)

import gpu_model as g  # noqa: E402

BG, BORDER, BAR, SQUARE = 0, 1, 2, 3
PALETTE = (0x101828, 0xF0F0F0, 0xE04040, 0xE0E040)
BAR_W, SQ = 24, 24


def frame_commands(n):
    """The packets that draw picture n into the buffer drawing goes to."""
    bx = (n * 6) % (g.W - BAR_W)                      # the bar sweeps right, then wraps
    sx = (g.W - SQ) - (n * 4) % (g.W - SQ)            # the square sweeps left
    sy = 20 + (n * 3) % (g.H - SQ - 40)
    return b"".join([
        g.clear(BG),
        g.fill_rect(0, 0, g.W - 1, 1, BORDER), g.fill_rect(0, g.H - 2, g.W - 1, g.H - 1, BORDER),
        g.fill_rect(0, 0, 1, g.H - 1, BORDER), g.fill_rect(g.W - 2, 0, g.W - 1, g.H - 1, BORDER),
        g.fill_rect(bx, 0, bx + BAR_W - 1, g.H - 1, BAR),
        g.fill_rect(sx, sy, sx + SQ - 1, sy + SQ - 1, SQUARE),
    ])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--single", action="store_true", help="draw into the displayed buffer, no FLIP (SW0 up)")
    ap.add_argument("--rtscts", action="store_true", help="use the adapter's CTS input")
    args = ap.parse_args(argv)

    model = g.Gpu()
    nreply = 1 if args.single else 2
    frames = bad = 0
    first_bad = None
    with serial.Serial(args.port, args.baud, timeout=2.0, rtscts=args.rtscts) as ser:
        ser.reset_input_buffer()
        setup = b"".join(g.set_palette(i, c) for i, c in enumerate(PALETTE))
        setup += g.clear(BG) if args.single else g.clear(BG) + g.flip() + g.clear(BG) + g.flip()
        ser.write(setup)
        model.feed(setup)
        print(("single buffer: SW0 must be up, drawing goes to the displayed buffer" if args.single else
               "double buffer: SW0 must be down, drawing goes to the back buffer, FLIP after each picture")
              + f", {args.seconds:g} s")
        t0 = time.time()
        next_t = t0
        while time.time() - t0 < args.seconds:
            pic = frame_commands(frames)
            out = pic + g.fb_crc() + (b"" if args.single else g.flip() + g.fb_crc())
            ser.write(out)
            want = model.feed(out)
            have = ser.read(4 * nreply)
            if len(have) != 4 * nreply:
                print(f"FAIL: flip_demo: no reply to frame {frames}. Check the wiring, the baud rate and that the "
                      f"design is loaded; press BTN0 to reset the parser and try again.")
                return 1
            if have != want:
                bad += 1
                if first_bad is None:
                    first_bad = (frames, have.hex(), want.hex())
            frames += 1
            if args.single:                          # a timer, not locked to the monitor
                next_t += 1 / 60
                time.sleep(max(0.0, next_t - time.time()))
        dt = time.time() - t0

    print(f"{frames} frames in {dt:.1f} s: {frames / dt:.1f} frames/s")
    if bad:
        f, have, want = first_bad
        print(f"FAIL: flip_demo ({bad} of {frames} frames had a wrong FB_CRC; first at frame {f}: "
              f"hardware {have}, Python {want})")
        return 1
    print("PASS: flip_demo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
