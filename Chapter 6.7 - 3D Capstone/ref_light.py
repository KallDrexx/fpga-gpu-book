#!/usr/bin/env python3
"""ref_light.py -- test vectors, stream and floating-point checks for the lighting pipeline (Chapter 6.7).

Usage (from the repo root; commit the files it writes):
    .venv/bin/python sim/py/ref_light.py --check        the numpy sanity checks (fixed point against floating point, no files)
    .venv/bin/python sim/py/ref_light.py --files sim    writes sim/light_cases.hex, sim/light_sys_stream.hex, sim/light_sys_expect.hex

Writes:
    sim/light_cases.hex       for tb_light_unit: 64-bit words (16 hex digits each). Word 0 = number of cases. Each case:
                                  {16'h0, count[15:0], first[15:0], 16'h0}
                                  3 words: the matrix (SET_MATRIX payload layout: entry k at bits 16k+15:16k of the 192-bit value, low word first)
                                  1 word:  the light {ambient, lz, ly, lx}
                                  count words: the normals {16'h0, nz, ny, nx}, slot first, first + 1, ...
                                  count words: the expected intensity (byte 7) of each, in the low 8 bits
    sim/light_sys_stream.hex  for tb_light_sys: 00NN send byte NN, 1NNN wait for NNN more reply bytes, FFFF end
    sim/light_sys_expect.hex  one row per reply byte MMBB (mask MM, expected byte BB)

Every expected value comes from host/light_model.py (the definition) through host/gpu_model.py; this script also checks the
definition against floating point.
"""
import math
import os
import random
import sys

import numpy as np  # noqa: E402

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))

import gpu_model as g  # noqa: E402
import light_model as lm  # noqa: E402
import lit_scene  # noqa: E402
import mesh_gen  # noqa: E402
import mesh_model as mm  # noqa: E402
import vertex_model as vm  # noqa: E402

SEED = 6767


def rnd_unit(rng):
    return lm.unit([rng.gauss(0, 1) for _ in range(3)])


def rnd_rotation(rng, scale=1.0):
    """A random rotation as the 12-int SET_MATRIX tuple (translation 0), times scale."""
    a, b, c = (rng.uniform(0, 360) for _ in range(3))
    return mm.rot_matrix(a, b, c, 0, 0, 0, scale=scale)


def rnd_normal(rng):
    r = rng.random()
    if r < 0.06:
        return tuple(rng.choice((-32768, -1, 0, 1, 32767, 16384, -16384)) for _ in range(3))
    if r < 0.12:
        return tuple(rng.randint(-32768, 32767) for _ in range(3))
    return tuple(lm.q14(x) for x in rnd_unit(rng))


def rnd_light(rng):
    r = rng.random()
    if r < 0.1:
        return tuple(rng.randint(-32768, 32767) for _ in range(3))
    s = rng.choice((1.0, 0.8, 0.5, rng.uniform(0.1, 1.0)))
    return tuple(lm.q14(s * x) for x in rnd_unit(rng))


def rnd_ambient(rng):
    r = rng.random()
    if r < 0.15:
        return rng.choice((-32768, -1, 0, 1, 32767, 16384, 16912, 16911, 3277))
    return rng.randint(-3000, 12000)


def cases(rng):
    """The unit test's cases: (first, count, matrix, light, ambient, normals)."""
    out = []
    for k in range(90):                                    # ordinary: rotations of every scale, unit-ish normals
        m = rnd_rotation(rng, scale=rng.choice((1.0, 1.0, 0.5, 1.9, rng.uniform(0.1, 1.9))))
        count = rng.choice((1, 2, 3, 5, 17, 40))
        first = rng.randint(0, 1024 - count)
        out.append((first, count, m, rnd_light(rng), rnd_ambient(rng), [rnd_normal(rng) for _ in range(count)]))
    for k in range(30):                                    # raw random matrices: the light-direction sums saturate
        m = tuple(rng.randint(-32768, 32767) for _ in range(9)) + tuple(rng.randint(-32768, 32767) for _ in range(3))
        count = rng.choice((1, 4, 9))
        first = rng.randint(0, 1024 - count)
        out.append((first, count, m, rnd_light(rng), rnd_ambient(rng), [rnd_normal(rng) for _ in range(count)]))
    # the dot product at and around zero: normals at right angles to the light (identity matrix), and a hair either side
    ident = vm.identity()
    for L in ((8192, 8192, 0), (0, 0, -16384), (16384, 0, 0)):
        ns = [(0, 0, 16384), (0, 0, 1), (0, 0, -1), (-16384, 0, 0), (0, 16384, 0), (1, 1, 0), (-1, -1, 0), (0, 0, 0), (-1, 1, 0)]
        out.append((7, len(ns), ident, L, 0, ns))
        out.append((900, len(ns), ident, L, 2048, ns))
    # brightness at the clamp: full, a hair under, a hair over 255 (b = 16911 is 255 after rounding, 16912 would be 256 -> 255)
    for amb in (16384 - 1, 16384, 16384 + 1, 16911, 16912, 20000, 32767, -32768, -1, 1):
        out.append((3, 3, ident, (0, 0, 16384), amb, [(0, 0, 16384), (0, 0, -16384), (0, 0, 8192)]))
    # the ends of the buffer, one slot, all 1024 slots, an empty range
    out.append((1023, 1, rnd_rotation(rng), (0, 0, -13000), 3277, [(1, 2, -16384)]))
    out.append((0, 1, rnd_rotation(rng), (0, 0, -13000), 3277, [(1, 2, -16384)]))
    out.append((0, 1024, rnd_rotation(rng), lit_scene.LIGHT_DIR, lit_scene.AMBIENT, [rnd_normal(rng) for _ in range(1024)]))
    out.append((10, 0, rnd_rotation(rng), lit_scene.LIGHT_DIR, lit_scene.AMBIENT, []))
    # the three models, as the capstone sends them
    scene = lit_scene.build()
    for name, mdl in scene.items():
        for pose in (lit_scene.spin(0), lit_scene.spin(13), lit_scene.spin(41)):
            out.append((mdl.v0, mdl.nv, pose, lit_scene.LIGHT_DIR, lit_scene.AMBIENT, list(mdl.mesh.normals)))
    return out


def expect(case):
    first, count, m, light, amb, normals = case
    lmv = lm.model_light(m, light)
    return [lm.intensity(n, lmv, amb) for n in normals]


def write_unit_cases(path, rng):
    cs = cases(rng)
    words = [len(cs)]
    nverts = 0
    for c in cs:
        first, count, m, light, amb, normals = c
        words.append((count << 32) | (first << 16))
        mv = 0
        for k, e in enumerate(m):
            mv |= (e & 0xFFFF) << (16 * k)
        words += [(mv >> (64 * i)) & ((1 << 64) - 1) for i in range(3)]
        words.append(((amb & 0xFFFF) << 48) | ((light[2] & 0xFFFF) << 32) | ((light[1] & 0xFFFF) << 16) | (light[0] & 0xFFFF))
        for n in normals:
            words.append(((n[2] & 0xFFFF) << 32) | ((n[1] & 0xFFFF) << 16) | (n[0] & 0xFFFF))
        words += expect(c)
        nverts += count
    with open(path, "w") as f:
        f.write("".join("%016x\n" % w for w in words))
    return len(cs), nverts


# ---------------------------------------------------------------- tb_light_sys
class Sys:
    """A stream for tb_light_sys: bytes go to the model, replies become expect rows. uart_gpu reports capabilities 000F."""

    def __init__(self):
        self.m = g.Gpu(caps=g.CAPS_UART)
        self.items, self.exp, self.pending, self.nbytes = [], [], 0, 0

    def send(self, data):
        data = bytes(data)
        self.items.extend(data)
        self.nbytes += len(data)
        r = self.m.feed(data)
        self.exp.extend(0xFF00 | b for b in r)
        self.pending += len(r)
        return r

    def fence(self):
        while self.pending:
            n = min(self.pending, 4095)
            self.items.append(0x1000 | n)
            self.pending -= n


def good_vertices(m, ids):
    """The slots among ids whose record is defined and not NEAR (a NEAR record has no defined position fields) and whose byte 7 is defined."""
    return [i for i in ids if m.vdst[i] is not None and not m.vdst[i][3] & vm.FLAG_NEAR and m.vlit[i] is not None]


def sys_stream():
    s = Sys()
    m = s.m
    rng = random.Random(SEED)
    scene = lit_scene.build()
    notes = {}
    s.send(g.get_info())
    s.fence()
    # the whole scene: vertices, normals, indices
    for mdl in scene.values():
        s.send(b"".join(g.vertex_packets(mdl.v0, mdl.mesh.verts)) + b"".join(g.normal_packets(mdl.v0, mdl.mesh.normals)))
    nv = sum(mdl.nv for mdl in scene.values())

    def read(ids):
        for i in ids:
            s.send(g.read_vertex(i))
        s.fence()

    pose = lit_scene.spin(7)
    # 1. power-up: lighting is off, byte 7 of every record is 0 whatever the normals say
    s.send(g.set_projection(lit_scene.FOCAL, 1) + g.set_matrix(pose) + g.transform(0, nv))
    ids = good_vertices(m, range(0, nv, 7))
    assert ids and all(m.vlit[i] == 0 for i in ids)
    read(ids)
    # 2. lighting on: every vertex's intensity
    s.send(g.set_light(lit_scene.LIGHT_DIR, lit_scene.AMBIENT) + g.transform(0, nv))
    ids = good_vertices(m, range(nv))
    assert len(ids) > 100
    read(ids)
    # 3. SET_LIGHT changes nothing until the next TRANSFORM; a different direction and ambient then do
    probe = good_vertices(m, range(24, 66))[:16] + good_vertices(m, range(66, 162))[:24]
    s.send(g.set_light((lm.q14(0.6), lm.q14(-0.3), lm.q14(-0.5)), lm.q14(0.05)))
    read(probe)
    s.send(g.transform(0, nv))
    read(probe)
    # 4. the light is fixed in camera space: a new matrix moves the shading
    for k in (0, 1, 2):
        s.send(g.set_matrix(lit_scene.spin(10 * k + 3)) + g.transform(24, 42 + 96))
        read(good_vertices(m, range(24, 162))[::5])
    # 5. a scaled matrix scales the lit part (the scale rides through M^T L); a matrix with scale 0.5
    s.send(g.set_light(lit_scene.LIGHT_DIR, lit_scene.AMBIENT) + g.set_matrix(mm.rot_matrix(20, 40, 10, 5, -5, 330, scale=0.5)) + g.transform(66, 96))
    read(good_vertices(m, range(66, 162))[::4])
    s.send(g.set_matrix(mm.rot_matrix(20, 40, 10, 5, -5, 330, scale=1.9)) + g.transform(66, 96))
    read(good_vertices(m, range(66, 162))[::4])
    # 6. ambient: negative, zero, full, beyond full (clamped to 255); the diffuse term clamped at 0
    s.send(g.set_matrix(pose))
    for amb in (-5000, 0, 16384, 32767, -32768):
        s.send(g.set_light(lit_scene.LIGHT_DIR, amb) + g.transform(66, 96))
        read(good_vertices(m, range(66, 162))[::6])
    s.send(g.set_light((0, 0, 0), 4000) + g.transform(24, 42))                      # a zero light: ambient only
    read(good_vertices(m, range(24, 66))[:6])
    s.send(g.set_light((-32768, -32768, -32768), 0) + g.transform(24, 42))          # saturated l_m
    read(good_vertices(m, range(24, 66))[:6])
    # 7. lighting off again: byte 7 returns to 0 at the next TRANSFORM, the other bytes are as ever
    s.send(g.set_light(lit_scene.LIGHT_DIR, lit_scene.AMBIENT, enable=False) + g.transform(24, 42))
    read(good_vertices(m, range(24, 66))[:8])
    s.send(g.set_light(lit_scene.LIGHT_DIR, lit_scene.AMBIENT) + g.transform(24, 42))
    read(good_vertices(m, range(24, 66))[:8])
    # 8. a partial range changes only that range (the cube's records keep their old byte 7); reserved flag bits are ignored
    s.send(g.set_matrix(lit_scene.spin(20)) + g.packet(g.SET_LIGHT, bytes([0xFE]) + b"".join(int(v & 0xFFFF).to_bytes(2, "little") for v in (*lit_scene.LIGHT_DIR, lit_scene.AMBIENT))) + g.transform(70, 5))
    assert m.light_en == 0
    read(good_vertices(m, range(66, 80)) + good_vertices(m, range(0, 24))[:6])
    s.send(g.packet(g.SET_LIGHT, bytes([0x03]) + b"".join(int(v & 0xFFFF).to_bytes(2, "little") for v in (*lit_scene.LIGHT_DIR, lit_scene.AMBIENT)))
           + g.transform(70, 5))
    read(good_vertices(m, range(66, 80)))
    # 9. the whole buffer: normals for every slot, 1024 vertices lit, a sample read back; the clip at 1023; TRANSFORM runs to the last slot
    big = [((k % 32 - 16) * 9, (k // 32 - 16) * 7, 400 + (k * 7) % 50) for k in range(1024)]
    nrm = [tuple(lm.q14(x) for x in lm.unit((math.sin(k * 0.37), math.cos(k * 0.91), math.sin(k * 0.13) + 0.2))) for k in range(1024)]
    s.send(b"".join(g.vertex_packets(0, big)) + b"".join(g.normal_packets(0, nrm)))
    s.send(g.set_matrix(vm.identity()) + g.transform(0, 1024))
    read(good_vertices(m, range(0, 1024, 37)) + [1023, 1022])
    s.send(g.set_matrix(mm.rot_matrix(30, 50, 0, 0, 0, 100)) + g.transform(1020, 100) + g.transform(1024, 5) + g.transform(0, 0))   # clipped; the two no-ops
    read(good_vertices(m, range(1018, 1024)))
    # 10. UPLOAD_NORMALS: the end of the buffer (past-the-end slots dropped, no flag), the first slot, normals kept apart from vertices
    s.send(g.clear_errors(0x0F) + g.upload_normals(1022, [(0, 0, -16000)] * 4) + g.upload_normals(65535, [(0, 0, -4000)]) + g.upload_normals(0, [(0, 0, -16384)]))
    s.send(g.set_matrix(vm.identity()) + g.set_light((0, 0, -16384), 0) + g.transform(1022, 2) + g.transform(0, 2) + g.get_info())
    read(good_vertices(m, [0, 1, 1022, 1023]))
    s.send(g.get_info())
    s.fence()
    # 11. wrong lengths: BAD_LEN, each alone, nothing changes (a 1-byte-short and a 1-byte-long SET_LIGHT; UPLOAD_NORMALS not 2 + 6k)
    for pk in [g.packet(g.UPLOAD_NORMALS, bytes(n)) for n in (0, 1, 2, 7, 9, 13)] + [g.packet(g.SET_LIGHT, bytes(n)) for n in (0, 1, 8, 10, 12)]:
        s.send(g.clear_errors(0x0F) + pk + g.get_info())
        s.fence()
    s.send(g.set_matrix(vm.identity()) + g.transform(0, 3))
    read(good_vertices(m, [0, 1, 2]))
    # 12. unknown opcodes next to the new ones (0x6D, 0x6E and 0x6F are free): BAD_OP, skipped
    s.send(g.clear_errors(0x0F) + g.packet(0x6D, b"\x01\x02") + g.packet(0x6E, b"") + g.packet(0x6F, bytes(9)) + g.get_info())
    s.fence()
    # 13. display lists: SET_LIGHT and TRANSFORM may be in a list; UPLOAD_NORMALS may not (BAD_OP, skipped, nothing changes)
    body = (g.set_light((lm.q14(-0.5), lm.q14(0.5), lm.q14(-0.5)), lm.q14(0.1)) + g.set_matrix(lit_scene.spin(33)) + g.transform(66, 30)
            + g.upload_normals(66, [(16384, 0, 0)]) + g.transform(66, 4))
    s.send(g.clear_errors(0x0F) + b"".join(g.list_packets(5, body)) + g.set_matrix(vm.identity()) + g.run_list(5) + g.get_info())
    s.fence()
    read(good_vertices(m, range(66, 96)))
    s.send(g.run_list(5))
    read(good_vertices(m, range(66, 70)))
    # 14. a vertex with no normal: the lit TRANSFORM of it is not tested (undefined); the rest of its range is
    assert m.lit_undefined == 0 and m.read_undefined == 0
    s.items.append(0xFFFF)
    return s, notes


# ---------------------------------------------------------------- floating-point checks
def float_checks(verbose=True):
    """Fixed point against floating point, on the three models in 300 random poses each and 20 lights: every vertex's shade
    within 1 of the floating-point shade; also the unit length of the normals and the closed forms."""
    rng = random.Random(SEED)
    scene = {"cube": mesh_gen.cube(), "icosphere": mesh_gen.icosphere(2), "torus": mesh_gen.torus()}
    worst_shade, worst_int, nchecked = 0, 0.0, 0
    for _ in range(300):
        m = rnd_rotation(rng)
        light = tuple(lm.q14(0.8 * x) for x in rnd_unit(rng))
        amb = lm.q14(rng.choice((0.0, 0.1, 0.2, 0.3)))
        for mdl in scene.values():
            lmv = lm.model_light(m, light)
            for n in mdl.normals:
                b_fixed = lm.brightness(n, lmv, amb)
                b_float = lm.float_brightness(n, m, light, amb)
                i = lm.to_intensity(b_fixed)
                sh = lm.shade(i)
                dshade = abs(sh - lm.float_shade(b_float))
                worst_shade = max(worst_shade, dshade)
                worst_int = max(worst_int, abs(i - min(255.0, 248 * b_float)))
                nchecked += 1
    assert worst_shade <= 1, worst_shade
    assert worst_int <= 1.0, worst_int
    # a true (unquantised) rotation, unit normals and the scene's light as real numbers: the rounding of every input to Q2.14 included
    worst2 = 0.0
    light_true = [0.8 * x for x in lm.unit((-0.45, 0.55, -0.70))]
    for _ in range(300):
        ax, ay, az = (rng.uniform(0, 360) for _ in range(3))
        m = mm.rot_matrix(ax, ay, az, 0, 0, 0)
        a, b_, c = (math.radians(t) for t in (ax, ay, az))
        rx = [[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]]
        ry = [[math.cos(b_), 0, math.sin(b_)], [0, 1, 0], [-math.sin(b_), 0, math.cos(b_)]]
        rz = [[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]]
        rot = np.array(rz) @ np.array(ry) @ np.array(rx)                  # the same order as mesh_model.rot_matrix
        lm_ = lm.model_light(m, lit_scene.LIGHT_DIR)
        for mdl in scene.values():
            for n in mdl.normals:
                nf = np.array(lm.unit(n))                                 # the direction the Q2.14 normal stands for, unit length
                i = lm.intensity(n, lm_, lit_scene.AMBIENT)
                bf = 0.2 + max(float((rot @ nf) @ light_true), 0.0)
                worst2 = max(worst2, abs(i - min(255.0, 248 * bf)))
    assert worst2 <= 1.0, worst2
    if verbose:
        print("lighting, fixed against floating point: %d vertex evaluations, worst shade difference %d (limit 1), worst intensity difference %.2f of 255"
              % (nchecked, worst_shade, worst_int))
        print("against a true rotation, unit normals and the real light (Q2.14 rounding of every input included): worst intensity difference %.2f" % worst2)
    return worst_shade, worst_int


def write_hex(path, words, width):
    with open(path, "w") as f:
        f.write("".join(("%0*x\n" % (width, w)) for w in words))


def main():
    a = sys.argv[1:]
    if a == ["--check"]:
        float_checks()
        print("PASS: ref_light checks")
        return 0
    if len(a) == 2 and a[0] == "--files":
        d = a[1]
        float_checks(verbose=False)
        rng = random.Random(SEED)
        n, nv = write_unit_cases(os.path.join(d, "light_cases.hex"), rng)
        print("wrote %s/light_cases.hex: %d cases, %d vertices" % (d, n, nv))
        s, notes = sys_stream()
        write_hex(os.path.join(d, "light_sys_stream.hex"), s.items, 4)
        write_hex(os.path.join(d, "light_sys_expect.hex"), s.exp, 4)
        print("wrote %s/light_sys_stream.hex (%d entries, %d bytes) and light_sys_expect.hex (%d rows)" % (d, len(s.items), s.nbytes, len(s.exp)))
        print("PASS: ref_light checks")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
