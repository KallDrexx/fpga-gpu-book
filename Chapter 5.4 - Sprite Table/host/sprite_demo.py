#!/usr/bin/env python3
"""sprite_demo.py -- 32 bouncing sprites moved with 4-byte rewrites over SPI (Chapter 5.4).

Usage:
    .venv/bin/python host/sprite_demo.py [--url ftdi://ftdi:232h/1] [--freq 5000000] [--frames 600]
    .venv/bin/python host/sprite_demo.py --model

Uploads a palette of four 15-color ramps (entries 16p + 1 to 16p + 15 for palette
number p), two 16 x 16 sprite images (a ball and a ring, drawn with indices 1 to
15), fills table entries 0-31 (image i % 2, palette i % 4) and enables sprites.
Then, once per video frame, right after VSYNC rises, it sends 32 MOVE_SPRITE
packets: 32 x 7 = 224 bytes, 4 table bytes each. Everything else in the entries
(image, palette, flags) is never sent again. The GPU latches the new positions
at the start of the next vertical blanking, so they show one frame later.
Sprites bounce off the edges of the screen, a little outside of it too, so
you can watch them slide in and out. Ctrl-C stops it early.

Wiring and options are those of spi_test.py. --frames 0 runs until Ctrl-C.

--model needs no board: it runs the same host code against the Python model,
checks GET_INFO reports the SPRITES capability, that a frame of moves costs
224 bytes, that the entries keep their image, palette and flags while moving,
and that the sprites are where the host program says they are in the picture.
With --ppm FILE it also writes the last frame as a picture.
"""
import argparse
import os
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gpu_model as g  # noqa: E402
import spi_test  # noqa: E402

N = 32                       # sprites moved every frame
LEFT, RIGHT = -8, 320 - 8    # x range of the top-left corner (half a sprite outside)
TOP, BOTTOM = -8, 240 - 8


def palette_packets():
    """Entry 0 black; palette p owns entries 16p + 1 ... 16p + 15, a ramp of one hue."""
    hues = [(255, 80, 60), (70, 220, 90), (90, 140, 255), (250, 220, 60)]
    pk = [g.set_palette(0, 0x000000)]
    for p, (r, gr, b) in enumerate(hues):
        for k in range(1, 16):
            f = k / 15.0
            pk.append(g.set_palette(16 * p + k, (int(r * f) << 16) | (int(gr * f) << 8) | int(b * f)))
    return pk


def ball(ring):
    """16 x 16 indices 0-15: a shaded disc (or a ring) around the middle, 0 outside."""
    px = bytearray(256)
    for y in range(16):
        for x in range(16):
            d2 = (x - 7.5) ** 2 + (y - 7.5) ** 2
            if d2 <= 56:
                if ring and d2 < 20:
                    continue
                px[y * 16 + x] = max(1, 15 - int(d2 / 4) + (2 if x + y < 14 else 0))   # lit from the top left
                px[y * 16 + x] = min(15, px[y * 16 + x])
    return bytes(px)


def scene_packets():
    pk = [g.sprite_ctrl(False)] + palette_packets()
    pk += g.upload_packets(0, 16, 16, ball(False))
    pk += g.upload_packets(1, 16, 16, ball(True))
    for i in range(N):
        x, y, _, _ = start_state(i)
        pk.append(g.set_sprite(i, x, y, i % 2, i % 4))
    pk.append(g.sprite_ctrl(True))
    return pk


def start_state(i):
    """(x, y, dx, dy) of sprite i at frame 0: spread over the screen, speeds 1-4 px per frame."""
    x = LEFT + (i * 37) % (RIGHT - LEFT)
    y = TOP + (i * 53) % (BOTTOM - TOP)
    dx = 1 + (i * 3) % 4
    dy = 1 + (i * 5) % 3
    return x, y, dx if i % 2 else -dx, dy if i % 3 else -dy


class Movers:
    def __init__(self):
        self.s = [list(start_state(i)) for i in range(N)]

    def step(self):
        """Advance every sprite one frame, bouncing; returns the MOVE_SPRITE packets."""
        out = []
        for i, st in enumerate(self.s):
            x, y, dx, dy = st
            x, y = x + dx, y + dy
            if x < LEFT or x > RIGHT:
                dx, x = -dx, min(max(x, LEFT), RIGHT)
            if y < TOP or y > BOTTOM:
                dy, y = -dy, min(max(y, TOP), BOTTOM)
            st[:] = [x, y, dx, dy]
            out.append(g.move_sprite(i, x, y))
        return b"".join(out)


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
    assert info["caps"] & 0x20, "GET_INFO does not report the SPRITES capability"
    link.send(b"".join(scene_packets()))
    m.vblank()
    movers = Movers()
    first = m.screen()
    assert any(first), "no sprite is on the screen after the upload"
    flags0 = [bytes(m.sprites[i * 8 + 4:i * 8 + 8]) for i in range(N)]
    per_frame = 0
    for frame in range(1, 301):
        data = movers.step()
        per_frame = len(data)
        link.send(data)
        m.vblank()
    assert per_frame == N * 7 == 224, "a frame of moves is not 224 bytes"
    assert [bytes(m.sprites[i * 8 + 4:i * 8 + 8]) for i in range(N)] == flags0, "a move changed more than x and y"
    assert all(m.sprites[i * 8:i * 8 + 4] == g.sprite_entry(movers.s[i][0], movers.s[i][1], 0)[:4] for i in range(N))
    last = m.screen()
    assert last != first, "the picture did not change while the sprites moved"
    # every disc sprite (even i) that is on the screen and not covered by a lower index shows its
    # center pixel: image index plus 16 * palette
    disc, checked = ball(False), 0
    for i in range(0, N, 2):
        x, y = movers.s[i][0], movers.s[i][1]
        cx, cy = x + 7, y + 7
        if not (0 <= cx < 320 and 0 <= cy < 240):
            continue
        if any(movers.s[j][0] <= cx < movers.s[j][0] + 16 and movers.s[j][1] <= cy < movers.s[j][1] + 16 for j in range(i)):
            continue
        assert last[cy * 320 + cx] == (disc[7 * 16 + 7] + 16 * (i % 4)) & 255, "sprite %d is not where the host put it" % i
        checked += 1
    assert checked >= 5, "too few sprites could be checked"
    assert link.info()["errors"] == 0
    return link, last


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    spi_test.add_link_args(ap)
    ap.add_argument("--frames", type=int, default=600, help="video frames to run (0: until Ctrl-C)")
    ap.add_argument("--ppm", help="with --model: write the last frame to this P6 file")
    args = ap.parse_args()

    if args.model:
        link, frame = run_model()
        if args.ppm:
            pal = link.port.gpu.pal
            with open(args.ppm, "wb") as f:
                f.write(b"P6\n320 240\n255\n")
                for c in frame:
                    v = pal[c]
                    f.write(bytes([v >> 16 & 255, v >> 8 & 255, v & 255]))
        print("PASS: sprite_demo (model)")
        return 0

    link = spi_test.open_link(args)
    info = link.info()
    if not info["caps"] & 0x20:
        print("FAIL: sprite_demo (GET_INFO capabilities %04X: no SPRITES bit; is the Chapter 5.4 bitstream loaded?)" % info["caps"])
        return 1
    link.send(g.clear_errors(0x0F))
    link.send(b"".join(scene_packets()))
    print("scene uploaded; moving %d sprites (Ctrl-C to stop)" % N)
    movers = Movers()
    frame, t0 = 0, time.time()
    try:
        while args.frames == 0 or frame < args.frames:
            wait_vblank_start(link)
            link.send(movers.step())
            frame += 1
    except KeyboardInterrupt:
        pass
    dt = time.time() - t0
    info = link.info()
    print("%d frames in %.1f s (%.1f per second; about 60 expected with the VSYNC pin); errors %X" %
          (frame, dt, frame / dt if dt else 0, info["errors"]))
    ok = info["errors"] == 0
    print("PASS: sprite_demo" if ok else "FAIL: sprite_demo (error flags set)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
