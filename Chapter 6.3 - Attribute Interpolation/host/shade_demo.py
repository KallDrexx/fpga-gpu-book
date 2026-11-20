#!/usr/bin/env python3
"""shade_demo.py -- shaded triangles on the board (Chapter 6.3).

Usage:
    .venv/bin/python host/shade_demo.py [--url ...] [--freq 1000000] [--seconds 10]
    .venv/bin/python host/shade_demo.py --model [--ppm build/shade_demo.ppm]

Loads eight 32-shade palette ramps, draws a few DRAW_TRI_SHADED triangles into
the back buffer (the big one in the middle has corner intensities 0, 15.5 and
31 shades: a smooth gradient), checks FB_CRC against host/gpu_model.py and
flips. Then, for --seconds, it rotates the three corner intensities a little
every frame (a quad lit from a moving direction), redrawing into the back
buffer and flipping after VSYNC falls, as Chapter 5.7's game mode does.
Prints "PASS: shade_demo" if every FB_CRC matched, GET_INFO reports capability
bit 10 and no error flag is set. Wiring and options are those of spi_test.py.
"""
import argparse
import math
import os
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gpu_model as g  # noqa: E402
import spi_test  # noqa: E402

RAMPS = [((24, 24, 48), (255, 255, 255)), ((40, 0, 0), (255, 90, 90)), ((0, 40, 0), (120, 255, 120)),
         ((0, 0, 48), (110, 140, 255)), ((48, 40, 0), (255, 240, 60)), ((0, 40, 40), (120, 255, 255)),
         ((40, 0, 40), (255, 110, 255)), ((50, 20, 0), (255, 160, 40))]


def setup():
    return b"".join(b"".join(g.ramp_palette(r, d, l)) for r, (d, l) in enumerate(RAMPS))


def picture(t):
    """One frame: a gradient triangle and a quad whose corner intensities follow a light at angle t."""
    lit = [max(0, min(248, round(124 + 124 * math.cos(t - k * math.pi / 2)))) for k in range(4)]
    a, b, c, d = (190, 30), (300, 40), (290, 140), (180, 130)
    return (g.clear(0)
            + g.draw_tri_shaded((30, 20, 0, 0), (170, 70, 30000, 124), (60, 220, 60000, 248), 2)
            + g.draw_tri_shaded((*a, 0, lit[0]), (*b, 0, lit[1]), (*c, 0, lit[2]), 3)
            + g.draw_tri_shaded((*a, 0, lit[0]), (*c, 0, lit[2]), (*d, 0, lit[3]), 3)
            + g.draw_tri_shaded((200, 160, 0, 248), (310, 230, 0, 0), (190, 235, 0, 124), 7))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    spi_test.add_link_args(ap)
    ap.add_argument("--seconds", type=float, default=10.0, help="how long to animate (default 10; 0 with --model)")
    ap.add_argument("--ppm", help="with --model: write the first picture (as the model sees it) to this P6 file")
    args = ap.parse_args()
    link = spi_test.open_link(args)
    model = g.Gpu()

    def send(data):
        link.send(data)
        model.feed(data)

    bad = 0
    send(g.clear_errors(0x0F) + setup())
    send(picture(0.0))
    got, want = link.fb_crc(), g.parse_crc_reply(model.feed(g.fb_crc()))
    print("first picture: FB_CRC %08X  model %08X  %s" % (got, want, "ok" if got == want else "MISMATCH"))
    bad += got != want
    if args.model and args.ppm:
        with open(args.ppm, "wb") as f:
            f.write(b"P6\n%d %d\n255\n" % (g.W, g.H))
            for c in model.fb:
                v = model.pal[c]
                f.write(bytes([v >> 16 & 255, v >> 8 & 255, v & 255]))
    send(g.flip())

    frames, t0 = 0, time.time()
    while not args.model and time.time() - t0 < args.seconds:
        while link.vsync():                     # wait for VSYNC to fall (the end of vertical blanking)
            pass
        send(picture(frames * 0.05) + g.flip())
        frames += 1
    if frames:
        print("animated %d frames in %.1f s" % (frames, time.time() - t0))

    info = link.info()
    print("GET_INFO: caps %04X errors %X" % (info["caps"], info["errors"]))
    if not info["caps"] & g.CAPS_3D:
        print("no 3D capability bit: is the Chapter 6.3 bitstream loaded?")
        bad += 1
    bad += info["errors"] != 0
    print("PASS: shade_demo" if not bad else "FAIL: shade_demo (%d problems)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
