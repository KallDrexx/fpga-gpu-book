# Chapter 6.7 - 3D Capstone

Chapter 6.6 left one hook open on purpose: a draw mode that takes the brightness of each triangle corner from byte 7 of its transformed vertex record, a byte the vertex pipeline writes as 0. This last chapter fills it. Every vertex gets a **normal**, the GPU computes how directly that normal faces a light with the multiply-accumulate unit of Chapter 6.1, and the result becomes the intensity that Chapter 6.3's rasterizer already knows how to interpolate across a triangle. Then everything built since Chapter 4.1 goes to work at once: three models are uploaded, one display list per model is defined, and from then on the MCU sends **one matrix and one `RUN_LIST` per frame** (29 bytes) while a lit, shaded, depth-tested model turns on the screen.

By the end of this chapter you will have:

- a `light_unit` that turns the normals of a range of vertices into intensities and writes them into the transformed records, built around one `mac_unit`, and a `TRANSFORM` that calls it when lighting is on,
- a normal buffer (1,024 x 48 bits) and two commands, `UPLOAD_NORMALS` and `SET_LIGHT` (`0x6B`, `0x6C`), capability bit 14 (`GET_INFO` now says `FF 7F`), and `docs/protocol.md` section 19,
- `host/light_model.py` (the lighting arithmetic, bit for bit), `host/mesh_gen.py` (a cube of 24 split vertices, an icosphere and a torus of 192 triangles, all generated in code), `host/lit_scene.py` (the three models in the GPU's buffers, their display lists and the 29-byte frame) and `host/lit_demo.py`,
- `tb_light_unit` (2,776 vertices bit-exact), `tb_light_sys` (every intensity read back through the serial line), `make frame-lit` (six frames, three models, compared pixel for pixel with the Python renderer), `tb_light_budget` (the per-frame traffic and the frame time, measured in clocks) and the replacement `tb_gpu_fuzz`,
- a floating-point renderer written in numpy that agrees with the fixed-point pictures, and the lighting within one shade of floating point on 84,600 vertex evaluations,
- the older tests still passing with the edits of Section 9, and a short wrap-up of the whole book in Section 11.

Read `docs/protocol.md` section 19 once now; this chapter explains the decisions behind it.

## 1. Lighting in one equation

A diffuse (Lambert) surface is as bright as it is turned towards the light. With `n` the surface normal and `L` the unit vector from the surface towards the light, the brightness is `max(n . L, 0)`: 1 when the surface faces the light, 0 when it is edge-on, and also 0 (not negative) for every surface facing away, because a surface cannot take light away. Real scenes are not black on the dark side, so a constant **ambient** term is added after the clamp:

    b = ambient + max(n . L, 0)

Order matters. `max(ambient + n . L, 0)` is a different function: it makes the dark side darker than the ambient level wherever `n . L` is negative. `tb_light_unit` has cases for exactly this.

**The numbers.** Normals, the light direction and `b` are Q2.14 (Chapter 6.1: 16,384 is 1.0). Chapter 6.3's rasterizer interpolates an **intensity**, a Q5.3 byte where 248 is full brightness (shade 31 of the 32 in a ramp). The mapping was fixed in Chapter 6.3 and is the one line of arithmetic that joins the two chapters:

    i = clamp(round(b * 248 / 2^14), 0, 255)

248 is 256 - 8, so the multiplication is a subtraction of two shifts; there is no second multiplier. A brightness above 1.0 (an ambient of 0.2 plus a light of length 0.9) gives an intensity above 248 and saturates at 255, which the ramp lookup of Chapter 6.3 turns into shade 31 anyway.

**Where the smoothness comes from.** The rasterizer interpolates intensity linearly across each triangle, so what you light is the **vertices**: Gouraud shading. A sphere looks round if its vertices share a normal that points away from the centre, and each triangle blends smoothly into its neighbour. A cube must look flat: every face one brightness, an edge between faces. If a cube corner had one normal, the three faces meeting there would blend into each other. So a cube needs **split vertices**: a corner that belongs to three faces appears three times, once per face, each copy with that face's normal. The cube of this chapter has 24 vertices for that reason; the icosphere and the torus share theirs (42 and 96 vertices).

## 2. Where does the light live?

The light direction is a vector, the normals are vectors, and the model turns: they must be compared in one space. There are three ways to do that, and they cost different things on the two scarce resources of this design, the host's bytes per frame and the GPU's clocks per vertex.

| Choice | Light is fixed in | Per frame the host sends | The GPU does per vertex |
| --- | --- | --- | --- |
| A. Light in **model space** | the model (the sun turns with it) | SET_MATRIX and the light, turned by the MCU | 3 MACs |
| B. **Turn each normal** by the matrix's 3 x 3 | the camera | SET_MATRIX | 9 MACs, 3 more for the dot product |
| C. **Turn the light once** per `TRANSFORM` by the transpose | the camera | SET_MATRIX | 3 MACs |

Option A looks painted: the lit side of a spinning torus never changes. To keep the light still in the world the MCU would have to turn it backwards every frame and send `SET_LIGHT` as well (11 more bytes), which defeats "the MCU sends only a matrix". B and C both keep the frame at 29 bytes and give the same answer, because of one identity:

    (M n) . L  =  n . (M^T L)

B turns every normal into camera space (9 MAC beats per vertex) and then dots it with `L` (3 more). C turns the light into **model space** once per `TRANSFORM` (`l_m = M^T L`, 9 beats in all) and then needs only the 3 beats of `n . l_m` per vertex: a quarter of B's work. This is the choice of the chapter.

Two caveats, both because the matrix itself is used to turn normals:

- **Scale.** The intended matrix is a rotation `R` times a uniform scale `s` (Chapter 6.1). Then `M n = s (R n)`: the right direction, `s` times too long, so the lit term is `s` times too large. Keep `s = 1` for lit models (then `M^T` is the inverse rotation `R^T`) and put the size in the vertices. A non-uniform scale would also bend the normals (they would need the inverse transpose of `M`), which this GPU does not do.
- **Rounding.** C rounds in different places from B (once in `l_m`, once in `d`), so the two can differ by one count of `d`. `host/light_model.py` defines the C order, and `ref_light.py --check` shows it within about half an intensity count of floating point (Section 7).

## 3. Storing normals, and the two commands

**A separate buffer, not a wider vertex buffer.** Widening the vertex buffer from 48 to 96 bits would force every existing `UPLOAD_VERTICES` test (and the vertex unit's 1-clock source read) to change, and make lighting-free models pay for it. A parallel 1,024 x 48 buffer in which slot *k* is the normal of vertex *k* costs 3 RAMB18E1, leaves every earlier path alone, and mirrors the upload you already have.

| Opcode | Name | Length | Payload |
| --- | --- | --- | --- |
| `6B` | `UPLOAD_NORMALS` | 2 + 6k, k = 1..42 | first slot (u16), then k normals nx, ny, nz (int16, Q2.14) |
| `6C` | `SET_LIGHT` | 9 | flags (bit 0 = lighting on), lx, ly, lz (Q2.14, camera space, **towards** the light), ambient (Q2.14) |

- **`UPLOAD_NORMALS`** is `UPLOAD_VERTICES` for normals, down to the details: slots 1,024 and up dropped without a flag, a wrong length skipped with `BAD_LEN`, a packet cut by a frame start keeps its whole normals and sets `TRUNC`. **Not allowed in a display list** (`BAD_OP`), like every other upload.
- **`SET_LIGHT`** is allowed in a list. Bits 7:1 of `flags` are ignored (the test sends `0xFE` and expects lighting off). It takes effect at the next `TRANSFORM`: records that exist keep their intensities.
- **Lighting is off at power-up**, so a GPU that never sees `SET_LIGHT` writes 0 into byte 7 exactly as Chapter 6.5's did, and **none of the Chapter 6.5 or 6.6 tests changes** (that is why the switch exists).
- **A vertex whose normal was never uploaded** has no defined intensity when lighting is on; the tests never light one (the fuzz uploads every normal before anything else, as Chapter 6.6 did for vertices and indices).
- **Capability bit 14** (`LIGHT`): caps `0x7FFF`, `GET_INFO` says `47 01 FF 7F 40 01 F0 00 00`.
- **Opcodes.** Only the fuzz used `0x6B` as an unknown opcode; its replacement uses `0x6D`. No test used `0x6C`. `0x6D`-`0x6F` and `0x75`-`0x7F` stay free.

## 4. The lighting unit

`light_unit` is the one new arithmetic module. It runs after the vertex pipeline for the same range of vertices, so Chapter 6.5's `vertex_unit` and its tests are untouched, and with lighting off nothing happens at all.

    module light_unit (
        input  i_clk,
        input  i_start, input [9:0] i_first, input [10:0] i_count,        // same range as TRANSFORM
        output o_busy, output o_done,
        input  i_mat_we, input [191:0] i_matrix,                          // SET_MATRIX payload: the 3 x 3 part is used
        input  i_light_we, input [63:0] i_light,                          // {ambient, lz, ly, lx}, Q2.14 each
        output [9:0] o_nrm_addr, input [47:0] i_nrm_data,                 // normal RAM: {nz, ny, nx}, one clock of latency
        output [9:0] o_rec_raddr, input [63:0] i_rec_data,                // transformed record RAM, read side
        output o_rec_we, output [9:0] o_rec_waddr, output [63:0] o_rec_wdata);

**Per start.** Compute `l_m[j] = mac(M[i][j] * L[i], i = 0..2)` for `j = 0, 1, 2`: nine beats into the `mac_unit`, which delivers three results in order. The matrix is Chapter 6.5's row-major `m00 .. m22`, so `M[i][j]` is entry `3i + j`: the **column** `j` of the matrix dotted with the light, which is the transpose at work. Both outputs saturate to signed 16 bits; with a rotation and `|L| <= 1` they cannot.

**Per vertex.**

1. Read the normal `n` and the record `r` of the slot (two clocks for the registered RAMs).
2. Three beats into the MAC: `d = mac(n[k] * l_m[k], k = 0..2)`. Wait for `o_valid`; do not count latency.
3. `b = ambient + (d < 0 ? 0 : d)`, floored at 0 (the ambient can be negative).
4. `i = min((b * 248 + 8192) >> 14, 255)` with `b * 248 = (b << 8) - (b << 3)`.
5. Write the record back with byte 7 replaced by `i` and bytes 6:0 as read.

Step 5 is a **read-modify-write** of a 64-bit record so that the lighting unit does not need a byte-write port on the record RAM; the engine gives the unit the RAM's read address and its write port while a lighting pass runs (the vertex pipeline and the lighting unit never run together). The reference takes 13 clocks per vertex: two for the RAM, three beats, about five for the MAC's pipeline, two of arithmetic and the write. Against the 160 clocks the vertex pipeline spends per vertex (three divisions) lighting is **8 %** more work.

**Keep the unit self-contained.** It has its own `mac_unit` (one more DSP48E1 of 220) and its own copy of the matrix and the light, taken on the strobes. `vertex_unit` is idle during a lighting pass and already holds the matrix, so sharing either is possible, but it would give `light_unit` ports that `tb_light_unit` does not drive, and it saves little (Section 9). Inside the unit you are free: take MAC results by `o_valid`, never by counting clocks, and the testbench accepts any latency, a unit that recomputes `l_m` for every vertex, a MAC with one more pipeline stage, or a pause between vertices.

**The engine.**

- **Normal RAM:** 1,024 x 48 bits, written by `UPLOAD_NORMALS` exactly as the vertex RAM is by `UPLOAD_VERTICES` (a normal is written when its sixth byte arrives); yosys maps it to three RAMB18E1. No `initial` loop.
- **`TRANSFORM`:** when `vertex_unit` is done, if lighting is on, start `light_unit` on the same `first` and `count` and wait for it. The engine stays busy through both, and a reset abandons neither, as before.
- **`SET_MATRIX`:** its payload bytes already sit in the engine's argument buffer, so the strobe goes to both units. **`SET_LIGHT`:** the payload gives the enable bit (the engine keeps it) and the 8 bytes `{ambient, lz, ly, lx}` for the unit.
- If your engine's state register is full (the reference's 5 bits were), widen it, or keep `TRANSFORM` in one state with a flag that says which unit it is waiting for.

## 5. Three models, generated

`host/mesh_gen.py` makes all three in about 100 lines, so there is no model file and no licence to worry about. Each function returns vertices (int16), normals (Q2.14) and triangles, wound counter-clockwise from outside (Chapter 6.6's convention: `((b - a) x (c - a)) . n_out < 0`; the generator tests every triangle against its normals and checks that the closed meshes have every edge shared by two triangles in opposite directions).

| Model | Vertices | Triangles | Normals | Notes |
| --- | --- | --- | --- | --- |
| cube, half-size 60 | 24 (4 per face) | 12 | the face normal, flat | six palette ramps, one per face |
| icosphere, radius 80 | 42 (subdivision 1) | 80 | the vertex direction, smooth | subdivision 2 is 162 / 320 |
| torus, ring 75, tube 30, 12 x 8 | 96 | **192** | analytic, smooth | the "about 200 triangle" model; self-occluding, so the depth buffer works |

Together they take 162 of the 1,024 vertex slots and 284 of the 512 index entries (the subdivision-2 sphere would not fit beside the torus, which is why the scene uses subdivision 1). `host/lit_scene.py` puts them in the buffers one after another and gives each its own display list:

| List | Model | Vertices | Entries |
| --- | --- | --- | --- |
| 0 | cube | 0 - 23 | 0 - 11 |
| 1 | icosphere | 24 - 65 | 12 - 91 |
| 2 | torus | 66 - 161 | 92 - 283 |

Each list is `TRANSFORM first count`, `CLEAR_ALL`, `DRAW_MESH first count`, `FLIP`: 19 bytes. The setup (palette, projection, `SET_LIGHT`, depth test, mesh mode 5 = cull back + vertex intensity, the three models' vertices, normals and entries, the three lists) is 5,934 bytes sent once.

**The light.** `L = 0.8 * unit(-0.45, 0.55, -0.70)` in camera space (x right, y up, z forward: the light is above, left and on the camera's side of the objects), ambient 0.2. The length of `L` is the **diffuse gain**: 0.8 + 0.2 reaches exactly full brightness for a surface facing the light, and nothing in the GPU needs a separate "diffuse" parameter.

## 6. A frame, its bytes and its clocks

**The host.** Once per frame, right after `VSYNC` falls (the pattern of Chapter 5.7, section 14):

    SET_MATRIX  26 bytes  (2 header + 12 x int16)
    RUN_LIST n   3 bytes

That is **29 bytes**: 1,740 bytes a second at 60 frames per second, 46 microseconds of SPI at 5 MHz (about 90 with the two chip-select frames) of the 15.4 ms visible part of a frame. The MCU's work is to build the matrix: nine Q2.14 entries from a rotation, plus a translation. The model, the light, the mode and the lists are already there. Changing the model changes no byte of the traffic (only which list `RUN_LIST` names), and moving the light adds one 11-byte `SET_LIGHT`.

**The GPU**, per frame, in order: `TRANSFORM` (vertex pipeline, then lighting), `CLEAR_ALL`, `DRAW_MESH`, `FLIP`. The `FLIP` waits for vertical blanking, so a picture drawn in frame *N* is on the screen in frame *N* + 1 and the engine is idle again in time for the next matrix. `tb_light_budget` measures these steps for the torus, with `GET_MESH_STATS` as a marker (the time from the last stop bit of a command to the first start bit of the reply), against the reference engine at 50 MHz:

| Step (torus: 96 vertices, 192 triangles) | Clocks | Time |
| --- | --- | --- |
| `TRANSFORM`, vertices | 15,365 | 0.31 ms |
| lighting (`TRANSFORM` with lighting on, minus off) | 1,265 | 0.025 ms |
| `CLEAR_ALL` (colour and depth, 76,800 words) | 76,803 | 1.54 ms |
| `DRAW_MESH` (192 entries: 80 to 93 drawn in the tumble, the rest culled) | 55,851 | 1.12 ms |
| **The list, from the last byte of `RUN_LIST` to the `FLIP` request** | **149,100 to 153,250** | **3.0 to 3.1 ms** |
| One 30 fps frame / one 60 fps frame | 1,666,667 / 833,333 | 33.3 / 16.7 ms |

**The budget.** The render is 9 % of a 30 fps frame and 18 % of a 60 fps one. A frame's `FLIP` lands in the next blanking if the render ends before it, that is within the 480 visible lines (15.4 ms) after the host sent the matrix; then the model turns at **60 frames per second**, which is what the testbench measures. A render that misses that blanking catches the next one, so up to about 32 ms the model still turns at 30. `tb_light_budget` passes at the Summary's 30 fps: frame starts at most 1,666,667 clocks apart on average, and no render over 30 ms (1,500,000 clocks). A GPU four times slower than the reference still runs at 60; one up to about ten times slower runs at 30 and passes.

**Where the time goes** decides what to improve next. `CLEAR_ALL` is the largest item (76,800 words whatever the model) and nothing in this chapter changes it. The vertex pipeline spends about 160 clocks per vertex, almost all in Chapter 6.5's three divisions; lighting adds 13. A drawn triangle costs about 17 clocks of mesh loop, about 120 of shaded setup and about one per bounding-box pixel; a culled one costs 17. At 60 frames per second the 15.4 ms of visible lines are about 770,000 clocks, so after the clear a model of 200 vertices and 400 average triangles fits several times over. The host's 90 microseconds are 0.6 % of a frame.

## 7. The reference renderer, and the floating-point check

**There is one definition of the picture: `host/gpu_model.py`.** Driven by the same packets a host sends (`lit_scene.setup_packets`, `frame_packets`), it is the Python renderer for "this model and this matrix": the fixed-point vertex transform of Chapter 6.5, `host/light_model.py` for the intensities, `mesh_model.py` for the faces and `raster_model.py` for the pixels. `ref_lit.py build` writes its six pictures as `ref_lit_f0.ppm` to `f5.ppm`, and `compare.py` demands that the simulated frames match them exactly. The model's new parts are small: the normal buffer and the light state, the intensity written by `TRANSFORM` (byte 7, 0 with lighting off), `READ_VERTEX` returning it, and `DRAW_MESH` using it with mode bit 2.

**Floating point is the independent check.** Two kinds, both in numpy and both run by `--check` options:

- `sim/py/ref_light.py --check` evaluates the **lighting alone** on the cube, an icosphere of subdivision 2 and the torus in 300 random orientations with random lights and ambients: 84,600 vertex evaluations. The fixed-point intensity is within **0.51** of the floating-point `min(255, 248 b)` computed from the same Q2.14 numbers (the half is the rounding itself), and within **0.52** of one computed from a true rotation, unit normals and the real light, so rounding the inputs to Q2.14 costs almost nothing. The shade (0 to 31) is **within 1** on every vertex; 1 appears where `248 b` is close to a multiple of 8 and the two roundings (to intensity, then to shade) disagree with the single rounding of the float.
- `sim/py/ref_lit.py --check` draws the six frames again with a **floating-point renderer** written for the purpose (float transform, projection with no rounding to pixels, float lighting, barycentric interpolation and z-buffer) and compares it with the fixed-point pictures:

| Frame | Model | Pixels covered | Covered in only one picture (all at an edge) | Ramp differs | Shade differs by more than 1 | Largest shade difference |
| --- | --- | --- | --- | --- | --- | --- |
| f0 | cube | 18,225 | 116 | 51 | 45 | 25 |
| f1 | icosphere | 14,913 | 59 | 0 | 5 | 4 |
| f2 | torus | 11,801 | 99 | 0 | 26 | 12 |
| f3 | torus | 14,514 | 111 | 0 | 18 | 14 |
| f4 | torus | 16,767 | 81 | 0 | 35 | 12 |
| f5 | torus | 18,074 | 138 | 0 | 8 | 9 |

Every difference is on a silhouette or next to a triangle edge, where the exact fixed-point positions put a pixel on the other side of an edge than the float positions do; the large shade differences are edge pixels where a different surface wins. The assertions: every pixel covered in only one picture is within one pixel of the other picture's edge; every pixel whose ramp differs or whose shade differs by more than 1 is within one pixel of a triangle edge of the float picture (inside the triangles the two agree to one shade); and each class stays under 0.5 % of the covered pixels (3 % for "covered in only one").

```sh
.venv/bin/python host/light_model.py                 # the arithmetic's self-check
.venv/bin/python host/mesh_gen.py                    # the meshes: sizes, closed, consistently wound
.venv/bin/python sim/py/ref_light.py --check         # lighting against floating point
.venv/bin/python sim/py/ref_lit.py --check           # the frames against the float renderer
.venv/bin/python sim/py/ref_light.py --files sim     # writes light_cases.hex, light_sys_stream.hex, light_sys_expect.hex
.venv/bin/python sim/py/ref_lit.py --files sim       # writes lit_stream.hex, lit_replies.hex, budget_*.hex
.venv/bin/python host/lit_demo.py --model            # the host program against the model
```

## 8. The tests

**`tb_light_unit`** (about 3 seconds): the unit alone, with the testbench playing the RAMs (registered read, one clock of latency; the records start as junk with a junk byte 7). 149 cases, 2,776 vertices: random rotations of every scale and random raw matrices (so `l_m` saturates), normals including the extremes -32,768 and 32,767, the dot product at, just above and just below zero, brightness around the clamp at 255 (`b` = 16,383 to 32,767) and negative, the first and last slots, all 1,024 slots, an empty range and the three models in three poses. Checked for every run: byte 7 of each record in the range is exactly the model's and bytes 6:0 are unchanged; nothing outside the range is written; `o_busy` rises within two clocks of `i_start`; `o_done` is one clock long; no write comes after `o_done`; the unit finishes within 600 clocks per vertex; and every second case runs **again with no new matrix or light** (they must be kept) with junk on `i_matrix` and `i_light`.

    PASS: tb_light_unit (149 cases, 2776 vertices bit-exact; slowest run 13327 clocks)

**`tb_light_sys`** (about 75 seconds alone): `uart_gpu` at 2.5 Mbaud, no framebuffer. The evidence is `READ_VERTEX`, which returns all 8 bytes of a record. Lighting off (byte 7 stays 0 although normals are uploaded), then on for all 162 vertices of the scene; a `SET_LIGHT` that changes nothing until the next `TRANSFORM`; three more matrices under the same light (the light does not turn with the model); scaled matrices (0.5 and 1.9); ambient from -32,768 to 32,767, a zero light and a saturated one; lighting off again; a partial range; flags `0xFE` (off); all 1,024 slots lit and `TRANSFORM` clipped at slot 1,023; `UPLOAD_NORMALS` at the end of the buffer (the slots past it dropped, slot 65,535 included, and the normals below untouched); every wrong length of both commands alone (`GET_INFO` shows exactly `BAD_LEN`); `0x6D`-`0x6F` as unknown; and a display list with `SET_LIGHT`, `SET_MATRIX` and `TRANSFORM` (run twice) and an `UPLOAD_NORMALS` that a list may not hold.

    PASS: tb_light_sys (17900 command bytes, 5080 reply bytes, 41 fences, all as the model says)

**`make frame-lit`** (about six minutes): `tb_frame_lit` plays `sim/lit_stream.hex` into `gpu_link` and dumps six frames; `ref_lit.py` draws the same six with the model and `compare.py` demands an exact match, as for Chapters 6.3 to 6.6. Setup first (the three models, the palette, the three lists: 287 packets, one SPI frame each), then:

| Frame | What it shows | What the host sent for it |
| --- | --- | --- |
| `f0` | the cube, three faces, each one flat brightness | `SET_MATRIX` + `RUN_LIST 0` |
| `f1` | the icosphere, smooth | `SET_MATRIX` + `RUN_LIST 1` |
| `f2`, `f3`, `f4` | the torus in three consecutive poses of a tumble | `SET_MATRIX` + `RUN_LIST 2`: 29 bytes each |
| `f5` | the torus again, the light moved | `SET_LIGHT` + `SET_MATRIX` + `RUN_LIST 2`: 40 bytes |

As in Chapter 6.3, `tb_frame_lit` alone does not check pixels: a wrong picture still prints PASS and only the comparison catches it. It checks the geometry and sync alignment of the video, the SPI status, that the commands meant for the middle of a frame were sent there, and the 30 reply bytes (`GET_INFO`, `GET_MESH_STATS` over the six pictures, `GET_INFO`: flags 0, capabilities `FF 7F`). `ref_lit.py` checks, independently of the model of the hardware, that the cube's picture shows exactly the faces numpy says point at the camera.

**`tb_light_budget`** (about three minutes): the frame budget and the traffic, with a modelled 60 Hz display (a blank of 70,000 clocks every 833,333, the flip acknowledged at its start). It sends the setup, measures the four steps of the torus list (the table of Section 6), clears the counters and runs three frames the way a host does: wait until the previous `FLIP` was acknowledged, wait for the frame start, send **SET_MATRIX + RUN_LIST and nothing else**. The testbench builds those 29 bytes itself from the matrices in `sim/budget_frames.hex`, so the GPU gets nothing else per frame. It fails when a render takes more than 30 ms, when the frames are not 30 per second or better, or when the final `GET_MESH_STATS` and `GET_STATS` (24 bytes, the clock bytes masked) differ from the model, so the three frames, each with a different matrix, drew exactly the model's triangles and pixels. The frames' matrices come from the same `spin(k)` that the demo uses.

    PASS: tb_light_budget (3 frames of 29 bytes; render 153249 clocks (3064 us) at most; TRANSFORM 15365 (+1265 for lighting), CLEAR_ALL 76803, DRAW_MESH 55851 clocks; frames 833333 clocks apart = 60 frames/s; 0 flips after the blank start)

**`tb_gpu_fuzz`** (about 15 minutes, a little longer than Chapter 6.6's: the first round now also uploads 1,024 normals) is replaced: capabilities `FF 7F`; `SET_LIGHT` (lighting often on, so `TRANSFORM` really writes byte 7, and once with flags `0xFE` and a non-zero light, which must leave it 0) and `UPLOAD_NORMALS` among the valid packets, in lists and with wrong lengths; `0x6D` as the unknown reserved opcode.

What failures usually mean:

| Symptom | Likely cause |
| --- | --- |
| `tb_light_unit`: many cases wrong, byte 7 wrong everywhere | `l_m` computed with the matrix, not its transpose; or `M[i][j]` indexed `3j + i` |
| `tb_light_unit`: the cases with `d < 0` wrong | the clamp left out, or applied after the ambient |
| `tb_light_unit`: bytes 6:0 of a record changed | the write put the intensity in the wrong byte (it is byte 7, bits 63:56) |
| `tb_light_unit`: only the bright cases wrong | no saturation at 255 |
| `tb_light_unit`: off by one in many cases | truncation instead of round-half-up in the `* 248 >> 14` |
| `tb_light_unit`: the second run of a case wrong | matrix or light not kept between starts |
| `tb_light_sys`: byte 7 non-zero with lighting off | lighting not gated by the enable bit, or the reserved flag bits tested |
| `tb_light_sys`: only the first or last vertex of a `TRANSFORM` wrong | range off by one in the lighting pass |
| `tb_light_sys`: the cube's intensities change after a partial `TRANSFORM` | the lighting pass covered more than the range |
| `tb_light_sys`: slot 1,023 or 0 wrong after the end-of-buffer uploads | slots past 1,023 wrapped instead of dropped |
| `make frame-lit`: all frames wrong, `tb_light_sys` passes | mode bit 2 ignored, or the three corner records fetched in the wrong order |
| `tb_light_budget`: counters wrong | a list that does not run to its `FLIP`, or the lighting pass not finishing before `DRAW_MESH` |
| `tb_light_budget`: render far over its limit | the lighting pass restarting the vertex pipeline for every vertex, or waiting a fixed number of clocks |

## 9. Older files and resources

- **Replace** `host/gpu_model.py`; add `host/light_model.py`, `host/mesh_gen.py`, `host/lit_scene.py` and `host/lit_demo.py`. `vertex_model.py`, `mesh_model.py` and `raster_model.py` are unchanged; `gpu_model.py` imports `light_model`.
- **Fuzz:** replace `sim/tb_gpu_fuzz.v` (it expects caps `FF 7F`) and `sim/py/ref_fuzz.py`, then run `ref_fuzz.py sim`.
- **Capabilities:** in `rtl/common/gpu_link.v` change `.CAPS(16'h3FFF)` to `.CAPS(16'h7FFF)`. In `host/spi_info.py` append `"LIGHT"` to the `CAPS` list.
- **Older frame tests:** change `0xFF, 0x3F, 0x40` to `0xFF, 0x7F, 0x40` in `sim/py/ref_layers.py` (1 place), `ref_sprite_table.py` (1), `ref_compositor.py` (1), `ref_streaming.py` (2), `ref_display_lists.py` (3), `ref_shaded.py` (2), `ref_depth.py` (2) and `ref_mesh.py` (2), and `FF 3F 40 01` to `FF 7F 40 01` in the failure messages of `sim/tb_frame_layers.v`, `tb_frame_sprite_table.v`, `tb_frame_compositor.v`, `tb_frame_streaming.v` (2), `tb_frame_display_lists.v`, `tb_frame_shaded.v` (2), `tb_frame_depth.v` (2) and `tb_frame_mesh.v` (2), and `3FFF` to `7FFF` in the header comments of `tb_frame_shaded.v`, `tb_frame_depth.v`, `tb_frame_mesh.v` and in the docstrings of `ref_depth.py` and `ref_mesh.py`.
- **Regenerate** with `--stream sim` for `ref_layers`, `ref_sprite_table`, `ref_compositor`, `ref_streaming`, `ref_display_lists`, `ref_shaded`, `ref_depth` and `ref_replay`, and `ref_mesh.py --files sim`: the nine `*_replies.hex` files change (the capability bytes); nothing else. No unknown-opcode edit is needed in the older tests: the only one that used `0x6B` is the fuzz (replaced above).
- **Tie-offs:** none. No port of any existing module changes (`light_unit` is new and `cmd_engine` instantiates it).
- **`tri_raster`:** unchanged from Chapter 6.6 (with its registered bounding box).
- **Makefile:** add `FRAME_TESTS += lit` after the others (`make frame-lit`); the three unit tests run as `make test TESTS="tb_light_unit tb_light_sys tb_light_budget"` from `build/sim`. `ref_light.py` and `ref_lit.py` go to `sim/py/`.

**Resources.** One build of the full Arty design from the reference (`yosys synth_xilinx -flatten -abc9 -arch xc7`, `nextpnr-xilinx`), against the Chapter 6.6 builds in the same flow:

| | Chapter 6.6 | with lighting |
| --- | --- | --- |
| LUTs (yosys `stat`, LUT1-LUT6 cells) | 6,261 | 6,640 |
| LUTs (`SLICE_LUTX` cells nextpnr creates) | 11,855 | 12,452 |
| Flip-flops | 4,654 | 5,273 |
| DSP48E1 | 41 | **42** |
| RAMB36E1 + RAMB18E1 | 96 + 5 | **96 + 8** |
| `clk_sys` maximum | 59.0 to 74.9 MHz (eight seeds) | 68.4, 65.2 and 71.2 MHz (seeds 1, 3 and 4; seed 2 had not finished routing after 25 minutes on a busy machine) |

The normal buffer is three RAMB18E1 (the 48-bit word is split in 18-bit slices) and the lighting unit's MAC is one DSP48E1. The reference adds about 380 yosys LUTs and **620 flip-flops**: the unit's copy of the matrix (192; only the nine 3 x 3 entries, 144, are needed), the light and ambient (64), and the normal, record and result in flight (about 180). An independent build of the chapter's text (3 x 3 copy only) came out at 6,674 yosys LUTs, 12,507 `SLICE_LUTX`, 5,201 flip-flops, 42 DSP48E1, 96 + 8 block RAMs and 56.4 to 68.5 MHz on four seeds (a fifth was still routing after 25 minutes on a busy machine, like the reference's seed 2). Sharing `vertex_unit`'s matrix registers would save about 150 flip-flops, 0.14 % of the device's 106,400, at the price of new ports on both units and a `tb_light_unit` that could no longer test the unit alone: not worth it. In neither build is the critical path in the lighting unit: depending on the seed it is in `tri_raster` or in the engine's command decoding, mostly routing.

## 10. Done when

`tb_light_unit`, `tb_light_sys`, `tb_light_budget` and the replacement `tb_gpu_fuzz` pass; `make frame-lit` matches all six frames; `ref_light.py --check` and `ref_lit.py --check` print their PASS lines; the older tests (`make test`, `make frame`) still pass with the edits of Section 9; `host/lit_demo.py --model` prints PASS (29 bytes in every frame); the full Arty build meets 50 MHz on `clk_sys`; and the three conditions of the Summary hold:

- **the MCU sends only a matrix per frame:** `SET_MATRIX` and `RUN_LIST`, 29 bytes, are all that `tb_light_budget` and `lit_demo.py` send per frame;
- **the model rotates at 30 frames per second or better:** `tb_light_budget` turns the torus through three different matrices at 30 frames per second or better (the reference renders a frame in 3.1 ms and runs at 60);
- **a simulated frame matches a Python reference renderer for the same model and matrix:** `make frame-lit`, six frames, zero differing pixels.

You should also be able to say why the light is set in camera space and turned into model space once per `TRANSFORM`, why a clamped dot product is added to the ambient and not the other way round, and why the cube has 24 vertices.

## 11. What you built, and where to go next

**The whole book in one paragraph.** You started with a blinking LED, a simulator and a UART (Phase 0), generated video timing and a frame-dump harness (Phase 1), and built games the hard way, in logic, on a tiny FPGA (Phase 2). On the Arty you brought up HDMI with a TMDS encoder of your own (Phase 3), then grew a **framebuffer GPU**: a command processor, rectangles, lines, triangles and a sprite blitter, double buffering with vsync (Phase 4). Phase 5 made it a **console** that a microcontroller can drive: an SPI link and a documented protocol, tile layers, a sprite table, a compositor, streamed maps and display lists, finished by a side-scroller that sends about 140 bytes a frame (at most about 320). Phase 6 added the third dimension: fixed-point math, a divider, attribute interpolation, a depth buffer, a vertex transform, culling and, here, lighting. Every step came with a bit-exact Python model and a test that fails when the hardware is wrong, and the frame-dump comparison that ties a whole picture to the model.

**Where to go next**, in roughly the order of payoff (the Summary's stretch goals), with what you can reuse:

- **Texture mapping.** Affine first: interpolate `u` and `v` the way you interpolate intensity (Chapter 6.3), keep a texture in the asset RAM of Chapter 4.8, and look the texel up per pixel. Perspective-correct texturing then needs a reciprocal per pixel, which is a use for Chapter 6.2's divider.
- **Near-plane clipping** instead of rejection: a triangle crossing the near plane becomes one or two new triangles with interpolated attributes; the places to change are the rejection step of `DRAW_MESH` and the vertex records, which would need room for the new vertices.
- **Formal verification** of the FIFO and the command parser with SymbiYosys: the parser's invariants (a packet is skipped whole, a frame start always begins a packet) are written down in `protocol.md`.
- **Per-line scroll registers** for raster effects: the tile layers of Chapter 5.3 already latch their scroll at blanking; latching it per line is a small table beside that.
- **Alpha blending and additive sprites:** the compositor of Chapter 5.5 chooses a winner per pixel; blending needs a read of the pixel below and a blend step.
- **A DDR3 framebuffer** for 640 x 480 at 16 bits per pixel, through the Zynq's processor side; that needs processor configuration first.

Smaller ideas that fall out of this chapter: a **spot or point light** (the direction becomes per vertex and needs the position in camera space, which `vertex_unit` already computes); **two lights** (a second pass that adds its term to byte 7 instead of replacing it); specular highlights per vertex (a power function: a small table); and a per-face normal computed on the GPU from the cross product, removing the normal buffer for flat models.

## Phase 6 conventions set here

- **Normals:** a parallel buffer of 1,024 x 48 bits in `cmd_engine`, slot *k* the normal (nx, ny, nz int16, Q2.14) of vertex *k*, model space, `UPLOAD_NORMALS` (`0x6B`, 2 + 6k, k <= 42, streamed, not in lists).
- **Light:** `SET_LIGHT` (`0x6C`, 9 bytes: flags bit 0 = on, lx, ly, lz, ambient; Q2.14), direction **in camera space towards the light**, power-up off. Takes effect at the next `TRANSFORM`. In lists: allowed.
- **Lighting arithmetic:** `l_m = M3^T L` once per `TRANSFORM` (mac, frac 14, saturating int16), `d = n . l_m`, `b = max(ambient + max(d, 0), 0)`, `i = min(round(b * 248 / 2^14), 255)` into **byte 7** of each record `TRANSFORM` writes; with lighting off byte 7 is 0. Bit-exact definition: `host/light_model.py`. Use matrices with scale 1.
- **Frame pattern:** `SET_MATRIX` + `RUN_LIST` of a list `TRANSFORM, CLEAR_ALL, DRAW_MESH, FLIP`: 29 bytes. Capability bit 14, caps `0x7FFF`; bit 15 is free.
- **Unknown opcodes the tests use:** `0x6D`-`0x6F` (`ref_light.py`; `0x6D` and `0x6F` also the fuzz), `0x75` and `0x7F` (`ref_vertex.py`, fuzz), `0x77` (fuzz), `0x7E` (`ref_shaded.py`, `ref_depth.py`, fuzz), `0x80` and up. A later chapter that takes one of these must move those tests. `0x76`, `0x78`-`0x7D` are not used by any test.
- **Cost:** lighting about 13 clocks per vertex over the vertex pipeline's 160.

## Testbench requirements

**Modules you provide.** `light_unit` in `rtl/common/light_unit.v` with the ports of Section 4; `cmd_engine` with the normal RAM, `UPLOAD_NORMALS` and `SET_LIGHT`, the lighting pass inside `TRANSFORM`, and the byte-7 write; `gpu_link` with caps `0x7FFF`. The unit contract: `i_mat_we` and `i_light_we` are one-clock strobes **while the unit is idle**, with junk on `i_matrix` and `i_light` between them; the matrix and the light are kept until the next strobe; `i_start` is a one-clock pulse with `i_first` and `i_count` valid in that clock only (junk after it); `o_busy` is high from within two clocks of `i_start` until `o_done`, `o_done` is one clock long and `o_busy` is low from the clock after it; the normal RAM and the record RAM answer one clock after an address (`i_nrm_data`, `i_rec_data`); `o_rec_we` with `o_rec_waddr` and `o_rec_wdata` writes the full 64-bit record (byte 7 replaced, bytes 6:0 as read) for each vertex of `first .. first + count - 1` and for no other, the last write no later than the clock of `o_done`; any latency, any order, any use of the MAC (the tb allows 600 clocks per vertex). A `count` of 0 finishes with `o_done` and no write. The engine contract is the protocol: `docs/protocol.md` section 19.

- **`tb_light_unit`** (from `build/sim`, `make test`): reads `../../sim/light_cases.hex` (64-bit words, layout in `ref_light.py`'s docstring); instantiates `light_unit` as `dut` with the port names of Section 3; models both RAMs; a few seconds.
- **`tb_light_sys`** (from `build/sim`, `make test`): `uart_gpu` at 2.5 Mbaud; reads `../../sim/light_sys_stream.hex` and `light_sys_expect.hex`; every reply byte is compared as it arrives; the reply count must match exactly. About 75 seconds alone.
- **`tb_frame_lit`** (from the repo root, `make frame-lit`): `gpu_link` and `fb_scanout` wired as in Chapter 5.7 with a 640 x 480 beam; reads `sim/lit_stream.hex` and `sim/lit_replies.hex`; writes `build/frame_lit_f0.ppm` to `f5.ppm`; the pixels are checked by `sim/py/ref_lit.py build` and `compare.py` (about six minutes).
- **`tb_light_budget`** (from `build/sim`, `make test`): `uart_gpu` at 2.5 Mbaud with a modelled 60 Hz display (`i_flip_ack` follows `o_flip_req` at the start of each blank); reads `../../sim/budget_setup.hex`, `budget_frames.hex` and `budget_expect.hex`; prints the measured clocks in its PASS line (about three minutes alone).
- **`tb_gpu_fuzz`** (from `build/sim`, `make test`, about 15 minutes): the Chapter 5.2 fuzz with the lighting commands added; capabilities `FF 7F`.

**Files from this chapter:** `tb_light_unit.v`, `tb_light_sys.v`, `tb_frame_lit.v`, `tb_light_budget.v` and `tb_gpu_fuzz.v` go to `sim/`; `ref_light.py`, `ref_lit.py` and `ref_fuzz.py` to `sim/py/`; `gpu_model.py`, `light_model.py`, `mesh_gen.py`, `lit_scene.py` and `lit_demo.py` to `host/`; `protocol.md` to `docs/`. Run `sim/py/ref_light.py --files sim`, `sim/py/ref_lit.py --files sim` and `sim/py/ref_fuzz.py sim` once and commit the files they write (`light_cases.hex`, `light_sys_stream.hex`, `light_sys_expect.hex`, `lit_stream.hex`, `lit_replies.hex`, `budget_setup.hex`, `budget_frames.hex`, `budget_expect.hex`, `fuzz_*`).
