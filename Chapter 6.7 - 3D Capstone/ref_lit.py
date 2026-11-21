#!/usr/bin/env python3
"""ref_lit.py -- the capstone's reference renderer, frame story and budget stream (Chapter 6.7).

Usage (from the repo root; commit the files it writes):
    .venv/bin/python sim/py/ref_lit.py build                 writes build/ref_lit_f0.ppm .. f5.ppm (what `make frame-lit` compares against)
    .venv/bin/python sim/py/ref_lit.py --files sim           writes sim/lit_stream.hex, lit_replies.hex, budget_setup.hex,
                                                             budget_frames.hex, budget_expect.hex
    .venv/bin/python sim/py/ref_lit.py --check               the pictures against an independent floating-point renderer (numpy)

The reference renderer IS host/gpu_model.py driven by the same packets a host sends (host/lit_scene.py): the Python
fixed-point renderer for the same model and matrix. The checks add two independent views of it: the faces of the cube that
the pictures show are the ones numpy says point at the camera, and a floating-point renderer written here (float transform,
float lighting, float barycentric interpolation, float depth) agrees with the fixed-point pictures to within a small, stated bound.

tb_frame_lit plays sim/lit_stream.hex (the entries are those of Chapter 6.6's tb_frame_mesh: 00NN byte, A000 open frame,
A001 close frame, 3000 wait for the next frame start, 2YYY wait for line YYY, 4000 arm the dumpers, 1NNN fence, FFFF end). Its six
frames, one per picture:   f0 the cube      f1 the icosphere      f2 f3 f4 the torus in three consecutive poses
                           f5 the torus again, with a new light direction sent in the same frame
Every frame after the setup is the host's whole traffic for it: SET_MATRIX + RUN_LIST (29 bytes), plus SET_LIGHT (11) in f5.
The 30 reply bytes are  GET_INFO (flags 0, capabilities 7FFF)  GET_MESH_STATS (all six frames' counters)  GET_INFO.
"""
import math
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))

import numpy as np  # noqa: E402

import gpu_model as g  # noqa: E402
import light_model as lm  # noqa: E402
import lit_scene as ls  # noqa: E402
import mesh_model as mm  # noqa: E402
import vertex_model as vm  # noqa: E402
from ppm import write_ppm  # noqa: E402

W, H = g.W, g.H
OPEN, CLOSE, FRAME_START, ARM, FENCE, END, LINE = 0xA000, 0xA001, 0x3000, 0x4000, 0x1000, 0xFFFF, 0x2000
LABELS = ["f0", "f1", "f2", "f3", "f4", "f5"]
PATCH_LINE = 20
NEW_LIGHT = (lm.q14(0.55), lm.q14(0.35), lm.q14(-0.45))          # f5: the light moves (|L| about 0.8)
POSE_F0 = ls.rotation(25, 30, 0)
POSE_F1 = ls.rotation(20, 200, 15)
POSES_TORUS = [ls.spin(7), ls.spin(8), ls.spin(9)]
POSE_F5 = ls.spin(10)


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


def frame_story():
    """Returns (stream words, replies, {label: screen index bytes}, model, per-frame byte counts)."""
    scene = ls.build()
    m = g.Gpu()
    items, replies, shots, sizes = [], bytearray(), {}, []
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

    frame(g.clear(0) + g.flip() + g.clear(0))
    start_frame()
    for p in ls.setup_packets(scene):
        frame(p)
    frame(b"")                                                                 # an empty frame
    # the picture of f0, sent before the dumpers are armed
    pk = ls.frame_packets(scene["cube"], POSE_F0)
    frame(pk + g.get_info())
    items.append(FENCE | 9)
    assert m.errors == 0 and len(pk) == ls.FRAME_BYTES
    start_frame()                                                              # k = 0
    items += [LINE | PATCH_LINE, ARM]
    state["armed"] = True
    todo = [(scene["icosphere"], POSE_F1, b"")] + [(scene["torus"], p, b"") for p in POSES_TORUS] \
        + [(scene["torus"], POSE_F5, g.set_light(NEW_LIGHT, ls.AMBIENT))]
    for k, (mdl, pose, extra) in enumerate(todo):
        start_frame()                                                          # k = 1 .. 5: f(k-1) on the screen; draw f(k)
        pk = extra + ls.frame_packets(mdl, pose)
        sizes.append(len(pk))
        if k == len(todo) - 1:                                                 # the last frame also reads the counters back
            frame(pk, PATCH_LINE)
            frame(g.get_mesh_stats() + g.get_info())
            items.append(FENCE | 21)
        else:
            frame(pk, PATCH_LINE)
    start_frame()                                                              # k = 6: f5 on the screen
    assert len(shots) == 6, len(shots)
    want_head = bytes([0x47, 1, 0xFF, 0x7F, 0x40, 0x01, 0xF0, 0x00, 0x00])
    assert bytes(replies[:9]) == want_head, bytes(replies).hex()
    assert bytes(replies[-9:]) == want_head, bytes(replies).hex()
    assert len(replies) == 30
    assert m.mesh_undefined == 0 and m.lit_undefined == 0 and m.errors == 0
    assert sizes == [29, 29, 29, 29, 29 + 11], sizes
    return items, bytes(replies), shots, m, sizes


def faces_in(shot):
    """The cube face numbers whose ramp occurs in a picture (ramp = index >> 5; ramp 0 is the background)."""
    return {int(c) - 1 for c in set(int(x) >> 5 for x in shot) if 1 <= c <= 6}


# ---------------------------------------------------------------- an independent floating-point renderer
def float_render(matrix, mdl, light, ambient, f=ls.FOCAL, near=ls.NEAR):
    """The model drawn in floating point: float camera transform, float projection (no pixel rounding), float lighting (the matrix,
    normals, light read as value / 16384, the normal turned into camera space), back faces culled, float barycentric
    interpolation of brightness and of 1/z-less camera depth, float z-buffer. Returns (covered bool HxW, index uint8 HxW,
    triangle number HxW, -1 where nothing is drawn)."""
    mat = np.array(matrix[:9], dtype=np.float64).reshape(3, 3) / 16384.0
    tr = np.array(matrix[9:], dtype=np.float64)
    verts = np.array(mdl.mesh.verts, dtype=np.float64)
    cam = verts @ mat.T + tr
    sx = 160 + f * cam[:, 0] / cam[:, 2]
    sy = 120 - f * cam[:, 1] / cam[:, 2]
    nrm = np.array(mdl.mesh.normals, dtype=np.float64) / 16384.0
    nc = nrm @ mat.T
    lt = np.array(light, dtype=np.float64) / 16384.0
    b = ambient / 16384.0 + np.maximum(nc @ lt, 0.0)
    zbuf = np.full((H, W), np.inf)
    img = np.zeros((H, W), dtype=np.uint8)
    cov = np.zeros((H, W), dtype=bool)
    tid = np.full((H, W), -1, dtype=np.int32)
    ys, xs = np.mgrid[0:H, 0:W]
    for k, (a, bb, c) in enumerate(mdl.mesh.tris):
        if cam[[a, bb, c], 2].min() <= near:
            continue
        x0, y0, x1, y1, x2, y2 = sx[a], sy[a], sx[bb], sy[bb], sx[c], sy[c]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if area >= 0:                                                          # raster_model.area2 = -area: positive area2 (front) is area < 0
            continue
        xlo, xhi = int(max(0, math.floor(min(x0, x1, x2)))), int(min(W - 1, math.ceil(max(x0, x1, x2))))
        ylo, yhi = int(max(0, math.floor(min(y0, y1, y2)))), int(min(H - 1, math.ceil(max(y0, y1, y2))))
        if xlo > xhi or ylo > yhi:
            continue
        px, py = xs[ylo:yhi + 1, xlo:xhi + 1], ys[ylo:yhi + 1, xlo:xhi + 1]
        w0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) / area
        w1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) / area
        w2 = 1.0 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        z = w0 * cam[a, 2] + w1 * cam[bb, 2] + w2 * cam[c, 2]
        bv = w0 * b[a] + w1 * b[bb] + w2 * b[c]
        shade = np.clip(np.floor(bv * 31 + 0.5), 0, 31).astype(np.uint8)
        sub = zbuf[ylo:yhi + 1, xlo:xhi + 1]
        win = inside & (z < sub)
        sub[win] = z[win]
        img[ylo:yhi + 1, xlo:xhi + 1][win] = ((mdl.ramps[k] & 7) << 5) | shade[win]
        cov[ylo:yhi + 1, xlo:xhi + 1] |= win
        tid[ylo:yhi + 1, xlo:xhi + 1][win] = k
    return cov, img, tid


def dil(a):
    """a grown by one pixel in all eight directions"""
    d = a.copy()
    d[1:] |= a[:-1]; d[:-1] |= a[1:]; d[:, 1:] |= a[:, :-1]; d[:, :-1] |= a[:, 1:]
    d[1:, 1:] |= a[:-1, :-1]; d[:-1, :-1] |= a[1:, 1:]; d[1:, :-1] |= a[:-1, 1:]; d[:-1, 1:] |= a[1:, :-1]
    return d


def tri_edges(tid):
    """The pixels of the float picture next to a triangle edge or a silhouette: a neighbour belongs to another triangle (or to none)."""
    e = np.zeros(tid.shape, dtype=bool)
    e[1:] |= tid[1:] != tid[:-1]; e[:-1] |= tid[:-1] != tid[1:]; e[:, 1:] |= tid[:, 1:] != tid[:, :-1]; e[:, :-1] |= tid[:, :-1] != tid[:, 1:]
    return e


def compare_with_float(shot, cov_f, img_f, tid_f):
    """Statistics of a fixed-point picture (index bytes) against the float one: (covered pixels, pixels covered in only one picture,
    of those the ones more than one pixel from the other's edge, covered in both and ramp differs, covered in both and shade differs by > 1,
    largest shade difference, the pixels with a ramp or shade difference that are more than one pixel from every triangle edge)."""
    img = np.frombuffer(shot, dtype=np.uint8).reshape(H, W)
    cov = img != 0
    only = cov ^ cov_f
    # within one pixel of the other picture's coverage boundary
    far = only & ~(dil(cov_f & ~only) & dil(cov & ~only))
    both = cov & cov_f
    ramp_bad = both & ((img >> 5) != (img_f >> 5))
    dsh = np.abs((img & 31).astype(int) - (img_f & 31).astype(int))
    dsh[~both] = 0
    interior = ~dil(tri_edges(tid_f))
    return (int(cov.sum()), int(only.sum()), int(far.sum()), int(ramp_bad.sum()), int((dsh > 1).sum()), int(dsh.max()),
            int(((ramp_bad | (dsh > 1)) & interior).sum()))


def run_checks(verbose=True):
    items, replies, shots, m, sizes = frame_story()
    # the cube's picture shows exactly the faces that point at the camera (numpy / plane test of Chapter 6.6)
    towards = mm.faces_toward_camera(POSE_F0)
    assert faces_in(shots["f0"]) == towards and len(towards) <= 3, (faces_in(shots["f0"]), towards)
    scene = ls.build()
    rows = []
    poses = {"f0": ("cube", POSE_F0, ls.LIGHT_DIR), "f1": ("icosphere", POSE_F1, ls.LIGHT_DIR), "f2": ("torus", POSES_TORUS[0], ls.LIGHT_DIR),
             "f3": ("torus", POSES_TORUS[1], ls.LIGHT_DIR), "f4": ("torus", POSES_TORUS[2], ls.LIGHT_DIR), "f5": ("torus", POSE_F5, NEW_LIGHT)}
    for label, (name, pose, light) in poses.items():
        cov_f, img_f, tid_f = float_render(pose, scene[name], light, ls.AMBIENT)
        st = compare_with_float(shots[label], cov_f, img_f, tid_f)
        rows.append((label, name) + st)
        covered, only, far, ramp_bad, shade_bad, maxd, inner = st
        assert inner == 0, (label, "pixels inside a triangle whose ramp or shade differs by more than 1", inner)
        assert covered > 3000, (label, covered)
        assert far == 0, (label, "pixels covered in only one picture and not at an edge", far)
        assert only <= 0.03 * covered, (label, only, covered)
        assert ramp_bad <= 0.005 * covered, (label, ramp_bad)           # the pixels that differ are on silhouettes and where one surface passes over another
        assert shade_bad <= 0.005 * covered, (label, shade_bad, covered)
    # the pictures differ from one another and the torus frames show a moving light (not the same picture)
    assert len({bytes(shots[x]) for x in LABELS}) == 6
    if verbose:
        print("float renderer against the fixed-point frames (pixels):")
        print("  frame model       covered  only-in-one  (not at an edge)  ramp differs  shade differs by >1  largest shade difference  (inside a triangle)")
        for r in rows:
            print("  %-5s %-10s %7d %12d %17d %13d %20d %26d %20d" % r)
    return rows


def write_hex(path, words, width):
    with open(path, "w") as f:
        f.write("".join(("%0*x\n" % (width, w)) for w in words))


# ---------------------------------------------------------------- the budget test (tb_light_budget)
NFR = 3                                  # frames the budget testbench runs after its measurements


def budget_streams():
    """budget_setup (bytes with 00NN entries, FFFF), budget_frames (the light, then NFR matrices), budget_expect (rows MMBB of the
    final GET_MESH_STATS and GET_STATS replies). The testbench: sets up, measures TRANSFORM (lighting off and on), CLEAR_ALL and
    DRAW_MESH of the torus with GET_MESH_STATS as the marker, clears the counters, then runs NFR frames of SET_MATRIX + RUN_LIST 2."""
    scene = ls.build()
    m = g.Gpu(caps=g.CAPS_UART)
    t = scene["torus"]
    # the budget test needs only the torus (no palette: there is no framebuffer): the same slots as in the full scene
    pk = [g.set_projection(ls.FOCAL, ls.NEAR), g.set_light(ls.LIGHT_DIR, ls.AMBIENT), g.set_depth(True, True), g.set_mesh_mode(mm.CULL_BACK, True)]
    pk += g.vertex_packets(t.v0, t.mesh.verts) + g.normal_packets(t.v0, t.mesh.normals) + g.index_packets(t.t0, t.entries())
    pk += list(g.list_packets(t.list_id, t.list_body()))
    setup = b"".join(pk)
    m.feed(setup)
    mats = [ls.spin(k) for k in range(NFR)]
    # the measurement phase (the model only needs the state it leaves behind: depth and colour are cleared by every frame)
    m.feed(g.set_matrix(mats[0]) + g.transform(t.v0, t.nv))
    m.feed(g.clear_mesh_stats() + g.clear_stats())
    for k in range(NFR):
        m.feed(ls.frame_packets(t, mats[k]))
    rep = m.feed(g.get_mesh_stats()) + m.feed(g.get_stats())
    assert len(rep) == 24 and m.errors == 0 and m.mesh_undefined == 0
    rows = [0xFF00 | b for b in rep]
    for k in range(1, 5):
        rows[-k] = 0x0000                                                      # the clock counter is the engine's own business
    light_words = [ls.LIGHT_DIR[0] & 0xFFFF, ls.LIGHT_DIR[1] & 0xFFFF, ls.LIGHT_DIR[2] & 0xFFFF, ls.AMBIENT & 0xFFFF]
    frames = light_words + [e & 0xFFFF for mat in mats for e in mat]
    return list(setup), frames, rows, m


def main():
    a = sys.argv[1:]
    if a == ["--check"]:
        run_checks()
        print("PASS: ref_lit checks")
        return 0
    if len(a) == 2 and a[0] == "--files":
        d = a[1]
        run_checks(verbose=False)
        items, replies, shots, m, sizes = frame_story()
        write_hex(os.path.join(d, "lit_stream.hex"), items + [END], 4)
        write_hex(os.path.join(d, "lit_replies.hex"), replies, 2)
        print("wrote %s/lit_stream.hex (%d entries) and lit_replies.hex (%d bytes)" % (d, len(items) + 1, len(replies)))
        setup, frames, rows, bm = budget_streams()
        write_hex(os.path.join(d, "budget_setup.hex"), setup + [0xFFFF], 4)
        write_hex(os.path.join(d, "budget_frames.hex"), frames, 4)
        write_hex(os.path.join(d, "budget_expect.hex"), rows, 4)
        print("wrote %s/budget_setup.hex (%d bytes), budget_frames.hex (%d words) and budget_expect.hex (%d rows)" % (d, len(setup), len(frames), len(rows)))
        print("PASS: ref_lit checks")
        return 0
    if len(a) == 1 and not a[0].startswith("-"):
        items, replies, shots, m, sizes = frame_story()
        pal = np.array([[(c >> 16) & 255, (c >> 8) & 255, c & 255] for c in m.pal], dtype=np.uint16)
        for label, idx in shots.items():
            small = pal[np.frombuffer(idx, dtype=np.uint8).reshape(H, W)]
            write_ppm(os.path.join(a[0], "ref_lit_%s.ppm" % label), small.repeat(2, axis=0).repeat(2, axis=1), 255)
        print("wrote %d reference frames to %s" % (len(shots), a[0]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
