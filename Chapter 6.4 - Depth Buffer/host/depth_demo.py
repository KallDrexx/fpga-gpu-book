#!/usr/bin/env python3
"""depth_demo.py -- the depth buffer and the performance counters on the board (Chapter 6.4).

Usage:
    .venv/bin/python host/depth_demo.py [--url ...] [--freq 1000000] [--seconds 10]
    .venv/bin/python host/depth_demo.py --model [--ppm build/depth_demo.ppm]

Loads eight palette ramps and draws two shaded triangles that pass through each other (a green and a yellow one,
depth test and write on, CLEAR_ALL first), checks FB_CRC against host/gpu_model.py, draws them again in the opposite
order and checks that the picture is the same, then flips. For --seconds it slides the green triangle's depths
back and forth, so the line where the two triangles meet sweeps across the screen, one frame per VSYNC. Every frame
starts with CLEAR_STATS; the script reads GET_STATS for the first picture and for the last animated frame and
prints the pixels the rasterizer produced, the pixels written, the clocks (50 MHz) and the pixels per clock.
Prints "PASS: depth_demo" if every FB_CRC matched, GET_INFO reports capability bit 11 and no error flag is set.
With --model the clock counter reads 0 (the model cannot know it). Wiring and options are those of spi_test.py.
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
BG = [[(-10, -10, 62000, 40), (330, -10, 62000, 120), (-10, 250, 62000, 200)],
      [(330, -10, 62000, 120), (330, 250, 62000, 248), (-10, 250, 62000, 200)]]


def setup():
    return b"".join(b"".join(g.ramp_palette(r, d, l)) for r, (d, l) in enumerate(RAMPS))


def triangles(shift):
    """The green and the yellow triangle; shift (-20000..20000) moves the green one's depths."""
    s = int(shift)
    green = [(20, 30, 8000 + 20000 + s, 0), (290, 60, 58000 - 1000 + s, 248), (80, 215, 8000 + 20000 + s, 124)]
    yellow = [(300, 40, 8000 + 20000, 248), (30, 70, 58000 - 1000, 0), (260, 220, 40000, 124)]
    return (2, green), (4, yellow)


def picture(shift, reverse=False):
    tris = list(triangles(shift))
    if reverse:
        tris.reverse()
    return (g.clear_all(9) + g.set_depth(True, True) + b"".join(g.draw_tri_shaded(*v, 0) for v in BG)
            + b"".join(g.draw_tri_shaded(*v, r) for r, v in tris))


def stats_line(link, label):
    link.send(g.get_stats())
    cov, wr, clk = g.parse_stats(link.read_reply(12))
    rate = cov / clk if clk else float("nan")
    print("%-14s %7d pixels, %7d written, %7d clocks, %.2f pixels per clock" % (label, cov, wr, clk, rate))
    return cov, clk


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
    crcs = []
    for reverse in (False, True):
        send(g.clear_stats() + picture(0.0, reverse))
        got, want = link.fb_crc(), g.parse_crc_reply(model.feed(g.fb_crc()))
        print("draw order %s: FB_CRC %08X  model %08X  %s" % ("B, A" if reverse else "A, B", got, want, "ok" if got == want else "MISMATCH"))
        bad += got != want
        crcs.append(got)
    print("the two orders give %s picture" % ("the same" if crcs[0] == crcs[1] else "a different (only equal-depth pixels may differ)"))
    send(g.clear_stats() + picture(0.0))
    stats_line(link, "first picture:")
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
        send(g.clear_stats() + picture(20000 * math.sin(frames * 0.05)) + g.flip())
        frames += 1
    if frames:
        print("animated %d frames in %.1f s" % (frames, time.time() - t0))
        link.send(g.get_stats())
        cov, wr, clk = g.parse_stats(link.read_reply(12))      # the last frame (the FLIP stall is not counted)
        print("last frame: %d pixels, %d written, %d clocks, %.2f pixels per clock (%.2f ms of triangle drawing)"
              % (cov, wr, clk, cov / clk if clk else float("nan"), clk / 50e3))

    info = link.info()
    print("GET_INFO: caps %04X errors %X" % (info["caps"], info["errors"]))
    if not info["caps"] & g.CAPS_DEPTH:
        print("no depth capability bit: is the Chapter 6.4 bitstream loaded?")
        bad += 1
    bad += info["errors"] != 0
    print("PASS: depth_demo" if not bad else "FAIL: depth_demo (%d problems)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
