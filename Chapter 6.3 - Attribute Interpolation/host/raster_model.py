#!/usr/bin/env python3
"""raster_model.py -- Python model of the triangle rasterizer (Chapters 4.6, 4.7, 6.3).

Chapter 6.3 adds attribute interpolation (section "Attribute interpolation" at the end of
this file): shade_pixels() gives every covered pixel its interpolated depth and intensity,
in exactly the fixed-point arithmetic of the hardware.  Copy this file to host/raster_model.py,
replacing the Chapter 4.6 version; everything the old one did is unchanged.

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


# ---------------------------------------------------------------- attribute interpolation (Chapter 6.3)
#
# A shaded vertex is (x, y, z, i): z is an unsigned 16-bit depth, i is an 8-bit intensity in Q5.3
# (i / 8 shades, so 0 to 31.875).  The triangle must be normalized first (positive area2).
#
# Barycentric form:  A(P) = A0 + (E20(P) * (A1 - A0) + E01(P) * (A2 - A0)) / area2.
# Stepping form:     dA/dx = ((y0-y2) * (A1-A0) + (y1-y0) * (A2-A0)) / area2
#                    dA/dy = ((x2-x0) * (A1-A0) + (x0-x1) * (A2-A0)) / area2
# Each gradient is rounded once, to FRAC fraction bits, round half up (the Chapter 6.2 divider with
# no saturation).  The attribute at the corner (xmin, ymin) of the clamped bounding box is
#     A0 * 2^FRAC + gx * (xmin - x0) + gy * (ymin - y0)
# and pixel (x, y) has   start + gx * (x - xmin) + gy * (y - ymin)   (the hardware adds gx per step
# in x and gy per row; the sum is the same integer).  The pixel's value is that integer rounded
# to the output format (z: 16 bits, i: 5 bits), half up, clamped.
FRAC = 16                    # fraction bits of gradients and accumulators
I_FRAC = 3                   # fraction bits of the vertex intensity
Z_MAX = 65535                # depth range 0 .. 65535
SHADE_MAX = 31               # intensity range 0 .. 31 (32 shades per palette ramp)
Z_ACC_BITS = 34              # hardware accumulator widths: the in-triangle value always fits,
I_ACC_BITS = 26              # and the (huge) extrapolated values outside wrap harmlessly


def rdiv(num, den):
    """num * 2^FRAC / den rounded half up, den > 0 (the divider of Chapter 6.2 with a wide enough result)."""
    return ((num << (FRAC + 1)) + den) // (2 * den)


def attr_gradients(tri, k):
    """(gx, gy) of attribute number k (2 = z, 3 = i) of a normalized triangle, as integers with FRAC fraction bits."""
    (x0, y0), (x1, y1), (x2, y2) = [(v[0], v[1]) for v in tri]
    a0, a1, a2 = tri[0][k], tri[1][k], tri[2][k]
    d1, d2 = a1 - a0, a2 - a0
    a2x = area2(*tri)
    gx_num = (y0 - y2) * d1 + (y1 - y0) * d2
    gy_num = (x2 - x0) * d1 + (x0 - x1) * d2
    return rdiv(gx_num, a2x), rdiv(gy_num, a2x)


def attr_start(tri, k, gx, gy, xmin, ymin):
    """The attribute (FRAC fraction bits) at pixel (xmin, ymin), from vertex 0 and the rounded gradients."""
    v0 = tri[0]
    return (v0[k] << FRAC) + gx * (xmin - v0[0]) + gy * (ymin - v0[1])


def z_out(acc):
    """Accumulator -> 16-bit depth: round half up, clamp."""
    return min(max((acc + (1 << (FRAC - 1))) >> FRAC, 0), Z_MAX)


def i_out(acc):
    """Accumulator (Q5.3 intensity with FRAC more bits) -> 5-bit shade: round half up, clamp."""
    return min(max((acc + (1 << (FRAC + I_FRAC - 1))) >> (FRAC + I_FRAC), 0), SHADE_MAX)


def shade_setup(v0, v1, v2, w, h):
    """Everything the hardware computes before the first pixel: (tri, box, (gxz, gyz, gxi, gyi), (z_start, i_start)),
    or None when nothing is drawn (zero area or empty box)."""
    tri = normalize(v0, v1, v2)
    if tri is None:
        return None
    bb = bbox(tri, w, h)
    if bb is None:
        return None
    gxz, gyz = attr_gradients(tri, 2)
    gxi, gyi = attr_gradients(tri, 3)
    zs = attr_start(tri, 2, gxz, gyz, bb[0], bb[1])
    is_ = attr_start(tri, 3, gxi, gyi, bb[0], bb[1])
    return tri, bb, (gxz, gyz, gxi, gyi), (zs, is_)


def shade_pixels(v0, v1, v2, w, h):
    """Covered pixels with their attributes, in raster order, as (x, y, z, i).  Vertices are (x, y, z, i).
    Same pixels as tri_pixels(); this is the definition of the shaded triangle."""
    st = shade_setup(v0, v1, v2, w, h)
    if st is None:
        return []
    tri, (xmin, ymin, xmax, ymax), (gxz, gyz, gxi, gyi), (zs, is_) = st
    out = []
    for y in range(ymin, ymax + 1):
        for x in range(xmin, xmax + 1):
            if covers(tri, x, y):
                az = zs + gxz * (x - xmin) + gyz * (y - ymin)
                ai = is_ + gxi * (x - xmin) + gyi * (y - ymin)
                out.append((x, y, z_out(az), i_out(ai)))
    return out


def shade_pixels_stepped(v0, v1, v2, w, h, wrap=True):
    """The same list, found the way the hardware does: edge values and both attributes stepped by additions.
    With wrap=True the accumulators are Z_ACC_BITS / I_ACC_BITS wide and wrap, as in the hardware."""
    st = shade_setup(v0, v1, v2, w, h)
    if st is None:
        return []
    tri, (xmin, ymin, xmax, ymax), (gxz, gyz, gxi, gyi), (zs, is_) = st
    es = edges(*tri)
    dxs = [p1[0] - p0[0] for p0, p1, _ in es]
    dys = [p1[1] - p0[1] for p0, p1, _ in es]
    erow = [edge(p0, p1, xmin, ymin) + b for p0, p1, b in es]

    def sext(v, bits):
        v &= (1 << bits) - 1
        return v - (1 << bits) if v >> (bits - 1) else v
    wz = (lambda v: sext(v, Z_ACC_BITS)) if wrap else (lambda v: v)
    wi = (lambda v: sext(v, I_ACC_BITS)) if wrap else (lambda v: v)
    zrow, irow = wz(zs), wi(is_)
    out = []
    for y in range(ymin, ymax + 1):
        e, az, ai = list(erow), zrow, irow
        for x in range(xmin, xmax + 1):
            if e[0] >= 0 and e[1] >= 0 and e[2] >= 0:
                out.append((x, y, z_out(az), i_out(ai)))
            for i in range(3):
                e[i] += dys[i]
            az, ai = wz(az + gxz), wi(ai + gxi)
        for i in range(3):
            erow[i] -= dxs[i]
        zrow, irow = wz(zrow + gyz), wi(irow + gyi)
    return out


def shade_pixel_hash(x, y, z, i, w):
    """Order-independent signature terms for one shaded pixel (tb_tri_shade has the same): (sum term, xor term)."""
    m32 = 0xFFFFFFFF
    a = ((y * w + x + 1) * 0x9E3779B1) & m32
    b = ((z + 1) * 0x85EBCA6B) & m32
    c = ((i + 1) * 0xC2B2AE35) & m32
    m = (((a ^ b) + c) & m32)
    m ^= m >> 15
    m = (m * 0x2C1B3C6D) & m32
    m ^= m >> 12
    n = (((a + b) & m32) ^ c) & m32
    n = (n * 0x297A2D39) & m32
    n ^= n >> 13
    return m, n


def shade_signature(pixels, w):
    """(count, sum of the first hash, xor of the second hash) over the (x, y, z, i) pixels."""
    s = x = 0
    for px, py, pz, pi in pixels:
        m, n = shade_pixel_hash(px, py, pz, pi, w)
        s = (s + m) & 0xFFFFFFFF
        x ^= n
    return len(pixels), s, x


def interp_error(v0, v1, v2, w, h):
    """Largest distance, over the covered pixels, between the fixed-point accumulator and the exact barycentric
    value, in output units (z steps, Q5.3 intensity steps).  Returns (max z error, max i error)."""
    from fractions import Fraction
    st = shade_setup(v0, v1, v2, w, h)
    if st is None:
        return 0.0, 0.0
    tri, (xmin, ymin, xmax, ymax), (gxz, gyz, gxi, gyi), (zs, is_) = st
    a2x = area2(*tri)
    (p0, p1, p2) = tri
    ez = ei = Fraction(0)
    for y in range(ymin, ymax + 1):
        for x in range(xmin, xmax + 1):
            if not covers(tri, x, y):
                continue
            e20, e01 = edge(p2, p0, x, y), edge(p0, p1, x, y)
            for k, g1, g2, s, cur in ((2, gxz, gyz, zs, "z"), (3, gxi, gyi, is_, "i")):
                exact = Fraction(p0[k] * a2x + e20 * (p1[k] - p0[k]) + e01 * (p2[k] - p0[k]), a2x)
                acc = s + g1 * (x - xmin) + g2 * (y - ymin)
                err = abs(Fraction(acc, 1 << FRAC) - exact)
                if cur == "z":
                    ez = max(ez, err)
                else:
                    ei = max(ei, err)
    return float(ez), float(ei)


def max_gradient_bits():
    """Worst-case widths behind the hardware: (numerator bits, quotient bits) for z and for i, signed."""
    zn = 2 * 65535 * 65535
    inum = 2 * 65535 * 255
    return (signed_bits(zn), signed_bits(zn << FRAC)), (signed_bits(inum), signed_bits(inum << FRAC))


def _self_check_shading():
    rng = random.Random(63)
    # A flat triangle (equal attributes) has zero gradients and a constant value.
    px = shade_pixels((0, 0, 1000, 80), (0, 9, 1000, 80), (9, 0, 1000, 80), 64, 64)
    assert px and all(z == 1000 and i == 10 for _, _, z, i in px)
    assert [(x, y) for x, y, _, _ in px] == tri_pixels((0, 0), (0, 9), (9, 0), 64, 64)
    # The pixel at a corner carries that corner's attributes (z exactly, i rounded to a shade).  The top-left
    # corner (10, 10) is covered; the others lie on bottom or right edges and are not.
    v = [(10, 10, 100, 124), (10, 30, 60000, 248), (30, 10, 31000, 0)]
    d = {(x, y): (z, i) for x, y, z, i in shade_pixels(*v, 64, 64)}
    assert d[(10, 10)] == (100, 16) and (10, 30) not in d and (30, 10) not in d
    assert d[(10, 29)] == (57005, 30) and d[(29, 10)][0] == 29455
    # Winding does not matter, and the swap keeps each attribute with its corner.
    assert shade_pixels(v[0], v[2], v[1], 64, 64) == shade_pixels(*v, 64, 64)
    # The stepped (wrapping) form equals the definition, including far off-screen corners.
    for n in range(400):
        big = n % 4 == 3
        lo, hi = (-32768, 32767) if big else (-30, 94)
        vs = [(rng.randint(lo, hi), rng.randint(lo, hi), rng.randint(0, 65535), rng.randint(0, 255)) for _ in range(3)]
        assert shade_pixels(*vs, 64, 64) == shade_pixels_stepped(*vs, 64, 64), vs
    # Accuracy: on screen, the drift stays below 0.005 of an output step; far off-screen below 1.1.
    wz = wi = 0.0
    for _ in range(40):
        vs = [(rng.randint(-20, 340), rng.randint(-20, 260), rng.randint(0, 65535), rng.randint(0, 255)) for _ in range(3)]
        ez, ei = interp_error(*vs, 320, 240)
        wz, wi = max(wz, ez), max(wi, ei)
    assert wz < 0.005 and wi < 0.005, (wz, wi)
    (zn, zq), (inum, iq) = max_gradient_bits()
    print("  shading: gradient drift on screen at most %.4f z steps, %.4f intensity steps (40 random triangles)" % (wz, wi))
    print("  widths: z numerator %d bits, gradient %d bits; i numerator %d bits, gradient %d bits (signed)" % (zn, zq, inum, iq))


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
    _self_check_shading()
    print("raster_model: self-check OK")
    print("  screen 320x240, vertices int16: max |E| = %d (%d signed bits), "
          "max |area2| = %d (%d signed bits)" % (e, signed_bits(e), a, signed_bits(a)))


if __name__ == "__main__":
    _self_check()
