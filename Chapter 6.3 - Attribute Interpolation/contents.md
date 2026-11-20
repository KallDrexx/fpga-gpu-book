# Chapter 6.3 - Attribute Interpolation

Chapter 4.6 decides which pixels a triangle covers. A 3D model needs more: every pixel also needs a brightness and a depth, and those are known only at the three corners. The rasterizer must **interpolate** them across the triangle.

This chapter extends `tri_raster` to do that and adds the first command of the 3D block, `DRAW_TRI_SHADED`. Each corner carries a 16-bit depth `z` and an 8-bit intensity `i`. The interpolated intensity picks one of 32 shades in a palette ramp (the Phase 6 plan: 8 ramps of 32 shades); the interpolated depth is computed and handed out for Chapter 6.4's depth buffer. The per-triangle setup uses Chapter 6.2's divider; the inner loop uses Chapter 4.6's idea of stepping by additions.

By the end of this chapter you will have:

- `rtl/common/tri_raster.v` with three depths and three intensities in, and a depth and a 5-bit shade out with every pixel,
- `rtl/common/cmd_engine.v` with `DRAW_TRI_SHADED` (`0x60`), and capability bit 10 set,
- `host/raster_model.py` and `host/gpu_model.py` replaced by this chapter's versions, with a bit-exact `shade_pixels()`,
- `sim/tb_tri_shade.v` passing: 3,100 triangles, 3.3 million pixels, every pixel's x, y, depth and shade equal to the model,
- `make frame-shaded` passing: four frames with a smooth gradient, a seamless quad and a display list of shaded triangles, identical to the model,
- a smooth gradient on the monitor (`host/shade_demo.py`).

## 1. Edge functions are barycentric coordinates

Chapter 4.6 noted that for any point P

    E01(P) + E12(P) + E20(P) = area2

where each E is twice the area of the small triangle that P forms with one edge. Divide by area2 and the three values become **barycentric coordinates**: weights that are 0 or more inside the triangle and add up to 1.

    w0 = E12(P) / area2     (big near v0, 0 on the opposite edge v1-v2)
    w1 = E20(P) / area2
    w2 = E01(P) / area2

At v0 the weights are (1, 0, 0); at the middle of edge v1-v2 they are (0, 1/2, 1/2). Any value known at the corners is interpolated by the same blend:

    A(P) = w0·A0 + w1·A1 + w2·A2

This is **linear interpolation across the triangle**: A is the plane through the three corner values. The rasterizer already computes the edge functions, so it already has the weights, apart from the division by area2.

## 2. Gradients: from weights to steps

Three multiplies and a divide per pixel would be far too expensive. But A is linear in x and y, so, as with the edge functions in Chapter 4.6, it changes by a constant amount per pixel step. Use w0 = 1 − w1 − w2 to start from corner 0:

    A(P) = A0 + ( E20(P)·(A1 − A0) + E01(P)·(A2 − A0) ) / area2

One step in x adds an edge's dy to its edge function, and one step in y subtracts its dx (Chapter 4.6). Edge 20 runs v2→v0, so its dy is y0 − y2 and its dx is x0 − x2; edge 01 has dy = y1 − y0 and dx = x1 − x0. So the **gradients** are:

    dA/dx = ( (y0 − y2)·(A1 − A0) + (y1 − y0)·(A2 − A0) ) / area2
    dA/dy = ( (x2 − x0)·(A1 − A0) + (x0 − x1)·(A2 − A0) ) / area2

Check with A = x: the numerator of dA/dx becomes (y0 − y2)(x1 − x0) + (y1 − y0)(x2 − x0), which is area2, so dA/dx = 1, and dA/dy is 0.

**Worked example.** The triangle (0, 0), (0, 4), (4, 0) has area2 = 16. Give its corners the intensities 0, 32 and 64. Then A1 − A0 = 32, A2 − A0 = 64, and

    dA/dx = ( 0·32 + 4·64 ) / 16 = 16
    dA/dy = ( 4·32 + 0·64 ) / 16 = 8

At (4, 0) the value is 0 + 4·16 = 64 and at (0, 4) it is 4·8 = 32, the corner values. At (1, 1) it is 24.

Each numerator is two multiplies and an add, once per triangle. The division by area2 is the expensive part: two gradients for each of two attributes is **four divisions per triangle**.

**What interpolates linearly on the screen.** Intensity does: that is Gouraud shading. Depth does if the corner values are screen-space depths, which Chapter 6.5 will deliver. Texture coordinates would need a divide per pixel (a stretch goal). In this chapter z is simply a number to interpolate.

## 3. Per-triangle setup

The setup is Chapter 4.6's, plus an attribute part:

| State | Does |
| --- | --- |
| `IDLE` | wait for `i_start`; latch corners, depths, intensities and `i_shaded` |
| `AREA` | area2 |
| `NORM` | area2 = 0 or empty box: finish. area2 < 0: swap v1 and v2 **with their z and i**, negate area2. Clamp the box |
| `COEF` | each edge's dx, dy and bias, and the attribute differences A1 − A0, A2 − A0. A flat triangle goes straight to `INIT` |
| `NUM` | the four numerators (z and i; x and y), from `COEF`'s dx and dy |
| `DIV` | four divisions by area2 |
| `START` | the value at the box corner (Section 5) |
| `INIT`, `SCAN`, `FIN` | as in Chapter 4.6, with the attributes stepping beside the edge values |

The most natural bug is to swap the corners but not their attributes: every clockwise triangle then gets its depths and intensities on the wrong corners.

**Dividers.** Each division is a Chapter 6.2 `seq_divider`, about 50 clocks. How many to build is a speed-for-area choice:

| Choice | Setup, clocks | Cost |
| --- | --- | --- |
| One divider, four divisions in a row | about 220 | one divider |
| **Two, one per attribute (reference)** | about 116 | two dividers, a few hundred LUTs each |
| Four, one per gradient | about 65 | four dividers |
| Divide the next triangle while this one draws | hidden | a second set of triangle registers and a hand-over |

The reference runs the z divider and the intensity divider side by side, each doing x then y. For a 200-triangle model that is 200 × 116 = 23,200 clocks, 0.46 ms at 50 MHz, against about 2 ms of pixels and a 33 ms frame. That is affordable, and Chapter 6.4's throughput counter will show whether overlapping setups is ever worth building. A flat triangle (`DRAW_TRI`) skips the attribute states and takes no longer than before.

## 4. Formats and precision

**Formats.**

- **Depth** `z`: unsigned 16 bits, 0 to 65,535. **Smaller is nearer**: 0 is the nearest depth and 65,535 the farthest, the value Chapter 6.4 will clear the depth buffer to.
- **Intensity** `i`: 8 bits, Q5.3, so 0 to 31.875 shades; 8 is one shade and **248 is full brightness** (shade 31). The three fraction bits let the corners sit between shades, which keeps slow gradients smooth.
- **Gradients and accumulators:** 16 fraction bits (`FRAC = 16`), in the unit of the attribute (z steps, Q5.3 steps). Every division rounds half up, as everywhere in Phase 6.

**Widths of the numerators and of area2.** Coordinate differences and depth differences are each 17-bit signed numbers (up to ±65,535), so a depth numerator, the sum of two such products, is below 2·65,535² ≈ 2^33: **34 signed bits**. An intensity numerator (differences up to ±255) needs 26. area2 is positive after the swap and below 2^32 (Chapter 4.6), so a 34-bit signed divisor holds it.

**The quotient must never saturate.** A gradient is a ratio, and a thin triangle makes it huge: area2 can be 1 while the numerator is 2^33, so a z gradient can be 2^33 z steps per pixel, which with 16 fraction bits is a 50-bit number. Chapter 6.2 left the widths to this chapter and gave `#(36, 34, 16, 34)` as an example; its 34-bit quotient saturates on such slivers, the pixels drawn from a clamped gradient are wrong, and the testbench catches it. So this chapter makes the quotients wide enough never to saturate:

| Lane | Divider `#(NUM_W, DEN_W, FRAC, Q_W)` | Clocks (reference) | Accumulator |
| --- | --- | --- | --- |
| z | `#(34, 34, 16, 50)` | 52 | 34 bits |
| i | `#(26, 34, 16, 42)` | 44 | 26 bits |

`raster_model.max_gradient_bits()` computes these widths. `o_ovf` is never set, and the Python model's `rdiv()` is Chapter 6.2's `div_ref()` without the clamp.

**The accumulators can be narrower than the gradients.** At a covered pixel the true value is a blend of the corner values, so it lies between 0 and 65,535, give or take the small drift described below. With 16 fraction bits, a sign bit and one guard bit, **34 bits** hold every such value; the intensity needs **26**. Values outside the triangle can be enormous: the value at the box corner of a sliver whose corners are 30,000 pixels away, for instance. That does not matter, because two's complement addition and multiplication are exact **modulo 2^34**: every add and multiply keeps only the low 34 bits, so the low 34 bits of the result are always right. A value that fits in 34 bits is fully determined by its low 34 bits, so every covered pixel comes out exactly right even if the walk passed through values that wrapped. Only the low 34 (or 26) bits of a quotient are kept, too. A 32-bit z accumulator, by contrast, wraps real depths above 32,767, and the testbench catches it.

**Why the hardware equals the model exactly.** The Python model is not the ideal real-number plane; it is the integer arithmetic of the hardware:

    gx, gy = the rounded gradients
    start  = A0·2^16 + gx·(xmin − x0) + gy·(ymin − y0)        at the box corner (xmin, ymin)
    value  = start + gx·(x − xmin) + gy·(y − ymin)            at pixel (x, y)

The hardware's additions compute the same integers (modulo 2^34, which as just shown does not matter), so `tb_tri_shade` compares for equality with no tolerance. A pixel that differs from the model is a bug, not a rounding difference. Note that the plane is anchored at **vertex 0**, which the swap never moves; anchoring at another corner gives slightly different roundings.

**How close to the ideal.** The only error is in the gradients: each is rounded to 2^-16, an error of at most 2^-17 of an output step per pixel step, and it grows with the distance from vertex 0. For corners on the screen that distance is at most 319 + 239 = 558 steps, so the value drifts by at most 558·2^-17 ≈ 0.004 of a step (the model's self-check measures 0.0025). For corners at the int16 limits, up to about 66,000 steps away, the drift stays below half a step. The final rounding to a whole z step or shade adds up to another half step.

**Why not a reciprocal.** Multiplying by a precomputed 1/area2 has a fixed absolute precision, so a large triangle (area2 up to 2^32) gets almost no relative precision from a Q2.32 reciprocal (Chapter 6.2). One rounded division per gradient is within 2^-17 of the true gradient whatever the size.

## 5. The inner loop

Next to the three edge values, the loop keeps an accumulator A and a row-start copy R for each attribute:

```
A = R = start                           (the value at the box corner (xmin, ymin))
for y in ymin..ymax:
    for x in xmin..xmax:
        if all three edges pass: emit (x, y, round(A))
        edges += their dy;   A += gx
    R += gy;  A = R                     (the edges restart the same way, as in 4.6)
```

**One add per pixel** per attribute, plus one per row. The start value is the attribute at the corner of the clamped box, not at a triangle corner, for the same reason the edge values start there: the box was clamped to the screen. It costs two multiplies per attribute, `gx·(xmin − x0)` and `gy·(ymin − y0)`, added to `A0·2^16`.

A walk that reverses direction on alternate rows (serpentine) subtracts gx on the way back and adds gy at the end of the row instead of keeping R. It visits the same pixels with the same values; the testbench accepts any order.

## 6. Outputs: shade, color and depth

Every covered pixel leaves with two rounded values:

    z     = clamp( (A_z + 2^15) >> 16, 0, 65535 )
    shade = clamp( (A_i + 2^18) >> 19, 0, 31 )         16 fraction bits plus the 3 of Q5.3

The shift is arithmetic, so this is round half up. The clamps matter at the ends of a range: a corner intensity of 31.875 (`i` = 255) rounds to 32 and must become 31, and a value that drifts to −0.004 must give 0, not wrap to 31. Every intensity from 244 (30.5 shades) up gives shade 31.

**Color.** `DRAW_TRI_SHADED` writes `{ramp[2:0], shade[4:0]}`, that is `ramp·32 + shade`. **Ramp** r is palette entries 32r to 32r + 31, which the host fills with `SET_PALETTE`, dark (shade 0) to bright (shade 31). Eight ramps use the whole palette; the palette is shared with the tile layers and sprites, so a host that needs other colors too uses fewer ramps. `ramp_palette(r, dark, light)` in the model builds the 32 packets of one ramp.

**Feeding it from lighting (Chapter 6.7).** A brightness b computed in Q2.14 (1.0 = `0x4000`) becomes an intensity with one multiply: `i = clamp(round(b · 248 / 2^14), 0, 255)`, so full light is exactly shade 31.

**Depth.** `o_z` is valid with `o_valid` like `o_x` and `o_y`. Nothing uses it yet: until Chapter 6.4 the command engine leaves it unconnected, triangles overwrite each other in drawing order, and synthesis removes the depth lane (Section 11).

## 7. The extended `tri_raster`

```verilog
module tri_raster #(
    parameter SCREEN_W = 320, SCREEN_H = 240,
    parameter SHADE    = 0                      // 1: build the attribute hardware
) (
    input  wire               i_clk, i_start,
    input  wire signed [15:0] i_x0, i_y0, i_x1, i_y1, i_x2, i_y2,
    input  wire               i_shaded,         // 1: interpolate; 0: a flat triangle, as in 4.6
    input  wire        [15:0] i_z0, i_z1, i_z2,
    input  wire         [7:0] i_i0, i_i1, i_i2, // Q5.3
    output wire               o_busy,
    output reg                o_valid,
    output reg         [15:0] o_x, o_y,
    output reg         [15:0] o_z,              // valid with o_valid
    output reg          [4:0] o_i,              // the shade, 0 to 31
    output reg                o_done
);
```

The handshake and the pixel set are Chapter 4.6's; shading only adds the two values. All inputs are latched at `i_start`.

- `SHADE = 0` is the 4.6 module: the new inputs are ignored, so Chapter 4.6's `tb_tri_raster` still passes with the new ports left open (iverilog warns that they float; that is harmless). To let synthesis drop the attribute hardware in that case, put it inside `generate if (SHADE != 0)`.
- With `SHADE = 1`, `i_shaded = 0` is a flat triangle: the 4.6 pixels and setup time, and `o_z`, `o_i` are don't-cares. `i_shaded = 1` adds the setup of Section 3.
- The dividers use no DSP slices; the numerator and start multiplies do. Registers that feed a multiplier start at 0 (Chapter 6.1).

## 8. The command

| Opcode | Command | Length | Payload |
| --- | --- | --- | --- |
| `0x60` | `DRAW_TRI_SHADED` | 22 | x0, y0, x1, y1, x2, y2 (signed 16-bit), ramp, z0, z1, z2 (unsigned 16-bit), i0, i1, i2 (Q5.3) |

It is `DRAW_TRI` with the color byte read as a ramp (bits 7:3 ignored) and the attributes appended: the six coordinates and the ramp sit at the same offsets as in `DRAW_TRI`, so the rasterizer's corner inputs stay wired to the same argument bytes; the depths are payload bytes 13-18 and the intensities 19-21. A length other than 22 is skipped and sets `BAD_LEN`. The gradient triangle of the frame test, byte by byte:

    60 16  1E 00 14 00  96 00 3C 00  3C 00 C8 00  02  E8 03  30 75  60 EA  00 7C F8

Engine changes:

- the argument buffer grows from 16 to 32 bytes,
- `0x60` with length 22 starts the rasterizer like `0x08` does, remembering that the triangle is shaded and its ramp; `DRAW_TRI` clears the shaded flag,
- for a shaded triangle each pixel is written with `{ramp[2:0], o_i}` instead of the fixed color,
- `tri_raster` gets `SHADE = 1` and its depth and intensity inputs.

Everything else is Chapter 4.7's: the engine fetches no bytes while the triangle runs, a reset does not abandon it, the draw goes to the back buffer.

**Display lists.** A shaded triangle has no reply and is not an upload, so a list may hold it (24 bytes each, about 20 in a 512-byte list), and `WRITE_LIST_WORD` can patch a corner's intensity in place. The frame test does both.

**Capability bit 10** (`3D` in `spi_info.py`) is set now: `GET_INFO` from `gpu_link` reports `47 01 FF 07 ...`; `uart_gpu` keeps `0F 00`. As in Phase 5, a bit is set by the chapter whose commands it announces: bit 10 means `DRAW_TRI_SHADED` exists. A later Phase 6 chapter whose commands a host must be able to detect (the depth buffer, the vertex transform) takes the next free bit, 11 to 15.

`docs/protocol.md` gets the table row, a new section 15, the `BAD_LEN` and display-list notes and the capability changes; the replacement file is in this chapter.

## 9. The Python model and the unit test

`host/raster_model.py` keeps everything from Chapter 4.6 unchanged and adds:

- `attr_gradients()`, `attr_start()`, `z_out()`, `i_out()`: the formulas of Sections 2 to 6,
- `shade_pixels(v0, v1, v2, w, h)`, vertices `(x, y, z, i)`: the covered pixels as `(x, y, z, shade)` in raster order. **This is the definition.**
- `shade_pixels_stepped()`: the same by additions in 34- and 26-bit wrapping accumulators; the self-check shows the two agree, including for corners at the int16 limits,
- `shade_signature()`: a count and two hashes, which the testbench compares,
- `interp_error()`, `max_gradient_bits()`: the numbers of Section 4.

```sh
.venv/bin/python host/raster_model.py
```

    shading: gradient drift on screen at most 0.0025 z steps, 0.0020 intensity steps (40 random triangles)
    widths: z numerator 34 bits, gradient 50 bits; i numerator 26 bits, gradient 42 bits (signed)
    raster_model: self-check OK

`host/gpu_model.py` adds `DRAW_TRI_SHADED`, `draw_tri_shaded()`, `ramp_palette()` and caps `0x07FF`; run it to self-check.

`sim/py/ref_interp.py sim` writes `sim/interp_small.hex` (3,000 triangles for a 64x64 frame) and `sim/interp_big.hex` (100 for 320x240). Each line holds the corners with their depths and intensities, a shaded/flat flag, the pixel count and two hashes. The groups aim at specific failures: directed cases and extreme attributes (0 and 65,535; 0 and 255), clockwise copies, random and off-frame corners, **thin slivers** (huge gradients), equal attributes (zero gradients), degenerate triangles, **corners at the int16 limits**, whole-screen triangles, and a few flat ones that must keep the 4.6 setup time.

```sh
.venv/bin/python sim/py/ref_interp.py sim        # once; commit both files
make test TESTS="tb_tri_shade"
```

    PASS: tb_tri_shade (3011 shaded + 89 flat triangles, 3336256 pixels matched in x, y, z and intensity, clocks beyond one per box pixel: at most 116 shaded, 7 flat; worst triangle 76918 clocks)

It takes about two minutes. On a failure the testbench prints the first bad triangle's line and its first pixels; `ref_interp.py --show N [--big]` prints what they should be. What failures usually mean:

- **Every shaded triangle wrong, shapes right:** a gradient or start-value formula (a sign, a missing term, dx and dy swapped).
- **Only clockwise triangles:** the attributes were not swapped with the corners.
- **First row right, later rows wrong:** the per-row gy step.
- **Off by one shade here and there:** rounding (truncation instead of half up) or the wrong number of fraction bits.
- **Only large depths or far corners:** a width (a 32-bit z accumulator, a narrow numerator).
- **Only thin slivers:** a saturating gradient.

## 10. The frame test and the board

`tb_frame_shaded.v` (Chapter 5.7's `tb_frame_display_lists.v` without the HUD bookkeeping) plays `sim/shaded_stream.hex` into `gpu_link` over SPI and dumps four frames:

- **f0:** eight palette ramps, then eleven shaded triangles: a big one with corners off the screen; on top of it a triangle with intensities 0, 15.5 and 31 shades, **the smooth gradient**; a quad of two triangles sharing an edge and its corner values (no seam may show); a constant-intensity one; another winding; a sliver; a degenerate one; one with intensities past 31 (clamped); one with ramp bits 7:3 set; one with equal depths.
- **f1:** a display list holding a `CLEAR`, two shaded triangles and a `FLIP`, run once.
- **f2:** the same list after `WRITE_LIST_WORD` swapped two corner intensities.
- **f3:** a real shaded triangle one byte short (`BAD_LEN`, must draw nothing), an unknown opcode `0x61`, a small valid triangle and a `FLIP`: anything the bad packet drew would show.

```sh
.venv/bin/python sim/py/ref_shaded.py --stream sim     # once; commit sim/shaded_stream.hex and shaded_replies.hex
make frame-shaded
```

    PASS: tb_frame_shaded (latency 7 clocks, 619 SPI frames, 1962 bytes; build/frame_shaded_f0 to f3 .ppm written)
    PASS: compare shaded (4 frames match exactly)

The testbench checks the framing, the syncs and the two `GET_INFO` replies (capabilities `07FF`; flags `00`, then `06`); `compare.py` checks the pixels against `ref_shaded.py`, which draws every frame with `host/gpu_model.py`. About 3 minutes.

**On the board.** Build and program as in Chapter 5.7, then:

```sh
.venv/bin/python host/shade_demo.py --model      # no board: the host side against the model
.venv/bin/python host/shade_demo.py
```

It loads the ramps, draws a gradient triangle and a few others, compares `FB_CRC` with the model and flips; then for ten seconds it redraws a quad whose corner intensities follow a moving light, one frame per VSYNC. The green triangle should run smoothly from dark to bright: with 32 shades you may see faint steps, but no jumps, and no seam between the quad's two halves.

## 11. Older files and resources

- **Replace** `host/gpu_model.py` and `host/raster_model.py`; add `host/shade_demo.py`.
- **Fuzz:** replace `sim/tb_gpu_fuzz.v` (it expects caps `FF 07`) and `sim/py/ref_fuzz.py` (shaded triangles among the valid packets and in lists, and their wrong lengths); run `ref_fuzz.py sim` to rewrite the `fuzz_*` files.
- **Capabilities in older frame tests:** change `0xFF, 0x03, 0x40` to `0xFF, 0x07, 0x40` in `sim/py/ref_layers.py` (1 place), `ref_sprite_table.py` (1), `ref_compositor.py` (1), `ref_streaming.py` (2) and `ref_display_lists.py` (3), and `FF 03 40 01` to `FF 07 40 01` in the failure messages of `sim/tb_frame_layers.v`, `tb_frame_sprite_table.v`, `tb_frame_compositor.v`, `tb_frame_streaming.v` and `tb_frame_display_lists.v`. Regenerate with `--stream sim` for those five scripts and for `ref_replay.py` (no edit there: its replies come from the model); only the `*_replies.hex` files change.
- **Tie-offs:** none. `tb_tri_raster` leaves the new ports open (floating-input warnings only); the command engine is the only user of `SHADE = 1`.
- **Makefile:** `FRAME_TESTS += shaded`; `ref_interp.py` and `ref_shaded.py` go to `sim/py/`.

**Resources.** One run of the full Arty design (`yosys synth_xilinx`, `nextpnr-xilinx`):

| | Chapter 5.7 design | with shaded triangles |
| --- | --- | --- |
| LUTs | 7,408 | 8,112 (9,273 with the depth lane kept) |
| Flip-flops | 2,384 | 2,577 (3,016) |
| DSP48E1 | 20 | 28 (36) |
| RAMB36E1 + RAMB18E1 | 55 + 2 | 55 + 2 |
| `clk_sys` maximum | 60.7 MHz | 58.6 MHz (with the depth lane) |

Because `o_z` has no user yet, synthesis deletes the whole depth lane; the numbers in brackets come from a build that folds `o_z` into the pixel color so that it survives, as it will in Chapter 6.4. Each lane costs 8 DSP slices: four numerator products (17x17, or 17x9) and two start-value products of a 34- or 26-bit gradient by a 17-bit distance, which take two DSP slices each because they are wider than the DSP's 25x18 multiplier. That is a lot of multipliers for work done once per triangle while the dividers take 50 clocks anyway: one multiplier stepping through the eight numerator products and the four start products (two passes each for the wide ones) would add about 20 clocks to a setup of about 116. Chapter 6.4, which needs the depth lane, is the place to decide. Runs differ by a few MHz, and the margin over 50 MHz stays thin; earlier chapters found the critical path in `tri_raster`. The 57 block RAMs leave room for the 6.4 depth buffer (38 RAMB36).

**Done when:** `tb_tri_shade` and `make frame-shaded` pass, Chapter 4.6 to 5.8's tests still pass, `shade_demo.py` shows a smooth gradient on the monitor, and you can derive the two gradient formulas and explain why the hardware equals the model although neither equals the ideal.

## Phase 6 conventions set here

Later chapters use these without restating them.

- **Depth:** unsigned 16-bit, smaller is nearer; 65,535 is the farthest value (the depth-buffer clear value in 6.4). `tri_raster` delivers it as `o_z` with each pixel.
- **Intensity:** 8-bit Q5.3 per vertex; full brightness 248 = shade 31. Pixel shade = round half up of i/8, clamped to 0-31. A Q2.14 brightness b maps to `i = clamp(round(b·248/2^14), 0, 255)`.
- **Color of a shaded pixel:** `(ramp & 7)·32 + shade`; ramp r = palette entries 32r to 32r + 31, dark to bright.
- **Interpolation:** per attribute, gradients `dA/dx`, `dA/dy` (Section 2) each rounded half up to 16 fraction bits by one exact (never saturating) division by area2; the plane anchored at vertex 0 after the swap; start value at the clamped box corner; z accumulators 34 bits, intensity 26, wrapping. `raster_model.shade_pixels()` is the definition.
- **Module:** `tri_raster #(SCREEN_W, SCREEN_H, SHADE)` with the ports of Section 7; dividers `seq_divider #(34, 34, 16, 50)` (z) and `#(26, 34, 16, 42)` (i) in the reference.
- **Protocol:** `DRAW_TRI_SHADED` = `0x60`, length 22, allowed in display lists; `0x61`-`0x6F` free for the rest of Phase 6. Capability bit 10 = shaded triangles; later Phase 6 features that a host must detect take bits 11-15. Caps are now `0x07FF`.

## Testbench requirements

**Modules you provide:** `tri_raster #(SCREEN_W = 320, SCREEN_H = 240, SHADE = 0)` in `rtl/common/tri_raster.v` with the ports of Section 7 (the testbench uses `SHADE = 1` at 64x64 and at 320x240); `cmd_engine` with `DRAW_TRI_SHADED`; any divider inside (`seq_divider` in the reference).

- **Handshake and pixels:** as in Chapter 4.6: `o_busy` high the clock after `i_start` and low with the one-clock `o_done`; `o_valid` low with `o_done` and on the clock after; all inputs latched at `i_start`; no pixel twice, none outside the clamped box; the set of `(x, y)` is `raster_model.tri_pixels(...)`, shaded or flat.
- **Attributes:** for `i_shaded = 1`, every pixel's `o_z` and `o_i` equal `raster_model.shade_pixels(...)` exactly, and are never X with `o_valid`. Not checked for flat triangles.
- **Time:** a flat triangle takes at most `box pixels + 2·rows + 60` clocks; any triangle at most `2·(box pixels) + 1,000`, so any of the divider choices of Section 3 passes. The box is the clamped bounding box.
- **Command:** `DRAW_TRI_SHADED` with 22 payload bytes draws `(ramp & 7)·32 + shade` at the pixels of `gpu_model`'s `shade_pixels`; any other length sets `BAD_LEN` and draws nothing; `GET_INFO` from `gpu_link` reports capabilities `07FF`.

**Files from this chapter:** `tb_tri_shade.v`, `tb_frame_shaded.v` and `tb_gpu_fuzz.v` go to `sim/`; `ref_interp.py`, `ref_shaded.py` and `ref_fuzz.py` to `sim/py/`; `raster_model.py`, `gpu_model.py` and `shade_demo.py` to `host/`; `protocol.md` to `docs/`. Run `sim/py/ref_interp.py sim`, `sim/py/ref_shaded.py --stream sim` and `sim/py/ref_fuzz.py sim` once and commit the files they write.

**Testbenches:** `sim/tb_tri_shade.v` (top `tb_tri_shade`) runs from `build/sim/` with `make test`, about 2 minutes: 50 MHz clock, drives and samples on the falling edge, reads `../../sim/interp_small.hex` and `interp_big.hex` (`+dir=PATH/` for another folder), stops after 20 bad triangles. `sim/tb_frame_shaded.v` (top `tb_frame_shaded`) runs with `make frame-shaded` from the repo root, about 3 minutes: four 640x480 frames from `gpu_link` and `fb_scanout` wired as in `tb_frame_display_lists`. Both print one `PASS` or `FAIL` line.
