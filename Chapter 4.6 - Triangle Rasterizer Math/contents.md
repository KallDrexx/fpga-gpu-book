# Chapter 4.6 - Triangle Rasterizer Math

A rectangle's pixels come from two counters, and a line's from one error term. A triangle, the shape every 3D model is built from, needs a little geometry: three **edge functions**, one per side, whose signs say whether a pixel is inside. This chapter builds that math and a module, `tri_raster`, that turns three corners into a stream of covered pixels. It runs in simulation only; Chapter 4.7 connects it to the framebuffer as `DRAW_TRI`.

Finding the inside is the easy part. The hard part is deciding **exactly** which pixels on the border belong to which triangle. Two triangles that share an edge must not both draw a pixel on it, and must not both skip it, or every mesh gets seams or gaps. The **top-left fill rule** settles it, and the tests check it hard.

By the end of this chapter you will have:

- `rtl/common/tri_raster.v`, which streams the covered pixels of one triangle,
- `host/raster_model.py`, the Python model, with a self-check,
- `sim/py/ref_tri.py`, which writes `sim/tri_vectors.hex` and `sim/tri_grid.hex`,
- `sim/tb_tri_raster.v` passing: 10,000 random triangles match the model, and grids of triangles that share edges cover every pixel exactly once.

## 1. The edge function

For a directed edge from (x0, y0) to (x1, y1), with dx = x1 − x0 and dy = y1 − y0, define

    E(x, y) = (x - x0)·dy - (y - y0)·dx

This is a cross product: |E| is the distance from (x, y) to the line through the edge, times the edge's length. Only its sign matters here. E = 0 on the line, E > 0 on one side, E < 0 on the other. **Reverse the edge and E changes sign at every point.** That fact is what lets two neighboring triangles disagree about the pixels on their shared edge.

**Area and winding.** A triangle v0, v1, v2 has three directed edges, v0→v1, v1→v2 and v2→v0. The first edge's function at the third corner is twice the triangle's signed area:

    area2 = E01(v2) = (x2 - x0)(y1 - y0) - (y2 - y0)(x1 - x0)

For the corners (0, 0), (0, 4), (4, 0):

    area2 = (4 - 0)(4 - 0) - (0 - 0)(0 - 0) = 16     (the area is 8)

List the same corners as (0, 0), (4, 0), (0, 4) and area2 = −16. The sign gives the **winding**: with y pointing down, the first order runs counter-clockwise on the monitor and the second clockwise. Call the area2 > 0 order *positive*. area2 = 0 means the corners lie on one line (or coincide): a **degenerate** triangle with no inside.

**The three edges add up to the area.** For any point P, E01(P) + E12(P) + E20(P) = area2. Each E is twice the area of the small triangle that P forms with that edge, and the three small triangles make up the whole. Phase 6 divides these three values by area2 to get the weights for blending shades and depths across the triangle.

## 2. Inside

For a positive triangle, a point is inside when all three edge functions are positive: it is on the inner side of every edge. You can check this for (1, 1) in the example: E01 = 4, E12 = 8, E20 = 4, which add to 16.

For a negative triangle every sign flips. The simplest way to accept both windings is to **swap v1 and v2 when area2 < 0**. That reverses the winding and keeps the same triangle, so everything after the swap sees only positive triangles and the fill rule has to be written once. A degenerate triangle (area2 = 0) draws nothing; check for it before the swap.

## 3. Sample points

A pixel is a square, and the test needs one point to stand for it. GPUs use the pixel center, (x + 0.5, y + 0.5), which needs half-pixel arithmetic. This book samples pixel (x, y) at the **integer point (x, y)**, and the corners are integers on the same grid, like `FILL_RECT`'s and `DRAW_LINE`'s. Everything stays in plain integers.

The price is that many pixels lie exactly on an edge (E = 0), so the tie rule decides a lot of pixels. The example triangle (0, 0), (0, 4), (4, 0) has area 8 but covers 10 pixels: those on its top and left edges count, those on its slanted edge don't.

## 4. The top-left fill rule

A pixel with all three E > 0 is inside. For a pixel exactly on an edge (E = 0), the rule is:

**A pixel on an edge is covered only if that edge is a top edge or a left edge of the triangle.**

For a positive triangle with y pointing down, the edge's direction tells you which kind it is. One step in +x changes E by dy, one step in +y by −dx, and the inside is where E grows:

- **Left edge:** dy > 0. E grows toward +x, so the inside is to the right of the edge.
- **Top edge:** dy = 0 and dx < 0. A horizontal edge with the inside below it (−dx > 0).
- Any other edge (dy < 0, or dy = 0 and dx > 0) is a right or bottom edge.

**Why each shared pixel is drawn once.** Two positive triangles on opposite sides of an edge run along it in opposite directions. Reversing an edge flips the signs of dx and dy, so exactly one of the two directions counts as top-left, and only that triangle takes the pixels on the edge.

**Worked example.** Split the square from (0, 0) to (4, 4) along its diagonal into A = (0, 0), (0, 4), (4, 0) and B = (4, 0), (0, 4), (4, 4); both are positive (area2 = 16).

| Edge | dx, dy | Kind |
| --- | --- | --- |
| A: (0, 0)→(0, 4) | 0, 4 | left |
| A: (0, 4)→(4, 0) | 4, −4 | right/bottom |
| A: (4, 0)→(0, 0) | −4, 0 | top |
| B: (4, 0)→(0, 4) | −4, 4 | left |
| B: (0, 4)→(4, 4) | 4, 0 | bottom |
| B: (4, 4)→(4, 0) | 0, −4 | right |

The diagonal pixels (3, 1), (2, 2) and (1, 3) have E = 0 on the shared edge. For A that edge is right/bottom, for B it is left, so B covers them. A covers 10 pixels, among them its top row (0, 0) to (3, 0) and its left column down to (0, 3). B covers the other 6, and together they fill the 4x4 pixels (0, 0) to (3, 3) exactly once. B's bottom and right edges, including the corners (4, 0) and (0, 4), are left to the squares next door.

**The bias trick.** The test for one edge is "E > 0, or E = 0 on a top-left edge". For integers, E > 0 is the same as E − 1 ≥ 0. So give each edge a **bias**, 0 for a top-left edge and −1 for any other, and use one test everywhere:

    covered  =  E01 + bias01 >= 0  and  E12 + bias12 >= 0  and  E20 + bias20 >= 0

The hardware never compares with zero separately. Getting the bias wrong is the classic bug:

- bias 0 everywhere (`>= 0`) draws shared-edge pixels twice,
- bias −1 everywhere (`> 0`) leaves gaps, and thin triangles vanish,
- top-left classified for the wrong winding or with y pointing up gives double hits and gaps on slanted edges.

A correct rule also rejects every pixel of a degenerate triangle by itself (its three E values add to zero, and its edges run both ways along one line). The explicit area2 = 0 check is still worth its one compare: it skips the scan and doesn't depend on the bias being right.

## 5. Bounding-box traversal

A triangle can only cover pixels inside its **bounding box**: x from the smallest to the largest corner x, and likewise for y, both ends **inclusive**. Visit each pixel of the box, row by row, and emit those that pass.

Clamp the box to the frame first: 0 ≤ x ≤ SCREEN_W − 1, 0 ≤ y ≤ SCREEN_H − 1. A partly off-screen triangle then costs only its visible part, and a huge one never loops over 65,536 columns. If the clamped box is empty (xmin > xmax or ymin > ymax), nothing is drawn. Clamping is exact, because the edge functions alone decide coverage; but the starting E values must be computed at the clamped corner (xmin, ymin), not at a triangle corner.

A box's area is at least twice the triangle's, so half or more of the tests are wasted. That is fine for this book: at one test per clock, a full-screen box takes 1.5 ms, the same as a `CLEAR`.

## 6. Incremental stepping

Two multiplications per edge per pixel would be slow and large. But E changes by a constant per step, because x appears only in (x − x0)·dy and y only in −(y − y0)·dx:

    E(x + 1, y) = E(x, y) + dy
    E(x, y + 1) = E(x, y) - dx

So only setup multiplies. Compute the three E values at (xmin, ymin), add the biases once (a constant offset stays put under additions), and from then on only add:

```
E[i] = edge_i(xmin, ymin) + bias[i]          three running values
R[i] = E[i]                                  three row-start values
for y in ymin..ymax:
    for x in xmin..xmax:
        if E[0] >= 0 and E[1] >= 0 and E[2] >= 0: emit(x, y)
        E[i] += dy[i]                        for all i
    R[i] -= dx[i]; E[i] = R[i]
```

Any order that visits every box pixel once is fine: columns instead of rows, or a serpentine that reverses direction on alternate rows and needs no row-start copy (at a row end subtract dx to step down, then subtract dy for each step to the left).

## 7. Bit widths

Corners are signed 16-bit (−32768 to 32767), so `DRAW_TRI` can take a triangle much bigger than the screen, and the widths must allow it.

- **Differences** such as x1 − x0 range over ±65,535: **17 signed bits**. Sign-extend before subtracting, or 32767 − (−32768) wraps to −1. The same holds for (x − x0) with x in the box.
- **E and area2.** Each product of two differences is below 2^32, and E is a difference of two products. `raster_model.max_abs_edge()` finds the exact worst cases for int16 corners and a 320x240 frame: max |E| = 2,168,356,545 and max |area2| = 4,294,836,225. Both need **33 signed bits**; 34 gives margin. (The running values also step one pixel past the box; that still fits in 33.)
- A 32-bit area2 or E only fails for corners near the int16 limits, so small random tests won't catch it. About 700 of the test triangles have such corners; a 32-bit area2 fails dozens of them and a 32-bit E a few.

Declare all of these `signed`, and remember Chapter 4.3's trap: one unsigned operand makes the whole expression unsigned. A 1-bit bias flag subtracted from a signed E does exactly that, so write it as `$signed({1'b0, flag})` or store the bias as a signed constant.

A 17x17 signed multiply fits one DSP48E1 (25x18). Setup runs once per triangle, so it may take several clocks and share one multiplier.

## 8. The `tri_raster` module

```verilog
module tri_raster #(
    parameter SCREEN_W = 320,
    parameter SCREEN_H = 240
) (
    input  wire               i_clk,
    input  wire               i_start,
    input  wire signed [15:0] i_x0, i_y0, i_x1, i_y1, i_x2, i_y2,
    output wire               o_busy,
    output reg                o_valid,
    output reg         [15:0] o_x, o_y,
    output reg                o_done
);
```

The handshake:

- **Start.** While `o_busy` is low, a one-clock `i_start` latches the six corners. The inputs may change right after, so latch them.
- **Busy.** `o_busy` goes high the clock after `i_start` and is low again by the time `o_done` pulses. `i_start` while busy is ignored.
- **Pixels.** Each covered pixel appears once as a one-clock `o_valid` with `o_x` and `o_y`, always inside the frame. Any order, any rate, any latency, but all before `o_done`.
- **Done.** `o_done` is a one-clock pulse after the last pixel. A degenerate or off-screen triangle gives no pixels, just busy and then done. The next `i_start` may come the clock after `o_done`.

There is no back-pressure input: Chapter 4.7 writes one pixel per clock, so nothing needs to stall the module.

A state machine that follows the chapter:

| State | Does |
| --- | --- |
| `IDLE` | wait for `i_start`; latch the corners |
| `AREA` | area2 = E01(v2) |
| `NORM` | area2 == 0: finish. area2 < 0: swap v1 and v2. Clamp the box; empty box: finish |
| `COEF` | dx, dy and the bias of each edge |
| `INIT` | E at (xmin, ymin) plus bias for each edge; copy to the row-start values |
| `SCAN` | each clock: test the pixel and emit it; step x, or at the end of a row step y; after the last pixel go to `FIN` |
| `FIN` | pulse `o_done`, back to `IDLE` |

A box of N pixels then takes N clocks plus a few for setup. Written this way, with the setup multiplies as plain combinational products, the module synthesizes (openXC7 yosys `synth_xilinx`, the module alone, not placed) to about 1,200 LUTs, 600 flip-flops and 8 DSP48E1. A serial setup would be much smaller.

## 9. The Python model and the test vectors

`host/raster_model.py` defines the pixels; this chapter's tests import it, and so will Chapter 4.7's. Its main functions:

- `edge(p0, p1, x, y)`, `area2(v0, v1, v2)`: Section 1.
- `normalize(v0, v1, v2)`: the corners reordered to positive area, or `None` for zero area.
- `is_top_left(p0, p1)`, `edges(...)`: the fill rule and each edge's bias.
- `bbox(tri, w, h)`: the clamped box, or `None` if empty.
- `tri_pixels(v0, v1, v2, w, h)`: the covered pixels in raster order, testing every box pixel the slow way. This is the definition.
- `tri_pixels_stepped(...)`: Section 6's loop, which must give the same list.
- `signature(pixels, w)`: pixel count and an order-independent hash, which is what the testbench compares.
- `max_abs_edge()`: the worst cases behind Section 7.

A vertex is any tuple that starts with x and y; extra entries ride along through `normalize()`, ready for Phase 6's shading and depth. Python integers don't overflow, so the model is right for every input. Run the self-check:

```sh
.venv/bin/python host/raster_model.py
```

    raster_model: self-check OK
      screen 320x240, vertices int16: max |E| = 2168356545 (33 signed bits), max |area2| = 4294836225 (33 signed bits)

`ref_tri.py` writes the vectors for a 64x64 frame. Each of the 10,000 triangles gets its corner order rotated at random and its winding flipped half the time, and the groups aim at specific rules:

- directed cases, including the worked examples,
- random corners from −64 to 127, so many triangles are partly or fully off the frame, plus triangles entirely inside,
- small triangles and thin slivers, whose pixels almost all lie on edges,
- right triangles with horizontal and vertical edges in all four orientations, and exact 45-degree edges,
- degenerate ones: three equal corners, two equal, zero width, zero height, and collinear on a slope,
- about 700 with corners at or near the int16 limits.

It also writes 24 **grids**: jittered quads, each split into two triangles along a random diagonal, covering the frame and a margin around it, with random winding. Neighbors share edges, so every frame pixel must be covered exactly once per grid; the generator checks the model against this too.

```sh
.venv/bin/python sim/py/ref_tri.py sim
```

    ref_tri: 10000 triangles (4710 clockwise, 5026351 pixels in all) -> sim/tri_vectors.hex
      axis 744 (116 empty), degen 481 (481 empty), diag 507 (17 empty), directed 10 (1 empty), ...
    ref_tri: 24 grids, 7434 triangles, every pixel covered exactly once -> sim/tri_grid.hex

Commit both files. `ref_tri.py --show N` prints triangle N's expected coverage as ASCII art.

## 10. Simulate

Copy `tb_tri_raster.v` to `sim/`:

```
$ make test TESTS="tb_tri_raster"
  PASS  tb_tri_raster
$ cat build/sim/tb_tri_raster.log
PASS: tb_tri_raster (10000 triangles, 5026351 pixels matched, 7434 grid triangles in 24 grids tile the frame once, worst triangle 4103 clocks)
```

It takes about 1.5 minutes; the clock count depends on your design. For each triangle the testbench starts the DUT, collects pixels until `o_done`, and checks that none is outside the clamped box, none arrives twice, and the count and hash equal the model's. For each grid it counts how often each pixel was covered. On failure it prints the first few bad triangles and the DUT's picture of the first one, to compare with `ref_tri.py --show N`.

What typical failures mean:

- Many triangles wrong, and grid pixels covered twice or not at all along diagonals: the fill rule. Check each edge's bias and the top-left test.
- Thousands of triangles wrong, all of them clockwise: the swap on negative area.
- A zero-area triangle draws pixels: the area2 = 0 check is missing *and* the bias is wrong.
- A missing column or row at the right or bottom of a box: `xmax` and `ymax` are inclusive.
- Only a few extreme triangles fail: a width is too small (Section 7).

**Done when:** `tb_tri_raster` passes, and you can explain why the triangle (0, 0), (0, 4), (4, 0) covers exactly its 10 pixels.

## Testbench requirements

**Module you provide:** `tri_raster #(SCREEN_W = 320, SCREEN_H = 240)` in `rtl/common/tri_raster.v`, ports exactly as in Section 8: `i_clk`, `i_start`, signed 16-bit `i_x0, i_y0, i_x1, i_y1, i_x2, i_y2`, `o_busy`, `o_valid`, 16-bit `o_x`, `o_y`, `o_done`. The testbench uses `SCREEN_W = SCREEN_H = 64`.

- **Handshake.** A one-clock `i_start` while idle latches the six corners (the testbench scrambles the inputs the clock after). `o_busy` is high the clock after `i_start` and low when `o_done` pulses. `o_done` is one clock long, and `o_valid` is low with it and in the clock after it. `o_busy` and `o_valid` are low while idle. The next `i_start` comes the clock after `o_done`.
- **Pixels.** The set of pixels delivered between `i_start` and `o_done` equals `raster_model.tri_pixels(...)` for the frame: integer sample points, normalized to positive area, bias 0 on top-left edges (dy > 0, or dy = 0 and dx < 0) and −1 on the others, covered when all three E + bias ≥ 0, nothing when area2 = 0. Any order, any latency, no pixel twice, every pixel inside the clamped box.
- **Widths.** Corners anywhere in int16 work, including triangles whose area2 needs 33 signed bits.
- **Time.** Each triangle finishes within 2 · (clamped box pixels) + 1,000 clocks of `i_start`. One test per clock with any reasonable setup, even serial multiplies, passes easily; scanning the whole frame doesn't.
- **Shared edges.** In each of 24 grids of 98 to 722 triangles in random order and winding, every frame pixel is covered exactly once.

**Files from this chapter:** `tb_tri_raster.v` to `sim/`, `ref_tri.py` to `sim/py/`, `raster_model.py` to `host/`. Run `.venv/bin/python sim/py/ref_tri.py sim` once to write `sim/tri_vectors.hex` and `sim/tri_grid.hex`, and commit them. The testbench has their line counts (10,000 and 7,434) as `N_VEC` and `N_GRID`; if you change the generator, update them to the counts it prints.

**Testbench:** `sim/tb_tri_raster.v`, top module `tb_tri_raster`, run from `build/sim/` by `make test` (about 1.5 minutes). It uses a 50 MHz clock, drives and samples on the falling edge, reads `../../sim/tri_vectors.hex` and `tri_grid.hex` (override the folder with `+dir=PATH/`), and prints one `PASS` or `FAIL` line. The watchdog is 4 s simulated. By hand:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_tri_raster.vvp sim/tb_tri_raster.v
cd build/sim && vvp -n tb_tri_raster.vvp
```
