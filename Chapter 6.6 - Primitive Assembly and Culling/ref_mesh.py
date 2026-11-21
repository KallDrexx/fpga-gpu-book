#!/usr/bin/env python3
"""ref_mesh.py -- checks, test files and reference frames for Chapter 6.6 (primitive assembly and culling).

Usage (from the repo root):
    .venv/bin/python sim/py/ref_mesh.py --check        the checks only (a few seconds): the cube, its orientations, numpy
    .venv/bin/python sim/py/ref_mesh.py --files sim    once: write every testbench file (and run the checks); commit them
    .venv/bin/python sim/py/ref_mesh.py OUTDIR         write OUTDIR/ref_mesh_f0.ppm ... f5.ppm (what `make frame-mesh` compares)

Needs host/gpu_model.py, host/mesh_model.py, host/vertex_model.py, host/raster_model.py, sim/py/ppm.py and numpy.

The checks (one PASS/FAIL line at the end):
  1. The cube: 12 triangles, every one counter-clockwise (area2 > 0) when its face points at the camera.
  2. A table of named orientations with the faces that point at the camera, the culled / rejected / drawn counts the
     fixed-point model gives and the count a floating-point numpy version gives.
  3. numpy, 100,000 random poses: a cube never shows more than three faces; when no face is nearly edge-on the pixel-rounded
     triangle test culls exactly 12 - 2 x (faces visible).  (Rounding positions to whole pixels can flip a face that is
     almost exactly edge-on, so the claim has that exception; the script reports how often.)
  4. The frames: the script's own model draws the six pictures; the cube pictures are checked for the faces they show.

Files written by --files (the testbenches read them):
    sim/mesh_cull_cases.hex      for tb_mesh_cull  (64-bit words; format in tb_mesh_cull.v)
    sim/mesh_sys_stream.hex      for tb_mesh_sys   (format in tb_mesh_sys.v)
    sim/mesh_sys_expect.hex
    sim/mesh_stream.hex          for tb_frame_mesh (the entries are those of sim/depth_stream.hex)
    sim/mesh_replies.hex         the reply bytes tb_frame_mesh must read: GET_INFO, then GET_MESH_STATS and GET_INFO

The story of the six frames (fN shows the picture drawn while f(N-1) was on the screen; every picture starts with CLEAR_ALL
and uses the same cube, one ramp per face):
    f0  a pose with three faces towards the camera, cull mode BACK: three faces, 6 triangles drawn, 6 culled
    f1  the same pose, cull mode NONE with the depth test on: the same picture (the hidden faces lose the depth test);
        culling saves work, it does not change the picture
    f2  the same pose, cull mode FRONT: the inside of the cube, the three faces that were hidden
    f3  the cube poking through the near plane (near = 8): the triangles that use a NEAR vertex are rejected, the rest is drawn
    f4  vertex-intensity mode (intensity from the records, which are 0 in this chapter): every face at its darkest shade
    f5  a display list (SET_MATRIX, TRANSFORM, SET_MESH_MODE, CLEAR_ALL, DRAW_MESH, FLIP) whose matrix the host patches with
        twelve WRITE_LIST_WORDs before RUN_LIST (the form a self-running list needs; Chapter 6.7 can simply send SET_MATRIX + RUN_LIST); a second list holding commands a list may not
        hold; five bad packets first
The first GET_INFO (flags 00) says capabilities 3FFF; the last (flags 06) follows the bad packets and the second list.
"""
import os
import random
import struct
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))

import numpy as np  # noqa: E402

import gpu_model as g  # noqa: E402
import mesh_model as mm  # noqa: E402
import raster_model as rm  # noqa: E402
import vertex_model as vm  # noqa: E402
from ppm import write_ppm  # noqa: E402

SEED = 6666
W, H = g.W, g.H
RAMPS = {0: ((24, 24, 48), (255, 255, 255)), 1: ((40, 0, 0), (255, 90, 90)), 2: ((0, 40, 0), (120, 255, 120)),
         3: ((0, 0, 48), (110, 140, 255)), 4: ((48, 40, 0), (255, 240, 60)), 5: ((0, 40, 40), (120, 255, 255)),
         6: ((40, 0, 40), (255, 110, 255)), 7: ((50, 20, 0), (255, 160, 40))}
TEST, WRITE = 1, 2


def palette():
    return b"".join(b"".join(g.ramp_palette(r, dark, light)) for r, (dark, light) in RAMPS.items())


# ---------------------------------------------------------------- the poses
# name, (ax, ay, az degrees, tx, ty, tz)
POSES = [
    ("straight on", (0, 0, 0, 0, 0, 300)),
    ("yaw 30", (0, 30, 0, 0, 0, 300)),
    ("yaw 30, pitch 25", (25, 30, 0, 0, 0, 300)),
    ("corner view (45 / 35.26)", (35.26, 45, 0, 0, 0, 300)),
    ("upside down", (0, 0, 180, 0, 0, 300)),
    ("left of the axis", (0, 0, 0, -200, 0, 300)),
    ("right and below", (0, 0, 0, 150, -120, 300)),
    ("camera in the +y face's plane", (0, 0, 0, 0, -60, 300)),
    ("camera in the +y and +x planes", (0, 0, 0, -60, -60, 300)),
    ("far away", (30, 20, 10, 0, 0, 2000)),
    ("rolled", (10, 20, 70, 20, 30, 350)),
    ("poking through the near plane", (0, 0, 0, 0, 0, 40)),
    ("behind the camera", (0, 0, 0, 0, 0, -300)),
]


def pose_matrix(p, scale=1.0):
    return mm.rot_matrix(*p, scale=scale)


def named_pose(name):
    return pose_matrix(dict(POSES)[name])


# ---------------------------------------------------------------- numpy sanity
def random_rotations(rng, n):
    """n random proper rotations (QR of Gaussian matrices), numpy."""
    a = rng.standard_normal((n, 3, 3))
    q, r = np.linalg.qr(a)
    q = q * np.sign(np.diagonal(r, axis1=1, axis2=2))[:, None, :]
    det = np.linalg.det(q)
    q[det < 0, :, 0] *= -1
    return q


def numpy_visibility(n=100000, seed=1):
    """For n random poses (random rotation, a translation that keeps the whole cube in front of a camera 400 units away or more,
    f = 277): the number of faces whose normal points at the camera (numpy, unrounded), and the rounded-pixel results.
    Returns (max faces seen, poses checked for rounding, rounding disagreements, smallest |area2| of a disagreement)."""
    rng = np.random.default_rng(seed)
    R = random_rotations(rng, n)
    t = np.stack([rng.uniform(-250, 250, n), rng.uniform(-200, 200, n), rng.uniform(250, 700, n)], axis=1)
    verts = np.array(mm.CUBE_VERTS, dtype=float)                         # (8, 3)
    cam = np.einsum("nij,vj->nvi", R, verts) + t[:, None, :]             # (n, 8, 3) camera positions
    normals = np.array([f[0] for f in mm.CUBE_FACES], dtype=float)
    nrm = np.einsum("nij,fj->nfi", R, normals)                           # rotated normals
    centres = np.array([[sum(mm.CUBE_VERTS[i][k] for i in f[1]) / 4.0 for k in range(3)] for f in mm.CUBE_FACES])
    cc = np.einsum("nij,fj->nfi", R, centres) + t[:, None, :]
    vis = (nrm * cc).sum(axis=2) < 0                                     # (n, 6)
    count = vis.sum(axis=1)
    assert count.max() <= 3 and count.min() >= 1
    # unrounded screen areas: every triangle of a visible face has area2 > 0, every other one <= 0
    scr = np.stack([160 + 277 * cam[:, :, 0] / cam[:, :, 2], 120 - 277 * cam[:, :, 1] / cam[:, :, 2]], axis=2)
    tris = mm.cube_triangles()
    ok_float = 0
    for i0, i1, i2, f in tris:
        a = rm.area2(*[(scr[:, i, 0], scr[:, i, 1]) for i in (i0, i1, i2)])
        ok_float += int(((a > 0) == vis[:, f]).all())
    assert ok_float == 12, "the unrounded screen test must agree with the plane test for all 12 triangles"
    # the pixel-rounded verdicts of the fixed-point pipeline, on a subset
    checked = bad = most_drawn = 0
    worst = None
    for k in range(0, min(n, 3000)):
        m = tuple(int(round(R[k, i, j] * vm.ONE)) for i in range(3) for j in range(3)) + tuple(int(round(x)) for x in t[k])
        recs = [vm.transform(v, m, vm.DEFAULT_F, vm.DEFAULT_NEAR) for v in mm.CUBE_VERTS]
        culled = 0
        small = 10 ** 9
        drawn_faces = set()
        for i0, i1, i2, f in tris:
            r3 = [recs[i0], recs[i1], recs[i2]]
            a = rm.area2(r3[0][:2], r3[1][:2], r3[2][:2])
            small = min(small, abs(a))
            culled += mm.verdict(r3, (i0, i1, i2), mm.CULL_BACK) == mm.CULLED
            if mm.verdict(r3, (i0, i1, i2), mm.CULL_BACK) == mm.DRAW:
                drawn_faces.add(f)
        most_drawn = max(most_drawn, len(drawn_faces))
        checked += 1
        if culled != 12 - 2 * int(count[k]):
            bad += 1
            worst = small if worst is None else max(worst, small)
            assert small <= 64, ("rounding flipped a face although every triangle has |area2| > 64", k, small)
    assert most_drawn <= 3, "the pixel-rounded pipeline drew a fourth face"
    return int(count.max()), checked, bad, worst


# ---------------------------------------------------------------- tb_mesh_cull
def rec(sx, sy, depth=0, flags=0, inten=0):
    return (inten << 56) | (flags << 48) | ((depth & 0xFFFF) << 32) | ((sy & 0xFFFF) << 16) | (sx & 0xFFFF)


def rec_tuple(r):
    s = lambda v: v - 65536 if v >= 32768 else v  # noqa: E731
    return (s(r & 0xFFFF), s((r >> 16) & 0xFFFF), (r >> 32) & 0xFFFF, (r >> 48) & 0xFF)


def cull_case(r0, r1, r2, mode, bad):
    idx = (5000, 1, 2) if bad else (0, 1, 2)
    v = mm.verdict([rec_tuple(r0), rec_tuple(r1), rec_tuple(r2)], idx, mode)
    return (r0, r1, r2, (mode << 4) | (bad << 2) | v)


def cull_cases(rng):
    out = []

    def add(p0, p1, p2, flags=(0, 0, 0), mode=None, bad=0):
        for mode_ in ([mode] if mode is not None else [rng.randint(0, 3)]):
            fl = flags
            r = [rec(p[0], p[1], rng.getrandbits(16), f, rng.getrandbits(8)) for p, f in zip((p0, p1, p2), fl)]
            out.append(cull_case(*r, mode_, bad))

    def pt(lo, hi):
        return (rng.randint(lo, hi), rng.randint(lo, hi))
    for _ in range(6000):                                                 # ordinary screen positions, every mode
        add(pt(-100, 420), pt(-100, 420), pt(-100, 420), mode=rng.randint(0, 3))
    for _ in range(4000):                                                 # the whole int16 range: 17-bit differences, 34-bit products
        add(pt(-32768, 32767), pt(-32768, 32767), pt(-32768, 32767), mode=rng.randint(0, 3))
    corners = [-32768, 32767, 0, -1, 1]
    for _ in range(3000):
        add((rng.choice(corners), rng.choice(corners)), (rng.choice(corners), rng.choice(corners)),
            (rng.choice(corners), rng.choice(corners)), mode=rng.randint(0, 3))
    for _ in range(3000):                                                 # exactly collinear: area2 = 0 (neither front nor back-facing: BACK)
        p0 = pt(-3000, 3000)
        d = (rng.randint(-300, 300), rng.randint(-300, 300))
        k1, k2 = rng.randint(-5, 5), rng.randint(-5, 5)
        p1 = (p0[0] + k1 * d[0], p0[1] + k1 * d[1])
        p2 = (p0[0] + k2 * d[0], p0[1] + k2 * d[1])
        add(p0, p1, p2, mode=rng.randint(0, 3))
    for _ in range(3000):                                                 # area2 = +1 or -1, every mode
        a = pt(-200, 200)
        b = (a[0] + 1, a[1])
        c = (a[0] + rng.randint(-50, 50), a[1] + rng.choice([-1, 1]))
        tri = [a, b, c]
        rng.shuffle(tri)
        add(*tri, mode=rng.randint(0, 3))
    for _ in range(4000):                                                 # flags: NEAR, CLAMP, both, none, on any corner; bad indices
        add(pt(-100, 420), pt(-100, 420), pt(-100, 420), flags=tuple(rng.choice([0, 0, 0, 1, 2, 3]) for _ in range(3)),
            mode=rng.randint(0, 3), bad=1 if rng.random() < 0.1 else 0)
    for mode in range(4):                                                 # one fixed pair of triangles per mode: front, back, flat, each flag
        for tri, fl in ((((10, 10), (50, 10), (10, 50)), (0, 0, 0)), (((10, 10), (10, 50), (50, 10)), (0, 0, 0)),
                        (((10, 10), (20, 20), (30, 30)), (0, 0, 0)), (((10, 10), (50, 10), (10, 50)), (1, 0, 0)),
                        (((10, 10), (50, 10), (10, 50)), (0, 2, 0)), (((10, 10), (50, 10), (10, 50)), (0, 0, 3))):
            add(*tri, flags=fl, mode=mode)
    rng.shuffle(out)
    return out


def write_cull_cases(path, rng):
    cases = cull_cases(rng)
    with open(path, "w") as f:
        f.write("%016x\n" % len(cases))
        for r0, r1, r2, meta in cases:
            f.write("%016x\n%016x\n%016x\n%016x\n" % (r0, r1, r2, meta))
    kinds = [0, 0, 0]
    for c in cases:
        kinds[c[3] & 3] += 1
    return len(cases), kinds


# ---------------------------------------------------------------- tb_mesh_sys
class Sys:
    """A stream for tb_mesh_sys: bytes go to the GPU model, replies become expect rows."""

    def __init__(self):
        self.m = g.Gpu(caps=g.CAPS_UART)           # tb_mesh_sys uses uart_gpu, which reports capabilities 000F
        self.items, self.exp, self.pending, self.nbytes = [], [], 0, 0

    def send(self, data):
        data = bytes(data)
        self.items.extend(data)
        self.nbytes += len(data)
        r = self.m.feed(data)
        self.exp.extend(0xFF00 | b for b in r)
        self.pending += len(r)
        return r

    def stats(self):
        """GET_STATS: the clock counter (bytes 8-11) is the engine's own business: its rows are not compared."""
        r = self.send(g.get_stats())
        for k in range(1, 5):
            self.exp[-k] = 0x0000
        return g.parse_stats(r)

    def fence(self):
        while self.pending:
            n = min(self.pending, 4095)
            self.items.append(0x1000 | n)
            self.pending -= n


def sys_stream():
    s = Sys()
    m = s.m
    rng = random.Random(SEED)
    s.send(g.get_info())
    s.fence()
    s.send(b"".join(g.vertex_packets(0, mm.CUBE_VERTS)) + b"".join(g.index_packets(0, mm.cube_entries())))
    s.send(g.set_depth(True, True))
    notes = {"poses": []}

    def cube_draw(matrix, mode=None, first=0, count=12, f=277, near=1, vertex_i=False, label=""):
        """transform, clear, draw, read both counters; returns (culled, rejected, drawn)."""
        pk = g.set_projection(f, near) + g.set_matrix(matrix) + g.transform(0, 8) + g.clear_mesh_stats() + g.clear_stats() + g.clear_all(0, 65535)
        if mode is not None:
            pk += g.set_mesh_mode(mode, vertex_i)
        s.send(pk + g.draw_mesh(first, count) + g.get_mesh_stats())
        st = (m.mesh_culled, m.mesh_rejected, m.mesh_drawn)
        s.stats()
        s.fence()
        notes["poses"].append((label, mode, st))
        return st

    # power-up mode: nothing is culled
    st = cube_draw(named_pose("straight on"), mode=None, label="power-up mode, straight on")
    assert st == (0, 0, 12), st
    for k, (name, p) in enumerate(POSES):
        matrix = pose_matrix(p)
        st = cube_draw(matrix, mm.CULL_BACK, label=name)
        if k < 4 or name in ("camera in the +y face's plane", "poking through the near plane"):
            for mode in (mm.CULL_NONE, mm.CULL_FRONT, mm.CULL_BOTH):
                cube_draw(matrix, mode, label=name)
    cube_draw(pose_matrix(POSES[2][1]), mm.CULL_BACK, vertex_i=True, label="vertex-intensity mode")
    # a scaled and a rolled pose, and the near plane moved
    cube_draw(pose_matrix((15, 100, 5, 10, 10, 120), scale=1.5), mm.CULL_BACK, near=8, label="scale 1.5, near 8")
    cube_draw(pose_matrix((15, 100, 5, 10, 10, 90), scale=1.5), mm.CULL_BACK, near=40, label="near 40: many vertices rejected")
    for i in range(6):
        p = (rng.uniform(0, 360), rng.uniform(0, 360), rng.uniform(0, 360), rng.randint(-150, 150), rng.randint(-100, 100), rng.randint(180, 500))
        cube_draw(mm.rot_matrix(*p), mm.CULL_BACK, label="random pose")
    # the counters are separate: CLEAR_STATS leaves the mesh counters, CLEAR_MESH_STATS leaves GET_STATS
    s.send(g.clear_stats() + g.get_mesh_stats())
    s.stats()
    s.send(g.clear_mesh_stats())
    s.stats()
    s.send(g.get_mesh_stats())
    s.fence()
    # one draw after another: the counters add up
    s.send(g.set_projection(277, 1) + g.set_matrix(named_pose("yaw 30, pitch 25")) + g.transform(0, 8) + g.set_mesh_mode(mm.CULL_BACK)
           + g.draw_mesh(0, 6) + g.get_mesh_stats() + g.draw_mesh(6, 6) + g.get_mesh_stats() + g.draw_mesh(3, 5) + g.get_mesh_stats())
    s.fence()

    # ---- CLAMP, NEAR and bad indices on each of the three corners (slots 8 and up, entries 12 and up)
    verts = [(500, 0, 2), (0, 50, 300), (50, 0, 300), (60, 60, 300), (0, 0, 0)]       # 8: CLAMP, 9, 10, 11 fine, 12: NEAR
    s.send(g.upload_vertices(8, verts))
    E = lambda a, b, c, r=1, i=200: mm.pack_entry(a, b, c, r, i)  # noqa: E731
    entries = [E(9, 10, 11), E(8, 9, 10), E(9, 8, 10), E(9, 10, 8), E(12, 9, 10), E(9, 12, 10), E(9, 10, 12),
               E(1024, 9, 10), E(9, 1024 + 5, 10), E(9, 10, 0x8000 + 1), E(0xFFFF, 9, 10), E(9, 2048 + 9, 10), E(9, 10, 4096 + 11),
               E(9, 11, 10, 2, 100), E(11, 9, 10, 3, 50)]
    s.send(g.upload_indices(12, entries))
    s.send(g.set_matrix(vm.identity()) + g.set_projection(277, 1) + g.transform(8, 5) + g.set_depth(False, False) + g.clear_mesh_stats()
           + g.set_mesh_mode(mm.CULL_NONE) + g.draw_mesh(12, len(entries)) + g.get_mesh_stats())
    s.send(g.set_mesh_mode(mm.CULL_BACK) + g.clear_mesh_stats() + g.draw_mesh(12, len(entries)) + g.get_mesh_stats())
    s.send(g.set_mesh_mode(mm.CULL_FRONT) + g.clear_mesh_stats() + g.draw_mesh(12, len(entries)) + g.get_mesh_stats())
    s.fence()
    assert s.m.mesh_undefined == 0

    # ---- display lists: the 6.7 pattern
    pose1, pose2 = named_pose("yaw 30, pitch 25"), named_pose("corner view (45 / 35.26)")
    body = (g.set_matrix(pose1) + g.transform(0, 8) + g.set_mesh_mode(mm.CULL_BACK) + g.set_depth(True, True) + g.clear_all(0, 65535)
            + g.clear_mesh_stats() + g.draw_mesh(0, 12) + g.flip())
    s.send(b"".join(g.list_packets(0, body)))
    s.send(g.clear_stats() + g.run_list(0) + g.get_mesh_stats())
    s.stats()
    s.send(b"".join(g.write_list_word(0, 2 + 2 * k, pose2[k] & 0xFFFF) for k in range(12)) + g.clear_stats() + g.run_list(0) + g.get_mesh_stats())
    s.stats()
    s.fence()
    # a list with commands a list may not hold: skipped with BAD_OP, the counters change only by what is allowed
    forb = (g.clear_mesh_stats() + g.upload_indices(0, [mm.pack_entry(0, 0, 0, 0, 0)]) + g.get_mesh_stats() + g.set_mesh_mode(mm.CULL_FRONT)
            + g.draw_mesh(0, 12))
    s.send(g.clear_errors(0x0F) + b"".join(g.list_packets(1, forb)) + g.set_mesh_mode(mm.CULL_BACK) + g.run_list(1) + g.get_mesh_stats() + g.get_info())
    s.fence()
    # the engine finishes a mesh before the next command: a draw, then the counters, in one packet stream with no wait
    s.send(g.clear_errors(0x0F) + g.set_mesh_mode(mm.CULL_BACK) + g.clear_mesh_stats() + g.set_matrix(pose1) + g.transform(0, 8)
           + g.draw_mesh(0, 12) + g.draw_mesh(0, 12) + g.get_mesh_stats())
    s.fence()

    # ---- wrong lengths: BAD_LEN, nothing changes; ranges that name nothing: no flag
    s.send(g.clear_errors(0x0F))
    bad = [g.packet(g.UPLOAD_INDICES, bytes(n)) for n in (0, 1, 2, 9, 11, 17)] + [g.packet(g.SET_MESH_MODE, bytes(n)) for n in (0, 2)] \
        + [g.packet(g.DRAW_MESH, bytes(n)) for n in (0, 3, 5)] + [g.packet(g.GET_MESH_STATS, bytes(1)), g.packet(g.CLEAR_MESH_STATS, bytes(1))]
    for pk in bad:                                                    # each one alone: the flag, and only that one, must show
        s.send(g.clear_errors(0x0F) + pk + g.get_info())
        s.fence()
    s.send(g.get_mesh_stats() + g.get_info())
    s.send(g.clear_errors(0x0F) + g.draw_mesh(512, 4) + g.draw_mesh(65535, 65535) + g.draw_mesh(0, 0) + g.draw_mesh(13, 0)
           + g.packet(g.SET_MESH_MODE, bytes([0xFC])) + g.set_mesh_mode(mm.CULL_BACK) + g.get_mesh_stats() + g.get_info())
    s.fence()

    # ---- the whole buffer: 1,024 vertices, 512 entries, bad indices, one NEAR and one CLAMP vertex
    grid = []
    for j in range(1024):
        col, row = j % 32, j // 32
        grid.append(((col - 16) * 9, (row - 16) * 7, 400 + ((col * 7 + row * 13) % 50)))
    grid[100] = (0, 0, -500)                 # behind the camera
    grid[200] = (30000, 0, 2)                # just in front of the near plane, far to the side: CLAMP
    ents = []
    for k in range(512):
        col = k % 32
        t = (k, k + 1, k + 32) if col != 31 else (k, k - 1, k + 32)
        if k % 2:
            t = (t[0], t[2], t[1])
        if k % 97 == 5:
            t = (t[0], 1024 + 20000 * (k % 3), t[2])
        ents.append(mm.pack_entry(t[0], t[1], t[2], k % 8, (k * 37) % 256))
    ents[511] = mm.pack_entry(1023, 1022, 991, 7, 99)
    s.send(b"".join(g.vertex_packets(0, grid)) + b"".join(g.index_packets(0, ents)))
    s.send(g.set_matrix(vm.identity()) + g.set_projection(277, 1) + g.transform(0, 1024) + g.set_depth(True, True) + g.clear_all(0, 65535))
    for mode in (mm.CULL_BACK, mm.CULL_NONE, mm.CULL_FRONT):
        s.send(g.clear_mesh_stats() + g.clear_stats() + g.clear_all(0, 65535) + g.set_mesh_mode(mode) + g.draw_mesh(0, 512) + g.get_mesh_stats())
        s.stats()
        s.fence()
    s.send(g.clear_mesh_stats() + g.clear_stats() + g.set_mesh_mode(mm.CULL_NONE) + g.draw_mesh(510, 100) + g.get_mesh_stats())     # clipped at 511
    s.stats()
    s.send(g.clear_mesh_stats() + g.draw_mesh(500, 12) + g.get_mesh_stats())
    s.fence()
    # entries past 511 are dropped: three at 510 write two
    s.send(g.upload_indices(510, [mm.pack_entry(0, 1, 32, 1, 1), mm.pack_entry(1, 2, 33, 1, 1), mm.pack_entry(2, 3, 34, 1, 1)]) + g.clear_errors(0x0F)
           + g.upload_indices(65535, [mm.pack_entry(0, 1, 32, 1, 1)]) + g.clear_mesh_stats() + g.clear_stats() + g.draw_mesh(509, 3)
           + g.get_mesh_stats() + g.get_info())
    s.stats()
    s.fence()
    assert s.m.mesh_undefined == 0 and s.m.read_undefined == 0
    s.items.append(0xFFFF)
    return s, notes


# ---------------------------------------------------------------- tb_frame_mesh
OPEN, CLOSE, FRAME_START, ARM, FENCE, END, LINE = 0xA000, 0xA001, 0x3000, 0x4000, 0x1000, 0xFFFF, 0x2000
LABELS = ["f0", "f1", "f2", "f3", "f4", "f5"]
PATCH_LINE = 20
POSE_A = (25, 30, 0, 0, 0, 240)
POSE_B = (35.26, 45, 0, 0, 0, 240)
POSE_C = (-20, 200, 30, 10, -5, 240)
POSE_NEAR = (20, 25, 0, 10, 0, 70)


def cube_picture(pose, cull, near=1, vertex_i=False, clear=9):
    return (g.set_projection(277, near) + g.set_matrix(pose_matrix(pose)) + g.transform(0, 8) + g.set_mesh_mode(cull, vertex_i)
            + g.set_depth(True, True) + g.clear_all(clear, 65535) + g.draw_mesh(0, 12))


def frame_story():
    """Returns (stream words, replies, {label: screen index bytes}, model)."""
    m = g.Gpu()
    items, replies, shots = [], bytearray(), {}
    state = {"armed": False}

    def frame(data, line=None):
        if line is not None:
            items.append(LINE | line)
        data = bytes(data)
        items.extend([OPEN] + list(data) + [CLOSE])
        replies.extend(m.feed(data))

    def start_frame():
        items.append(FRAME_START)
        m.vblank()
        if state["armed"]:
            shots[LABELS[len(shots)]] = m.screen()

    def picture(data, line=None, tail=b""):
        """The commands of a picture in frames of at most 250 bytes, then FLIP."""
        first = True
        for p in split_packets(data):
            frame(p, line if first else None)
            first = False
        frame(g.flip() + tail)

    frame(g.clear(0) + g.flip() + g.clear(0))
    start_frame()
    pal = palette()
    for i in range(0, len(pal), 6 * 32):
        frame(pal[i:i + 6 * 32])
    frame(b"")                                                                 # an empty frame: CS falls and rises with nothing between
    for p in g.vertex_packets(0, mm.CUBE_VERTS) + g.index_packets(0, mm.cube_entries()):
        frame(p)
    picture(cube_picture(POSE_A, mm.CULL_BACK), tail=g.get_info())            # the picture of f0, flipped now
    items.append(FENCE | 9)
    assert m.errors == 0
    start_frame()                                                              # k = 0
    items += [LINE | PATCH_LINE, ARM]
    state["armed"] = True
    start_frame()                                                              # k = 1: f0 on the screen; draw f1
    picture(cube_picture(POSE_A, mm.CULL_NONE), PATCH_LINE)
    start_frame()                                                              # k = 2: f1; draw f2
    picture(cube_picture(POSE_A, mm.CULL_FRONT), PATCH_LINE)
    start_frame()                                                              # k = 3: f2; draw f3
    picture(cube_picture(POSE_NEAR, mm.CULL_NONE, near=8), PATCH_LINE)
    start_frame()                                                              # k = 4: f3; draw f4, define the lists that make f5
    picture(cube_picture(POSE_B, mm.CULL_BACK, vertex_i=True), PATCH_LINE)
    body = (g.set_matrix(pose_matrix(POSE_B)) + g.transform(0, 8) + g.set_projection(277, 1) + g.set_mesh_mode(mm.CULL_BACK)
            + g.set_depth(True, True) + g.clear_all(9, 65535) + g.draw_mesh(0, 12) + g.flip())
    for p in g.list_packets(0, body):
        frame(p)
    forb = g.upload_indices(0, [mm.pack_entry(0, 0, 0, 0, 0)]) + g.get_mesh_stats() + g.draw_mesh(0, 1)
    for p in g.list_packets(1, forb):
        frame(p)
    start_frame()                                                              # k = 5: f4; the host sends only a matrix and RUN_LIST
    badp = [g.packet(g.UPLOAD_INDICES, bytes(9)), g.packet(g.DRAW_MESH, bytes(3)), g.packet(g.SET_MESH_MODE, b""),
            g.packet(g.GET_MESH_STATS, bytes(1)), g.packet(g.CLEAR_MESH_STATS, bytes(2))]
    for n, p in enumerate(badp):
        frame(p, PATCH_LINE if n == 0 else None)
    pc = pose_matrix(POSE_C)
    frame(b"".join(g.write_list_word(0, 2 + 2 * k, pc[k] & 0xFFFF) for k in range(12)))
    frame(g.run_list(1) + g.run_list(0) + g.get_mesh_stats() + g.get_info())
    items.append(FENCE | 21)
    start_frame()                                                              # k = 6: f5 on the screen
    assert len(shots) == 6, len(shots)
    want_head = bytes([0x47, 1, 0xFF, 0x3F, 0x40, 0x01, 0xF0, 0x00, 0x00])
    assert bytes(replies[:9]) == want_head, bytes(replies).hex()
    assert bytes(replies[-9:]) == bytes([0x47, 1, 0xFF, 0x3F, 0x40, 0x01, 0xF0, 0x00, 0x06]), bytes(replies).hex()
    assert len(replies) == 30
    assert m.mesh_undefined == 0
    return items, bytes(replies), shots, m


def split_packets(data):
    """Whole packets of `data`, grouped so that no group is over 250 bytes."""
    out, cur, i = [], b"", 0
    while i < len(data):
        n = 2 + data[i + 1]
        if len(cur) + n > 250:
            out.append(cur)
            cur = b""
        cur += data[i:i + n]
        i += n
    if cur:
        out.append(cur)
    return out


def faces_in(shot):
    """The face numbers whose ramp occurs in a screen picture (index bytes, ramp = index >> 5; ramp 0 is the background)."""
    return {int(c) - 1 for c in set(int(x) >> 5 for x in shot) if 1 <= c <= 6}


def frame_checks(shots, m):
    notes = {}
    mat = pose_matrix(POSE_A)
    towards = mm.faces_toward_camera(mat)
    assert len(towards) == 3, towards
    f0, f1, f2, f3, f4, f5 = [shots[x] for x in LABELS]
    assert faces_in(f0) == towards, (faces_in(f0), towards)                      # the faces numpy says point at the camera
    # f1 against f0: the hidden faces lose the depth test, except along the silhouette, where a hidden face meets a visible one at
    # (nearly) equal depth and its rounded depth can be a level smaller: those pixels, and only those, differ
    a0 = np.frombuffer(f0, dtype=np.uint8).reshape(H, W)
    diff = [(i % W, i // W) for i, (a, b) in enumerate(zip(f0, f1)) if a != b]
    for x, y in diff:
        near_edge = any(a0[yy, xx] >> 5 == 0 for yy in range(max(0, y - 1), min(H, y + 2)) for xx in range(max(0, x - 1), min(W, x + 2)))
        assert near_edge, ("cull NONE differs from cull BACK away from the silhouette", x, y)
    assert len(diff) < 400
    notes["f1_f0_diff"] = len(diff)
    assert faces_in(f2) and faces_in(f2) <= set(range(6)) - towards              # the inside: only faces that point away
    assert f2 != f0
    assert 1 <= len(faces_in(f3)) and f3 != f0
    c4 = mm.faces_toward_camera(pose_matrix(POSE_B))
    assert faces_in(f4) == c4 and len(c4) == 3
    assert all(int(x) & 31 == 0 for x in f4 if int(x) >> 5), "vertex-intensity mode: every face pixel is shade 0 in this chapter"
    cf = mm.faces_toward_camera(pose_matrix(POSE_C))
    assert faces_in(f5) == cf, (faces_in(f5), cf)
    notes["faces"] = [sorted(faces_in(x)) for x in (f0, f1, f2, f3, f4, f5)]
    return notes


def write_hex(path, words, width):
    with open(path, "w") as f:
        f.write("".join(("%0*x\n" % (width, w)) for w in words))


# ---------------------------------------------------------------- the checks
def run_checks(verbose=True):
    rng = random.Random(SEED)
    tris = mm.cube_triangles()
    # 1. winding
    for k, (i0, i1, i2, f) in enumerate(tris):
        n = mm.CUBE_FACES[f][0]
        a, b, c = [mm.CUBE_VERTS[i] for i in (i0, i1, i2)]
        assert mm._cross_dot(n, a, b, c) < 0
    assert len(tris) == 12
    # 2. the table
    lines = ["| pose | faces towards the camera | culled | rejected | drawn | float culled |", "| --- | --- | --- | --- | --- | --- |"]
    for name, p in POSES:
        m = pose_matrix(p)
        recs = [vm.transform(v, m, 277, 1) for v in mm.CUBE_VERTS]
        cnt = mm.expected_counts(m, mm.CULL_BACK)
        fl = mm.faces_toward_camera(m)
        zc = [mm.float_camera(m, v)[2] for v in mm.CUBE_VERTS]
        floatc = "-" if min(zc) <= 1 else str(12 - 2 * len(fl))
        lines.append("| %s | %s | %d | %d | %d | %s |" % (name, " ".join(mm.FACE_NAMES[f] for f in sorted(fl)) if min(zc) > 0 else "-", *cnt, floatc))
        if min(zc) > 1:
            if cnt[1] == 0 and name not in ("camera in the +y face's plane", "camera in the +y and +x planes"):
                assert cnt[0] == 12 - 2 * len(fl), (name, cnt, fl)
    # 3. numpy
    most, checked, bad, worst = numpy_visibility()
    # 4. frames
    items, replies, shots, m = frame_story()
    notes = frame_checks(shots, m)
    if verbose:
        print("\n".join(lines))
        print("numpy, 100000 random poses: at most %d faces towards the camera; of %d poses run through the fixed-point pipeline, "
              "%d differ from the plane test (in each, some triangle has |area2| of at most %s: a face almost exactly edge-on)" % (most, checked, bad, worst))
        print("frame faces:", notes["faces"], "; f1 differs from f0 in", notes["f1_f0_diff"], "silhouette pixels")
    return most, checked, bad, worst


def main():
    a = sys.argv[1:]
    if a == ["--check"]:
        run_checks()
        print("PASS: ref_mesh checks")
        return 0
    if len(a) == 2 and a[0] == "--files":
        d = a[1]
        rng = random.Random(SEED)
        run_checks(verbose=False)
        n, kinds = write_cull_cases(os.path.join(d, "mesh_cull_cases.hex"), rng)
        print("wrote %s/mesh_cull_cases.hex: %d cases (DRAW %d, CULLED %d, REJECTED %d)" % (d, n, *kinds))
        s, notes = sys_stream()
        write_hex(os.path.join(d, "mesh_sys_stream.hex"), s.items, 4)
        write_hex(os.path.join(d, "mesh_sys_expect.hex"), s.exp, 4)
        print("wrote %s/mesh_sys_stream.hex (%d entries, %d bytes) and mesh_sys_expect.hex (%d rows)" % (d, len(s.items), s.nbytes, len(s.exp)))
        items, replies, shots, m = frame_story()
        write_hex(os.path.join(d, "mesh_stream.hex"), items + [END], 4)
        write_hex(os.path.join(d, "mesh_replies.hex"), replies, 2)
        print("wrote %s/mesh_stream.hex (%d entries) and mesh_replies.hex (%d bytes)" % (d, len(items) + 1, len(replies)))
        print("PASS: ref_mesh checks")
        return 0
    if len(a) == 1 and not a[0].startswith("-"):
        items, replies, shots, m = frame_story()
        pal = np.array([[(c >> 16) & 255, (c >> 8) & 255, c & 255] for c in m.pal], dtype=np.uint16)
        for label, idx in shots.items():
            small = pal[np.frombuffer(idx, dtype=np.uint8).reshape(H, W)]
            write_ppm(os.path.join(a[0], "ref_mesh_%s.ppm" % label), small.repeat(2, axis=0).repeat(2, axis=1), 255)
        print("wrote %d reference frames to %s" % (len(shots), a[0]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
