#!/usr/bin/env python3
"""stream_demo.py -- scroll a long level through a 64 x 64 map over SPI (Chapter 5.6).

Usage:
    .venv/bin/python host/stream_demo.py [--url ftdi://ftdi:232h/1] [--freq 5000000] [--frames 864]
    .venv/bin/python host/stream_demo.py --model

The level is 256 x 96 tiles (2,048 x 768 pixels), six times the area of a map. It is
the near layer (layer 1: ground, grass, trees). The camera moves right 2 pixels per
frame, and up and down (at most 1 pixel per frame) to follow the terrain; the far layer
(layer 0, hills and clouds) is a small periodic map at half speed that is written once.
Each frame the script asks level_stream.StreamedLayer which columns and rows have come
into view, sends them with WRITE_TILE_COLUMN / WRITE_TILE_ROW and then the two SET_SCROLLs,
and counts the bytes. A column is 132 bytes and the camera exposes one every 4 frames; the
camera never exposes a column and a row in the same frame (it delays the vertical step by a
frame instead), so a frame carries at most one 132-byte packet plus 14 bytes of scrolls. The
script prints the average and the largest frame and PASSes if every frame is under 200 bytes
(the one-off load of the first 64 columns is counted separately).

Wiring and options are those of spi_test.py. The packets are sent right after VSYNC rises:
the scroll sent now is latched at the NEXT blanking, and the map writes land at once, in ring
slots the frame about to be drawn does not show (the frame after it needs them).

--model needs no board: it runs the same host code against the Python model, checks the
map after every frame against the level (every tile the view shows), and checks that GET_INFO
reports the TILE_STREAM capability and no error flag. With --ppm FILE it writes the last frame.
"""
import argparse
import math
import os
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gpu_model as g  # noqa: E402
import layer_demo as ld  # noqa: E402  Chapter 5.3: the tile art, the palette, the far layer, wait_vblank_start
import spi_test  # noqa: E402
from level_stream import StreamedLayer, view_tiles, total_bytes  # noqa: E402

LEVEL_W, LEVEL_H = 256, 96            # tiles
SPEED = 2                             # pixels per frame to the right
MAX_SX = LEVEL_W * 8 - g.W            # 1728
MAX_SY = LEVEL_H * 8 - g.H            # 528


def terrain(c):
    """Row of the grass in level column c."""
    return 64 + int(7 * math.sin(c / 17.0) + 4 * math.sin(c / 5.3) + 4 * math.sin(c / 41.0))


def level(c, r):
    """Map entry of the near layer at level column c, row r (0, transparent, outside the level)."""
    if not (0 <= c < LEVEL_W and 0 <= r < LEVEL_H):
        return 0
    top = terrain(c)
    if r == top:
        t = ld.T_GRASS
    elif r > top:
        t = ld.T_DIRT
    elif r == top - 1 and c % 11 == 3:
        t = ld.T_TREE
    else:
        t = ld.T_EMPTY
    return g.map_entry(t)


class Camera:
    """Moves right at SPEED and follows the terrain up and down, at most 1 pixel a frame.
    When a step would expose a new column and a new row in the same frame, it makes the
    vertical step one frame later instead (never twice running), so at SPEED 2 no frame
    needs more than one 132-byte packet."""

    def __init__(self):
        self.x, self.y, self.held = 0, 0, False

    def step(self):
        x = min(self.x + SPEED, MAX_SX)
        want = min(max(terrain((x + g.W // 2) >> 3) * 8 - 150, 0), MAX_SY)
        y = self.y + (want > self.y) - (want < self.y)
        old = view_tiles(self.x, self.y)
        new = view_tiles(x, y)
        self.held = not self.held and new[:2] != old[:2] and new[2:] != old[2:]
        if self.held:
            y = self.y                    # a column this frame: the row can wait one frame
        self.x, self.y = x, y
        return x, y


def far_scroll(sx, sy):
    return (sx // 2) % 512, (sy // 8) % 512


def scene_packets(near):
    """Everything sent once: palette, tiles, the far map, the first 64 columns of the near level."""
    pk = [g.clear_errors(0x0F), g.set_tile_layer(0), g.set_scroll(0, 0, 0), g.set_scroll(1, 0, 0)]
    pk += [g.set_palette(i, c) for i, c in ld.COLORS.items()]
    gfx = bytearray(g.TILE_BYTES)
    for t in range(8):
        gfx[t * 64:t * 64 + 64] = bytes(ld.tile_pixels(t))
    pk += g.tile_packets(bytes(gfx[:8 * 64]))
    pk += g.map_packets(0, ld.far_map())
    first = near.load(0, 0)
    pk += first
    pk += [g.set_tile_layer(3)]
    return pk, total_bytes(first)


def frame_packets(near, cam):
    """The packets for one frame: new columns and rows, then both scrolls."""
    sx, sy = cam.step()
    fx, fy = far_scroll(sx, sy)
    pk = near.plan(sx, sy)
    pk += [g.set_scroll(1, sx, sy), g.set_scroll(0, fx, fy)]
    return pk


def check_map(m, near_view):
    """Every tile the view shows is in the ring where the screen will look for it."""
    c_lo, c_hi, r_lo, r_hi = near_view
    for r in range(r_lo, r_hi + 1):
        for c in range(c_lo, c_hi + 1):
            assert m.tmap[1][(r % 64) * 64 + c % 64] == level(c, r), "map is wrong at level tile (%d, %d)" % (c, r)


def report(sizes, load_bytes, frames):
    avg = sum(sizes) / len(sizes)
    print("%d frames: %.1f bytes per frame on average, at most %d, at least %d" % (frames, avg, max(sizes), min(sizes)))
    print("first load of 64 columns: %d bytes; spread over %d frames that adds %.1f per frame" %
          (load_bytes, frames, load_bytes / frames))
    return avg


def run_model(frames):
    link = spi_test.open_link(argparse.Namespace(model=True))
    m = link.port.gpu
    info = link.info()
    assert info["caps"] & g.CAPS_STREAMING, "GET_INFO does not report the TILE_STREAM capability"
    near = StreamedLayer(1, level, LEVEL_W, LEVEL_H)
    cam = Camera()
    scene, load_bytes = scene_packets(near)
    link.send(b"".join(scene))
    m.vblank()
    sizes = []
    for f in range(frames):
        pk = frame_packets(near, cam)
        sizes.append(total_bytes(pk))
        link.send(b"".join(pk))
        check_map(m, near.view)
        m.vblank()
    assert link.info()["errors"] == 0
    report(sizes, load_bytes, frames)
    assert max(sizes) < 200, "a frame needed %d bytes" % max(sizes)
    return link, m, sizes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    spi_test.add_link_args(ap)
    ap.add_argument("--frames", type=int, default=864, help="frames to scroll (864 is the whole level)")
    ap.add_argument("--ppm", help="with --model: write the last frame to this P6 file")
    args = ap.parse_args()

    if args.model:
        link, m, sizes = run_model(args.frames)
        if args.ppm:
            with open(args.ppm, "wb") as f:
                f.write(b"P6\n320 240\n255\n")
                for c in m.screen():
                    v = ld.COLORS.get(c, 0)
                    f.write(bytes([v >> 16 & 255, v >> 8 & 255, v & 255]))
        print("PASS: stream_demo (model)")
        return 0

    link = spi_test.open_link(args)
    info = link.info()
    if not info["caps"] & g.CAPS_STREAMING:
        print("FAIL: stream_demo (GET_INFO capabilities %04X: no TILE_STREAM bit; is the Chapter 5.6 bitstream loaded?)" % info["caps"])
        return 1
    near = StreamedLayer(1, level, LEVEL_W, LEVEL_H)
    cam = Camera()
    scene, load_bytes = scene_packets(near)
    link.send(b"".join(scene))
    print("scene and first 64 columns sent; scrolling")
    sizes, t0 = [], time.time()
    try:
        for _ in range(args.frames):
            ld.wait_vblank_start(link)
            pk = frame_packets(near, cam)
            sizes.append(total_bytes(pk))
            link.send(b"".join(pk))
    except KeyboardInterrupt:
        pass
    dt = time.time() - t0
    info = link.info()
    if not sizes:
        print("FAIL: stream_demo (no frames)")
        return 1
    report(sizes, load_bytes, len(sizes))
    print("%.1f s (%.1f frames per second); errors %X" % (dt, len(sizes) / dt if dt else 0, info["errors"]))
    ok = info["errors"] == 0 and max(sizes) < 200
    print("PASS: stream_demo" if ok else "FAIL: stream_demo (error flags set, or a frame needed 200 bytes or more)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
