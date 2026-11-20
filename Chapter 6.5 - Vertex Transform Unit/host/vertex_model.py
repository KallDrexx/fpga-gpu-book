"""vertex_model.py -- the fixed-point vertex transform of Chapter 6.5, in exact integer arithmetic.

Copy to host/vertex_model.py (new in 6.5; host/gpu_model.py imports it).

One vertex goes through three steps, each defined here to the last bit:

  1. Camera space.  Each camera coordinate is a four-term sum
         c_i = round_half_up( (m_i0*x + m_i1*y + m_i2*z + t_i * 0x4000) / 2^8 )
     of Q2.14 matrix entries and int16 vertex coordinates and translations, in an exact (48-bit) accumulator.
     The result is a 25-bit signed number in Q.6 (6 fraction bits: 64 = one model unit). It cannot saturate:
     |sum| < 3*2^15*2 + 2^15 model units = 229,376 units = 14.7 million in Q.6, less than 2^24.
  2. Near test.  The vertex is NEAR (flag bit 0) when  z_c <= near * 64,  that is z_c <= near units.
     near is an unsigned 16-bit number. A NEAR vertex has no screen position: the reference writes sx = sy = depth = 0.
     Every other vertex has z_c > near*64 >= 0, so the divisions below never see a divisor of 0 or less.
  3. Projection.  With f the focal length (signed 16-bit, normally 1..32767):
         qx = round_half_up(f * x_c / z_c)           (pixels; the Q.6 scales cancel)
         qy = round_half_up(f * y_c / z_c)
         qd = round_half_up(near*64 * 65535 / z_c)   (0..65535, because z_c > near*64)
     each saturated to 17 bits, as seq_divider #(41, 25, 0, 17) does.
         sx = clamp16(160 + qx)       sy = clamp16(120 - qy)       depth = 65535 - qd
     (camera space is x right, y UP, z forward; the screen has y down). CLAMP (flag bit 1) is set when sx or sy
     had to be clamped to the int16 range.  Smaller depth = nearer: z_c just above near gives 0, and depth
     grows towards 65535 as z_c goes to infinity.

Result: (sx, sy, depth, flags).  The 8-byte result record is   sx lo, sx hi, sy lo, sy hi, depth lo, depth hi, flags, 0.
"""
import struct
import sys

sys.dont_write_bytecode = True

N_VERTICES = 1024
SCREEN_CX, SCREEN_CY = 160, 120
CAM_FRAC = 6                       # fraction bits of camera-space coordinates
MAC_FRAC = 8                       # 14 (matrix) - 6 (camera)
CAM_W = 25                         # width of a camera coordinate
FLAG_NEAR = 1
FLAG_CLAMP = 2
DEFAULT_F = 277                    # 60 degree horizontal field of view on 320 pixels: 160 / tan(30 deg)
DEFAULT_NEAR = 1
ONE = 0x4000                       # 1.0 in Q2.14
DEPTH_MAX = 65535


def identity():
    """The power-up matrix: 12 ints, m00 m01 m02 m10 m11 m12 m20 m21 m22 tx ty tz."""
    return (ONE, 0, 0, 0, ONE, 0, 0, 0, ONE, 0, 0, 0)


def mac(terms, frac, out_w, acc_w=48):
    """What Chapter 6.1's mac_unit computes: exact sum, add half an LSB, floor shift, saturate. (No wrap here:
    the sums of this chapter are far below 2^47.)"""
    acc = sum(a * b for a, b in terms)
    assert -(1 << (acc_w - 1)) <= acc < (1 << (acc_w - 1))
    r = (acc + (1 << (frac - 1))) >> frac
    return max(-(1 << (out_w - 1)), min((1 << (out_w - 1)) - 1, r))


def divide(num, den, frac, q_w):
    """What Chapter 6.2's seq_divider computes. Returns the quotient only."""
    qmax, qmin = (1 << (q_w - 1)) - 1, -(1 << (q_w - 1))
    if den == 0:
        return qmin if num < 0 else qmax
    if den < 0:
        num, den = -num, -den
    q = ((num << (frac + 1)) + den) // (2 * den)
    return max(qmin, min(qmax, q))


def camera(v, m):
    """(x_c, y_c, z_c) in Q.6 for vertex v = (x, y, z) and matrix m (12 ints)."""
    x, y, z = v
    out = []
    for i in range(3):
        out.append(mac([(m[3 * i], x), (m[3 * i + 1], y), (m[3 * i + 2], z), (m[9 + i], ONE)], MAC_FRAC, CAM_W))
    return tuple(out)


def clamp16(v):
    return max(-32768, min(32767, v))


def project(c, f, near):
    """(sx, sy, depth, flags) for camera coordinates c (Q.6), focal length f and near (units)."""
    xc, yc, zc = c
    near64 = near << CAM_FRAC
    if zc <= near64:
        return (0, 0, 0, FLAG_NEAR)
    qx = divide(f * xc, zc, 0, 17)
    qy = divide(f * yc, zc, 0, 17)
    qd = divide(near64 * DEPTH_MAX, zc, 0, 17)
    sxu = SCREEN_CX + qx
    syu = SCREEN_CY - qy
    flags = FLAG_CLAMP if (sxu != clamp16(sxu) or syu != clamp16(syu)) else 0
    return (clamp16(sxu), clamp16(syu), DEPTH_MAX - qd, flags)


def transform(v, m, f, near):
    return project(camera(v, m), f, near)


# ---------------------------------------------------------------- wire formats
def pack_vertex(v):
    return struct.pack("<hhh", *v)


def pack_matrix(m):
    assert len(m) == 12
    return struct.pack("<12h", *m)


def pack_result(r):
    sx, sy, d, flags = r
    return struct.pack("<hhHBB", sx, sy, d, flags, 0)


def unpack_result(b):
    sx, sy, d, flags, zero = struct.unpack("<hhHBB", bytes(b))
    return (sx, sy, d, flags)


def float_transform(v, m, f, near):
    """The same computation in floating point, with the matrix entries read as m/16384: returns (sx, sy, depth, zc)
    as floats (no rounding, no clamping), or None for a vertex at or behind the near plane."""
    x, y, z = v
    c = [(m[3 * i] * x + m[3 * i + 1] * y + m[3 * i + 2] * z) / ONE + m[9 + i] for i in range(3)]
    if c[2] <= near:
        return None
    sx = SCREEN_CX + f * c[0] / c[2]
    sy = SCREEN_CY - f * c[1] / c[2]
    return (sx, sy, DEPTH_MAX * (1.0 - near / c[2]), c[2])


if __name__ == "__main__":
    # a tiny self-check: the default matrix, a vertex straight ahead, and one at the near plane
    assert transform((0, 0, 100), identity(), DEFAULT_F, DEFAULT_NEAR) == (160, 120, 65535 - round(65535 / 100), 0)
    assert transform((0, 0, 1), identity(), DEFAULT_F, DEFAULT_NEAR) == (0, 0, 0, FLAG_NEAR)
    assert transform((0, 0, 2), identity(), DEFAULT_F, DEFAULT_NEAR)[3] == 0
    print("PASS: vertex_model self-check")
