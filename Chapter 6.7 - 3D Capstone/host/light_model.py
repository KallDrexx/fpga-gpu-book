"""light_model.py -- the lighting of Chapter 6.7 in exact integer arithmetic, and a floating-point twin for sanity checks.

Copy to host/light_model.py (new in 6.7; host/gpu_model.py imports it).

The light is a direction FIXED IN CAMERA SPACE (the "sun" does not turn with the model). A vertex's brightness is

    n_c = M3 . n                      the model-space normal turned into camera space by the matrix's 3x3 part
    d   = n_c . L                     L = SET_LIGHT's direction (Q2.14, pointing TOWARDS the light)
    b   = ambient + max(d, 0)         Q2.14; a negative dot product (facing away from the light) contributes nothing
    i   = clamp(round(b * 248 / 2^14), 0, 255)       Chapter 6.3's Q5.3 intensity (248 = full shade 31)

The GPU does not turn every normal. (M3 n) . L  =  n . (M3^T L), so once per TRANSFORM it computes the model-space
light direction l_m = M3^T L (three 3-term sums), and each vertex then needs only the three products n . l_m:

    l_m[j] = mac( M[i][j] * L[i] for i = 0..2 )               frac 14, saturated to int16     (model_light)
    d      = mac( n[k] * l_m[k] for k = 0..2 )                frac 14, saturated to int16     (dot)

with mac exactly as in Chapter 6.1 (exact sum, add 2^13, arithmetic shift right 14, saturate). Both steps round, so the
result can differ by one count from the rotate-first order; the model here is the definition. The matrix is a rotation times
a uniform scale s: the lit term scales with s (and with |L|, which is therefore the diffuse gain). Keep s = 1 for models.
"""
import math
import sys

sys.dont_write_bytecode = True

ONE = 0x4000


def mac(terms, frac=14, out_w=16):
    """Chapter 6.1's multiply-accumulate: exact sum, round half up, saturate."""
    acc = sum(a * b for a, b in terms)
    r = (acc + (1 << (frac - 1))) >> frac
    return max(-(1 << (out_w - 1)), min((1 << (out_w - 1)) - 1, r))


def model_light(m, light):
    """l_m = M3^T L: the light direction in model space (three int16 in Q2.14). m is the 12-int SET_MATRIX tuple."""
    return tuple(mac([(m[3 * i + j], light[i]) for i in range(3)]) for j in range(3))


def dot(n, lm):
    return mac([(n[k], lm[k]) for k in range(3)])


def brightness(n, lm, ambient):
    """b in Q2.14 (not clamped from above): ambient + max(n . l_m, 0), floored at 0."""
    return max(ambient + max(dot(n, lm), 0), 0)


def to_intensity(b):
    """Q2.14 brightness -> Q5.3 intensity byte: clamp(round(b * 248 / 2^14), 0, 255), half up. b >= 0."""
    return min(((b * 248) + (1 << 13)) >> 14, 255)


def intensity(n, lm, ambient):
    """The byte stored in byte 7 of a transformed record."""
    return to_intensity(brightness(n, lm, ambient))


def vertex_intensity(n, m, light, ambient):
    return intensity(n, model_light(m, light), ambient)


def shade(i):
    """Palette shade (0..31) an intensity byte becomes: Chapter 6.3's round-half-up(i/8), clamped."""
    return min((i + 4) >> 3, 31)


# ---------------------------------------------------------------- wire format
def q14(x):
    """round(x * 16384), clamped to int16."""
    return max(-32768, min(32767, int(math.floor(x * ONE + 0.5))))


def unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


# ---------------------------------------------------------------- floating-point twin
def float_brightness(n, m, light, ambient):
    """The same lighting in floating point: the matrix entries, normal, light and ambient read as value / 16384, the normal
    turned into camera space first (no intermediate rounding). Returns b as a float (1.0 = full)."""
    nc = [sum(m[3 * i + j] * n[j] for j in range(3)) / (ONE * ONE) for i in range(3)]
    d = sum(nc[i] * light[i] / ONE for i in range(3))
    return max(ambient / ONE + max(d, 0.0), 0.0)


def float_shade(b):
    """The shade (0..31) a float brightness gives: round(b * 31), clamped."""
    return max(0, min(31, int(math.floor(b * 31 + 0.5))))


def _self_check():
    import random
    rng = random.Random(67)
    ident = (ONE, 0, 0, 0, ONE, 0, 0, 0, ONE, 0, 0, 0)
    L = (q14(-0.4), q14(0.5), q14(-0.6))
    lm = model_light(ident, L)
    assert lm == L
    # a normal facing the light is full, one facing away is ambient only, ambient 0 clamps to 0
    assert intensity((0, 0, 0x4000), (0, 0, 0x4000), 0) == 248
    assert intensity((0, 0, -0x4000), (0, 0, 0x4000), 0) == 0
    assert intensity((0, 0, -0x4000), (0, 0, 0x4000), 0x1000) == to_intensity(0x1000) == 62
    assert intensity((0, 0, 0x4000), (0, 0, 0x4000), 0x4000) == 255 and intensity((0, 0, 0), (0, 0, 0), -5000) == 0
    assert shade(248) == 31 and shade(255) == 31 and shade(0) == 0 and shade(3) == 0 and shade(4) == 1
    worst = 0
    for _ in range(20000):
        a, b_, c = (rng.uniform(0, 2 * math.pi) for _ in range(3))
        ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b_), math.sin(b_), math.cos(c), math.sin(c)
        rot = [[cc * cb, cc * sb * sa - sc * ca, cc * sb * ca + sc * sa],
               [sc * cb, sc * sb * sa + cc * ca, sc * sb * ca - cc * sa],
               [-sb, cb * sa, cb * ca]]
        m = tuple(q14(rot[i][j]) for i in range(3) for j in range(3)) + (0, 0, 0)
        n = tuple(q14(x) for x in unit([rng.gauss(0, 1) for _ in range(3)]))
        L = tuple(q14(x * rng.uniform(0.5, 1.0)) for x in unit([rng.gauss(0, 1) for _ in range(3)]))
        amb = rng.randint(-2000, 9000)
        b = brightness(n, model_light(m, L), amb)
        assert b >= 0 and 0 <= to_intensity(b) <= 255
        worst = max(worst, abs(to_intensity(b) - min(255.0, 248 * float_brightness(n, m, L, amb))))
    assert worst < 3.2, worst
    print("PASS: light_model self-check (largest intensity difference from floating point %.2f over 20,000 random cases)" % worst)


if __name__ == "__main__":
    _self_check()
