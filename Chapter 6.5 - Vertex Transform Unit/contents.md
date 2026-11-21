# Chapter 6.5 - Vertex Transform Unit

Until now the host has sent the GPU **screen** coordinates: Chapter 6.3's `DRAW_TRI_SHADED` takes pixel positions, a depth and an intensity per corner. A rotating model then costs the microcontroller a rotation, a perspective divide and a screen mapping for every vertex, every frame, and it sends 24 bytes per triangle. The plan of Phase 6 is the opposite: the mesh goes to the GPU **once**, in model coordinates, and each frame the host sends one small matrix. The GPU does the arithmetic.

This chapter builds the first half of that: a **vertex buffer** that the host fills, a **transform pipeline** that multiplies each vertex by a 3 x 4 matrix, divides by depth for perspective and maps the result to the 320 x 240 screen, and a **transformed-vertex buffer** that holds the results. Chapter 6.6 will fetch from that buffer to draw triangles. Here the host can also read it back, which is how the pipeline is tested to the last bit.

By the end of this chapter you will have:

- a `vertex_unit` that reuses Chapter 6.1's `mac_unit` for the matrix multiply and Chapter 6.2's `seq_divider` for the perspective divide,
- five commands, `UPLOAD_VERTICES`, `SET_MATRIX`, `SET_PROJECTION`, `TRANSFORM` and `READ_VERTEX` (`0x70`-`0x74`), and capability bit 12 (`GET_INFO` now says `FF 1F`),
- `host/vertex_model.py`, an exact integer model of the pipeline, and `sim/py/ref_vertex.py`, which checks it against Chapters 6.1 and 6.2 and against floating-point numpy, to within 1 pixel, in stated ranges,
- `tb_vertex_unit` passing (about 4,000 vertices under random matrices, bit for bit), `tb_vertex_sys` passing (1,000 random vertices through the serial line and back), and `tb_gpu_fuzz` passing with the new commands,
- `host/gpu_model.py` and `docs/protocol.md` (a new section 17) updated, and the older tests still passing with the edits of Section 8.

## 1. What the pipeline computes

A vertex enters in **model space**: x, y, z, signed 16-bit integers. Three steps take it to the screen.

| Step | Computes | Hardware |
| --- | --- | --- |
| 1. Camera space | `c = M v + t`: three 4-term sums | one `mac_unit`, 12 beats |
| 2. Near test | is the vertex in front of the near plane? | a comparator |
| 3. Projection | `sx = 160 + f x_c / z_c`, `sy = 120 - f y_c / z_c`, and a depth from `1 / z_c` | one multiplier, one `seq_divider` used three times |

**Camera space** is what the camera sees: x to the right, y **up**, z **forward** (so larger z is farther away). `M` is the 3 x 3 part of the matrix (rotation and a uniform scale) and `t` the translation, so moving or turning the model, or the camera, is a different matrix and nothing else. A 3 x 4 matrix is 12 numbers.

**Projection** is similar triangles. A point at (x, y) at distance z, seen through a window at distance f, appears at (f x / z, f y / z): twice as far means half as big. The divide by z is the whole of perspective. The screen has its origin at the top left and y growing down, so `sy` subtracts, and the centre of the 320 x 240 screen, (160, 120), is where the camera's axis lands.

**The near plane.** Dividing by a z that is zero or negative has no meaning: a vertex behind the camera would be drawn upside down and a vertex at the camera's z would divide by zero. A vertex whose camera-space z is at most `near` is flagged **NEAR** and gets no position. Chapter 6.6 will reject triangles that use such a vertex.

## 2. Formats, and the choices behind them

These are the decisions that make the pipeline bit-exact and cheap. The model in `host/vertex_model.py` is their specification, and `docs/protocol.md` section 17 states them as rules.

**Camera coordinates: Q.6, 25 bits.** A Q2.14 matrix entry times an int16 coordinate has 14 fraction bits (Chapter 6.1's Q18.14). Rounding the sum straight to an integer (the `mac_unit` defaults, `FRAC = 14`, `OUT_W = 16`) loses too much, because perspective **magnifies** errors: half a unit of error in `x_c` becomes `f / (2 z_c)` pixels, 4.6 pixels for `z_c` = 30 units and f = 277. So the unit instantiates

    mac_unit #(.A_W(16), .B_W(16), .ACC_W(48), .FRAC(8), .OUT_W(25))

which rounds the sum to **6 fraction bits** (64 = one model unit). Why 25 bits: the next step multiplies `f x_c`, and a DSP48E1 multiplies 25 by 18 bits, so a 25-bit `x_c` and a 16-bit `f` fit one DSP. Why 6 fraction bits: that is what 25 bits leave after the integer part. With every matrix entry, coordinate and translation at its largest magnitude (2.0 and 32,768), `|c| <= 3 x 2 x 32,768 + 32,768 = 229,376` units, 18 bits plus a sign; at 6 fraction bits that fits in 25 bits, so **the camera coordinate can never saturate**, for any matrix. (A too-narrow `OUT_W` shows up only for large, odd matrices; the test has some.)

The translation joins the sum as a fourth product `t x 0x4000`, as Chapter 6.1 planned (`0x4000` is 1.0 in Q2.14): 12 beats of one `mac_unit` for the three coordinates. The product `f x_c` that follows is a plain `*` (one more DSP) rather than a second `mac_unit`: it has no fraction bits to drop, and the `mac_unit` rounding constant 2^(FRAC-1) assumes FRAC >= 1.

**The near test is on z, and nothing else.** `NEAR` is `z_c <= near x 64` (`near` in model units, unsigned 16-bit, compared in Q.6). Two tempting shortcuts are wrong. The divider's `o_ovf` flag does not catch a vertex *behind* the camera: it divides without overflow and gives an ordinary, mirrored result. And testing only `z_c <= 0` leaves the band `0 < z_c <= near`, where the depth would be 0 or negative. Every vertex that is not `NEAR` has `z_c > near x 64 >= 0`, so no division ever meets a divisor of zero or less and `o_ovf` is never needed. For a `NEAR` vertex the position is **undefined** (the reference writes zeros): only the flag counts, and the tests do not look at the other fields. `near` is a register because it depends on the scene's scale, and it also sets the depth scale (below).

**Focal length: f = 277 by default, set with `SET_PROJECTION`.** `f` is the distance, in pixels, from the eye to the screen, and it fixes the field of view: a horizontal field of 60 degrees across 320 pixels puts the screen edge at 30 degrees, so `f = 160 / tan 30 degrees = 277` (the vertical field is then 47 degrees). It is a signed 16-bit register, 277 at power-up; a longer lens is a larger `f`.

**The divider: `seq_divider #(41, 25, 0, 17)`.** Chapter 6.2 left the sizing to this chapter; here is the reasoning.

| Parameter | Value | Why |
| --- | --- | --- |
| `NUM_W` | 41 | the numerator is `f x_c`, a 16-bit by 25-bit product: 40 bits and a sign |
| `DEN_W` | 25 | the divisor is `z_c`, 25 bits (always positive here) |
| `FRAC` | 0 | the rasterizer of Chapter 4.6 takes whole-pixel corners, so the quotient is rounded to a pixel straight away; the Q.6 scales of numerator and divisor cancel |
| `Q_W` | 17 | anything beyond about 32,768 pixels is clamped anyway, and the depth quotient (below) is at most 65,535: 17 signed bits cover both |

One division takes `NUM_W + 2` = 43 clocks in the reference, rounded half up as everywhere in Phase 6. This configuration is not one of `tb_seq_divider`'s; `tb_vertex_unit` exercises it, about 8,000 divisions through the unit, which is enough for a parameterized divider that already passed Chapter 6.2's five configurations.

**Depth: `65535 (1 - near / z_c)`.** Chapter 6.3 interpolates depth linearly across a triangle **on the screen**, so the depth must be a quantity that is linear in screen position. z is not: divide the plane equation `a x + b y + c z = d` by `z`, substitute `x / z = (sx - 160) / f` and `y / z = (120 - sy) / f`, and `d / z` comes out as a linear function of `sx` and `sy`. So the depth must be an affine function of **1 / z_c**, and the pipeline uses

    depth = 65535 - round(65535 x near / z_c)

which is 0 at the near plane and rises towards 65,535 (Chapter 6.4's clear value) as `z_c` goes to infinity: smaller is nearer, as Chapters 6.3 and 6.4 expect. Using z directly would save the third division and be wrong: two triangles that cross in space would cross on the screen in the wrong place.

The price is resolution far away. The depth changes by `65535 near / z_c^2` per unit of z: with `near = 8`, 52 levels per unit at z = 100 but half a level at z = 1,000. So choose `near` as large as the scene allows (just less than the distance to the nearest thing) and keep the scene's depth range modest. There is no far plane: mapping a far plane to 65,535 would replace the constants 65,535 and `65535 near` by two host-chosen ones, the same single division.

**Screen mapping and clamping.** `sx = 160 + qx` and `sy = 120 - qy`, both clamped to the signed 16-bit range, since Chapter 6.3's packet takes int16 corners. A vertex just in front of the near plane and far off to one side projects to a huge `sx`; clamping keeps it a legal number. The record's **CLAMP** flag marks such a vertex: it is not where the geometry puts it, so a triangle using it is distorted, and Chapter 6.6 can choose to reject it.

**Triangle orientation.** A triangle whose corners run counter-clockwise on the monitor has **positive** `area2` in Chapter 4.6's convention (`raster_model.area2`), a clockwise one negative. Chapter 6.6 fixes which is the front.

**The record: 8 bytes.** Each transformed vertex is `sx` (int16), `sy` (int16), `depth` (uint16), `flags` (uint8) and a reserved byte that is 0 for now (Chapter 6.7 may put the vertex's intensity there): 64 bits, one RAM word and one `READ_VERTEX` reply, short enough for the SPI link's 16-byte reply queue.

| Flag | Bit | Meaning |
| --- | --- | --- |
| `NEAR` | 0 | `z_c <= near`: no position; reject triangles that use it |
| `CLAMP` | 1 | `sx` or `sy` was clamped to the int16 range |

**A worked example.** The power-up state (identity, f = 277, near = 1) and the vertex (50, -20, 300):

- camera: `x_c = 50 x 64 = 3,200`, `y_c = -1,280`, `z_c = 19,200` (Q.6); not NEAR, since 19,200 > 64;
- `qx = round(277 x 3,200 / 19,200) = round(46.17) = 46`, `qy = round(277 x -1,280 / 19,200) = round(-18.47) = -18`;
- `sx = 160 + 46 = 206`, `sy = 120 - (-18) = 138`;
- `qd = round(65,535 x 64 / 19,200) = round(218.45) = 218`, `depth = 65,535 - 218 = 65,317`.

`tb_vertex_sys` checks this vertex among others; `.venv/bin/python host/gpu_model.py` runs a self-check with a similar one.

## 3. The pipeline and what is shared

Per vertex the reference unit does, in order: read the vertex (3 clocks), 12 multiply-accumulate beats plus the `mac_unit` latency (about 16 clocks), the near test (1 clock), then three times a multiply (a few clocks) and a division (43 clocks), and the write.

    read -> [mac: x_c, y_c, z_c] -> near? --yes--> write NEAR record
                                      |no
                       f*x_c / z_c -> f*y_c / z_c -> 65535*near / z_c -> write

That is **about 165 clocks per vertex** (NEAR vertices skip the divides). A full buffer of 1,024 vertices takes about 168,000 clocks, **3.4 ms at 50 MHz**, 10% of a 33 ms frame; a 150-vertex model takes half a millisecond. Where the time goes, and what a faster unit would do:

- **One `mac_unit` serves all twelve products**, one beat per clock: 12 clocks against 4 with three units, which would cost 2 more DSP slices. At 12 of 165 clocks it is not where the time is.
- **One divider serves all three divisions**, 130 of the 165 clocks. It costs no DSP slice and about 200 LUTs, so a design that wants speed would use **three dividers and multipliers in parallel** (about 60 clocks per vertex, 1.2 ms for 1,024, about 450 more LUTs).
- **Overlap.** The matrix multiply of the next vertex could run while the divider works on this one, hiding about 20 clocks (12%). The reference does not.

The tests accept all of these: they wait for `o_done` and allow a loose 800 clocks per vertex. The DSP count is 2 (the `mac_unit` and one shared multiplier for `f x_c`, `f y_c`); write the two products as two `*` and yosys builds two. The rule here: **size the sharing to the frame budget, not to the part.** A typical 200-triangle model has fewer than 300 vertices, so the reference favours small.

**The unit's interface.**

```
module vertex_unit (
    input  wire         i_clk,
    input  wire         i_start,  input wire [9:0] i_first,  input wire [10:0] i_count,  // transform i_first .. i_first+i_count-1
    output wire         o_busy,   output reg o_done,                                       // o_done: one clock, after the last write
    input  wire         i_mat_we, input wire [191:0] i_matrix,                             // 12 x 16 bits, entry k at [16k +: 16]
    input  wire         i_proj_we, input wire signed [15:0] i_f, input wire [15:0] i_near,
    output reg  [9:0]   o_src_addr, input wire [47:0] i_src_data,                          // vertex buffer: {z, y, x}, one clock of latency
    output reg          o_dst_we,  output reg [9:0] o_dst_addr, output reg [63:0] o_dst_data // transformed-vertex buffer
);
```

The unit owns the matrix and the two projection registers (identity, 277 and 1 at power-up), loaded by one-clock strobes while it is idle, so the testbench can test them without the command parser; `i_matrix`, `i_f` and `i_near` carry junk at all other times. Entry k of `i_matrix` is the k-th 16-bit field of the `SET_MATRIX` payload, so the 24 little-endian argument bytes wire straight onto the bus. The engine connects the buffers' RAMs, starts the unit only while it is idle, and guarantees `first + count <= 1024`.

## 4. The memory

| Buffer | Slots | Word | RAM | Written by | Read by |
| --- | --- | --- | --- | --- | --- |
| vertex buffer | 1,024 | 48 bits: x, y, z | 6 KB | `UPLOAD_VERTICES` (the command engine) | the unit, one read per vertex |
| transformed-vertex buffer | 1,024 | 64 bits: the record | 8 KB | the unit | `READ_VERTEX` now; Chapter 6.6's mesh drawing next |

Each is a simple dual-port RAM (one write port, one read port, one clock), inferred as block RAM. Neither needs an `initial` loop: the protocol leaves their power-up contents undefined (a transformed slot is defined only after a `TRANSFORM` has covered a vertex slot that an upload wrote), and the tests read nothing else. The transformed-vertex buffer's read port is shared by `READ_VERTEX` and, in Chapter 6.6, by the mesh drawing: a small mux in the engine.

## 5. The commands

| Opcode | Command | Length | Payload |
| --- | --- | --- | --- |
| `0x70` | `UPLOAD_VERTICES` | 2 + 6k | first slot (16-bit), then k vertices of x, y, z (int16 each), k = 1 to 42 |
| `0x71` | `SET_MATRIX` | 24 | `m00 m01 m02 m10 m11 m12 m20 m21 m22` (int16, Q2.14), `tx ty tz` (int16 model units) |
| `0x72` | `SET_PROJECTION` | 4 | `f` (int16, pixels), `near` (uint16, model units) |
| `0x73` | `TRANSFORM` | 4 | first slot (16-bit), count (16-bit) |
| `0x74` | `READ_VERTEX` | 2 | slot (16-bit); reply: the 8-byte record |

All 16-bit fields are little endian. `0x75`-`0x7F` stay unknown. A wrong length is skipped whole and sets `BAD_LEN`.

**`UPLOAD_VERTICES`** is streamed, like `UPLOAD_TILES`: each vertex is written when its sixth byte arrives, so a packet cut by a frame start keeps its whole vertices and sets `TRUNC`. The length byte is checked before any data arrives: a length that is not 2 + 6k (a 7, a 9, a 13) is `BAD_LEN` and writes **nothing**. Slots of 1,024 and up are dropped without a flag and do not wrap: four vertices at slot 1,022 write two. A 255-byte payload holds at most 42 vertices, so the tests' 1,024-vertex mesh is 25 packets.

**`SET_MATRIX`**: 24 bytes, not the "about 48" of the Phase 6 overview, which assumed 32-bit entries. The matrix is used by later `TRANSFORM`s; changing it changes nothing already transformed. A rotation times a uniform scale below 2 is the intended use, but any 12 numbers are legal (the camera coordinates cannot overflow, Section 2).

**`TRANSFORM`** processes the slots `first .. first + count - 1`, clipped at slot 1,023. A `count` of 0, or a `first` of 1,024 or more, does nothing and is not an error. While it runs the engine fetches no byte, as for a triangle: a reset does not abandon it, and the commands behind it wait in the FIFO. It is a separate command, not part of the upload, because the mesh is uploaded **once** and the matrix changes every frame: each frame is `SET_MATRIX`, `TRANSFORM`, draw.

**`READ_VERTEX`** is for debugging, for tests, and for hosts that do their own culling. A slot of 1,024 or more makes no reply (and no flag); a host that reads such a slot must not wait for one.

**Display lists** (Chapter 5.7): `SET_MATRIX`, `SET_PROJECTION` and `TRANSFORM` have no reply and change no big memory, so they are **allowed** in a list: a frame list can transform its own mesh and draw it. `UPLOAD_VERTICES` is a long upload (the rule that kept `UPLOAD_ASSET` and `UPLOAD_TILES` out) and `READ_VERTEX` makes a reply (the rule that kept `GET_STATS` out): both are **forbidden**, skipped whole with `BAD_OP` when the list runs.

**Capability bit 12** (`VERTEX`): `GET_INFO` from `gpu_link` now reports `47 01 FF 1F ...`; `uart_gpu` still reports `0F 00`.

**Engine changes**, all inside `cmd_engine`: the two RAMs and the `vertex_unit`; an upload branch in the byte parser (a 5-byte buffer, a byte position and a slot counter); for `SET_MATRIX` and `SET_PROJECTION` a strobe with the argument bytes as `i_matrix` or `i_f`/`i_near`; for `TRANSFORM` the clipped count, a start pulse and a wait state; for `READ_VERTEX` a one-clock RAM read and an 8-byte reply; the two forbidden opcodes in the list check; and the five length rules. `gpu_link` changes only its capability constant; no module's ports change.

## 6. The Python model, and the 1-pixel claim

`host/vertex_model.py` implements the three steps with exact integers; `host/gpu_model.py` imports it and adds the five commands (builders `upload_vertices()`, `vertex_packets()`, `set_matrix()`, `set_projection()`, `transform()`, `read_vertex()`, `parse_vertex()`; attributes `vsrc`, `vdst`, `matrix`, `proj_f`, `proj_near`). It knows which transformed slots are defined and counts reads of any other in `read_undefined`, which the test generators keep at 0.

`sim/py/ref_vertex.py` makes two checks and writes the testbench files:

1. **Against the earlier chapters.** On 20,000 random vertices under random matrices (7,200 of them NEAR), the camera step must equal `ref_mac.mac_ref(..., frac=8, out_w=25)` and each divide `ref_divider.div_ref(..., 0, 17)`: the pipeline is built from what you tested in 6.1 and 6.2.
2. **Against floating point.** Random vertices through numpy in floating point (`sx = 160 + f x_c / z_c` with no rounding anywhere) must land within **1 pixel** in x and y.

The 1-pixel claim holds only in a stated range. Besides the final rounding (0.5 pixel), the error comes from rounding each camera coordinate to 1/64 unit (at most 1/128), which the projection scales by `f / z_c` for `x_c` and by `P / z_c` for `z_c`:

    |error| <= 0.5 + (f + P) / (128 z_c) pixels

where `P` is the pixel offset from the screen centre: at most 0.75 pixel in range A below. Small `z_c` is the danger, so the claim has ranges, and two versions:

| | Claim A: same matrix | Claim B: true rotation |
| --- | --- | --- |
| float reference uses | the Q2.14 matrix as stored | the exact rotation, before rounding to Q2.14 |
| tests | the arithmetic of the pipeline | the whole thing, including the matrix's rounding |
| `f` | 64 to 1,024 | 64 to 400 |
| `z_c` | 64 units or more | 128 units or more |
| position | within 1,024 pixels of the centre | within 512 pixels of the centre |
| model coordinates | any int16 | at most 512 in each of x, y, z |
| result | at most 0.52 px in 6,000 vertices | at most 0.51 px in 6,000 vertices |

Claim B needs a smaller range because **rounding a rotation to Q2.14 moves a camera coordinate by up to `3 |v| / 2^15` units**: a tenth of a unit for coordinates in the thousands, which is visible at a small `z_c`. Outside the ranges the error is unbounded (the script prints the worst it sees with `z_c` down to 2 units: about 19 pixels). The script draws random cases and skips those outside the range, so the ranges are what it actually checks. The depth is checked too, within `1 + 512 near / z_c^2` levels (the same camera rounding seen through `65535 near / z_c`). The lessons are Chapter 6.1's: keep the fraction bits until the last rounding, and state the range in which a claim holds.

```sh
.venv/bin/python sim/py/ref_vertex.py sim          # writes sim/vertex_*.hex and runs the checks (a few seconds)
.venv/bin/python sim/py/ref_vertex.py --check      # only the checks
make test TESTS="tb_vertex_unit tb_vertex_sys tb_gpu_fuzz"
```

## 7. The tests

**`tb_vertex_unit`** (about 5 seconds): the unit alone, with two RAM models and a script of 86 runs: the power-up matrix, 40 random view matrices of 25 vertices each, 1,000 vertices under one matrix, 30 runs with wild matrices and projections (clamping, saturation, negative `f`, `near` of 0 and 65,535), the near plane to the bit (`z_c` = `near`, `near + 1`), the largest camera coordinates, a count of 0, a count of 1, the last slot and all 1,024 slots. 3,989 vertices (1,321 of them NEAR, 161 CLAMP) are compared bit for bit. It also checks the contract: `o_busy` high the clock after a start and low after `o_done`; one `o_done` per start; no write outside the run or after `o_done`; matrix and projection taken only on their strobes (the inputs carry junk otherwise); and at most 800 clocks per vertex.

    PASS: tb_vertex_unit (86 runs, 3989 vertices bit-exact, 1321 of them NEAR; 119 clocks per vertex on average, 168 at most)

**`tb_vertex_sys`** (one to two minutes): `uart_gpu` over a serial line at 2.5 Mbaud, no framebuffer. The stream uploads 1,024 random vertices in scrambled packets, applies 20 random matrices to 50 vertices each and reads **every one of the 1,000 back** (1,109 replies in all), then the end of the buffer (slots past 1,023 dropped, `TRANSFORM` clipped, a read of slot 1,024 with no reply), every wrong length (`BAD_LEN` each time and nothing changed), a display list with the three allowed commands and the two forbidden ones, the near plane to the bit, and saturation, `CLAMP`, `near` of 0 and a negative `f`. Each reply byte is checked as it arrives against the model; a NEAR vertex's position bytes are not compared.

    PASS: tb_vertex_sys (12229 command bytes, 9088 reply bytes, 56 fences, all as the model says)

**`tb_gpu_fuzz`** (about 10 minutes, as in Chapter 6.4; the vertex commands add only a few milliseconds of simulated work) is replaced: caps `FF 1F`, the vertex commands among the valid packets, in lists and with wrong lengths, vertices read back, an upload cut inside a vertex, and garbage that happens to contain `READ_VERTEX` made harmless (it becomes a `NOP`: the slot may be undefined).

What failures usually mean:

| Symptom | Likely cause |
| --- | --- |
| `tb_vertex_unit`: thousands of vertices wrong | the matrix transposed, or used from `i_matrix` instead of the stored copy; the translation not scaled by `0x4000`; `x`, `y`, `z` out of order; the vertex taken from the RAM a clock early; `sy` added instead of subtracted; the divider truncating; depth from z instead of `1 / z` |
| `tb_vertex_unit`: about 120 vertices wrong, NEAR expected | the near test is `z_c <= 0` (or `o_ovf`) instead of `z_c <= near` |
| `tb_vertex_unit`: 20 to 40 vertices wrong | `z_c < near` instead of `<=`; the camera sum truncated instead of rounded |
| `tb_vertex_unit`: about 160 vertices wrong | no clamp to int16, or no `CLAMP` flag |
| `tb_vertex_unit`: 1 to 3 vertices wrong, huge coordinates | `OUT_W` too narrow: the camera coordinate saturates |
| `tb_vertex_unit`: write outside the run | an extra write after the last vertex, or `TRANSFORM` of `count` 0 writing |
| `tb_vertex_sys`: thousands of reply bytes wrong | `UPLOAD_VERTICES` byte order; `SET_PROJECTION` fields swapped |
| `tb_vertex_sys`: a few to a few dozen bytes wrong | slots past 1,023 wrapping instead of dropped; `TRANSFORM` not clipped; a bad length streamed or executed anyway (shows in `GET_INFO`) |
| `tb_vertex_sys`: extra reply bytes | `READ_VERTEX` of slot 1,024 answers, or is allowed in a list |
| `tb_gpu_fuzz` fails in a "wrong length ... alone" round | a vertex command with the wrong length sets no `BAD_LEN` |

## 8. Older files and resources

- **Replace** `host/gpu_model.py`; add `host/vertex_model.py`. `host/raster_model.py` is unchanged. `gpu_model.py` imports `vertex_model`.
- **Fuzz:** replace `sim/tb_gpu_fuzz.v` (it expects caps `FF 1F`) and `sim/py/ref_fuzz.py` (which imports `rnd_matrix` from `ref_vertex.py`), then run `ref_fuzz.py sim`.
- **Capabilities:** in `rtl/common/gpu_link.v` change `.CAPS(16'h0FFF)` to `.CAPS(16'h1FFF)`. In `host/spi_info.py` append `"VERTEX"` to the `CAPS` list.
- **Older frame tests:** change `0xFF, 0x0F, 0x40` to `0xFF, 0x1F, 0x40` in `sim/py/ref_layers.py` (1 place), `ref_sprite_table.py` (1), `ref_compositor.py` (1), `ref_streaming.py` (2), `ref_display_lists.py` (3), `ref_shaded.py` (2) and **`ref_depth.py` (2)**, and `FF 0F 40 01` to `FF 1F 40 01` in the failure messages of `sim/tb_frame_layers.v`, `tb_frame_sprite_table.v`, `tb_frame_compositor.v`, `tb_frame_streaming.v` (2), `tb_frame_display_lists.v`, `tb_frame_shaded.v` (2) and `tb_frame_depth.v` (2), and `0FFF` to `1FFF` in the header comments of `tb_frame_shaded.v` and `tb_frame_depth.v` and in the docstring of `ref_depth.py`.
- **Regenerate** with `--stream sim` for `ref_layers`, `ref_sprite_table`, `ref_compositor`, `ref_streaming`, `ref_display_lists`, `ref_shaded`, `ref_depth` and with `ref_replay.py --stream sim` (no edit): only the `*_replies.hex` files change (eight of them), nothing else.
- **No opcode of an older test moves:** the unknown opcodes the older tests use (`0x66`, `0x7E`, `0x77`, `0x7F`, `0x80` and up) are still unknown; Chapter 6.5 takes only `0x70`-`0x74`.
- **Tie-offs:** none. No port of any module changes.
- **Makefile:** nothing: the new tests are unit tests (`make test TESTS=...`, from `build/sim`); `ref_vertex.py` goes to `sim/py/`.

**Resources.** Two builds of the full Arty design, from two independent designs that follow this chapter (`yosys synth_xilinx -flatten -abc9 -arch xc7`, `nextpnr-xilinx`), against the Chapter 6.4 build in the same flow:

| | Chapter 6.4 | with the vertex transform |
| --- | --- | --- |
| LUTs (`SLICE_LUTX`, nextpnr, as in earlier chapters) | 10,002 | 11,641 to 11,663 |
| LUTs (yosys `stat`, LUT1-LUT6 cells) | 5,204 | 5,893 |
| Flip-flops | 3,292 | 4,119 to 4,121 |
| DSP48E1 | 36 | **38** or 39 |
| RAMB36E1 + RAMB18E1 | 93 + 2 | **95 + 5** |
| `clk_sys` maximum | 61.2 MHz | 56.2 to 58.8 MHz |

The vertex buffer (1,024 x 48 bits) and the transformed-vertex buffer (1,024 x 64 bits) take the 2 extra RAMB36 and 3 extra RAMB18: 97.5 of 140 RAMB36-equivalents are now in use. The new DSP slices are the `mac_unit` and the `f x_c` multiplier (two of them if `f x_c` and `f y_c` are separate products). In yosys's count the design grows by about 690 LUTs; `vertex_unit` alone is about 580, of which the divider is about 210 and the 12-way matrix multiplexer only about 70, so keeping the matrix in a small RAM would save little. (nextpnr's `SLICE_LUTX` figure, which the earlier chapters quote, counts LUT sites and grows by more than twice as much.) The `clk_sys` critical path (inspected in one of the builds) runs from `tri_raster`'s vertex registers through its bounding-box min/max and compares (Chapters 4.6 and 6.3), not through the vertex unit; the maximum varies by a few MHz between runs, and the margin over 50 MHz stays thin, as in Chapters 6.3 and 6.4.

## 9. Done when

`tb_vertex_unit`, `tb_vertex_sys` and the replacement `tb_gpu_fuzz` pass; `ref_vertex.py` prints its PASS line (the model equals Chapters 6.1 and 6.2, and stays within 1 pixel of floating point in the stated ranges); the older tests (`make test`, `make frame`) still pass with the edits of Section 8; and you can say why the camera coordinates keep six fraction bits, why the near test is on z, and why the depth is `1 / z`.

## Phase 6 conventions set here

Later chapters use these without restating them.

- **Camera space:** x right, y up, z forward; model vertices and translations int16, matrix Q2.14 (`0x4000` = 1.0), row-major `m00 .. m22, tx, ty, tz`, 24 bytes. Camera coordinates Q.6, 25 bits, round half up, never saturate.
- **Projection:** `sx = 160 + f x_c / z_c`, `sy = 120 - f y_c / z_c`, rounded and clamped to int16 (`CLAMP`); `depth = 65535 - round(65535 near / z_c)`, 0 at the near plane, smaller nearer. `f` signed 16-bit (277 at power-up), `near` unsigned 16-bit in model units (1 at power-up). Divider `seq_divider #(41, 25, 0, 17)`.
- **Orientation:** counter-clockwise on the monitor is positive `area2` (Chapter 4.6's `raster_model.area2`).
- **`NEAR`:** `z_c <= near`, tested on z. The position fields of a `NEAR` record are undefined; Chapter 6.6 rejects any triangle with a `NEAR` vertex and may reject `CLAMP` ones.
- **Buffers:** 1,024 vertex slots (6 bytes) and 1,024 transformed-vertex slots, 8-byte records `sx lo, sx hi, sy lo, sy hi, depth lo, depth hi, flags, 0`; byte 7 is reserved (0) and free for Chapter 6.7 (for example the vertex intensity). Both RAMs are inside `cmd_engine`, 48 and 64 bits wide; the transformed buffer has one read port, shared by `READ_VERTEX` and, from Chapter 6.6, the mesh drawing.
- **Commands:** `0x70` `UPLOAD_VERTICES`, `0x71` `SET_MATRIX`, `0x72` `SET_PROJECTION`, `0x73` `TRANSFORM`, `0x74` `READ_VERTEX`; `0x75`-`0x7F` free. In lists: `0x71`-`0x73` allowed, `0x70` and `0x74` forbidden (`BAD_OP`). Capability bit 12; caps `0x1FFF`; bits 13-15 free.
- **Cost:** about 165 clocks per vertex in the reference (3.4 ms for 1,024 at 50 MHz); `TRANSFORM` blocks the engine like a triangle. Opcodes `0x66`-`0x6F` are still free for 3D (Chapters 6.6 and 6.7); `ref_shaded.py` uses `0x66` as its unknown opcode, so the chapter that takes `0x66` must move it.

## Testbench requirements

**Modules you provide.** `vertex_unit` with the ports of Section 3 in `rtl/common/vertex_unit.v`, and `cmd_engine` with the five commands, built on your `mac_unit` (Chapter 6.1) and `seq_divider` (Chapter 6.2); `gpu_link` with caps `0x1FFF`. The unit contract: `i_start` is pulsed only while `o_busy` is low; `o_busy` is high in the clock after `i_start` (when `i_count` > 0) and low in the clock after `o_done`; the unit reads `o_src_addr` and gets the vertex `{z, y, x}` (16 bits each, x in the low bits) one clock later; for each vertex in `i_first .. i_first + i_count - 1` it writes the record `{8'h00, flags, depth, sy, sx}` at `o_dst_addr` (once or more), only inside that range and only between the start and `o_done`; `o_done` is one clock, after the last write; `i_count` = 0 writes nothing and still gives `o_done`; `i_mat_we` loads `i_matrix` (entry k at `[16k +: 16]`) and `i_proj_we` loads `i_f` and `i_near`, pulsed only while idle, and these inputs carry junk at all other times; the power-up state is the identity matrix, f = 277, near = 1. Any latency and issue order is accepted. For a NEAR vertex only the flags and the zero byte are compared.

- **`tb_vertex_unit`** (from `build/sim`, `make test`): reads `../../sim/vertex_cases.hex` and `vertex_exp.hex`; instantiates `vertex_unit` (port names as in Section 3) with two RAM models; compares every record bit for bit (NEAR ones on flags only); limits 800 clocks per vertex plus 200 per run.
- **`tb_vertex_sys`** (from `build/sim`, `make test`): `uart_gpu` at 2.5 Mbaud; reads `../../sim/vertex_stream.hex` and `vertex_expect.hex`; every reply byte is compared as it arrives; the reply count must match exactly (a `READ_VERTEX` of slot 1,024 or more makes no reply). It takes one to two minutes.
- **`tb_gpu_fuzz`** (from `build/sim`, `make test`, about 10 minutes): the Chapter 5.2 fuzz with the vertex commands added; capabilities `FF 1F`.

**Files from this chapter:** `tb_vertex_unit.v`, `tb_vertex_sys.v` and `tb_gpu_fuzz.v` go to `sim/`; `ref_vertex.py` and `ref_fuzz.py` to `sim/py/`; `gpu_model.py` and `vertex_model.py` to `host/`; `protocol.md` to `docs/`. Run `sim/py/ref_vertex.py sim` and `sim/py/ref_fuzz.py sim` once and commit the files they write (`vertex_cases.hex`, `vertex_exp.hex`, `vertex_stream.hex`, `vertex_expect.hex`, `fuzz_*`).
