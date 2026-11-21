# Chapter 6.6 - Primitive Assembly and Culling

Chapter 6.5 ended with a buffer of transformed vertices: for each model vertex a screen position, a depth and two flags. A triangle is not a vertex, though. This chapter builds the stage that **assembles triangles from vertices** and decides which of them are worth drawing. It has two parts. An **index buffer** holds a list of triangles, each written as three vertex numbers. A command, `DRAW_MESH`, walks the list and fetches the three transformed vertices of each triangle. It **rejects** the triangles that cannot be drawn (a vertex behind the near plane), **culls** the ones that cannot be seen (a face turned away from the camera), and sends the rest to the shaded-triangle path of Chapters 6.3 and 6.4 with its depth test. The test for "turned away" is the edge function of Chapter 4.6 once more: the sign of a triangle's area.

By the end of this chapter you will have:

- a `mesh_cull` unit that makes the reject / cull / draw decision for one triangle, and a `DRAW_MESH` loop in the command engine around it,
- an index buffer of 512 triangles and five commands, `UPLOAD_INDICES`, `SET_MESH_MODE`, `DRAW_MESH`, `GET_MESH_STATS` and `CLEAR_MESH_STATS` (`0x66`-`0x6A`), and capability bit 13 (`GET_INFO` now says `FF 3F`),
- `host/mesh_model.py`, which holds the decision rules, a cube with consistent winding, and a floating-point check that a cube never shows more than three faces,
- `tb_mesh_cull` (23,000 triangles through the unit), `tb_mesh_sys` (a cube in 21 poses, 41 draws in all, through the serial line, with the counters read back) and `make frame-mesh` (six frames of the cube, compared pixel for pixel with the Python model) passing, and `tb_gpu_fuzz` passing with the new commands,
- `host/gpu_model.py`, `docs/protocol.md` (a new section 18) and `host/mesh_demo.py`, and the older tests still passing with the edits of Section 9,
- a registered bounding box in `tri_raster`, which gives `clk_sys` back its timing margin (Section 9).

## 1. From vertices to triangles: the index buffer

A cube has 8 corners and 12 triangles. Sent as independent triangles, as `DRAW_TRI_SHADED` takes them, that is 36 corners and 36 times the transform work and the bytes; with each vertex stored once and each triangle written as three **indices** into the vertex list, it is 8 vertices and 12 triples. Real models share even more: in a closed mesh every vertex belongs to about six triangles. Chapter 6.5 already stores each vertex once and transforms it once per frame, which is why a model of about 200 triangles costs only about 100 vertex transforms. All that is missing is the list of triangles.

The **index buffer** holds up to **512 entries** of 8 bytes, which is 4 KB, one block RAM 64 bits wide:

| Bytes | Field | Meaning |
| --- | --- | --- |
| 0-1, 2-3, 4-5 | `i0`, `i1`, `i2` | vertex slots of the three corners, unsigned 16-bit; only 0 to 1,023 name a vertex |
| 6 | `ramp` | bits 2:0: the palette ramp of Chapter 6.3 for this triangle |
| 7 | `intensity` | Q5.3 as in Chapter 6.3 (248 is full brightness): the brightness of all three corners, unless the mode says otherwise (Section 5) |

Why 16 bits for a number that never exceeds 1,023: it keeps the entry 8 bytes, one RAM word and one packet field, and it lets the hardware notice a **bad index** (1,024 or more) instead of silently using the low ten bits. A mesh with a typo in it is then rejected, counted and visible in `GET_MESH_STATS`, not drawn wrongly. Why 512 triangles: the plan's largest model is about 200 triangles, 512 fits one block RAM, and the bound is the same kind of bound as the 1,024 vertex slots. The ramp and intensity live in the entry because they belong to the triangle, not to the vertex: a cube's faces are different colours but share their corners.

The buffer is written by `UPLOAD_INDICES` and by nothing else, is read by `DRAW_MESH`, and has no `initial` loop: its power-up contents are undefined and a host must not draw an entry it has not written.

## 2. Which side is the front

A triangle seen from outside a closed solid and the same triangle seen from inside run in opposite directions: if its corners go counter-clockwise on the monitor from the outside, they go clockwise from the inside. The **sign of the triangle's area on the screen** therefore says which side the camera sees, and that is already computed: `area2 = edge(v0, v1, v2)` of Chapter 4.6, `raster_model.area2`, is **positive for a counter-clockwise triangle on the monitor** (the edge function's sign and the downward screen y cancel; Chapter 6.5 recorded this). The convention of Phase 6:

> **A triangle is front-facing if `area2 > 0`, counter-clockwise on the monitor. Everything else, a triangle of zero area included, is back-facing.**
> Meshes are wound **counter-clockwise as seen from outside**.

For a mesh with that winding, **cull mode** 1 (back faces) is the usual setting: a face that points away from the camera is always hidden by the front faces of the same closed solid, so it need not be drawn, and about half of the triangles of a typical model are skipped. Cull mode 2 (front faces) shows the inside of the solid, which is how you look inside a room; mode 3 culls everything (useful only for testing); mode 0 culls nothing, for models with single-sided sheets and for checking the picture. `SET_MESH_MODE` holds the mode, which persists, and is 0 at power-up.

Three things are worth being clear about.

**Culling is an optimisation, not a visibility algorithm.** With the depth buffer of Chapter 6.4 on, drawing everything gives the right picture, because the hidden faces lose the depth test. Culling saves the work of the hidden half: the setup time of its triangles and all their pixels. The test frames show it: `f1` draws the cube with cull mode 0 and the depth test, and its picture equals the culled `f0` except for 78 pixels along the silhouette, where a hidden face and a visible face meet at nearly the same depth. (Culling is not harmless for a model that is not a closed solid: remove the back of a mask and cull mode 1 shows the inside as a hole.)

**Handedness.** "Counter-clockwise as seen from outside" describes what you see, so it needs no coordinate system. Turning it into a formula does: Chapter 6.5's camera space (x right, y up, z forward) is **left-handed**, and in it a triangle `a, b, c` whose face has outward normal `n` is counter-clockwise from outside exactly when `((b - a) x (c - a)) . n < 0`, the opposite sign of the right-hand rule. `mesh_model.py` winds the cube with that test. A model from a right-handed tool (z towards the viewer, counter-clockwise front faces, as in OpenGL) keeps both its shape and its windings if you negate its z coordinates when you upload it; uploaded unchanged it comes out mirrored and inside out. A model drawn inside out (far faces shown, near ones missing) is wound clockwise: swap `i1` and `i2` of every entry, or use cull mode 2.

**Zero area.** A triangle that is seen exactly edge-on, or has two equal corners, has `area2 = 0`. The rasterizer draws nothing for it, so it does not matter for the picture. It does matter for the counters, so the rule is stated: it is back-facing, **culled in modes 1 and 3, drawn (that is, sent to the rasterizer, which draws nothing) in modes 0 and 2**. `tb_mesh_sys` draws the cube with the camera in the plane of one face, which makes that face exactly edge-on in all four cull modes, and `tb_mesh_cull` has thousands of collinear triangles: together they pin the rule.

## 3. Rejection: the triangles that cannot be drawn

Before the sign of the area means anything the three corners must be real, so `DRAW_MESH` first **rejects** a triangle if any corner:

- has an index of 1,024 or more (no such vertex),
- has the **NEAR** flag: its position fields are undefined (Chapter 6.5), so `area2` would be computed from junk,
- or has the **CLAMP** flag: its position was clamped to the 16-bit range, so it is not where the geometry puts it, and a triangle that uses it is distorted (typically a thin spike across the whole screen).

A rejected triangle is counted and not drawn. The choice to reject, not clip, is the plan's: proper clipping against the near plane would cut the triangle and create new vertices, a stage of its own (it is on the plan's list of stretch goals). The price is that a triangle that touches the near plane disappears whole, so a scene is built to keep the camera outside the objects and `near` small relative to the closest vertex. `f3` shows the effect: the cube pokes through the near plane, the triangles with a vertex in front of the camera vanish, and the hole shows the inside of the cube.

**CLAMP triangles** are always rejected, with no mode to draw them. A mode would be one more thing to test for a case that is wrong anyway: the vertex is more than 32,000 pixels off the screen centre and the triangle's slope across the screen is not the geometry's.

**The screen edges need nothing.** A triangle that is partly or wholly off the screen, with corners anywhere in the signed 16-bit range, is a legal input of `DRAW_TRI_SHADED`: its bounding box is clamped to the screen (Chapter 4.6) and a triangle wholly outside has an empty box. Such a triangle is **drawn** as far as the counters go (it was passed to the rasterizer), and costs only its setup.

The order matters: rejection first, then culling, because the culling test uses positions that only a rejected triangle lacks.

## 4. The face test in hardware

For corners `p0, p1, p2` (signed 16-bit each), `area2 = (x2 - x0)(y1 - y0) - (y2 - y0)(x1 - x0)`. Only the sign matters, and the widths are the whole difficulty:

| Quantity | Width | Why |
| --- | --- | --- |
| coordinate | 16 bits signed | the record |
| difference `x1 - x0` | 17 bits signed | two 16-bit numbers differ by up to 65,535 |
| product | 34 bits signed | 17 x 17 bits: up to 4.3 billion |
| `area2` | 35 bits signed | the difference of two products |

A design that computes the differences in 16 bits wraps for corners more than 32,767 apart, which off-screen vertices easily are; the unit test has thousands of such triangles. The sign is the comparison `x2' * y1' > x1' * y2'` of the two 34-bit products, so no 35-bit subtraction is needed, and each product is one DSP48E1 multiplier (17 x 17). One verdict takes three clocks in the reference: register the differences, multiply, compare.

The interface of the unit, which the engine and `tb_mesh_cull` share:

```
module mesh_cull (
    input  wire        i_clk,
    input  wire        i_valid,                 // one clock: the inputs below are valid only now
    input  wire        i_bad,                   // an index named no vertex
    input  wire [63:0] i_rec0, i_rec1, i_rec2,  // the three transformed-vertex records
    input  wire [1:0]  i_mode,                  // cull mode: 0 none, 1 back, 2 front, 3 both
    output reg         o_valid,                 // one clock, per i_valid
    output reg  [1:0]  o_verdict                // 0 DRAW, 1 CULLED, 2 REJECTED
);
```

A record is `{intensity[7:0], flags[7:0], depth[15:0], sy[15:0], sx[15:0]}`; the unit looks only at `sx`, `sy` and the two flag bits. The handshake is the simplest one that works: `i_valid` is a one-clock pulse, the engine holds the inputs steady from that clock until the answer (it keeps the three records in registers anyway, Section 5), the answer comes 1 to 16 clocks later, and the next `i_valid` only after it. So the unit may be a three-stage pipeline or a sequential design that uses one multiplier twice. Making it a small module with its own testbench is the point: the face test is the part of this chapter with arithmetic in it, and a failure in the full engine, with a rasterizer and a depth buffer in the loop, is much harder to read than a failure on one triangle.

## 5. `DRAW_MESH`: the loop in the engine

For each index entry from `first` to `first + count - 1` (clipped at entry 511) the engine does, one triangle at a time:

    read the entry -> read the three transformed records -> mesh_cull
        REJECTED -> count it         CULLED -> count it
        DRAW     -> count it; start the shaded rasterizer; wait for the last pixel and the depth unit to drain

The pieces and the choices around them:

- **The read port.** The transformed-vertex buffer (Chapter 6.5) has one read port, shared with `READ_VERTEX`: the loop sets the address, waits the one clock the block RAM takes, and captures the record, three times. The index buffer is a second simple RAM with its own port. Nothing here is fast (a rejected or culled triangle takes 17 clocks in the reference), and nothing needs to be.
- **Feeding the rasterizer.** `DRAW_TRI_SHADED` takes its 22 bytes from the engine's argument buffer. The mesh loop does not copy the triangle there byte by byte (that would be 22 more clocks per triangle for nothing). It holds the three records and the entry in registers and **selects** them in front of the rasterizer's inputs while a mesh runs: positions from `sx`, `sy`, depths from `depth`, the ramp and the intensities as below. The rasterizer, the depth unit and the pixel counters are the ones of Chapters 6.3 and 6.4, unchanged: a mesh triangle is *exactly* a `DRAW_TRI_SHADED`, which is what the Python model relies on. (The multiplexer sits in front of the rasterizer's vertex registers, off the critical path, which is behind them; see Section 9.)
- **Blocking.** Like a triangle, a `TRANSFORM` or a `FLIP`, `DRAW_MESH` keeps the engine busy: commands behind it wait in the FIFO, and a reset does not abandon it. If your engine's reset branch skips the state machine, catch the one-clock answers (`o_valid` of `mesh_cull`, `o_done` of the rasterizer) in sticky flags outside it, or a reset that lands on one leaves the loop waiting forever. The reference starts a triangle only after the previous one has drained out of the depth unit, so the forwarding of Chapter 6.4's `depth_unit` is never needed; starting it as soon as the rasterizer is done is also correct, because of that forwarding.
- **The counters.** Three 32-bit counters, saturating, count each triangle exactly once: culled, rejected, drawn. *Drawn* means passed to the rasterizer, so a front-facing triangle that covers no pixel (a sliver, or wholly off screen) is drawn. `GET_MESH_STATS` returns them in that order, 12 bytes, low byte first, and `CLEAR_MESH_STATS` clears them. `GET_STATS` and `CLEAR_STATS` (Chapter 6.4) are a separate set: they keep counting the *pixels* of the triangles a mesh draws, and whether their clock counter includes the fetching and testing is up to you (the tests do not compare that field for meshes). The counters are how the tests see the culling without a framebuffer.

**Where the colour comes from.** The triangle's ramp is the entry's `ramp`. Its brightness has two sources, picked by bit 2 of `SET_MESH_MODE`:

| Mode bit 2 | Intensity of corner k | Use |
| --- | --- | --- |
| 0 (power-up) | the entry's `intensity` byte, the same for all three corners: **flat shading** | this chapter's cube; a model whose lighting the host computes |
| 1 | byte 7 of corner k's transformed record: **per-vertex**, interpolated across the triangle by Chapter 6.3's rasterizer | Chapter 6.7's lit models |

Byte 7 of a record is the reserved byte Chapter 6.5 left for this. Today the vertex unit writes 0 there, so mode 1 draws every face at its darkest shade (`f4` shows exactly that). **How Chapter 6.7 plugs in:** it adds a lighting pass to `TRANSFORM` that computes a brightness per vertex, converts it to Q5.3 as Chapter 6.3 prescribes and writes it to byte 7 of the record, and the host sets mode bit 2. `DRAW_MESH` itself does not change, and the Python model fills the same byte where it now leaves 0.

## 6. The commands

| Opcode | Command | Length | Payload |
| --- | --- | --- | --- |
| `0x66` | `UPLOAD_INDICES` | 2 + 8k | first entry (16-bit), then k entries of 8 bytes, k = 1 to 31 |
| `0x67` | `SET_MESH_MODE` | 1 | bits 1:0 cull mode, bit 2 vertex intensity; bits 7:3 ignored |
| `0x68` | `DRAW_MESH` | 4 | first entry (16-bit), count (16-bit) |
| `0x69` | `GET_MESH_STATS` | 0 | reply: culled, rejected, drawn, 32-bit each |
| `0x6A` | `CLEAR_MESH_STATS` | 0 | |

All 16-bit fields are little endian. `0x6B`-`0x6F` and `0x75`-`0x7F` stay unknown. A wrong length is skipped whole and sets `BAD_LEN`.

**`UPLOAD_INDICES`** is streamed exactly like `UPLOAD_VERTICES`: an entry is written when its eighth byte arrives, a packet cut by a frame start keeps its whole entries and sets `TRUNC`, the length is checked before any byte is used (a 9 or an 11 writes nothing), and entries from 512 up are dropped without a flag and do not wrap. A 512-entry buffer is 17 packets of 31.

**`DRAW_MESH`**: a `count` of 0, a `first` of 512 or more, and a range that runs past entry 511 (which is clipped) are not errors. The records are read when the command runs, so the order of a frame is `SET_MATRIX`, `TRANSFORM`, `DRAW_MESH`.

**Display lists** (Chapter 5.7). `SET_MESH_MODE`, `DRAW_MESH` and `CLEAR_MESH_STATS` have no reply and write no big memory, so they are **allowed**; `UPLOAD_INDICES` (a long upload) and `GET_MESH_STATS` (a reply) are **forbidden** and skipped with `BAD_OP` when the list runs, the same rule that keeps `UPLOAD_VERTICES` and `GET_STATS` out. This completes the set Chapter 6.7 needs. With the frame list

    TRANSFORM, CLEAR_ALL, DRAW_MESH, FLIP          (6 + 5 + 6 + 2 = 19 bytes)

the host's whole per-frame job is `SET_MATRIX` then `RUN_LIST`: 26 + 3 = 29 bytes. (A list that runs by itself, `SET_AUTO_LIST`, must hold its own `SET_MATRIX`, 45 bytes in all, and the host patches the 24 matrix bytes with twelve `WRITE_LIST_WORD`s, 84 bytes; `f5` and `tb_mesh_sys` test that harder form.) For a 200-triangle model with about 100 vertices the GPU's frame is about 0.35 ms of `TRANSFORM`, 1.5 ms of `CLEAR_ALL` and about 1.1 ms of `DRAW_MESH` (protocol section 18): some 3 ms of a 33 ms frame at 30 fps.

**Capability bit 13** (`MESH`): `GET_INFO` from `gpu_link` reports `47 01 FF 3F ...`; `uart_gpu` still reports `0F 00`.

**Opcode `0x66` was in use.** Chapter 6.5 listed `0x66` as "free", and two older generators (`ref_shaded.py`, `ref_depth.py`) used it as their *unknown opcode*. The edits of Section 9 move them to `0x7E`, which is still unknown.

**Engine changes**, all inside `cmd_engine` (plus the new module and `gpu_link`'s capability constant): the index RAM and its upload branch in the byte parser (a 7-byte buffer, a byte position and a slot counter, as for vertices); the mode register; the nine states of the mesh loop and the three counters; the multiplexers in front of the rasterizer; the 12-byte reply; the two opcodes added to the list check; and the five length rules.

## 7. The Python model, the cube, and "at most three faces"

`host/mesh_model.py` holds the decision (`verdict(records, indices, mode)`), the cube, and the checks that do not need hardware; `host/gpu_model.py` imports it and adds the five commands (builders `upload_indices()`, `index_packets()`, `set_mesh_mode()`, `draw_mesh()`, `get_mesh_stats()`, `clear_mesh_stats()`, `parse_mesh_stats()`; attributes `idx`, `mesh_mode`, `mesh_culled`, `mesh_rejected`, `mesh_drawn`, and `mesh_undefined`, which counts triangles that used an entry or vertex nothing defined and which the generators keep at 0). A drawn triangle is executed *as a `DRAW_TRI_SHADED` packet*, so the pixel counters, the depth test and the rasterizer model come for free.

**The cube.** `CUBE_VERTS` are the eight corners (+-60, +-60, +-60); `CUBE_FACES` lists each face's outward normal and four corners in cyclic order; each face becomes two triangles, wound by the handedness test of Section 2. Six palette ramps, one per face, and distinct intensities make every face recognisable in the frames.

**The orientations.** `ref_mesh.py` names thirteen poses (a rotation about x, y and z in degrees, then a translation; the camera sits at the origin, f = 277, near = 1). For each it prints the faces that point at the camera (floating point, from the normals), the counts the fixed-point pipeline gives with cull mode BACK, and the float expectation:

| Pose | Faces towards the camera | Culled | Rejected | Drawn |
| --- | --- | --- | --- | --- |
| straight on | -z | 10 | 0 | 2 |
| yaw 30 | +x -z | 8 | 0 | 4 |
| yaw 30, pitch 25 | +x -y -z | 6 | 0 | 6 |
| corner view (45 / 35.26) | +x -y -z | 6 | 0 | 6 |
| upside down (roll 180) | -z | 10 | 0 | 2 |
| left of the axis | +x -z | 8 | 0 | 4 |
| right and below | -x +y -z | 6 | 0 | 6 |
| far away (z = 2,000) | +x -y -z | 6 | 0 | 6 |
| rolled | +x -z | 8 | 0 | 4 |
| camera in the plane of the +y face | -z | 10 | 0 | 2 |
| camera in the planes of the +y and +x faces | -z | 10 | 0 | 2 |
| poking through the near plane (z = 40) | none | 2 | 10 | 0 |
| behind the camera (z = -300) | none | 0 | 12 | 0 |

Read the table as the chapter's argument. Every face that points at the camera contributes two drawn triangles and every other face two culled ones: 12 - 2f culled for f visible faces, and **f is never more than 3**. The two edge-on rows are the zero-area rule: the +y face (and in the second row the +x face too) is exactly edge-on, its triangles have `area2 = 0` and are culled as back-facing, so those poses show 1 face. The last two rows are rejection, which comes first: the 10 triangles with a vertex in front of the near plane are rejected, and only the far face's two triangles reach the face test.

**"A cube shows at most three faces" is a geometric fact.** A face is visible when the camera is on the outer side of its plane, and the camera cannot be on the outer side of both faces of an opposite pair (beyond x = +60 and x = -60 at once), so at most one face per axis is visible. The script checks this with numpy on 100,000 random poses, using the plane test `normal . (face centre) < 0` in camera space, and checks that the *unrounded* screen test (`area2 > 0` from floating-point positions) agrees with it on every triangle. The hardware rounds the corners to whole pixels first, so a face within about half a pixel of edge-on can come out either way: of 3,000 poses run through the fixed-point model, **60 differ** from `12 - 2f` culled, each of them with a triangle of `|area2|` at most 39, a sliver that covers almost no pixels; none of them drew a fourth face. So the claim has the form it has everywhere in Phase 6: *exact in the hardware's arithmetic, equal to the geometry except for faces within a pixel of edge-on*. The named poses of the table stay clear of that (the two edge-on rows are exactly edge-on, all four corners of the face on one screen row or column), which is why their counts are exact.

```sh
.venv/bin/python host/mesh_model.py                  # the model's own self-check
.venv/bin/python sim/py/ref_mesh.py --check          # the table above, numpy, the frames' faces
.venv/bin/python sim/py/ref_mesh.py --files sim      # writes the testbench files; commit them
```

## 8. The tests

**`tb_mesh_cull`** (a few seconds): the unit alone. 23,024 triangles, one at a time, each verdict compared with the model: ordinary screen positions (6,000), the whole int16 range (4,000, for the 17-bit differences and 34-bit products), the corner values -32,768, -1, 0, 1, 32,767 (3,000), exactly collinear points (3,000), `area2` of +1 and -1 (3,000), every flag on every corner and bad indices (4,000), and a fixed set per mode. It checks the contract of Section 4: inputs held from `i_valid` to the answer and junk between requests, one `o_valid` per request within 16 clocks, never one without a request, no X.

    PASS: tb_mesh_cull (23024 cases: 9802 DRAW, 9687 CULLED, 3535 REJECTED; slowest answer 3 clocks)

**`tb_mesh_sys`** (about three minutes): `uart_gpu` over a serial line at 2.5 Mbaud, no framebuffer. The evidence is the replies. For 21 cube poses (41 draws: each pose with cull mode BACK and several with all four modes), the stream sends `SET_PROJECTION`, `SET_MATRIX`, `TRANSFORM`, `CLEAR_ALL`, `DRAW_MESH` and reads `GET_MESH_STATS` and `GET_STATS` (with the depth test on, so the pixel counts depend on every vertex's position and depth and on the order of the corners). Then: the near plane and a cube behind the camera; a scene with a CLAMP, a NEAR or a bad index on each of the three corners of a triangle, and bad indices with bits 10, 11, 12 and 15 set; the counters adding up over three `DRAW_MESH`es and the two sets being independent; the mesh in a display list run twice with a patched matrix, and a list holding `UPLOAD_INDICES` and `GET_MESH_STATS` (skipped, `BAD_OP`, nothing changed); every wrong length, each alone after a `CLEAR_ERRORS` so that `GET_INFO` shows exactly `BAD_LEN`, and then the counters unchanged; ranges that name nothing (no flag); the whole buffer, 512 triangles over 1,024 vertices with bad indices, a NEAR vertex and a CLAMP vertex, drawn in three cull modes and clipped at entry 511; and an upload that runs past the end. The four clock bytes of `GET_STATS` are not compared. Each reply byte is checked as it arrives.

    PASS: tb_mesh_sys (13981 command bytes, 1494 reply bytes, 67 fences, all as the model says)

**`make frame-mesh`** (five to eight minutes): `tb_frame_mesh` plays `sim/mesh_stream.hex` into `gpu_link` and dumps six frames; `ref_mesh.py` draws the same frames with the model, and `compare.py` demands an exact match, as for Chapters 6.3 and 6.4. The frames:

| Frame | What it shows |
| --- | --- |
| `f0` | the cube with three faces towards the camera, cull mode BACK, depth test on: three faces |
| `f1` | the same pose with cull mode NONE: the same picture, but for 78 silhouette pixels |
| `f2` | the same pose with cull mode FRONT: the inside of the box, the three faces that were hidden |
| `f3` | the cube through the near plane (`near` = 8): rejected triangles are holes |
| `f4` | vertex-intensity mode: every face at shade 0 |
| `f5` | a display list that transforms, sets the mode, clears and draws, with its matrix patched by twelve `WRITE_LIST_WORD`s; five bad packets first; a second list with the forbidden commands |

The script also checks the pictures by themselves, independently of the model of the hardware: the faces whose ramps appear in `f0`, `f4` and `f5` are exactly the faces numpy says point at the camera, so **the pictures show three faces**, and `f2` shows only faces that point away. As in Chapter 6.3, `tb_frame_mesh` alone does not check pixels: a wrong picture still prints PASS, and only the comparison catches it. The replies it reads are `GET_INFO` (flags 00), then `GET_MESH_STATS` and `GET_INFO` (flags 06).

**`tb_gpu_fuzz`** (about 13 minutes, a third longer than Chapter 6.5's: the first round below adds 10 KB to the stream) is replaced: capabilities `FF 3F`; the mesh commands among the valid packets, in lists and with wrong lengths; `GET_MESH_STATS` among the replies; and a first round that **defines every vertex slot and every index entry once** (1,024 vertices, a `TRANSFORM` of all of them, 512 entries), so that garbage that happens to contain `DRAW_MESH` is deterministic: the hardware never reads a RAM word that was not written.

What failures usually mean:

| Symptom | Likely cause |
| --- | --- |
| `tb_mesh_cull`: about half the cases wrong | front and back swapped (`area2` sign reversed, or `<` for `>`) |
| `tb_mesh_cull`: a few hundred zero-area cases wrong | `area2 >= 0` taken as front |
| `tb_mesh_cull`: the int16-range cases wrong, the ordinary ones right | differences or products computed in 16 or 32 bits |
| `tb_mesh_cull`: only flagged cases wrong | only the first corner's flags tested, or CLAMP forgotten, or `i_bad` ignored |
| `tb_mesh_cull`: only mode 3 (or mode 2) wrong | the cull mode decoded from the wrong bits |
| `tb_mesh_sys`: every `GET_MESH_STATS` wrong | the order of the three counters, or rejected counted as culled |
| `tb_mesh_sys`: counters right, `GET_STATS` wrong | corners in the wrong order, `x` and `y` swapped, depth of the wrong vertex, the entry's ramp where the intensity goes |
| `tb_mesh_sys`: the last scenes wrong | range not clipped at entry 511, `count` 0 drawing, a bad index aliased to its low ten bits, entries past 511 wrapping |
| `tb_mesh_sys`: extra reply bytes or wrong flags | a list that accepts `GET_MESH_STATS` or `UPLOAD_INDICES`, a length check that accepts too much |
| `make frame-mesh`: `f4` wrong only | vertex-intensity bit ignored or mis-decoded |
| `make frame-mesh`: all frames shifted or mirrored | `sy` used with the wrong sign, corner order |

## 9. Older files and resources

- **Replace** `host/gpu_model.py`; add `host/mesh_model.py` and `host/mesh_demo.py`. `host/vertex_model.py` and `host/raster_model.py` are unchanged. `gpu_model.py` imports `mesh_model`.
- **Fuzz:** replace `sim/tb_gpu_fuzz.v` (it expects caps `FF 3F`) and `sim/py/ref_fuzz.py`, then run `ref_fuzz.py sim`.
- **Capabilities:** in `rtl/common/gpu_link.v` change `.CAPS(16'h1FFF)` to `.CAPS(16'h3FFF)`. In `host/spi_info.py` append `"MESH"` to the `CAPS` list.
- **Older frame tests:** change `0xFF, 0x1F, 0x40` to `0xFF, 0x3F, 0x40` in `sim/py/ref_layers.py` (1 place), `ref_sprite_table.py` (1), `ref_compositor.py` (1), `ref_streaming.py` (2), `ref_display_lists.py` (3), `ref_shaded.py` (2) and `ref_depth.py` (2), and `FF 1F 40 01` to `FF 3F 40 01` in the failure messages of `sim/tb_frame_layers.v`, `tb_frame_sprite_table.v`, `tb_frame_compositor.v`, `tb_frame_streaming.v` (2), `tb_frame_display_lists.v`, `tb_frame_shaded.v` (2) and `tb_frame_depth.v` (2), and `1FFF` to `3FFF` in the header comments of `tb_frame_shaded.v` and `tb_frame_depth.v` and in the docstring of `ref_depth.py`.
- **Move the unknown opcode:** in `sim/py/ref_shaded.py` change `0x66` to `0x7E` (the docstring and the `frame(...)` call, 2 places) and in `sim/py/ref_depth.py` change `g.packet(0x66, bytes(3))` to `g.packet(0x7E, bytes(3))` (1 place). `0x7E` is unknown in every chapter so far.
- **Regenerate** with `--stream sim` for `ref_layers`, `ref_sprite_table`, `ref_compositor`, `ref_streaming`, `ref_display_lists`, `ref_shaded`, `ref_depth` and with `ref_replay.py --stream sim`: the eight `*_replies.hex` files change (the capability bytes), and so do `shaded_stream.hex` and `depth_stream.hex` (the moved opcode); nothing else.
- **`tri_raster`:** register the bounding box, as described under *Timing* below. No port changes.
- **Tie-offs:** none. No port of any existing module changes (`mesh_cull` is new and `cmd_engine` instantiates it).
- **Makefile:** add `FRAME_TESTS += mesh` after the others (this chapter's `make frame-mesh`); the two unit tests run as `make test TESTS="tb_mesh_cull tb_mesh_sys"` from `build/sim`. `ref_mesh.py` goes to `sim/py/`.

**Resources.** One build of the full Arty design from a design that follows this chapter (`yosys synth_xilinx -flatten -abc9 -arch xc7`, `nextpnr-xilinx`), against the Chapter 6.5 builds in the same flow:

| | Chapter 6.5 | with meshes |
| --- | --- | --- |
| LUTs (`SLICE_LUTX`, nextpnr, as in earlier chapters) | 11,641 to 11,663 | 12,448 to 12,454 |
| LUTs (yosys `stat`, LUT1-LUT6 cells) | 5,893 | 6,314 |
| Flip-flops | 4,119 to 4,121 | 4,631 |
| DSP48E1 | 38 or 39 | **41** |
| RAMB36E1 + RAMB18E1 | 95 + 5 | **96 + 5** |
| `clk_sys` maximum | 56.2 to 58.8 MHz | 43.5 to 59.2 MHz (eight seeds); **59.0 to 74.9 MHz** with the bounding box registered (below) |

The index buffer (512 x 64 bits) is one more RAMB36 (96 of 140 in use). The two new DSP slices are the two 17 x 17 products of `mesh_cull`. In yosys's count the design grows by about 420 LUTs and 510 flip-flops: three 64-bit vertex records and the entry register that the mesh loop holds (about 260 flip-flops), the three 32-bit counters (96), the pipeline of `mesh_cull`, the 64-bit index read register, the rasterizer-input multiplexers and the mesh loop's control. `DRAW_MESH` itself is small; the registers are the cost of holding a whole triangle while it is drawn.

**Timing: register the bounding box (a change to your `tri_raster`).** Without it this chapter's design meets the 50 MHz of `clk_sys` only by luck of placement. One design that follows this chapter gave 43.5, 46.8, 52.8, 55.8, 57.3, 58.6, 58.7 and 59.2 MHz over eight `nextpnr-xilinx` seeds; a second, independently written one gave 50.4, 55.7, 58.0 and 58.1 MHz over seeds 2 to 5, and seeds 1 and 6 had not finished routing after 25 minutes (the router keeps fighting the failing path). The critical path is the one of the earlier chapters, inside `tri_raster`: from the vertex registers through the bounding box (minimum of three, maximum of three, clamp to the screen: three compare-and-select levels, each a carry chain) and then the empty-box test (`xmin > xmax`, a fourth carry chain) into the state register. Nothing of this chapter is on it; the design around it has simply grown. The fix is to cut that path in two:

- In the setup clock that registers `area2` (it depends only on the vertex registers, as the box does), also register the clamped box `xmin`, `xmax`, `ymin`, `ymax`.
- In the next clock, test the *registered* box for emptiness (together with `area2 = 0`) and swap the corners for a negative area as before; the box does not depend on the corner order.

In a rasterizer organised like the reference this costs no clock at all; at worst it costs one setup clock per triangle, which the throughput limits of Chapters 4.7, 6.3 and 6.4 leave room for. With it, the second design gave **65.1, 68.6, 68.5, 74.9, 69.3, 59.0, 68.9 and 64.2 MHz** over seeds 1 to 8, every run routed in 4 to 8 minutes, and the critical path moved to the z divider of Chapter 6.3 (and, in the slowest seed, a `% 6` in the `UPLOAD_VERTICES` length check; if yours has one and you need more margin, test the 42 legal lengths with a small table instead). After the change, run `tb_tri_raster`, `tb_tri_shade`, `tb_gpu_tri`, `tb_depth_stats`, `make frame-shaded`, `make frame-depth` and this chapter's tests again; they must pass unchanged.

## 10. Done when

`tb_mesh_cull`, `tb_mesh_sys` and the replacement `tb_gpu_fuzz` pass; `make frame-mesh` matches all six frames; `ref_mesh.py --check` prints its PASS line; the older tests (`make test`, `make frame`) still pass with the edits of Section 9; the demo's `--model` run prints PASS; the full Arty build meets 50 MHz on `clk_sys` with margin (the bounding-box change of Section 9); and you can say why the face test looks at the sign of `area2` and why the rejection comes first, why a zero-area triangle is back-facing, and where Chapter 6.7 will put the intensities.

## Phase 6 conventions set here

Later chapters use these without restating them.

- **Front face:** counter-clockwise on the monitor, `area2 > 0` (`raster_model.area2`); everything else, zero area included, is back-facing. Meshes are wound counter-clockwise **as seen from outside**; the camera space is left-handed (x right, y up, z forward), and the test `((b - a) x (c - a)) . n_out < 0` is what "counter-clockwise from outside" means in it.
- **Index buffer:** 512 entries of 8 bytes `i0 lo, i0 hi, i1 lo, i1 hi, i2 lo, i2 hi, ramp, intensity`; 1,024 vertex slots are addressable, an index of 1,024 or more is rejected. Inside `cmd_engine`, 512 x 64 bits, no initial loop, undefined at power-up.
- **Commands:** `0x66` `UPLOAD_INDICES` (2 + 8k, k <= 31), `0x67` `SET_MESH_MODE` (1), `0x68` `DRAW_MESH` (4), `0x69` `GET_MESH_STATS` (0; 12-byte reply culled, rejected, drawn), `0x6A` `CLEAR_MESH_STATS` (0). `0x6B`-`0x6F` and `0x75`-`0x7F` free. In lists: `0x67`, `0x68`, `0x6A` allowed; `0x66` and `0x69` forbidden (`BAD_OP`). Capability bit 13; caps `0x3FFF`; bits 14-15 free.
- **Mesh mode** (power-up 0): bits 1:0 cull (0 none, 1 back, 2 front, 3 both), bit 2 intensity from the vertex records' byte 7 (else the entry's `intensity`, flat). **Rejection** (before culling, always): bad index, `NEAR`, `CLAMP`.
- **Intensity hook for 6.7:** write the Q5.3 vertex intensity into byte 7 of the transformed record and set mode bit 2; `DRAW_MESH` needs no change.
- **Cost:** a rejected or culled triangle 17 clocks, a drawn one that plus the shaded triangle's setup (about 120 clocks) plus about one clock per bounding-box pixel (Chapter 6.4); `DRAW_MESH` blocks the engine like a triangle.
- **Unknown opcodes the tests use:** `0x6B`, `0x6F` (fuzz), `0x75` (`ref_vertex.py`), `0x77`, `0x7E` (`ref_shaded.py`, `ref_depth.py`, fuzz, `gpu_model` self-check), `0x7F`, and `0x80` and up. A later chapter that takes one of `0x6B`-`0x7F` must move the tests that use it.
- **`tri_raster` timing:** the clamped bounding box is registered in the setup clock that registers `area2` and tested for emptiness one clock later; with it `clk_sys` closes at about 59 to 75 MHz. Keep long compare chains out of single clocks in later changes to the rasterizer.

## Testbench requirements

**Modules you provide.** `mesh_cull` in `rtl/common/mesh_cull.v` with the ports of Section 4; `cmd_engine` with the five commands, the 512-entry index RAM, the mode register and the three counters, using `mesh_cull`, the transformed-vertex buffer of Chapter 6.5 and the shaded-triangle path; `gpu_link` with caps `0x3FFF`. The unit contract: `i_valid` is a one-clock pulse; the inputs (`i_bad`, the three records, `i_mode`) are held from that clock until the clock of the answer and carry junk between requests; a new `i_valid` is sent only after the answer for the previous one; each `i_valid` is answered by exactly one one-clock `o_valid` 1 to 16 clocks later (the latency may vary), with `o_verdict`, read in that clock, 0 (DRAW), 1 (CULLED) or 2 (REJECTED) as Sections 2 and 3 say. The engine contract is the protocol: `docs/protocol.md` section 18.

- **`tb_mesh_cull`** (from `build/sim`, `make test`): reads `../../sim/mesh_cull_cases.hex` (64-bit words; word 0 the case count, then four words per case: three records and `{mode, bad, verdict}`); instantiates `mesh_cull` with the port names of Section 4; a few seconds.
- **`tb_mesh_sys`** (from `build/sim`, `make test`): `uart_gpu` at 2.5 Mbaud; reads `../../sim/mesh_sys_stream.hex` and `mesh_sys_expect.hex`; every reply byte is compared as it arrives (the clock bytes of `GET_STATS` are masked); the reply count must match exactly. About three minutes.
- **`tb_frame_mesh`** (from the repo root, `make frame-mesh`): `gpu_link` and `fb_scanout` wired as in Chapter 5.7 with a 640 x 480 beam; reads `sim/mesh_stream.hex` and `sim/mesh_replies.hex`; writes `build/frame_mesh_f0.ppm` to `f5.ppm`; the pixels are checked by `sim/py/ref_mesh.py build` and `compare.py` (five to eight minutes).
- **`tb_gpu_fuzz`** (from `build/sim`, `make test`, about 13 minutes): the Chapter 5.2 fuzz with the mesh commands added; capabilities `FF 3F`.

**Files from this chapter:** `tb_mesh_cull.v`, `tb_mesh_sys.v`, `tb_frame_mesh.v` and `tb_gpu_fuzz.v` go to `sim/`; `ref_mesh.py` and `ref_fuzz.py` to `sim/py/`; `gpu_model.py`, `mesh_model.py` and `mesh_demo.py` to `host/`; `protocol.md` to `docs/`. Run `sim/py/ref_mesh.py --files sim` and `sim/py/ref_fuzz.py sim` once and commit the files they write (`mesh_cull_cases.hex`, `mesh_sys_stream.hex`, `mesh_sys_expect.hex`, `mesh_stream.hex`, `mesh_replies.hex`, `fuzz_*`).
