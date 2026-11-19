#!/usr/bin/env python3
"""ref_tri.py -- test vectors for tb_tri_raster (Chapter 4.6).

Usage (from the repo root, once; commit the two files it writes):
    .venv/bin/python sim/py/ref_tri.py sim

Writes:
    sim/tri_vectors.hex  10,000 triangles for a 64x64 frame, one per line, as 36 hex digits
                         x0 y0 x1 y1 x2 y2 (16 bits each, two's complement), count (16 bits),
                         hash (32 bits).  count and hash describe the covered pixels:
                         their number, and the sum mod 2^32 of pixel_hash(x, y) over them.
    sim/tri_grid.hex     grids of triangles that tile the frame with shared edges, same
                         layout; the 'count' field holds the grid number instead.  Every pixel
                         of the frame must be covered exactly once per grid.

    --show N             print triangle N of tri_vectors.hex and its expected coverage.
"""
import os
import random
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # raster_model

import raster_model as rm  # noqa: E402

W = H = 64
N_TOTAL = 10000
SEED = 4646


def rot(rng, v):
    """Random vertex order: any rotation, and a random flip (which reverses the winding)."""
    v = list(v)
    k = rng.randrange(3)
    v = v[k:] + v[:k]
    if rng.random() < 0.5:
        v[1], v[2] = v[2], v[1]
    return v


def gen_vectors(rng):
    tris = []   # (category, [v0, v1, v2])

    def add(cat, v):
        tris.append((cat, rot(rng, v)))

    # Directed cases first: the worked example and the fill-rule corners.
    add("directed", [(0, 0), (0, 4), (4, 0)])
    add("directed", [(0, 0), (4, 0), (0, 4)])
    add("directed", [(10, 10), (30, 10), (10, 30)])
    add("directed", [(10, 10), (30, 30), (10, 30)])
    add("directed", [(0, 0), (63, 0), (63, 63)])
    add("directed", [(0, 0), (63, 63), (0, 63)])
    add("directed", [(-5, -5), (70, -5), (30, 70)])
    add("directed", [(20, 20), (21, 20), (20, 21)])
    add("directed", [(20, 20), (21, 21), (20, 22)])
    add("directed", [(63, 63), (63, 63), (63, 62)])

    while len(tris) < N_TOTAL:
        r = rng.random()
        pt = lambda lo, hi: (rng.randint(lo, hi), rng.randint(lo, hi))
        if r < 0.40:
            add("random", [pt(-64, 127) for _ in range(3)])
        elif r < 0.55:
            add("inframe", [pt(0, 63) for _ in range(3)])
        elif r < 0.67:
            bx, by = pt(-4, 66)
            add("small", [(bx + rng.randint(-4, 4), by + rng.randint(-4, 4)) for _ in range(3)])
        elif r < 0.75:
            # thin slivers: two long edges almost on top of each other
            a, b = pt(0, 63), pt(0, 63)
            c = (a[0] + (b[0] - a[0]) // 2 + rng.randint(-1, 1), a[1] + (b[1] - a[1]) // 2 + rng.randint(-1, 1))
            add("sliver", [a, b, c])
        elif r < 0.83:
            # axis-aligned edges: horizontal and vertical edges, all top/left/bottom/right cases
            x0, y0 = pt(-8, 60)
            dx, dy = rng.randint(1, 30), rng.randint(1, 30)
            sx, sy = rng.choice((-1, 1)), rng.choice((-1, 1))
            corner = [(x0, y0), (x0 + sx * dx, y0), (x0, y0 + sy * dy)]
            add("axis", corner)
        elif r < 0.88:
            # a diagonal 45-degree edge: pixels exactly on the edge
            x0, y0 = pt(-8, 60)
            n = rng.randint(1, 30)
            add("diag", [(x0, y0), (x0 + n, y0 + n), (x0 + rng.randint(-20, 20), y0 + n + rng.randint(1, 20))])
        elif r < 0.93:
            # degenerate: collinear, repeated points, zero width or height
            k = rng.randrange(6)
            if k == 0:
                a = pt(-10, 74)
                add("degen", [a, a, a])
            elif k == 1:
                a, b = pt(-10, 74), pt(-10, 74)
                add("degen", [a, a, b])
            elif k == 2:
                x = rng.randint(-10, 74)
                add("degen", [(x, rng.randint(-10, 74)) for _ in range(3)])        # zero width
            elif k == 3:
                y = rng.randint(-10, 74)
                add("degen", [(rng.randint(-10, 74), y) for _ in range(3)])        # zero height
            else:
                a = pt(-10, 40)
                d = (rng.randint(-6, 6), rng.randint(-6, 6))
                if d == (0, 0):
                    d = (1, 2)
                ts = [rng.randint(-3, 6) for _ in range(3)]
                add("degen", [(a[0] + t * d[0], a[1] + t * d[1]) for t in ts])    # collinear
        else:
            # int16 extremes: widths, clamping and sign handling
            ext = [-32768, -32767, -1, 0, 63, 64, 32766, 32767]
            def c():
                if rng.random() < 0.7:
                    return rng.choice(ext)
                return rng.randint(-32768, 32767)
            add("extreme", [(c(), c()) for _ in range(3)])
    return tris


def gen_grids(rng):
    """Grids of jittered quads, each split into two triangles on a random diagonal, with random
    vertex order and winding.  The grid reaches past every side of the frame."""
    out = []   # (grid number, [v0, v1, v2])
    specs = [(8, 0), (8, 2), (5, 1), (6, 0), (7, 2), (16, 0), (16, 5), (4, 1)]
    g = 0
    for rep in range(3):
        for s, j in specs:
            n = (W + 2 * s) // s + 2           # vertices per side
            gv = [[(-s + ix * s + rng.randint(-j, j), -s + iy * s + rng.randint(-j, j))
                   for ix in range(n)] for iy in range(n)]
            for iy in range(n - 1):
                for ix in range(n - 1):
                    a, b, c, d = gv[iy][ix], gv[iy][ix + 1], gv[iy + 1][ix + 1], gv[iy + 1][ix]
                    if rng.random() < 0.5:
                        pair = ([a, b, c], [a, c, d])
                    else:
                        pair = ([a, b, d], [b, c, d])
                    for t in pair:
                        out.append((g, rot(rng, t)))
            g += 1
    return out


def fmt(v, count, h):
    word = 0
    for x, y in v:
        word = (word << 32) | ((x & 0xFFFF) << 16) | (y & 0xFFFF)
    return "%024x%04x%08x" % (word, count & 0xFFFF, h & 0xFFFFFFFF)


def main():
    args = sys.argv[1:]
    show = None
    if "--show" in args:
        i = args.index("--show")
        show = int(args[i + 1])
        del args[i:i + 2]
    outdir = args[0] if args else "sim"
    rng = random.Random(SEED)
    tris = gen_vectors(rng)

    if show is not None:
        cat, v = tris[show]
        px = set(rm.tri_pixels(*v, W, H))
        print("triangle %d (%s): %s, area2 = %d, %d pixels" % (show, cat, v, rm.area2(*v), len(px)))
        for y in range(H):
            print("".join("#" if (x, y) in px else "." for x in range(W)))
        return

    lines = []
    stats = {}
    npix = ncw = 0
    for cat, v in tris:
        px = rm.tri_pixels(*v, W, H)
        assert len(set(px)) == len(px)
        cnt, hsh = rm.signature(px, W)
        lines.append(fmt(v, cnt, hsh))
        s = stats.setdefault(cat, [0, 0, 0])
        s[0] += 1
        s[1] += cnt == 0
        npix += cnt
        ncw += rm.area2(*v) < 0
    # Cross-check: the stepped form agrees with the definition on a sample.
    for cat, v in rng.sample(tris, 300):
        assert rm.tri_pixels(*v, W, H) == rm.tri_pixels_stepped(*v, W, H)

    grid = gen_grids(random.Random(SEED + 1))
    glines = []
    cover = {}
    for g, v in grid:
        glines.append(fmt(v, g, 0))
        for p in rm.tri_pixels(*v, W, H):
            cover.setdefault(g, {}).setdefault(p, 0)
            cover[g][p] += 1
    ngrids = max(g for g, _ in grid) + 1
    for g in range(ngrids):
        assert len(cover[g]) == W * H and set(cover[g].values()) == {1}, "grid %d is not an exact tiling" % g

    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "tri_vectors.hex"), "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(outdir, "tri_grid.hex"), "w") as f:
        f.write("\n".join(glines) + "\n")
    print("ref_tri: %d triangles (%d clockwise, %d pixels in all) -> %s/tri_vectors.hex"
          % (len(lines), ncw, npix, outdir))
    print("  " + ", ".join("%s %d (%d empty)" % (k, v[0], v[1]) for k, v in sorted(stats.items())))
    print("ref_tri: %d grids, %d triangles, every pixel covered exactly once -> %s/tri_grid.hex"
          % (ngrids, len(glines), outdir))


if __name__ == "__main__":
    main()
