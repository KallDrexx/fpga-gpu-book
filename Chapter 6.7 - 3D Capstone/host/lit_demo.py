#!/usr/bin/env python3
"""lit_demo.py -- the Phase 6 capstone: a lit, rotating model, with a matrix as the only thing the host sends per frame (Chapter 6.7).

Usage:
    .venv/bin/python host/lit_demo.py --model [--frames 120] [--shape torus] [--ppm build/lit_demo.ppm]
    .venv/bin/python host/lit_demo.py [--url ftdi://ftdi:232h/1] [--freq 5000000] [--seconds 20] [--shape torus] [--every 1]

Setup, once (about 6 KB): the palette (eight ramps), SET_PROJECTION, SET_LIGHT (a light fixed in camera space), the depth test, the
mesh mode "cull back, intensity from the vertices", three models (a cube of 24 split vertices, an icosphere, a torus of 192
triangles: host/mesh_gen.py) with their normals, and one display list per model: TRANSFORM, CLEAR_ALL, DRAW_MESH, FLIP.
Every frame after that, right after VSYNC falls (protocol.md section 14):

    SET_MATRIX  (26 bytes)      the model's orientation: host/lit_scene.py spin(k)
    RUN_LIST n  ( 3 bytes)      the model's list; it ends in FLIP, which waits for the next blanking

That is 29 bytes. The GPU transforms the vertices, lights them (the camera-space light is turned into model space once per
frame by the matrix), culls the back faces, draws with the depth buffer and flips. --shape picks cube, icosphere or torus, or `all` to
change model every 60 frames (the lists are all there already, so changing the model changes no byte of the traffic).

--model needs no board: the host code drives host/gpu_model.py, one gpu.vblank() per frame, checks as it goes that every frame sent
exactly the 29 bytes, that no error flag was set, that the counters add up (every triangle is culled or drawn), and that the
frame's traffic fits in the visible part of a 60 Hz frame at the SPI clock given by --freq. It PASSes if all that holds.
Without --model the same loop waits for the VSYNC pin; --every 2 gives 30 frames per second. It checks GET_INFO for capability bit 14
and, after the last frame, FB_CRC against the model.
"""
import argparse
import os
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gpu_model as g  # noqa: E402
import lit_scene as ls  # noqa: E402
import mesh_model as mm  # noqa: E402
import spi_test  # noqa: E402

VISIBLE_US = 480 * 800 / 25.0      # the visible part of a 640x480@60 frame: 480 lines of 800 clocks at 25 MHz = 15.36 ms
CS_OVERHEAD_US = 20.0              # per CS frame: CS setup and hold, generous


def traffic_us(nbytes, freq, frames=1):
    return nbytes * 8 / freq * 1e6 + CS_OVERHEAD_US * frames


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    spi_test.add_link_args(ap)
    ap.add_argument("--seconds", type=float, default=20.0, help="how long to animate on the board (default 20)")
    ap.add_argument("--frames", type=int, default=120, help="with --model: how many frames (default 120)")
    ap.add_argument("--shape", default="torus", choices=["cube", "icosphere", "torus", "all"])
    ap.add_argument("--every", type=int, default=1, help="a new picture every N-th VSYNC (1 = 60 frames per second, 2 = 30)")
    ap.set_defaults(freq=5e6)                   # --freq (SCK in Hz) comes from spi_test; 5 MHz here, and with --model it sets the traffic budget
    ap.add_argument("--ppm", help="with --model: write the last picture (as the model sees it) to this P6 file")
    args = ap.parse_args()
    link = spi_test.open_link(args)
    model = g.Gpu()
    scene = ls.build()
    order = list(scene.values()) if args.shape == "all" else [scene[args.shape]]

    def send(data):
        link.send(data)
        model.feed(data)

    bad = 0
    send(g.clear_errors(0x0F))
    setup = ls.setup_packets(scene)
    for p in setup:
        send(p)
    print("setup: %d packets, %d bytes, sent once" % (len(setup), sum(len(p) for p in setup)))
    info = link.info()
    if not info["caps"] & g.CAPS_LIGHT:
        print("no lighting capability bit (bit 14): is the Chapter 6.7 design loaded? (GET_INFO caps %04X)" % info["caps"])
        bad += 1

    sizes = []
    t0 = time.time()
    k = 0
    while True:
        if args.model:
            if k >= args.frames:
                break
            model.vblank()
        else:
            if time.time() - t0 >= args.seconds:
                break
            for _ in range(args.every):
                while not link.vsync():         # wait for VSYNC to rise ...
                    pass
                while link.vsync():             # ... and fall (the end of vertical blanking)
                    pass
        mdl = order[(k // 60) % len(order)]
        pk = ls.frame_packets(mdl, ls.spin(k))
        send(pk)
        sizes.append(len(pk))
        k += 1
    if not sizes:
        print("no frames")
        return 1
    print("%d frames: %d bytes each (min %d, max %d)  SET_MATRIX %d + RUN_LIST %d"
          % (len(sizes), sizes[0], min(sizes), max(sizes), 2 + 24, 2 + 1))
    bad += (min(sizes) != ls.FRAME_BYTES or max(sizes) != ls.FRAME_BYTES)
    us = traffic_us(ls.FRAME_BYTES, args.freq, 2)
    print("traffic per frame at %.1f MHz SPI: %.0f us of the %.0f us visible part of a 60 Hz frame (%.1f %%)"
          % (args.freq / 1e6, us, VISIBLE_US, 100 * us / VISIBLE_US))
    bad += us > VISIBLE_US
    last = order[((k - 1) // 60) % len(order)]
    c, r, d = model.mesh_culled, model.mesh_rejected, model.mesh_drawn
    print("model counters over all frames: %d culled, %d rejected, %d drawn" % (c, r, d))
    bad += model.errors != 0 or model.mesh_undefined != 0 or model.lit_undefined != 0 or r != 0
    if args.model:
        if args.ppm:
            with open(args.ppm, "wb") as f:
                f.write(b"P6\n%d %d\n255\n" % (g.W, g.H))
                for c_ in model.front_fb:
                    v = model.pal[c_]
                    f.write(bytes([v >> 16 & 255, v >> 8 & 255, v & 255]))
        # the first frame of a given pose is reproducible: the same packets give the same picture
        m2 = g.Gpu()
        for p in setup:
            m2.feed(p)
        m2.feed(ls.frame_packets(last, ls.spin(k - 1)))
        same = bytes(m2.front_fb) == bytes(model.front_fb)
        print("the last picture %s a fresh model given the setup and that one matrix" % ("equals" if same else "DIFFERS FROM"))
        bad += not same
    else:
        got, want = link.fb_crc(), g.parse_crc_reply(model.feed(g.fb_crc()))
        print("FB_CRC %08X model %08X %s" % (got, want, "ok" if got == want else "MISMATCH"))
        bad += got != want
        info = link.info()
        print("GET_INFO: caps %04X errors %X" % (info["caps"], info["errors"]))
        bad += info["errors"] != 0
    print("PASS: lit_demo" if not bad else "FAIL: lit_demo (%d problems)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
