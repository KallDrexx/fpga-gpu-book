#!/usr/bin/env python3
"""ref_interp.py -- test vectors for tb_tri_shade (Chapter 6.3).

Usage (from the repo root, once; commit the two files it writes):
    .venv/bin/python sim/py/ref_interp.py sim

Writes:
    sim/interp_small.hex  3,000 triangles for a 64x64 frame
    sim/interp_big.hex      100 triangles for the real 320x240 frame (long spans, int16 corners)

Each line is 66 hex digits (264 bits), most significant field first:
    x0 y0 x1 y1 x2 y2   16 bits each, two's complement
    z0 z1 z2            16 bits each, unsigned
    i0 i1 i2             8 bits each (Q5.3 intensity)
    shaded               8 bits: 1 = interpolate (i_shaded = 1), 0 = a flat triangle (z and i are not checked)
    count               24 bits: covered pixels
    hash                32 bits: sum  over the pixels of raster_model.shade_pixel_hash(...)[0]  (mod 2^32)
    xhash               32 bits: xor  over the pixels of raster_model.shade_pixel_hash(...)[1]
A pixel is the tuple (x, y, z, i) of raster_model.shade_pixels(); for a flat triangle z = i = 0.
The sum and the xor are independent, so a single wrong z or i anywhere in a triangle changes them.

    --show N [--big]      print the expected pixels of triangle N (first 40, then the intensity as a picture)
"""
import os
import random
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # raster_model

import raster_model as rm  # noqa: E402

N_SMALL, N_BIG = 3000, 100
SEED = 6363


def rot(rng, v):
    """Random vertex order: any rotation and a random flip (which reverses the winding); attributes stay with their corners."""
    v = list(v)
    k = rng.randrange(3)
    v = v[k:] + v[:k]
    if rng.random() < 0.5:
        v[1], v[2] = v[2], v[1]
    return v


def gen_small(rng):
    out = []   # (category, shaded, [vertices (x, y, z, i)])

    def add(cat, v, shaded=1):
        out.append((cat, shaded, rot(rng, v)))

    def rz():
        return rng.choice((0, 65535, rng.randint(0, 65535), rng.randint(0, 65535), rng.randint(0, 255)))

    def ri():
        return rng.choice((0, 255, rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 40), 248))

    def pt(lo, hi):
        return rng.randint(lo, hi), rng.randint(lo, hi)

    def at(p):
        return (p[0], p[1], rz(), ri())

    # directed: the worked examples, extreme attributes, a smooth ramp, equal attributes
    add("directed", [(0, 0, 0, 0), (0, 4, 65535, 255), (4, 0, 32768, 128)])
    add("directed", [(0, 0, 0, 0), (4, 0, 32768, 128), (0, 4, 65535, 255)])
    add("directed", [(10, 10, 0, 0), (50, 10, 65535, 124), (10, 50, 30000, 248)])
    add("directed", [(0, 0, 100, 0), (63, 0, 100, 248), (63, 63, 100, 0)])
    add("directed", [(-5, -5, 0, 0), (70, -5, 65535, 248), (30, 70, 1000, 255)])
    add("directed", [(20, 20, 5, 7), (21, 20, 6, 8), (20, 21, 7, 9)])
    add("directed", [(20, 20, 65535, 255), (21, 21, 0, 0), (20, 22, 65535, 0)])
    add("directed", [(63, 63, 1, 1), (63, 63, 2, 2), (63, 62, 3, 3)])
    add("directed", [(5, 5, 0, 31 * 8 + 4), (60, 8, 0, 0), (30, 60, 0, 255)])
    add("directed", [(2, 2, 1234, 80), (60, 2, 1234, 80), (2, 60, 1234, 80)])        # equal attributes

    while len(out) < N_SMALL:
        r = rng.random()
        if r < 0.28:
            add("random", [at(pt(-64, 127)) for _ in range(3)])
        elif r < 0.40:
            add("inframe", [at(pt(0, 63)) for _ in range(3)])
        elif r < 0.50:
            bx, by = pt(-4, 66)
            add("small", [at((bx + rng.randint(-4, 4), by + rng.randint(-4, 4))) for _ in range(3)])
        elif r < 0.60:
            # thin slivers: very large gradients across the thin direction
            a, b = pt(0, 63), pt(0, 63)
            c = (a[0] + (b[0] - a[0]) // 2 + rng.randint(-1, 1), a[1] + (b[1] - a[1]) // 2 + rng.randint(-1, 1))
            add("sliver", [at(a), at(b), at(c)])
        elif r < 0.68:
            x0, y0 = pt(-8, 60)
            dx, dy = rng.randint(1, 30), rng.randint(1, 30)
            sx, sy = rng.choice((-1, 1)), rng.choice((-1, 1))
            add("axis", [at((x0, y0)), at((x0 + sx * dx, y0)), at((x0, y0 + sy * dy))])
        elif r < 0.74:
            # equal attributes: constant z and i (zero gradients), possibly at the extremes
            z, i = rng.choice((0, 65535, rng.randint(0, 65535))), rng.choice((0, 255, 248, rng.randint(0, 255)))
            add("equal", [(*pt(-20, 84), z, i) for _ in range(3)])
        elif r < 0.78:
            # one attribute equal, the other free
            z, i = rng.randint(0, 65535), rng.randint(0, 255)
            if rng.random() < 0.5:
                add("equalz", [(*pt(-20, 84), z, ri()) for _ in range(3)])
            else:
                add("equali", [(*pt(-20, 84), rz(), i) for _ in range(3)])
        elif r < 0.84:
            # degenerate: collinear, repeated points, zero width or height
            k = rng.randrange(6)
            if k == 0:
                a = pt(-10, 74)
                add("degen", [at(a), at(a), at(a)])
            elif k == 1:
                a, b = pt(-10, 74), pt(-10, 74)
                add("degen", [at(a), at(a), at(b)])
            elif k == 2:
                x = rng.randint(-10, 74)
                add("degen", [at((x, rng.randint(-10, 74))) for _ in range(3)])
            elif k == 3:
                y = rng.randint(-10, 74)
                add("degen", [at((rng.randint(-10, 74), y)) for _ in range(3)])
            else:
                a = pt(-10, 40)
                d = (rng.randint(-6, 6), rng.randint(-6, 6))
                if d == (0, 0):
                    d = (1, 2)
                ts = [rng.randint(-3, 6) for _ in range(3)]
                add("degen", [at((a[0] + t * d[0], a[1] + t * d[1])) for t in ts])
        elif r < 0.92:
            # large triangles whose corners are far off the frame (int16 range): long extrapolation from the corner
            ext = [-32768, -32767, -1000, -1, 0, 63, 64, 1000, 32766, 32767]

            def c():
                if rng.random() < 0.7:
                    return rng.choice(ext)
                return rng.randint(-32768, 32767)
            add("extreme", [(c(), c(), rz(), ri()) for _ in range(3)])
        elif r < 0.97:
            # large but nearer: corners a few hundred pixels away
            add("far", [at(pt(-600, 700)) for _ in range(3)])
        else:
            add("flat", [(*pt(-30, 94), 0, 0) for _ in range(3)], shaded=0)
    return out


def gen_big(rng):
    out = []

    def add(cat, v, shaded=1):
        out.append((cat, shaded, rot(rng, v)))

    # the longest spans: a whole 320x240 box walked by additions
    add("screen", [(0, 0, 0, 0), (319, 0, 65535, 248), (0, 239, 30000, 255)])
    add("screen", [(0, 0, 65535, 0), (319, 0, 0, 248), (319, 239, 32768, 124)])
    add("screen", [(-1, -1, 0, 0), (400, 100, 65535, 255), (100, 300, 40000, 17)])
    add("equal", [(0, 0, 777, 99), (319, 0, 777, 99), (0, 239, 777, 99)])
    add("corner", [(-32768, -32768, 0, 0), (32767, -32768, 65535, 248), (-32768, 32767, 20000, 255)])
    add("corner", [(-32768, -32768, 65535, 255), (32767, 32767, 0, 0), (-32768, 32767, 12345, 100)])
    add("corner", [(32767, 32767, 65535, 0), (-32768, 32767, 0, 255), (32767, -32768, 40000, 130)])
    add("corner", [(-32768, 0, 0, 0), (32767, 239, 65535, 255), (0, 32767, 100, 248)])
    add("sliver", [(0, 0, 0, 0), (319, 239, 65535, 255), (150, 112, 30000, 100)])
    add("sliver", [(0, 120, 0, 0), (319, 121, 65535, 255), (160, 120, 1, 2)])
    while len(out) < N_BIG:
        r = rng.random()
        ext = [-32768, -32767, -20000, -1, 0, 319, 320, 20000, 32766, 32767]
        rz = lambda: rng.choice((0, 65535, rng.randint(0, 65535), rng.randint(0, 65535)))
        ri = lambda: rng.choice((0, 255, rng.randint(0, 255), rng.randint(0, 255), 248))
        if r < 0.4:
            add("random", [(rng.randint(-100, 420), rng.randint(-100, 340), rz(), ri()) for _ in range(3)])
        elif r < 0.7:
            add("extreme", [(rng.choice(ext), rng.choice(ext), rz(), ri()) for _ in range(3)])
        elif r < 0.85:
            add("onscreen", [(rng.randint(0, 319), rng.randint(0, 239), rz(), ri()) for _ in range(3)])
        else:
            add("far", [(rng.randint(-3000, 3300), rng.randint(-3000, 3300), rz(), ri()) for _ in range(3)])
    return out


def pixels_of(shaded, v, w, h):
    if shaded:
        return rm.shade_pixels(*v, w, h)
    return [(x, y, 0, 0) for x, y in rm.tri_pixels(*[(p[0], p[1]) for p in v], w, h)]


def fmt(shaded, v, cnt, hs, hx):
    word = 0
    for x, y, _, _ in v:
        word = (word << 32) | ((x & 0xFFFF) << 16) | (y & 0xFFFF)
    for _, _, z, _ in v:
        word = (word << 16) | (z & 0xFFFF)
    for _, _, _, i in v:
        word = (word << 8) | (i & 0xFF)
    word = (word << 8) | shaded
    word = (word << 24) | cnt
    word = (word << 32) | hs
    word = (word << 32) | hx
    return "%066x" % word


def main():
    args = sys.argv[1:]
    show = None
    big = "--big" in args
    if big:
        args.remove("--big")
    if "--show" in args:
        i = args.index("--show")
        show = int(args[i + 1])
        del args[i:i + 2]
    outdir = args[0] if args else "sim"
    rng = random.Random(SEED)
    small = gen_small(rng)
    bigs = gen_big(random.Random(SEED + 1))

    if show is not None:
        cat, shaded, v = (bigs if big else small)[show]
        w, h = (320, 240) if big else (64, 64)
        px = pixels_of(shaded, v, w, h)
        print("triangle %d (%s, %s): %s, area2 = %d, %d pixels (x, y, z, i):" %
              (show, cat, "shaded" if shaded else "flat", v, rm.area2(*v), len(px)))
        for p in px[:40]:
            print("  ", p)
        if px and not big:
            d = {(x, y): i for x, y, _, i in px}
            for y in range(h):
                print("".join("." if (x, y) not in d else "0123456789abcdefghijklmnopqrstuv"[d[(x, y)]] for x in range(w)))
        return

    for name, tris, w, h in (("small", small, 64, 64), ("big", bigs, 320, 240)):
        lines, stats, npix = [], {}, 0
        for cat, shaded, v in tris:
            px = pixels_of(shaded, v, w, h)
            assert len({(x, y) for x, y, _, _ in px}) == len(px)
            cnt, hs, hx = rm.shade_signature(px, w)
            lines.append(fmt(shaded, v, cnt, hs, hx))
            s = stats.setdefault(cat, [0, 0])
            s[0] += 1
            s[1] += cnt == 0
            npix += cnt
        # cross-check: the stepped (wrapping) form agrees with the definition on a sample
        for cat, shaded, v in rng.sample(tris, 120 if name == "small" else 25):
            if shaded:
                assert rm.shade_pixels(*v, w, h) == rm.shade_pixels_stepped(*v, w, h)
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "interp_%s.hex" % name), "w") as f:
            f.write("\n".join(lines) + "\n")
        print("ref_interp: %d triangles (%d pixels in all) -> %s/interp_%s.hex" % (len(lines), npix, outdir, name))
        print("  " + ", ".join("%s %d (%d empty)" % (k, a, b) for k, (a, b) in sorted(stats.items())))


if __name__ == "__main__":
    main()
