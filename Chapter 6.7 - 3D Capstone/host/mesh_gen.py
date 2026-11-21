"""mesh_gen.py -- the three models of Chapter 6.7, generated in code (no model files): a cube with 24 split vertices, an
icosphere and a torus. Each function returns a Mesh: vertices (int16 x, y, z), normals (int16 Q2.14), triangles
(i0, i1, i2), every triangle wound counter-clockwise on the monitor when seen from outside (the Chapter 6.6 convention:
(b - a) x (c - a) . n_outward < 0 in model space), so cull mode BACK keeps the front faces.

Copy to host/mesh_gen.py (new in 6.7).  Self-check:  python host/mesh_gen.py

  cube(half=60)                    24 vertices, 12 triangles; four vertices per face, each with the FACE normal (flat shading)
  icosphere(subdiv=1, radius=80)   42 vertices / 80 triangles at subdiv 1, 162 / 320 at subdiv 2; smooth normals (the direction of the vertex)
  torus(nu=12, nv=8, big=75, small=30)   nu * nv vertices, 2 * nu * nv triangles: the "about 200 triangle" model (12 x 8 = 96 vertices, 192 triangles)
"""
import math
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import light_model  # noqa: E402


class Mesh:
    def __init__(self, name, verts, normals, tris):
        self.name, self.verts, self.normals, self.tris = name, verts, normals, tris

    def __repr__(self):
        return "Mesh(%s: %d vertices, %d triangles)" % (self.name, len(self.verts), len(self.tris))


def _wind(verts, normals, tri):
    """Order the three indices so that (b - a) x (c - a) . n < 0, n the sum of the three vertex normals (outward)."""
    a, b, c = [verts[i] for i in tri]
    u = [b[k] - a[k] for k in range(3)]
    v = [c[k] - a[k] for k in range(3)]
    cr = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    n = [sum(normals[i][k] for i in tri) for k in range(3)]
    d = sum(cr[k] * n[k] for k in range(3))
    return tri if d < 0 else (tri[0], tri[2], tri[1])


def cube(half=60):
    """24 vertices, 12 triangles. Split vertices: a corner belongs to three faces, so it appears three times, once per face, with that face's normal."""
    faces = [((-1, 0, 0), (0, 2, 6, 4)), ((1, 0, 0), (1, 3, 7, 5)), ((0, -1, 0), (0, 1, 5, 4)),
             ((0, 1, 0), (2, 3, 7, 6)), ((0, 0, -1), (0, 1, 3, 2)), ((0, 0, 1), (4, 5, 7, 6))]
    corners = [(half * (2 * (i & 1) - 1), half * (2 * ((i >> 1) & 1) - 1), half * (2 * ((i >> 2) & 1) - 1)) for i in range(8)]
    verts, normals, tris = [], [], []
    for n, quad in faces:
        base = len(verts)
        for c in quad:
            verts.append(corners[c])
            normals.append(tuple(light_model.q14(x) for x in n))
        for t in ((0, 1, 2), (0, 2, 3)):
            tris.append(_wind(verts, normals, (base + t[0], base + t[1], base + t[2])))
    return Mesh("cube", verts, normals, tris)


def icosphere(subdiv=1, radius=80):
    """A unit icosahedron subdivided `subdiv` times, every vertex pushed onto the sphere. Smooth normals."""
    t = (1 + math.sqrt(5)) / 2
    pts = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
           (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    pts = [light_model.unit(p) for p in pts]
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
             (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    for _ in range(subdiv):
        cache, out = {}, []

        def mid(a, b):
            key = (min(a, b), max(a, b))
            if key not in cache:
                pts.append(light_model.unit([(pts[a][k] + pts[b][k]) / 2 for k in range(3)]))
                cache[key] = len(pts) - 1
            return cache[key]
        for a, b, c in faces:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            out += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = out
    verts = [tuple(int(round(radius * x)) for x in p) for p in pts]
    normals = [tuple(light_model.q14(x) for x in p) for p in pts]
    return Mesh("icosphere", verts, normals, [_wind(verts, normals, f) for f in faces])


def torus(nu=12, nv=8, big=75, small=30):
    """A torus around the y axis: nu steps around the ring, nv around the tube. Smooth normals (the direction from the tube's centre)."""
    verts, normals = [], []
    for i in range(nu):
        u = 2 * math.pi * i / nu
        for j in range(nv):
            v = 2 * math.pi * j / nv
            n = (math.cos(u) * math.cos(v), math.sin(v), math.sin(u) * math.cos(v))
            verts.append((int(round((big + small * math.cos(v)) * math.cos(u))), int(round(small * math.sin(v))),
                          int(round((big + small * math.cos(v)) * math.sin(u)))))
            normals.append(tuple(light_model.q14(x) for x in n))
    tris = []
    for i in range(nu):
        for j in range(nv):
            a, b = i * nv + j, i * nv + (j + 1) % nv
            c, d = ((i + 1) % nu) * nv + j, ((i + 1) % nu) * nv + (j + 1) % nv
            tris.append(_wind(verts, normals, (a, b, c)))
            tris.append(_wind(verts, normals, (b, d, c)))
    return Mesh("torus", verts, normals, tris)


def _self_check():
    c, s, t = cube(), icosphere(1), torus()
    assert (len(c.verts), len(c.tris)) == (24, 12) and (len(s.verts), len(s.tris)) == (42, 80) and (len(t.verts), len(t.tris)) == (96, 192)
    s2 = icosphere(2)
    assert (len(s2.verts), len(s2.tris)) == (162, 320)
    for m in (c, s, s2, t):
        assert all(len(set(tr)) == 3 and max(tr) < len(m.verts) for tr in m.tris)
        assert all(-32768 <= x <= 32767 for v in m.verts for x in v)
        # every normal has length 1 within Q2.14 rounding
        for n in m.normals:
            assert abs(math.sqrt(sum((x / 16384.0) ** 2 for x in n)) - 1) < 2e-4, n
    # closed meshes: every edge is shared by exactly two triangles with opposite directions (split-vertex cube: by position)
    for m in (s, s2, t):
        edges = {}
        for a, b, c_ in m.tris:
            for e in ((a, b), (b, c_), (c_, a)):
                edges[e] = edges.get(e, 0) + 1
        assert all(n == 1 and (e[1], e[0]) in edges for e, n in edges.items()), m.name
    print("PASS: mesh_gen self-check (cube 24/12, icosphere 42/80 and 162/320, torus 96/192; closed and consistently wound)")


if __name__ == "__main__":
    _self_check()
