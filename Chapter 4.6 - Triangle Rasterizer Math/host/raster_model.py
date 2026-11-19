#!/usr/bin/env python3
"""raster_model.py -- Python model of the triangle rasterizer (Chapters 4.6, 4.7).

Conventions (Chapter 4.6):
  * Pixel (x, y) is sampled at the integer point (x, y).  Vertices are integers.
  * y grows downward.  E(p0, p1, x, y) = (x-x0)*(y1-y0) - (y-y0)*(x1-x0).
  * A triangle is normalized to positive area (area2 > 0) by swapping v1 and v2.
    Zero area draws nothing.
  * Inside means E_i + bias_i >= 0 for all three edges (v0->v1, v1->v2, v2->v0),
    bias = 0 on a top or left edge and -1 on any other edge.
        left edge: dy > 0        top edge: dy == 0 and dx < 0
  * The frame is [0, w) x [0, h); only the part of the bounding box inside it is
    visited.
  * A vertex is any tuple whose first two entries are x and y.  Extra entries
    (a later chapter's depth or intensity) travel with the vertex through
    normalize(), so they stay attached to the right corner.

Python integers never overflow, so this model is also the reference for the bit
widths: see max_abs_edge() and the self-check at the bottom.

Run `python host/raster_model.py` for the self-check.
"""
import random
import sys

sys.dont_write_bytecode = True


def edge(p0, p1, x, y):
    """Edge function of the directed edge p0 -> p1 at (x, y)."""
    return (x - p0[0]) * (p1[1] - p0[1]) - (y - p0[1]) * (p1[0] - p0[0])


def area2(v0, v1, v2):
    """Twice the signed area: edge(v0, v1) evaluated at v2."""
    return edge(v0, v1, v2[0], v2[1])


def normalize(v0, v1, v2):
    """Return the vertices reordered to positive area, or None if degenerate."""
    a = area2(v0, v1, v2)
    if a == 0:
        return None
    return (v0, v1, v2) if a > 0 else (v0, v2, v1)


def is_top_left(p0, p1):
    """For a positive-area triangle, is the directed edge p0 -> p1 a top or left edge?"""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    return dy > 0 or (dy == 0 and dx < 0)


def edges(v0, v1, v2):
    """The three directed edges of a normalized triangle as (p0, p1, bias)."""
    out = []
    for p0, p1 in ((v0, v1), (v1, v2), (v2, v0)):
        out.append((p0, p1, 0 if is_top_left(p0, p1) else -1))
    return out


def covers(tri, x, y):
    """Is pixel (x, y) covered by the (already normalized) triangle?"""
    return all(edge(p0, p1, x, y) + b >= 0 for p0, p1, b in edges(*tri))


def bbox(tri, w, h):
    """Bounding box clamped to the frame: (xmin, ymin, xmax, ymax), inclusive,
    or None if it is empty."""
    xs = [v[0] for v in tri]
    ys = [v[1] for v in tri]
    xmin, xmax = max(min(xs), 0), min(max(xs), w - 1)
    ymin, ymax = max(min(ys), 0), min(max(ys), h - 1)
    if xmin > xmax or ymin > ymax:
        return None
    return xmin, ymin, xmax, ymax


def tri_pixels(v0, v1, v2, w, h):
    """Covered pixels of the triangle, in raster order (row by row), as (x, y).
    This is the definition; it evaluates the edge functions the slow way."""
    tri = normalize(v0, v1, v2)
    if tri is None:
        return []
    bb = bbox(tri, w, h)
    if bb is None:
        return []
    xmin, ymin, xmax, ymax = bb
    return [(x, y) for y in range(ymin, ymax + 1) for x in range(xmin, xmax + 1)
            if covers(tri, x, y)]


def tri_pixels_stepped(v0, v1, v2, w, h):
    """The same pixels, found the way the hardware does: three edge values at
    the corner of the box, then only additions.  Used by the self-check."""
    tri = normalize(v0, v1, v2)
    if tri is None:
        return []
    bb = bbox(tri, w, h)
    if bb is None:
        return []
    xmin, ymin, xmax, ymax = bb
    es = edges(*tri)
    dxs = [p1[0] - p0[0] for p0, p1, _ in es]
    dys = [p1[1] - p0[1] for p0, p1, _ in es]
    bias = [b for _, _, b in es]
    row = [edge(p0, p1, xmin, ymin) + b for p0, p1, b in es]
    out = []
    for y in range(ymin, ymax + 1):
        e = list(row)
        for x in range(xmin, xmax + 1):
            if e[0] >= 0 and e[1] >= 0 and e[2] >= 0:
                out.append((x, y))
            for i in range(3):
                e[i] += dys[i]          # one step in x
        for i in range(3):
            row[i] -= dxs[i]            # one step in y
    return out


def pixel_hash(x, y, w):
    """Order-independent signature term for one pixel (the testbench has the same)."""
    m = ((y * w + x + 1) * 0x9E3779B1) & 0xFFFFFFFF
    return m ^ (m >> 15)


def signature(pixels, w):
    """(count, sum of pixel_hash mod 2^32): a set signature, valid when no pixel repeats."""
    return len(pixels), sum(pixel_hash(x, y, w) for x, y in pixels) & 0xFFFFFFFF


def max_abs_edge(w=320, h=240, lo=-32768, hi=32767):
    """Largest |E| at a pixel inside the frame, and largest |area2|, for vertices
    in [lo, hi].  Both are attained at corners, so checking corners is exact."""
    corners_v = [lo, hi]
    corners_p = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    best_e = best_a = 0
    for x0 in corners_v:
        for y0 in corners_v:
            for x1 in corners_v:
                for y1 in corners_v:
                    for px, py in corners_p:
                        best_e = max(best_e, abs(edge((x0, y0), (x1, y1), px, py)))
                    for x2 in corners_v:
                        for y2 in corners_v:
                            best_a = max(best_a, abs(area2((x0, y0), (x1, y1), (x2, y2))))
    return best_e, best_a


def signed_bits(n):
    """Fewest bits of a two's complement number that can hold every value in [-n, n]."""
    b = 1
    while not (-(1 << (b - 1)) <= -n and n <= (1 << (b - 1)) - 1):
        b += 1
    return b


def _self_check():
    # The worked example of Chapter 4.6.
    assert area2((0, 0), (0, 4), (4, 0)) == 16
    assert area2((0, 0), (4, 0), (0, 4)) == -16
    assert normalize((0, 0), (4, 0), (0, 4)) == ((0, 0), (0, 4), (4, 0))
    assert normalize((0, 0), (2, 2), (4, 4)) is None
    assert is_top_left((4, 0), (0, 0)) and is_top_left((0, 0), (0, 4))
    assert not is_top_left((0, 4), (4, 0))
    p = tri_pixels((0, 0), (0, 4), (4, 0), 64, 64)
    assert len(p) == 10 and (0, 0) in p and (4, 0) not in p and (0, 4) not in p and (3, 0) in p
    assert tri_pixels((0, 0), (4, 0), (0, 4), 64, 64) == p       # same triangle, clockwise
    assert tri_pixels((5, 5), (5, 5), (5, 5), 64, 64) == []
    assert tri_pixels((0, 0), (9, 9), (3, 3), 64, 64) == []

    rng = random.Random(1)
    for _ in range(300):
        v = [(rng.randint(-20, 84), rng.randint(-20, 84)) for _ in range(3)]
        a = tri_pixels(*v, 64, 64)
        assert a == tri_pixels_stepped(*v, 64, 64)
        assert a == sorted(a, key=lambda q: (q[1], q[0]))
        tri = normalize(*v)
        if tri:
            # The edge values add up to the area at every point.
            x, y = rng.randint(-50, 100), rng.randint(-50, 100)
            assert sum(edge(p0, p1, x, y) for p0, p1, _ in edges(*tri)) == area2(*tri)
            # Mirror image: the same triangle given in the other order, same pixels.
            assert tri_pixels(v[0], v[2], v[1], 64, 64) == a

    # Two triangles sharing an edge never both cover a pixel (the grids in ref_tri.py check for gaps).
    for _ in range(200):
        a, b = (rng.randint(0, 40), rng.randint(0, 40)), (rng.randint(0, 40), rng.randint(0, 40))
        c, d = (rng.randint(0, 40), rng.randint(0, 40)), (rng.randint(0, 40), rng.randint(0, 40))
        if area2(a, b, c) * area2(a, b, d) >= 0:      # c and d must be on opposite sides of a-b
            continue
        s1 = set(tri_pixels(a, b, c, 64, 64))
        s2 = set(tri_pixels(b, a, d, 64, 64))
        assert not (s1 & s2)

    e, a = max_abs_edge()
    print("raster_model: self-check OK")
    print("  screen 320x240, vertices int16: max |E| = %d (%d signed bits), "
          "max |area2| = %d (%d signed bits)" % (e, signed_bits(e), a, signed_bits(a)))


if __name__ == "__main__":
    _self_check()
