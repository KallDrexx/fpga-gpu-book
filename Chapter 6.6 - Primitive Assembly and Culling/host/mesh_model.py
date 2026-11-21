"""mesh_model.py -- primitive assembly and culling of Chapter 6.6, and the cube the tests draw.

Copy to host/mesh_model.py (new in 6.6; host/gpu_model.py imports it).

What DRAW_MESH decides for each triangle, in this order (verdict(...)):

  1. REJECTED  if any of its three indices is 1024 or more (no such vertex), or any of the three transformed records
               has the NEAR flag (no position) or the CLAMP flag (a position the geometry does not give).
  2. CULLED    otherwise, by the sign of area2 = raster_model.area2 of the three screen positions (sx, sy) in the
               order i0, i1, i2.  FRONT-facing means area2 > 0 (counter-clockwise on the monitor); everything else,
               area2 = 0 included, is BACK-facing.  Cull mode 0 culls nothing, 1 culls back faces, 2 culls front faces,
               3 culls everything.
  3. DRAW      otherwise: DRAW_TRI_SHADED with the three screen positions, the three depths, the ramp from the index entry
               and, per corner, the entry's intensity byte (or, in vertex-intensity mode, byte 7 of that corner's record).

The cube: 8 vertices (x, y, z at +-HALF), 6 faces, 12 triangles.  Every triangle is wound counter-clockwise when
the viewer looks at its face from OUTSIDE the cube, so with cull mode 1 (back) only the faces that point at the camera
are drawn.  Camera space is left-handed (x right, y up, z forward), which is why the cross product test below has the sign
it has.  Self-check:  python host/mesh_model.py
"""
import math
import os
import struct
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import raster_model  # noqa: E402
import vertex_model  # noqa: E402

N_TRIANGLES = 512              # index buffer entries
ENTRY_BYTES = 8                # i0, i1, i2 (u16 each), ramp, intensity
ENTRY_CHUNK = 31               # entries in one UPLOAD_INDICES packet: 2 + 8 * 31 = 250 payload bytes
CULL_NONE, CULL_BACK, CULL_FRONT, CULL_BOTH = 0, 1, 2, 3
MODE_VERTEX_I = 4              # SET_MESH_MODE bit 2: intensity from the vertex records
DRAW, CULLED, REJECTED = 0, 1, 2
HALF = 60                      # the cube's half-size in model units


def pack_entry(i0, i1, i2, ramp, inten):
    """One index-buffer entry: 8 bytes, little endian."""
    return struct.pack("<HHHBB", i0, i1, i2, ramp, inten)


def verdict(recs, indices, mode):
    """DRAW, CULLED or REJECTED for the transformed records recs = [(sx, sy, depth, flags), ...] of the triangle's three
    corners (None for a record nothing defined), their indices and the SET_MESH_MODE value (bits 1:0 matter)."""
    if any(i >= vertex_model.N_VERTICES for i in indices):
        return REJECTED
    if any(r is None or r[3] & (vertex_model.FLAG_NEAR | vertex_model.FLAG_CLAMP) for r in recs):
        return REJECTED
    front = raster_model.area2(recs[0][:2], recs[1][:2], recs[2][:2]) > 0
    cull = mode & 3
    if cull == CULL_NONE:
        return DRAW
    if cull == CULL_BOTH:
        return CULLED
    if cull == CULL_BACK:
        return DRAW if front else CULLED
    return CULLED if front else DRAW


# ---------------------------------------------------------------- the cube
CUBE_VERTS = [(HALF * (2 * (i & 1) - 1), HALF * (2 * ((i >> 1) & 1) - 1), HALF * (2 * ((i >> 2) & 1) - 1)) for i in range(8)]
# face: (outward normal, four corners in cyclic order)
CUBE_FACES = [((-1, 0, 0), (0, 2, 6, 4)), ((1, 0, 0), (1, 3, 7, 5)),
              ((0, -1, 0), (0, 1, 5, 4)), ((0, 1, 0), (2, 3, 7, 6)),
              ((0, 0, -1), (0, 1, 3, 2)), ((0, 0, 1), (4, 5, 7, 6))]
FACE_NAMES = ["-x", "+x", "-y", "+y", "-z", "+z"]


def _cross_dot(n, a, b, c):
    """(b - a) x (c - a) . n"""
    u = [b[k] - a[k] for k in range(3)]
    v = [c[k] - a[k] for k in range(3)]
    cr = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    return sum(cr[k] * n[k] for k in range(3))


def _face_triangles(n, corners):
    """Two triangles of a face, wound so that, in the left-handed camera space, they run counter-clockwise on the monitor
    when the face points at the viewer: (b - a) x (c - a) . n_outward < 0."""
    out = []
    for t in ((corners[0], corners[1], corners[2]), (corners[0], corners[2], corners[3])):
        a, b, c = [CUBE_VERTS[i] for i in t]
        out.append(t if _cross_dot(n, a, b, c) < 0 else (t[0], t[2], t[1]))
    return out


def cube_triangles():
    """The 12 triangles as (i0, i1, i2, face number), two per face."""
    return [(t[0], t[1], t[2], f) for f, (n, c) in enumerate(CUBE_FACES) for t in _face_triangles(n, c)]


CUBE_RAMPS = [1, 2, 3, 4, 5, 6]          # one palette ramp per face
CUBE_INTENS = [248, 232, 216, 200, 184, 168]


def cube_entries(ramps=CUBE_RAMPS, intens=CUBE_INTENS):
    return [pack_entry(i0, i1, i2, ramps[f], intens[f]) for i0, i1, i2, f in cube_triangles()]


def rot_matrix(ax, ay, az, tx, ty, tz, scale=1.0):
    """Rotation about x, then y, then z (angles in degrees) times scale, as 12 ints in the SET_MATRIX layout (Q2.14, then
    tx ty tz). Deterministic: every entry is round(value * 16384)."""
    a, b, c = (math.radians(t) for t in (ax, ay, az))
    rx = [[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]]
    ry = [[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]]
    rz = [[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]]

    def mul(p, q):
        return [[sum(p[i][k] * q[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    r = mul(rz, mul(ry, rx))
    ent = [int(round(r[i][j] * scale * vertex_model.ONE)) for i in range(3) for j in range(3)]
    return tuple(ent) + (tx, ty, tz)


def float_camera(m, v):
    """The camera-space position of model vertex v under matrix m, in floating point (matrix entries read as m / 16384)."""
    x, y, z = v
    return [(m[3 * i] * x + m[3 * i + 1] * y + m[3 * i + 2] * z) / vertex_model.ONE + m[9 + i] for i in range(3)]


def faces_toward_camera(m):
    """Floating-point sanity check, independent of the screen arithmetic: the faces whose outward normal points at the
    camera (at the origin of camera space).  A face is visible iff  normal . (face centre) < 0  in camera space,
    where the normal is rotated by the matrix too.  Returns the set of face numbers."""
    out = set()
    for f, (n, corners) in enumerate(CUBE_FACES):
        centre = [sum(CUBE_VERTS[i][k] for i in corners) / 4.0 for k in range(3)]
        c = float_camera(m, centre)
        o = float_camera(m[:9] + (0, 0, 0), n)            # the normal: rotation only
        if sum(o[k] * c[k] for k in range(3)) < 0:
            out.add(f)
    return out


def float_screen(m, v, f=vertex_model.DEFAULT_F):
    """Floating-point (sx, sy) of a model vertex, no rounding, or None at or behind z_c <= 0."""
    c = float_camera(m, v)
    if c[2] <= 0:
        return None
    return (160 + f * c[0] / c[2], 120 - f * c[1] / c[2])


def float_front_faces(m, f=vertex_model.DEFAULT_F):
    """Faces whose two triangles are counter-clockwise (area2 > 0) with unrounded screen positions."""
    out = set()
    tris = cube_triangles()
    for face in range(6):
        ok = True
        for i0, i1, i2, fc in tris:
            if fc != face:
                continue
            p = [float_screen(m, CUBE_VERTS[i], f) for i in (i0, i1, i2)]
            if None in p or raster_model.area2(p[0], p[1], p[2]) <= 0:
                ok = False
        if ok:
            out.add(face)
    return out


def expected_counts(m, mode=CULL_BACK, f=vertex_model.DEFAULT_F, near=vertex_model.DEFAULT_NEAR):
    """(culled, rejected, drawn) for the cube under matrix m with the fixed-point pipeline: what GET_MESH_STATS must say."""
    recs = [vertex_model.transform(v, m, f, near) for v in CUBE_VERTS]
    c = [0, 0, 0]
    for i0, i1, i2, _ in cube_triangles():
        v = verdict([recs[i0], recs[i1], recs[i2]], (i0, i1, i2), mode)
        c[{CULLED: 0, REJECTED: 1, DRAW: 2}[v]] += 1
    return tuple(c)


def _self_check():
    tris = cube_triangles()
    assert len(tris) == 12 and all(len(set(t[:3])) == 3 for t in tris)
    # every triangle is wound counter-clockwise from outside (the defining property), for several proper rotations
    import random
    rng = random.Random(6)
    ident = vertex_model.identity()
    # at the identity matrix with the cube straight ahead only the -z face points at the camera
    m0 = rot_matrix(0, 0, 0, 0, 0, 300)
    assert faces_toward_camera(m0) == {4} and float_front_faces(m0) == {4}
    assert expected_counts(m0) == (10, 0, 2)
    # rotation and translation: the fixed-point verdicts agree with the plane test whenever no face is nearly edge-on
    agree = n = 0
    most = 0
    for _ in range(2000):
        m = rot_matrix(rng.uniform(0, 360), rng.uniform(0, 360), rng.uniform(0, 360),
                       rng.randint(-250, 250), rng.randint(-200, 200), rng.randint(200, 600))
        vis = faces_toward_camera(m)
        assert len(vis) <= 3, "a convex box has at most three faces turned towards a camera outside it"
        if all(float_camera(m, v)[2] > 0 for v in CUBE_VERTS):
            assert vis == float_front_faces(m), "the plane test and the unrounded screen test must agree"
        most = max(most, len(vis))
        n += 1
        agree += expected_counts(m) == (12 - 2 * len(vis), 0, 2 * len(vis))
    assert most == 3 and agree >= 0.95 * n, (most, agree, n)
    print("PASS: mesh_model self-check (a cube shows at most 3 faces in 2000 random poses; "
          "pixel-rounded verdicts equal the plane test in %d of %d)" % (agree, n))


if __name__ == "__main__":
    _self_check()
