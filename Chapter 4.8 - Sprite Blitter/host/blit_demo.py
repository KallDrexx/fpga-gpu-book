#!/usr/bin/env python3
"""blit_demo.py -- Chapter 4.8 hardware check: 20 animated sprites, one upload.

Usage:
    .venv/bin/python host/blit_demo.py PORT [--seconds 10] [--baud 115200] [--rtscts]
    .venv/bin/python host/blit_demo.py --model [--frames 8]

PORT is the USB-serial adapter on Pmod JA (e.g. /dev/ttyUSB0). Put board
switch SW0 down (double buffered).

At the start the script uploads five images, once (about 6.7 KB, in 250-byte
packets):

  asset 0   an 80 x 60 background tile, drawn opaque 16 times to tile the screen
  asset 1   a 16 x 16 ball          (its highlight is at the top left)
  asset 2   a 24 x 16 arrow          (it points right)
  asset 3   a 13 x 21 flag           (the pole is on the left)
  asset 4   a 31 x 29 star           (one arm longer than the others)

After that every frame is only BLIT commands, then the FB_CRC / FLIP / FB_CRC
check of Chapter 4.7: 16 background blits (the tiles cover the whole picture, so
nothing of the previous frame is left), then 20 sprites, five of each shape.
A sprite is mirrored left-right when it moves left (FLIP_X) and top-bottom when
it moves up (FLIP_Y), so every sprite turns as it bounces. Sprites slide
through the left and right edges and bounce a little beyond the top and bottom,
so every edge clips them. A frame is 36 BLITs, 8 bytes each: 288 bytes.

--model draws the same frames in the Python model only, with no board, and
prints the CRC of each frame: the numbers the hardware must reproduce.

The script prints the frame rate it reached and "PASS: blit_demo" if every
FB_CRC reply matched the model. Exit status 0 on PASS, 1 on FAIL.
"""
import argparse
import math
import os
import random
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gpu_model as g  # noqa: E402

PALETTE = (0x101828, 0xF0F0F0, 0xE04040, 0xE0E040, 0x40D060, 0x4080F0, 0xF040C0, 0x20C8D0,
           0x182034, 0x1C2C48, 0x243858, 0x2C4468)          # 0: background / transparent
TILE_W, TILE_H = 80, 60
SPRITES = 20


# ---------------------------------------------------------------- the images
def background_tile():
    """80 x 60 of palette 8-11: a diagonal pattern that repeats seamlessly."""
    return bytes(8 + ((x // 10 + y // 10) % 2) * 2 + ((x + y) % 20 == 0) for y in range(TILE_H) for x in range(TILE_W))


def ball():
    """16 x 16 disc with a highlight at the top left; index 0 outside."""
    rows = []
    for y in range(16):
        for x in range(16):
            d = (x - 7.5) ** 2 + (y - 7.5) ** 2
            rows.append(0 if d > 56 else 1 if (x - 4.5) ** 2 + (y - 4.5) ** 2 < 5 else 2)
    return bytes(rows)


def arrow():
    """24 x 16, pointing right: a body with a head at the right end."""
    rows = []
    for y in range(16):
        for x in range(24):
            body = x < 14 and 5 <= y <= 10
            head = x >= 14 and abs(y - 7.5) <= (23 - x) * 0.85 + 0.5
            rows.append(0 if not (body or head) else 3 if x < 3 else 4)
    return bytes(rows)


def flag():
    """13 x 21: a pole on the left and a waving pennant."""
    rows = []
    for y in range(21):
        for x in range(13):
            pole = x < 2
            pennant = 2 <= x and y < 11 and y >= x // 2 and y < 11 - x // 2 + 1
            rows.append(1 if pole else 6 if pennant else 0)
    return bytes(rows)


def star():
    """31 x 29: four arms, the right one longer, and a hole in the middle."""
    rows = []
    for y in range(29):
        for x in range(31):
            dx, dy = x - 12, y - 14
            arm = (abs(dy) <= 2 and -14 <= dx <= 18) or (abs(dx) <= 2 and abs(dy) <= 14)
            rows.append(0 if not arm or (abs(dx) < 2 and abs(dy) < 2) else 5 if (x + y) % 3 else 7)
    return bytes(rows)


IMAGES = {0: (TILE_W, TILE_H, background_tile()), 1: (16, 16, ball()), 2: (24, 16, arrow()),
          3: (13, 21, flag()), 4: (31, 29, star())}


def setup_commands():
    """Palette, the five uploads, and both buffers cleared: sent once."""
    out = [g.set_palette(i, c) for i, c in enumerate(PALETTE)]
    for a, (w, h, data) in IMAGES.items():
        out += g.upload_packets(a, w, h, data)
    out += [g.clear(0), g.flip(), g.clear(0), g.flip()]
    return b"".join(out)


# ------------------------------------------------------------------ sprites
def sprite_state(i, n):
    """Position and direction of sprite i in frame n: (asset, x, y, vx, vy).
    Deterministic, from a per-sprite random generator, so the model and the
    hardware run the same animation."""
    rng = random.Random(1000 + i)
    a = 1 + i % 4
    w, h = IMAGES[a][0], IMAGES[a][1]
    vx = rng.choice((-1, 1)) * rng.randint(1, 4)
    vy = rng.choice((-1, 1)) * rng.randint(1, 3)
    x0, y0 = rng.randrange(320), rng.randrange(240 - h)
    span_x = 320 + w                                         # leaves at one edge, comes back at the other
    x = (x0 + vx * n) % span_x - w + 1
    lo, hi = -8, 240 - h + 8                                 # bounces a little past the top and bottom
    p = (y0 + vy * n - lo) % (2 * (hi - lo))
    y = lo + (p if p <= hi - lo else 2 * (hi - lo) - p)
    going_up = (p > hi - lo) == (vy > 0)
    return a, x, y, vx, -1 if going_up else 1


def frame_commands(n):
    """The packets of picture n: only BLITs."""
    out = [g.blit(0, 80 * tx, 60 * ty, g.OPAQUE) for ty in range(4) for tx in range(4)]
    for i in range(SPRITES):
        a, x, y, vx, vy = sprite_state(i, n)
        flags = (g.FLIP_X if vx < 0 else 0) | (g.FLIP_Y if vy < 0 else 0)
        out.append(g.blit(a, x, y, flags))
    return b"".join(out)


class Model:
    """The Python model with the assets uploaded once, like the hardware."""

    def __init__(self):
        self.gpu = g.Gpu()
        self.gpu.feed(setup_commands())

    def picture_crc(self, n):
        """FB_CRC of picture n. The 16 opaque tiles cover every pixel, so the
        picture does not depend on what the buffer held before. Picture -1 is
        the blank buffer the setup leaves behind."""
        if n < 0:
            return g.crc32(bytes(g.FB_SIZE))
        self.gpu.feed(frame_commands(n))
        return self.gpu.crc()


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

    model = Model()
    if args.model:
        for n in range(args.frames):
            print(f"frame {n:3d}: CRC {model.picture_crc(n):08x}")
        return 0
    if not args.port:
        ap.error("PORT is required (or use --model)")

    import serial  # pyserial; imported here so --model needs nothing installed

    replies = []
    with serial.Serial(args.port, args.baud, timeout=2.0, rtscts=args.rtscts) as ser:
        ser.reset_input_buffer()
        setup = setup_commands()
        print(f"uploading {sum(w * h for w, h, _ in IMAGES.values())} bytes of images (once) ...")
        ser.write(setup)
        ser.write(g.fb_crc())                  # the reply shows the uploads are done: the buffers are blank
        blank = ser.read(4)
        if len(blank) != 4 or g.parse_crc_reply(blank) != g.crc32(bytes(g.FB_SIZE)):
            print("FAIL: blit_demo: no (or a wrong) reply after the upload. Check the wiring and the baud rate; "
                  "press BTN0 and try again.")
            return 1
        print(f"animating {SPRITES} sprites: only BLIT commands per frame (36 of them), FLIP after each picture, "
              f"{args.seconds:g} s")
        t0 = time.time()
        while time.time() - t0 < args.seconds:
            n = len(replies)
            ser.write(frame_commands(n) + g.fb_crc() + g.flip() + g.fb_crc())
            have = ser.read(8)
            if len(have) != 8:
                print(f"FAIL: blit_demo: no reply to frame {n}. Check the wiring, the baud rate and that the "
                      f"design is loaded; press BTN0 to reset the parser and try again.")
                return 1
            replies.append((g.parse_crc_reply(have[:4]), g.parse_crc_reply(have[4:])))
        dt = time.time() - t0

    # After a FLIP the buffer behind the picture holds the previous picture, so
    # the second reply is the CRC of picture n-1.
    n_frames = len(replies)
    print(f"{n_frames} frames in {dt:.1f} s: {n_frames / dt:.1f} frames/s; checking every {args.check_every}th against the model")
    bad = checked = 0
    for n in range(0, n_frames, args.check_every):
        want = (model.picture_crc(n), model.picture_crc(n - 1))
        checked += 1
        if n % (60 * args.check_every // math.gcd(60, args.check_every)) == 0:
            print(f"  frame {n:4d}: hardware CRC {replies[n][0]:08x}, Python CRC {want[0]:08x}")
        if replies[n] != want:
            bad += 1
            if bad == 1:
                print(f"  frame {n}: hardware {replies[n][0]:08x} {replies[n][1]:08x}, "
                      f"Python {want[0]:08x} {want[1]:08x}")
    if bad:
        print(f"FAIL: blit_demo ({bad} of {checked} checked frames had a wrong FB_CRC)")
        return 1
    print(f"PASS: blit_demo ({checked} frames checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
