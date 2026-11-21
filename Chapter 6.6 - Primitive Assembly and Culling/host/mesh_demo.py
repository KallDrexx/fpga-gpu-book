#!/usr/bin/env python3
"""mesh_demo.py -- a spinning cube from an index buffer, with face culling (Chapter 6.6).

Usage:
    .venv/bin/python host/mesh_demo.py [--url ...] [--freq 1000000] [--seconds 10]
    .venv/bin/python host/mesh_demo.py --model [--ppm build/mesh_demo.ppm]

Uploads the cube once (8 vertices, 12 index entries, six palette ramps), then for every frame sends only a matrix and a
short draw sequence: SET_MATRIX, TRANSFORM, CLEAR_ALL, DRAW_MESH, FLIP. It starts with the cull mode BACK, reads GET_MESH_STATS for
the first picture and checks FB_CRC against host/gpu_model.py, draws the same pose with cull mode NONE and prints how many more
triangles that sends to the rasterizer (and that the picture is almost the same: only silhouette pixels may differ), then
spins the cube for --seconds, one frame per VSYNC, and prints the culled / drawn counts of the last frame.
Prints "PASS: mesh_demo" if every FB_CRC matched, the culled count is the one the model gives, GET_INFO reports capability bit
13 and no error flag is set. With --model the cube is drawn only by the model. Wiring and options are those of spi_test.py.
"""
import argparse
import math
import os
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gpu_model as g  # noqa: E402
import mesh_model as mm  # noqa: E402
import spi_test  # noqa: E402

RAMPS = [((24, 24, 48), (255, 255, 255)), ((40, 0, 0), (255, 90, 90)), ((0, 40, 0), (120, 255, 120)),
         ((0, 0, 48), (110, 140, 255)), ((48, 40, 0), (255, 240, 60)), ((0, 40, 40), (120, 255, 255)),
         ((40, 0, 40), (255, 110, 255)), ((50, 20, 0), (255, 160, 40))]


def setup():
    """Palette, vertices and index entries: sent once."""
    pal = b"".join(b"".join(g.ramp_palette(r, d, l)) for r, (d, l) in enumerate(RAMPS))
    return (pal + b"".join(g.vertex_packets(0, mm.CUBE_VERTS)) + b"".join(g.index_packets(0, mm.cube_entries()))
            + g.set_projection(277, 1) + g.set_depth(True, True))


def frame_commands(angle, cull):
    """What the host sends per frame: a matrix, then the draw. Nothing else changes."""
    m = mm.rot_matrix(angle * 0.7, angle, angle * 0.3, 0, 0, 260)
    return g.set_matrix(m) + g.transform(0, 8) + g.set_mesh_mode(cull) + g.clear_all(9, 65535) + g.draw_mesh(0, 12)


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
    for cull in (mm.CULL_BACK, mm.CULL_NONE):
        send(g.clear_mesh_stats() + frame_commands(40.0, cull))
        link.send(g.get_mesh_stats())
        got_stats = g.parse_mesh_stats(link.read_reply(12))
        want_stats = (model.mesh_culled, model.mesh_rejected, model.mesh_drawn)
        got, want = link.fb_crc(), g.parse_crc_reply(model.feed(g.fb_crc()))
        print("cull %-5s: culled %2d rejected %d drawn %2d (model %s) FB_CRC %08X model %08X %s"
              % ("BACK" if cull == mm.CULL_BACK else "NONE", *got_stats, "ok" if got_stats == want_stats else "MISMATCH %s" % (want_stats,),
                 got, want, "ok" if got == want else "MISMATCH"))
        bad += (got != want) + (got_stats != want_stats)
        crcs.append(got)
    print("the two cull modes %s picture (hidden faces lose the depth test; only silhouette pixels can differ)"
          % ("give the same" if crcs[0] == crcs[1] else "give a slightly different"))
    if args.model and args.ppm:
        with open(args.ppm, "wb") as f:
            f.write(b"P6\n%d %d\n255\n" % (g.W, g.H))
            for c in model.fb:
                v = model.pal[c]
                f.write(bytes([v >> 16 & 255, v >> 8 & 255, v & 255]))
    send(g.flip())

    frames, t0, last = 0, time.time(), None
    while not args.model and time.time() - t0 < args.seconds:
        while link.vsync():                     # wait for VSYNC to fall (the end of vertical blanking)
            pass
        send(g.clear_mesh_stats() + frame_commands(40.0 + frames * 2.0, mm.CULL_BACK) + g.flip())
        frames += 1
    if frames:
        print("animated %d frames in %.1f s" % (frames, time.time() - t0))
        link.send(g.get_mesh_stats())
        c, r, d = g.parse_mesh_stats(link.read_reply(12))
        print("last frame: %d triangles culled, %d rejected, %d drawn (of 12)" % (c, r, d))
        bad += c + r + d != 12
    info = link.info()
    print("GET_INFO: caps %04X errors %X" % (info["caps"], info["errors"]))
    if not info["caps"] & g.CAPS_MESH:
        print("no mesh capability bit: is the Chapter 6.6 bitstream loaded?")
        bad += 1
    bad += info["errors"] != 0
    print("PASS: mesh_demo" if not bad else "FAIL: mesh_demo (%d problems)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
