#!/usr/bin/env python3
"""layer_demo.py -- two-layer parallax scroll over SPI (Chapter 5.3).

Usage:
    .venv/bin/python host/layer_demo.py [--url ftdi://ftdi:232h/1] [--freq 5000000] [--frames 600]
    .venv/bin/python host/layer_demo.py --model

Uploads a small scene (palette, 8 tiles, two 64 x 64 maps), then moves the layers
once per video frame: the far layer (hills and clouds) 1 pixel every 2 frames,
the near layer (ground and trees) 2 pixels per frame, the way a side-scroller does.
Both maps repeat every 64 tiles, the width of the world, so the scroll wraps
without a seam. One pair of scroll packets is sent right after VSYNC rises,
so there is one pair per frame; the GPU latches each pair at the next start of
vertical blanking, so it shows one frame later. Ctrl-C stops it early.

Wiring and options are those of spi_test.py. --frames 0 runs until Ctrl-C.

--model needs no board: it runs the same host code against the Python model, checks
GET_INFO reports the TILES capability, and checks that the picture after 256 frames
of scrolling is the picture at the start (the near layer moved exactly one world),
that it changed in between, and that the scene looks as intended (sky on top, ground at
the bottom). With --ppm FILE it also writes the last frame as a picture.
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

# palette entries
SKY, CLOUD, HILL, HILL2, GRASS, DIRT, TRUNK, LEAF = 1, 2, 3, 4, 5, 6, 7, 8
COLORS = {0: 0x000000, SKY: 0x4A9DF0, CLOUD: 0xF4F8FF, HILL: 0x2E7D4F, HILL2: 0x3FA66A,
          GRASS: 0x58C13B, DIRT: 0x7A4B24, TRUNK: 0x5B3A1A, LEAF: 0x1F8A2E}

# tile numbers
T_EMPTY, T_SKY, T_CLOUD, T_HILL, T_HILL_TOP, T_GRASS, T_DIRT, T_TREE = 0, 1, 2, 3, 4, 5, 6, 7


def tile_pixels(t):
    """64 palette indices for tile t, row by row."""
    px = [0] * 64
    for y in range(8):
        for x in range(8):
            c = 0
            if t == T_SKY:
                c = SKY
            elif t == T_CLOUD:
                c = CLOUD if (y >= 2 and (x - 3.5) ** 2 + (y - 5) ** 2 < 14) else SKY
            elif t == T_HILL:
                c = HILL if (x + y) % 5 else HILL2
            elif t == T_HILL_TOP:
                c = HILL2 if y == 0 else HILL
            elif t == T_GRASS:
                c = GRASS if y < 3 else DIRT
            elif t == T_DIRT:
                c = DIRT if (x * 3 + y) % 7 else TRUNK
            elif t == T_TREE:
                c = LEAF if y < 5 and (x - 3.5) ** 2 + (y - 2) ** 2 < 14 else (TRUNK if 3 <= x <= 4 else 0)
            px[y * 8 + x] = c
    return px


def far_map():
    """Layer 0: sky everywhere, hills with a repeating outline, a few clouds."""
    ents = []
    for row in range(64):
        for col in range(64):
            t = T_SKY
            top = 22 - int(3 * math.sin(2 * math.pi * col / 64) + 2 * math.sin(2 * math.pi * 3 * col / 64))
            if row == top:
                t = T_HILL_TOP
            elif row > top:
                t = T_HILL
            if row == 6 and col % 16 in (3, 4):
                t = T_CLOUD
            if row == 10 and col % 24 in (9, 10, 11):
                t = T_CLOUD
            ents.append(g.map_entry(t, flip_x=(t == T_CLOUD and col % 2 == 1)))
    return ents


def near_map():
    """Layer 1: transparent except a ground strip at the bottom of the view and trees."""
    ents = []
    for row in range(64):
        for col in range(64):
            t = T_EMPTY
            if row == 28:
                t = T_GRASS
            elif row == 29:
                t = T_DIRT
            elif row == 27 and col % 9 == 4:
                t = T_TREE
            ents.append(g.map_entry(t))
    return ents


def scene_packets():
    pk = [g.set_tile_layer(0), g.set_scroll(0, 0, 0), g.set_scroll(1, 0, 0)]
    pk += [g.set_palette(i, c) for i, c in COLORS.items()]
    gfx = bytearray(g.TILE_BYTES)
    for t in range(8):
        gfx[t * 64:t * 64 + 64] = bytes(tile_pixels(t))
    pk += g.tile_packets(bytes(gfx[:8 * 64]))
    pk += g.map_packets(0, far_map())
    pk += g.map_packets(1, near_map())
    pk += [g.set_tile_layer(3)]
    return pk


def scroll_for(frame):
    """(layer 0 x, layer 1 x) at a frame number."""
    return (frame // 2) % 512, (2 * frame) % 512


def wait_vblank_start(link, timeout=0.1):
    """Wait for VSYNC (the vblank pin) to rise; without the pin, sleep one frame."""
    if link.vsync is None:
        time.sleep(1 / 60)
        return
    t0 = time.time()
    while link.vsync() and time.time() - t0 < timeout:   # leave the current blanking
        pass
    while not link.vsync() and time.time() - t0 < timeout:
        pass


def run_model():
    link = spi_test.open_link(argparse.Namespace(model=True))
    m = link.port.gpu
    info = link.info()
    assert info["caps"] & 0x10, "GET_INFO does not report the TILES capability"
    link.send(b"".join(scene_packets()))
    m.vblank()
    first = m.screen()
    assert set(first[:320]) == {SKY}, "the top row is not sky"
    assert set(first[239 * 320: 240 * 320]) <= {DIRT, TRUNK}, "bottom row is not dirt"
    second = None
    for frame in range(1, 257):
        x0, x1 = scroll_for(frame)
        link.send(g.set_scroll(0, x0, 0) + g.set_scroll(1, x1, 0))
        m.vblank()
        if frame == 1:
            second = m.screen()
    last = m.screen()
    assert second != first, "one frame of scrolling changed nothing"
    # the near layer is back where it was (256 frames * 2 px = 512); the far layer moved 128 px
    assert m.live_scroll == [[128, 0], [0, 0]], m.live_scroll
    far_back = m.screen()
    m.live_scroll[0] = [0, 0]
    assert m.screen() == first, "the picture after one world of scrolling is not the first one"
    return link, far_back, last


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    spi_test.add_link_args(ap)
    ap.add_argument("--frames", type=int, default=600, help="video frames to scroll (0: until Ctrl-C)")
    ap.add_argument("--ppm", help="with --model: write the last frame to this P6 file")
    args = ap.parse_args()

    if args.model:
        link, frame, _ = run_model()
        if args.ppm:
            with open(args.ppm, "wb") as f:
                f.write(b"P6\n320 240\n255\n")
                for c in frame:
                    v = COLORS.get(c, 0)
                    f.write(bytes([v >> 16 & 255, v >> 8 & 255, v & 255]))
        print("PASS: layer_demo (model)")
        return 0

    link = spi_test.open_link(args)
    info = link.info()
    if not info["caps"] & 0x10:
        print("FAIL: layer_demo (GET_INFO capabilities %04X: no TILES bit; is the Chapter 5.3 bitstream loaded?)" % info["caps"])
        return 1
    link.send(g.clear_errors(0x0F))
    link.send(b"".join(scene_packets()))
    print("scene uploaded; scrolling (Ctrl-C to stop)")
    frame, t0 = 0, time.time()
    try:
        while args.frames == 0 or frame < args.frames:
            wait_vblank_start(link)
            x0, x1 = scroll_for(frame)
            link.send(g.set_scroll(0, x0, 0) + g.set_scroll(1, x1, 0))
            frame += 1
    except KeyboardInterrupt:
        pass
    dt = time.time() - t0
    info = link.info()
    print("%d frames in %.1f s (%.1f per second; about 60 expected with the VSYNC pin); errors %X" %
          (frame, dt, frame / dt if dt else 0, info["errors"]))
    ok = info["errors"] == 0
    print("PASS: layer_demo" if ok else "FAIL: layer_demo (error flags set)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
