"""lit_scene.py -- the capstone scene of Chapter 6.7: three models in the GPU's buffers at once, one display list per model, and
the two packets that make a frame.

Copy to host/lit_scene.py (new in 6.7; sim/py/ref_lit.py, sim/py/ref_light.py and host/lit_demo.py use it).

Buffer layout (all slots are in the GPU's one vertex buffer, one normal buffer and one index buffer):

    model        vertices          normals (same slots)   index entries      list
    cube         0 .. 23           0 .. 23                0 .. 11            0
    icosphere    24 .. 65          24 .. 65               12 .. 91           1
    torus        66 .. 161         66 .. 161              92 .. 283          2

Each list is  TRANSFORM(its vertices), CLEAR_ALL, DRAW_MESH(its entries), FLIP  (19 bytes). TRANSFORM lights its vertices too:
the light is set once (SET_LIGHT, in camera space), the mesh mode is "cull back, intensity from the vertices" once, and from
then on a frame is SET_MATRIX (26 bytes) + RUN_LIST (3 bytes) = 29 bytes.
"""
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gpu_model as g  # noqa: E402
import light_model  # noqa: E402
import mesh_gen  # noqa: E402
import mesh_model  # noqa: E402
import vertex_model  # noqa: E402

FOCAL, NEAR = 277, 1
LIGHT_DIR = tuple(light_model.q14(0.8 * c) for c in light_model.unit((-0.45, 0.55, -0.70)))   # |L| = 0.8: the diffuse gain
AMBIENT = light_model.q14(0.2)                                                                  # 0.2 + 0.8 = 1.0 = full
BACKGROUND = 0                                  # the clear colour: ramp 0, shade 0
DEPTH_FAR = 65535
CUBE_RAMPS = [1, 2, 3, 4, 5, 6]                 # one palette ramp per cube face (so the faces are recognisable)
SPHERE_RAMP, TORUS_RAMP = 3, 4
MODE = mesh_model.CULL_BACK | mesh_model.MODE_VERTEX_I     # SET_MESH_MODE value 5

RAMPS = {0: ((24, 24, 48), (255, 255, 255)), 1: ((40, 0, 0), (255, 90, 90)), 2: ((0, 40, 0), (120, 255, 120)),
         3: ((0, 0, 48), (110, 140, 255)), 4: ((48, 40, 0), (255, 240, 60)), 5: ((0, 40, 40), (120, 255, 255)),
         6: ((40, 0, 40), (255, 110, 255)), 7: ((50, 20, 0), (255, 160, 40))}


class Model:
    def __init__(self, name, mesh, v0, t0, list_id, ramps):
        self.name, self.mesh, self.v0, self.t0, self.list_id = name, mesh, v0, t0, list_id
        self.nv, self.nt = len(mesh.verts), len(mesh.tris)
        self.ramps = ramps                      # ramp of each triangle

    def entries(self):
        return [mesh_model.pack_entry(self.v0 + a, self.v0 + b, self.v0 + c, self.ramps[k], 0) for k, (a, b, c) in enumerate(self.mesh.tris)]

    def list_body(self, clear=BACKGROUND):
        return g.transform(self.v0, self.nv) + g.clear_all(clear, DEPTH_FAR) + g.draw_mesh(self.t0, self.nt) + g.flip()


def build(subdiv=1):
    cube, sphere, torus = mesh_gen.cube(), mesh_gen.icosphere(subdiv), mesh_gen.torus()
    models, v0, t0 = [], 0, 0
    for k, (name, mesh, ramps) in enumerate((("cube", cube, [CUBE_RAMPS[i // 2] for i in range(12)]),
                                             ("icosphere", sphere, [SPHERE_RAMP] * len(sphere.tris)),
                                             ("torus", torus, [TORUS_RAMP] * len(torus.tris)))):
        models.append(Model(name, mesh, v0, t0, k, ramps))
        v0 += len(mesh.verts)
        t0 += len(mesh.tris)
    assert v0 <= vertex_model.N_VERTICES and t0 <= mesh_model.N_TRIANGLES
    return {m.name: m for m in models}


def palette_packets():
    out = []
    for r, (dark, light) in RAMPS.items():
        out += g.ramp_palette(r, dark, light)
    return out


def setup_packets(scene, light=True):
    """Everything the host sends once: palette, projection, light, depth test, mesh mode, the three models, the three lists."""
    pk = list(palette_packets())
    pk += [g.set_projection(FOCAL, NEAR), g.set_light(LIGHT_DIR, AMBIENT, enable=light), g.set_depth(True, True), g.set_mesh_mode(mesh_model.CULL_BACK, True)]
    for m in scene.values():
        pk += g.vertex_packets(m.v0, m.mesh.verts)
        pk += g.normal_packets(m.v0, m.mesh.normals)
        pk += g.index_packets(m.t0, m.entries())
    for m in scene.values():
        pk += list(g.list_packets(m.list_id, m.list_body()))
    return pk


def frame_packets(model, matrix):
    """One frame: SET_MATRIX + RUN_LIST. 26 + 3 = 29 bytes."""
    return g.set_matrix(matrix) + g.run_list(model.list_id)


FRAME_BYTES = 29


def rotation(ax, ay, az, tz=320, tx=0, ty=0):
    return mesh_model.rot_matrix(ax, ay, az, tx, ty, tz)


def spin(k, n=60):
    """The k-th matrix of a tumble of n frames: one turn about x, two about y and half a turn about z, 320 units from the camera.
    The torus is symmetric about its own axis, so a turn about y alone would show nothing; the tumble shows the light moving over it."""
    return rotation(360.0 * k / n, 720.0 * k / n + 20.0, 180.0 * k / n)
