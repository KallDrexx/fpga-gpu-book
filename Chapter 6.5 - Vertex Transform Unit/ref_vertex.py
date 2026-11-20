#!/usr/bin/env python3
"""ref_vertex.py -- the checks and test files of Chapter 6.5 (vertex transform unit).

Usage (from the repo root; the first form is the one to run once and commit):
    .venv/bin/python sim/py/ref_vertex.py sim          # write the testbench files AND run the checks
    .venv/bin/python sim/py/ref_vertex.py --check      # only the checks (a few seconds)

Needs sim/py/ref_mac.py (Chapter 6.1), sim/py/ref_divider.py (Chapter 6.2), host/vertex_model.py, host/gpu_model.py and numpy.

Checks (printed, one PASS/FAIL line at the end):
  1. host/vertex_model.py agrees with ref_mac.mac_ref and ref_divider.div_ref on 20,000 random vertices
     under random matrices (so the model is built from the two units you tested earlier).
  2. The fixed-point model stays within 1 pixel of a floating-point numpy version, in the ranges stated by
     the claims below (A and B). The ranges are the claim: outside them it may be worse, and it says so.

  Claim A: "same matrix". The float version uses the matrix entries exactly as the hardware holds them
           (m / 16384), so only the arithmetic is compared.
           Holds for  64 <= f <= 1024,  z_c >= 64 (model units),  |sx - 160| <= 1024,  |sy - 120| <= 1024.
           Error budget: 0.5 pixel for the final rounding, plus at most (f + P) / (128 z_c) pixels
           from rounding the camera coordinates to 1/64 unit (P = the pixel offset from the centre), which is at most
           0.25 pixel in this range.
  Claim B: "true rotation". The float version uses the exact rotation, the hardware its Q2.14 rounding.
           Matrix rounding moves a camera coordinate by up to 3 |v|max / 2^15 units, so the range is smaller:
           |x|, |y|, |z| <= 512,  z_c >= 128,  f <= 400,  |sx - 160|, |sy - 120| <= 512.
  The depth is checked too: |depth - float depth| <= 1 + 512 * near / z_c^2.

Files written (the testbenches read them from build/sim):
    sim/vertex_cases.hex, sim/vertex_exp.hex       for tb_vertex_unit  (format in tb_vertex_unit.v)
    sim/vertex_stream.hex, sim/vertex_expect.hex   for tb_vertex_sys   (format in tb_vertex_sys.v)
"""
import os
import random
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import gpu_model as g  # noqa: E402
import vertex_model as vm  # noqa: E402
from ref_divider import div_ref  # noqa: E402
from ref_mac import mac_ref  # noqa: E402

SEED = 6565


# ---------------------------------------------------------------- random inputs
def clamp(v, lo=-32768, hi=32767):
    return max(lo, min(hi, v))


def rnd_vertex(rng, kind=None):
    kind = rng.randrange(10) if kind is None else kind
    if kind <= 3:                                                       # near the origin
        return tuple(rng.randint(-300, 300) for _ in range(3))
    if kind <= 6:
        return tuple(rng.randint(-2500, 2500) for _ in range(3))
    if kind <= 8:                                                       # log-uniform magnitude up to int16
        return tuple(clamp(rng.choice([-1, 1]) * int(2 ** rng.uniform(0, 15))) for _ in range(3))
    return tuple(rng.choice([-32768, 32767, 0, 1, -1, 16384]) for _ in range(3))


def rotation(rng):
    """A random rotation matrix (3 x 3 floats) from a random unit quaternion."""
    q = np.array([rng.gauss(0, 1) for _ in range(4)])
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def quantize(R):
    return [clamp(int(round(float(e) * vm.ONE))) for e in R.reshape(-1)]


def rnd_matrix(rng, kind=None):
    """12 ints. 'view': a rotation times a scale, a translation that usually puts the model in front of the camera."""
    kind = rng.choice(["view"] * 6 + ["wild", "corner"]) if kind is None else kind
    if kind == "view":
        s = rng.uniform(0.2, 1.99)
        m = quantize(rotation(rng) * s)
        t = [rng.randint(-500, 500), rng.randint(-500, 500), rng.randint(200, 3000)]
        return tuple(m + t)
    if kind == "wild":
        return tuple(rng.randint(-32768, 32767) for _ in range(12))
    pool = [-32768, 32767, 16384, -16384, 0, 1, -1]
    return tuple(rng.choice(pool) for _ in range(12))


def rnd_proj(rng, kind="view"):
    if kind == "view":
        return rng.randint(100, 600), rng.randint(1, 100)
    return (rng.choice([-32768, -1000, -1, 0, 1, 277, 5000, 32767, rng.randint(-32768, 32767)]),
            rng.choice([0, 1, 2, 64, 1000, 65535, rng.randint(0, 65535)]))


# ---------------------------------------------------------------- checks
def check_model(rng, n=20000):
    """vertex_model against the Chapter 6.1 and 6.2 references: the camera step is mac_ref, each divide is div_ref."""
    flagged = 0
    for i in range(n):
        v = rnd_vertex(rng)
        m = rnd_matrix(rng)
        f, near = rnd_proj(rng, "view" if i % 2 else "wild")
        c = tuple(mac_ref([(m[3 * r], v[0]), (m[3 * r + 1], v[1]), (m[3 * r + 2], v[2]), (m[9 + r], 0x4000)], frac=8, out_w=25)
                  for r in range(3))
        assert c == vm.camera(v, m), (v, m)
        got = vm.transform(v, m, f, near)
        xc, yc, zc = c
        if zc <= near * 64:
            assert got == (0, 0, 0, vm.FLAG_NEAR)
            flagged += 1
            continue
        qx = div_ref(f * xc, zc, 0, 17)[0]
        qy = div_ref(f * yc, zc, 0, 17)[0]
        qd = div_ref(near * 64 * 65535, zc, 0, 17)[0]
        sxu, syu = 160 + qx, 120 - qy
        want = (clamp(sxu), clamp(syu), 65535 - qd, vm.FLAG_CLAMP if (sxu != clamp(sxu) or syu != clamp(syu)) else 0)
        assert got == want, (v, m, f, near, got, want)
    return n, flagged


def claim_a(rng, want=6000):
    worst = [0.0, 0.0, 0.0]
    n = tried = 0
    while n < want:
        tried += 1
        v = rnd_vertex(rng, rng.randrange(9))
        m = rnd_matrix(rng, "view")
        f = rng.randint(64, 1024)
        near = rng.randint(1, 64)
        r = vm.float_transform(v, m, f, near)
        if r is None:
            continue
        fx, fy, fd, zc = r
        if zc < 64 or abs(fx - 160) > 1024 or abs(fy - 120) > 1024:
            continue
        sx, sy, d, fl = vm.transform(v, m, f, near)
        assert fl == 0
        ex, ey, ed = abs(sx - fx), abs(sy - fy), abs(d - fd)
        assert ex <= 1 and ey <= 1, ("claim A", v, m, f, near, sx, sy, fx, fy)
        assert ed <= 1 + 512 * near / (zc * zc) + 1e-9, ("claim A depth", v, m, f, near, d, fd)
        worst = [max(worst[0], ex), max(worst[1], ey), max(worst[2], ed)]
        n += 1
    return n, tried, worst


def claim_b(rng, want=6000):
    worst = [0.0, 0.0]
    n = tried = 0
    while n < want:
        tried += 1
        v = tuple(rng.randint(-512, 512) for _ in range(3))
        s = rng.uniform(0.2, 1.99)
        R = rotation(rng) * s
        t = [rng.randint(-500, 500), rng.randint(-500, 500), rng.randint(200, 3000)]
        m = tuple(quantize(R) + t)
        f = rng.randint(64, 400)
        near = rng.randint(1, 64)
        c = [float(R[i] @ np.array(v, dtype=float)) + t[i] for i in range(3)]
        if c[2] < 128:
            continue
        fx, fy = 160 + f * c[0] / c[2], 120 - f * c[1] / c[2]
        if abs(fx - 160) > 512 or abs(fy - 120) > 512:
            continue
        sx, sy, d, fl = vm.transform(v, m, f, near)
        assert fl == 0
        ex, ey = abs(sx - fx), abs(sy - fy)
        assert ex <= 1 and ey <= 1, ("claim B", v, m, f, near, sx, sy, fx, fy)
        worst = [max(worst[0], ex), max(worst[1], ey)]
        n += 1
    return n, tried, worst


def outside_claim(rng, n=20000):
    """How bad can it get outside the stated range? (informational: the largest pixel error among non-flagged,
    non-clamped vertices with z_c >= 2 units and the same-matrix float reference)"""
    worst = 0.0
    for _ in range(n):
        v = rnd_vertex(rng, rng.randrange(9))
        m = rnd_matrix(rng, "view")
        f, near = rng.randint(64, 1024), 1
        r = vm.float_transform(v, m, f, near)
        if r is None or r[3] < 2:
            continue
        sx, sy, d, fl = vm.transform(v, m, f, near)
        if fl:
            continue
        worst = max(worst, abs(sx - r[0]), abs(sy - r[1]))
    return worst


def run_checks():
    rng = random.Random(SEED)
    n, fl = check_model(rng)
    print(f"check 1: vertex_model == ref_mac + ref_divider on {n} random vertices ({fl} at or behind the near plane)")
    n, tried, w = claim_a(rng)
    print(f"check 2A: same matrix, {n} vertices in range (of {tried} drawn): worst error {w[0]:.3f} px in x, "
          f"{w[1]:.3f} px in y, {w[2]:.2f} in depth  (limit 1 px)")
    n, tried, w = claim_b(rng)
    print(f"check 2B: true rotation, {n} vertices in range (of {tried} drawn): worst error {w[0]:.3f} px in x, "
          f"{w[1]:.3f} px in y  (limit 1 px)")
    ow = outside_claim(rng)
    print(f"info: outside the claimed range (z_c down to 2 units, any offset not clamped) the worst error seen is {ow:.1f} px")
    print("PASS: ref_vertex checks")


# ---------------------------------------------------------------- tb_vertex_unit files
def le48(v):
    return (v[0] & 0xFFFF) | ((v[1] & 0xFFFF) << 16) | ((v[2] & 0xFFFF) << 32)


def result_word(r):
    sx, sy, d, fl = r
    return (sx & 0xFFFF) | ((sy & 0xFFFF) << 16) | (d << 32) | (fl << 48)


class Script:
    """The testbench script of tb_vertex_unit: 64-bit words (16 hex digits), see tb_vertex_unit.v."""

    def __init__(self):
        self.words, self.exp = [], []
        self.vs = [None] * 1024
        self.m, self.f, self.near = vm.identity(), vm.DEFAULT_F, vm.DEFAULT_NEAR
        self.nvert = 0
        self.runs = 0
        self.flagged = self.clamped = 0

    def load(self, idx, v):
        self.vs[idx] = v
        self.words.append((0 << 60) | (idx << 48) | le48(v))

    def matrix(self, m):
        self.m = tuple(m)
        for p in range(4):
            w = 0
            for k in range(3):
                w |= (m[3 * p + k] & 0xFFFF) << (16 * k)
            self.words.append((1 << 60) | (p << 56) | w)

    def proj(self, f, near):
        self.f, self.near = f, near
        self.words.append((2 << 60) | (near << 16) | (f & 0xFFFF))

    def run(self, first, count, gap=0):
        self.words.append((3 << 60) | (first << 32) | (count << 16) | gap)
        self.runs += 1
        for i in range(first, first + count):
            r = vm.transform(self.vs[i], self.m, self.f, self.near)
            self.exp.append(result_word(r))
            self.nvert += 1
            self.flagged += r[3] & 1
            self.clamped += (r[3] >> 1) & 1

    def end(self):
        self.words.append(0xF << 60)


def build_unit_script(rng):
    s = Script()
    for i in range(1000):
        s.load(i, rnd_vertex(rng))
    for i in range(1000, 1024):
        s.load(i, (rng.randint(-300, 300), rng.randint(-300, 300), 0))
    s.run(0, 16)                                                       # power-up matrix, f and near (nothing set yet)
    s.run(1000, 4, gap=3)
    for k in range(40):                                                # 40 random view matrices, 25 vertices each
        s.matrix(rnd_matrix(rng, "view"))
        if k % 3 == 0:
            s.proj(*rnd_proj(rng))
        s.run(25 * k, 25, gap=rng.choice([0, 0, 1, 5, 15]))
    s.matrix(rnd_matrix(rng, "view"))
    s.proj(277, 16)
    s.run(0, 1000)                                                     # 1,000 vertices under one matrix
    for k in range(30):                                                # wild matrices and projections: flags, clamps, saturation
        s.matrix(rnd_matrix(rng, "wild" if k % 2 else "corner"))
        s.proj(*rnd_proj(rng, "wild"))
        s.run(rng.randrange(0, 960), 30)
    # the near plane to the unit: identity matrix, z_c = z + tz exactly
    s.matrix((0x4000, 0, 0, 0, 0x4000, 0, 0, 0, 0x4000, 7, -9, 100))
    s.proj(277, 20)
    for i, dz in enumerate(list(range(-85, -75)) + [-80]):             # z_c = 15 .. 24: NEAR up to 20, depth 0 at 21
        s.load(1000 + i, (rng.randint(-100, 100), rng.randint(-100, 100), dz))
    s.run(1000, 11)
    s.proj(-300, 0)
    s.run(1000, 11)
    s.proj(32767, 65535)                                               # near = 65535: everything is NEAR
    s.run(1000, 11)
    s.matrix((0x7FFF,) * 9 + (32767, 32767, 32767))
    s.load(1010, (32767, 32767, 32767))                                # the largest possible camera coordinate
    s.load(1011, (-32768, -32768, -32768))
    s.proj(1, 0)
    s.run(1010, 2)
    s.proj(32767, 1)
    s.run(1010, 2)
    s.matrix((-0x8000,) * 9 + (-32768, -32768, -32768))
    s.proj(100, 0)
    s.run(1010, 2)
    s.load(1015, (32767, 32767, 1000))                                 # |x_c|, |y_c| above 2^17 units with a moderate z_c:
    s.proj(1, 0)                                                       # a 24-bit camera coordinate would saturate here
    s.matrix((0x7FFF, 0x7FFF, 0, -0x8000, -0x8000, 0, 0, 0, 0x4000, 32767, -32768, 0))
    s.run(1015, 1)
    s.matrix((-0x8000, -0x8000, 0, 0x7FFF, 0x7FFF, 0, 0, 0, 0x4000, -32768, 32767, 0))
    s.run(1015, 1)
    s.matrix(vm.identity()[:9] + (0, 0, 1))
    s.proj(100, 0)
    s.load(1012, (32767, -32768, 1))                                   # z_c = 2 units: the divider saturates
    s.load(1013, (-32768, 32767, 1))
    s.load(1014, (0, 0, 0))
    s.run(1012, 3)
    s.run(0, 0)                                                        # count 0: done, nothing written
    s.run(1023, 1)
    s.run(5, 0, gap=2)
    s.run(0, 1024)                                                     # the whole buffer
    s.end()
    return s


def write_unit_files(outdir, rng):
    s = build_unit_script(rng)
    with open(os.path.join(outdir, "vertex_cases.hex"), "w") as f:
        f.write("// tb_vertex_unit script, 64-bit words (made by sim/py/ref_vertex.py)\n")
        f.write("\n".join(f"{w:016x}" for w in s.words) + "\n")
    with open(os.path.join(outdir, "vertex_exp.hex"), "w") as f:
        f.write("// tb_vertex_unit expected transformed vertices, one 64-bit word per vertex of every RUN, in order\n")
        f.write("\n".join(f"{w:016x}" for w in s.exp) + "\n")
    print(f"ref_vertex: tb_vertex_unit script: {len(s.words)} words, {s.runs} runs, {s.nvert} vertices transformed "
          f"({s.flagged} NEAR, {s.clamped} CLAMP) -> {outdir}/vertex_cases.hex, vertex_exp.hex")


# ---------------------------------------------------------------- tb_vertex_sys files
class SysStream:
    """Bytes for uart_gpu and the reply bytes they must produce, from the gpu_model."""

    def __init__(self):
        self.m = g.Gpu(caps=g.CAPS_UART)
        self.items = []                                    # 16-bit stream entries
        self.rows = []                                     # one (mask, byte) per expected reply byte
        self.pending = 0
        self.nbytes = 0
        self.reads = 0

    def send(self, pkt):
        pkt = bytes(pkt)
        reply = self.m.feed(pkt)
        op = pkt[0]
        self.items += list(pkt)
        self.nbytes += len(pkt)
        if op == g.READ_VERTEX and reply:
            self.reads += 1
            flagged = reply[6] & 1
            for i, b in enumerate(reply):
                self.rows.append((0x00 if (flagged and i < 6) else 0xFF, b))
        else:
            for b in reply:
                self.rows.append((0xFF, b))
        self.pending += len(reply)
        if self.pending >= 400:
            self.fence()
        return reply

    def fence(self):
        while self.pending > 0:
            n = min(self.pending, 0xFFF)
            self.items.append(0x1000 | n)
            self.pending -= n

    def read(self, i):
        self.send(g.read_vertex(i))

    def read_range(self, a, b):
        for i in range(a, b):
            self.read(i)
        self.fence()

    def info(self):
        self.send(g.get_info())
        self.fence()


def build_sys_stream(rng):
    s = SysStream()
    s.info()
    # 1. power-up defaults: identity, f = 277, near = 1. z = 1 is NEAR, z = 2 is the nearest drawable vertex.
    s.send(g.upload_vertices(0, [(0, 0, 100), (50, -20, 300), (0, 0, 1), (0, 0, 2), (-1000, 800, 5000)]))
    s.send(g.transform(0, 5))
    s.read_range(0, 5)
    s.info()
    # 2. 1,024 random vertices in packets of up to 42, in a scrambled order, some of them written twice
    verts = [rnd_vertex(rng) for _ in range(1024)]
    chunks = [(i, verts[i:i + 42]) for i in range(0, 1024, 42)]
    rng.shuffle(chunks)
    for first, vs in chunks:
        s.send(g.upload_vertices(first, vs))
    s.send(g.upload_vertices(500, verts[500:507]))                      # again: no change
    s.send(g.upload_vertices(3, [(1, 1, 1)]))                           # one vertex, then put the right one back
    s.send(g.upload_vertices(3, verts[3:4]))
    # 3. twenty random matrices, each transforming its own 50 vertices (1,000 in all), every one read back
    for k in range(20):
        s.send(g.set_matrix(rnd_matrix(rng, "view" if k % 5 else "wild")))
        if k % 4 == 0:
            s.send(g.set_projection(*rnd_proj(rng, "view" if k % 8 else "wild")))
        s.send(g.transform(50 * k, 50))
        s.read_range(50 * k, 50 * k + 50)
    # 4. the previous results stay while a new matrix and new vertices arrive; only TRANSFORM changes them
    s.send(g.set_matrix(rnd_matrix(rng, "view")))
    s.send(g.upload_vertices(10, [rnd_vertex(rng) for _ in range(3)]))
    s.read_range(8, 16)
    s.send(g.transform(10, 3))
    s.read_range(8, 16)
    # 5. the end of the buffer: slots 1024 and up are dropped, TRANSFORM is clipped, a far read has no reply
    s.send(g.clear_errors())
    s.send(g.upload_vertices(1022, [(11, 22, 700), (33, 44, 800), (55, 66, 900), (77, 88, 950)]))
    s.send(g.upload_vertices(1023, [(-5, 5, 600), (1, 1, 1)]))
    s.send(g.upload_vertices(65535, [(1, 2, 3), (4, 5, 6)]))
    s.send(g.set_matrix(vm.identity()))
    s.send(g.set_projection(200, 8))
    s.send(g.transform(1020, 100))
    s.send(g.transform(1024, 5))
    s.send(g.transform(65535, 65535))
    s.send(g.transform(1023, 0))
    s.send(g.transform(0, 0))
    s.read_range(1020, 1024)
    s.read(1024)
    s.read(65535)
    s.read(0)
    s.info()                                                            # flags 00: none of that was an error
    s.send(g.transform(0, 1024))                                        # the whole buffer at once
    s.read_range(0, 24)
    s.read_range(1000, 1024)
    # 6. wrong lengths: skipped whole, BAD_LEN, nothing changes
    s.send(g.clear_errors())
    s.send(g.set_matrix(rnd_matrix(rng, "view")))
    s.send(g.set_projection(300, 4))
    s.send(g.transform(0, 8))
    s.read_range(0, 8)
    for pkt in (g.packet(g.SET_MATRIX, bytes(23)), g.packet(g.SET_MATRIX, bytes(25)), g.packet(g.SET_MATRIX, b""),
                g.packet(g.SET_PROJECTION, bytes(3)), g.packet(g.SET_PROJECTION, bytes(5)), g.packet(g.TRANSFORM, bytes(3)),
                g.packet(g.TRANSFORM, bytes(5)), g.packet(g.TRANSFORM, b""), g.packet(g.READ_VERTEX, bytes(1)),
                g.packet(g.READ_VERTEX, bytes(3)), g.packet(g.READ_VERTEX, b""),
                g.packet(g.UPLOAD_VERTICES, b""), g.packet(g.UPLOAD_VERTICES, bytes(1)), g.packet(g.UPLOAD_VERTICES, bytes(2)),
                g.packet(g.UPLOAD_VERTICES, bytes(7)), g.packet(g.UPLOAD_VERTICES, bytes(9)), g.packet(g.UPLOAD_VERTICES, bytes(13)),
                g.packet(g.UPLOAD_VERTICES, bytes([0, 0] + [9] * 11))):
        s.send(pkt)
        s.info()
        s.send(g.clear_errors())
    s.send(g.packet(g.SET_MATRIX, bytes(range(24))[:23]))               # a bad SET_MATRIX must not have changed the matrix,
    s.send(g.packet(g.SET_PROJECTION, bytes([0xD0, 0x02, 0x05])))       # nor a bad SET_PROJECTION the focal length
    s.send(g.transform(0, 8))
    s.read_range(0, 8)
    s.send(g.packet(0x75, bytes(3)) + g.packet(0x7F, b""))              # 75-7F are still unknown
    s.info()
    s.send(g.clear_errors())
    # 7. display lists: SET_MATRIX, SET_PROJECTION and TRANSFORM may be in a list; UPLOAD_VERTICES and READ_VERTEX may not
    s.send(g.upload_vertices(100, [(10, 20, 400), (-30, 15, 410)]))
    body = (g.set_matrix(rnd_matrix(rng, "view")) + g.set_projection(250, 12) + g.transform(100, 2)
            + g.read_vertex(100) + g.upload_vertices(100, [(0, 0, 0)]) + g.transform(100, 1))
    for p in g.list_packets(2, body):
        s.send(p)
    s.send(g.run_list(2))
    s.read_range(98, 102)
    s.info()                                                            # flags 04: BAD_OP twice, no reply from the list's READ_VERTEX
    s.send(g.clear_errors())
    # 8. near plane to the bit: identity, tz = 100, near = 20: z = -80 is z_c = 20 (NEAR), z = -79 is z_c = 21 (depth 0)
    s.send(g.set_matrix((0x4000, 0, 0, 0, 0x4000, 0, 0, 0, 0x4000, 0, 0, 100)))
    s.send(g.set_projection(277, 20))
    s.send(g.upload_vertices(200, [(5, 5, -81), (5, 5, -80), (5, 5, -79), (5, 5, -78), (-5, 5, 0)]))
    s.send(g.transform(200, 5))
    s.read_range(200, 205)
    # 9. extremes: clamping and the CLAMP flag, near = 0, a negative focal length
    s.send(g.set_matrix(vm.identity()[:9] + (0, 0, 1)))
    s.send(g.set_projection(32767, 0))
    s.send(g.upload_vertices(300, [(32767, -32768, 1), (-32768, 32767, 1), (0, 0, 0), (100, 100, 32767), (-100, 100, 20)]))
    s.send(g.transform(300, 5))
    s.read_range(300, 305)
    s.send(g.set_projection(-500, 3))
    s.send(g.transform(300, 5))
    s.read_range(300, 305)
    s.info()
    s.fence()
    s.items.append(0xFFFF)
    assert s.m.read_undefined == 0, s.m.read_undefined
    return s


def write_sys_files(outdir, rng):
    s = build_sys_stream(rng)
    with open(os.path.join(outdir, "vertex_stream.hex"), "w") as f:
        f.write("// tb_vertex_sys stream: 00NN send byte, 1NNN wait for NNN more reply bytes, FFFF end (made by sim/py/ref_vertex.py)\n")
        f.write("\n".join(f"{v:04x}" for v in s.items) + "\n")
    with open(os.path.join(outdir, "vertex_expect.hex"), "w") as f:
        f.write("// tb_vertex_sys expected reply bytes: MMBB, mask MM (FF compare, 00 ignore), byte BB (made by sim/py/ref_vertex.py)\n")
        f.write("\n".join(f"{m:02x}{b:02x}" for m, b in s.rows) + "\n")
    assert len(s.items) < 65536 and len(s.rows) < 65536
    print(f"ref_vertex: tb_vertex_sys stream: {s.nbytes} command bytes, {len(s.rows)} reply bytes, {s.reads} READ_VERTEX replies "
          f"-> {outdir}/vertex_stream.hex, vertex_expect.hex")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] != "--check":
        outdir = args[0]
        os.makedirs(outdir, exist_ok=True)
        write_unit_files(outdir, random.Random(SEED + 1))
        write_sys_files(outdir, random.Random(SEED + 2))
    run_checks()


if __name__ == "__main__":
    main()
