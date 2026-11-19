# Chapter 4.4 - Lines

Chapter 4.3 filled rectangles with two counters, one pixel per clock. A line is harder: its pixels follow a slope like 17/50, and the engine has no divider and no fractions. **Bresenham's algorithm** draws it with integer adds and compares only. It keeps one error term, and each clock a compare on that term decides whether to step in x, in y, or in both. This chapter adds it as `DRAW_LINE`.

Step 4.6 reuses the idea: a triangle's edge functions are stepped pixel by pixel with additions, and their signs decide which pixels are inside. A line is the easy first case, with one error term instead of three.

By the end of this chapter you will have:

- `rtl/common/cmd_engine.v` extended with `DRAW_LINE` (opcode `0x06`),
- `host/gpu_model.py` replaced by this chapter's version, which models `DRAW_LINE`,
- `sim/tb_gpu_line.v` passing against `sim/py/ref_line.py`, with Chapter 4.2's `tb_uart_gpu` and Chapter 4.3's `tb_gpu_rect` still passing,
- `projects/4.4-line/` on the board, a starburst on the monitor, and `host/line_test.py` showing that the hardware CRC after 500 random lines equals Python's.

## 1. The command

| Opcode | Command | Length | Payload |
| --- | --- | --- | --- |
| `0x06` | `DRAW_LINE` | 9 | x0, y0, x1, y1 (signed 16-bit, little-endian), color |

The payload is laid out exactly like `FILL_RECT`'s. `DRAW_LINE` writes `color` to every on-screen point that Bresenham's algorithm (Section 2) visits on its way from (x0, y0) to (x1, y1). The definition makes three choices:

- **Both endpoints are drawn.** A line from (5, 7) to (5, 7) is that one pixel, and a line from (0, 0) to (9, 0) is ten pixels. A line has max(|x1 − x0|, |y1 − y0|) + 1 points. This matches `PLOT` and `FILL_RECT`'s inclusive corners, so four `DRAW_LINE`s along a rectangle's edges cover exactly the edge pixels of the filled rectangle.
- **(x0, y0) is the start, and the direction matters.** Where the true line passes exactly half-way between two pixels, Section 2's rule rounds toward the end point. So `DRAW_LINE a b` and `DRAW_LINE b a` differ on some lines: from (0, 0) to (4, 2) the points at x = 1 and 3 are (1, 1) and (3, 2), and from (4, 2) back to (0, 0) they are (1, 0) and (3, 1). Don't sort the endpoints in hardware to save a case. It changes the pixels, and the testbench draws every tie case both ways.
- **Coordinates are signed 16-bit, and off-screen points are skipped.** A line partly off the screen draws exactly the visible points of the full line. One entirely off the screen draws nothing. Section 3 says what that costs.

Two examples, byte by byte:

    06 09  00 00 00 00 04 00 02 00  07   (0, 0) to (4, 2), color 7: (0,0) (1,1) (2,1) (3,2) (4,2)
    06 09  F6 FF 78 00 90 01 78 00  05   (-10, 120) to (400, 120): 411 points, draws x 0-319

A `DRAW_LINE` with any length other than 9 is skipped like any other wrong-length packet.

## 2. Bresenham's algorithm

Take a line from (x0, y0) to (x1, y1). Let

    DX = |x1 - x0|,  DY = |y1 - y0|,  sx = +1 if x0 < x1 else -1,  sy = +1 if y0 < y1 else -1

The signs sx and sy fold every direction into one quadrant: after u steps in x and v steps in y, the current point is (x0 + sx·u, y0 + sy·v), with u and v counting up from 0. The ideal line from (0, 0) to (DX, DY) is the set of (u, v) where

    E(u, v) = DX·v - DY·u = 0.

E is zero on the line and has opposite signs on its two sides, and |E| is the distance from the line times the line's length. So comparing |E| at two pixels compares their distances to the line, with no division.

The next point is one of three neighbours: an x step (u+1, v), a y step (u, v+1), or a diagonal step (u+1, v+1). Keep one number in a register, the error of the diagonal neighbour:

    err = E(u+1, v+1)

The other two neighbours are then err − DX (the x step) and err + DY (the y step). The decisions are:

- **Step in x** (alone or diagonally) if the diagonal is at least as close to the line as the y step: |err| ≤ |err + DY|, which reduces to `2·err >= -DY`.
- **Step in y** (alone or diagonally) if the diagonal is at least as close as the x step: |err| ≤ |err − DX|, which reduces to `2·err <= DX`.

When both are true, the step is diagonal. The result is the neighbour nearest the line, with ties going to the diagonal. Each step changes err by a constant: an x step adds −DY, a y step adds +DX. At the start, err = E(1, 1) = DX − DY. In the usual variable names, with `dx = DX` and `dy = −DY`:

```python
err = dx + dy                      # dx = |x1-x0|, dy = -|y1-y0|
x, y = x0, y0
while True:
    plot(x, y)
    if x == x1 and y == y1:
        break
    e2 = 2 * err
    if e2 >= dy: err += dy; x += sx
    if e2 <= dx: err += dx; y += sy
```

Both tests use the same `e2`, the value from before this step. At least one is always true, so every step moves, and the loop lands exactly on (x1, y1) after max(DX, DY) steps. It works in all eight octants without swapping anything, and horizontal and vertical lines need no special case: with DY = 0 the y test is never true, and with DX = 0 the x test is never true.

**Tie-breaking.** The `>=` and `<=` are the tie rule. When the diagonal and the straight step are exactly as far from the line, the diagonal wins, so a half-way minor coordinate rounds **toward (x1, y1)**. For (0, 0) to (4, 2), err starts at 2. At the first step e2 = 4 passes both tests, and the line goes to (1, 1), although the ideal line at x = 1 is at y = 0.5. Change `>=` to `>` or `<=` to `<` and you get another legal Bresenham, but not this one, and the tests check this one.

That is the whole definition. `line_points()` in `gpu_model.py` is this loop.

**The other common form.** Many books swap x and y for steep lines so that the major axis steps every clock, and keep a decision variable D = 2·dmin − dmaj that also steps the minor axis when D ≥ 0. With `>=` it gives the same pixels. `gpu_model.py` has it as `line_points_major()`, and its self-check compares the two forms. Use whichever you like. Ending after a counted number of steps instead of comparing with (x1, y1) is fine too.

**Widths.** `x1 - x0` ranges from −65,535 to 65,535, so the difference needs 17 signed bits. A 16-bit subtraction wraps (32767 − (−32768) = −1). The error term needs more. An x step happens only when err ≥ −DY/2 and subtracts DY, and a y step happens only when err ≤ DX/2 and adds DX, so err stays between −1.5·DY and 1.5·DX, at most ±98,302. That fits in 18 signed bits, and e2 in 19. Declare all of them `signed`, and remember Chapter 4.3's trap: one unsigned operand makes the whole compare unsigned.

## 3. Clipping: step the whole line, write only what's on screen

`FILL_RECT` had to clip before its loop, because a full-range rectangle has 4.3 billion points. A line has at most 65,536, so this chapter takes the simple, exact way:

- **step every point of the line**, on the screen or not, and
- **write a point only if 0 ≤ x ≤ 319 and 0 ≤ y ≤ 239**, comparing the full signed 16-bit values, like `PLOT`.

The pixels drawn are then exactly the visible points of the full line, with no special cases at the edges. The cost is time: a `DRAW_LINE` takes max(DX, DY) + 1 clocks plus setup, visible or not. The worst case, −32768 to 32767, is 65,536 clocks, 1.3 ms at 50 MHz. That is less than a `CLEAR`, and meanwhile the FIFO holds back the next commands as it did for `FILL_RECT`.

You may also skip a line whose bounding box misses the screen, with the same four compares as `FILL_RECT`'s reject. A real clipper that jumps to where the line enters the screen would need the error term at that point, which takes a division and is easy to get off by one. It isn't worth it for a 1.3 ms worst case. The tests accept any of these, as long as the pixels are right and every fence reply arrives within 50 ms.

Chapter 4.3's traps apply to the on-screen test: x = 320 or y = 240 must not be written (x = 320 lands on the first pixel of the next row), x = 0 must be, and x = 522 must not appear at x = 10, as it would if you tested only the low 9 bits.

## 4. One point per clock, in the engine

| State | Does |
| --- | --- |
| `EXEC` | `OP_DRAW_LINE: if (r_len == 8'd9)`: latch x = x0, y = y0, x1, y1, the color, dx, dy, sx, sy, go to `LINE_SETUP` |
| `LINE_SETUP` | err = dx + dy, go to `LINE` |
| `LINE` | each clock: write (x, y) if on screen; if (x, y) == (x1, y1) go to `FETCH`, else step |

The step is the loop body in Section 2, all in one clock:

```verilog
wire signed [18:0] w_e2  = {r_err, 1'b0};             // 2*err, r_err is signed [17:0]
wire               w_stx = (w_e2 >= r_dy);            // r_dy = -|y1 - y0|
wire               w_sty = (w_e2 <= r_dx);
...
S_LINE: begin
    o_fb_we   <= w_on;                                // (r_x, r_y) on the screen
    o_fb_addr <= {r_y[7:0], 8'd0} + {r_y[7:0], 6'd0} + r_x[8:0];
    if (r_x == r_x1 && r_y == r_y1) r_state <= S_FETCH;
    else begin
        // x += sx if w_stx, y += sy if w_sty, err += (w_stx ? dy : 0) + (w_sty ? dx : 0)
    end
end
```

The address uses only the low bits, which is safe because it matters only when `w_on` says the point is on the screen. Splitting `EXEC` and `LINE_SETUP` keeps the 17-bit subtraction and the absolute value away from the addition that makes err, so `LINE` has one chain: a compare of e2, then a three-input add.

The two new states take Chapter 4.3's eight to ten, which still fits the 4-bit state register. Other structures work too, such as writing one clock behind the stepping (as long as the last point is written) or the octant-swap form with a step counter. The testbench times four lines from first write to last and allows the number of visible pixels plus 8 clocks, so a design that needs two clocks per point fails there.

## 5. The Python model

Replace `host/gpu_model.py` with this chapter's version. It adds `DRAW_LINE = 0x06` with length 9, the builder `draw_line(x0, y0, x1, y1, c)`, `line_points()` (Section 2's loop, the definition), `line_points_major()` (the octant-swap form), and two counters, `Gpu.line_points` (points stepped) and `Gpu.line_pixels` (points written). Nothing else changes: Chapter 4.2's `ref_cmd.py` and Chapter 4.3's `ref_rect.py` still write the same files byte for byte, because their "unknown" opcodes are all `0x80` or above. Run `.venv/bin/python host/gpu_model.py` for the self-check.

Copy `ref_line.py` to `sim/py/` and run it once. Commit the five files it writes:

```sh
.venv/bin/python sim/py/ref_line.py sim
```

    ref_line: 10267 bytes, 901 DRAW_LINEs (616128 points, 51784 on screen), 28 FB_CRC replies -> sim/line_*
      random: 500 (204 with a tie, 241 steep, 10 single points, 99 partly and 100 entirely off screen)

## 6. Simulate: `tb_gpu_line`

Copy `tb_gpu_line.v` to `sim/`. It is Chapter 4.3's harness (real serial at 2.5 Mbaud, its own framebuffer on your draw ports, every reply, pixel and palette entry checked), now with several throughput windows:

```
$ make test TESTS="tb_uart_gpu tb_gpu_rect tb_gpu_line"
  PASS  tb_uart_gpu
  PASS  tb_gpu_rect
  PASS  tb_gpu_line
$ cat build/sim/tb_gpu_line.log
tb_gpu_line: throughput window 1: 320 writes in 320 clocks from first to last (328 allowed)
tb_gpu_line: throughput window 2: 240 writes in 240 clocks from first to last (248 allowed)
tb_gpu_line: throughput window 3: 320 writes in 320 clocks from first to last (328 allowed)
tb_gpu_line: throughput window 4: 320 writes in 320 clocks from first to last (328 allowed)
PASS: tb_gpu_line (10267 bytes sent, 28 FB_CRC replies match, 135275 pixel writes, 4 throughput windows at one point per clock)
```

It takes about 35 seconds. The stream draws on Chapter 4.1's test image, where every pixel differs, so a missing or extra point always shows:

1. `FB_CRC` of the untouched image.
2. Edge cases in ten groups, each followed by an `FB_CRC`: single points on and just off the screen; horizontal and vertical lines both ways; 45° diagonals; seven lines in each of the eight octants, drawn outward and then inward; exact ties (DX = 2·DY and others) in every octant, each drawn both ways; lines along the edges and corner to corner; lines clipped on each side; lines just off each edge, past a corner with an overlapping bounding box (−10, 5 to 5, −10), far away, or at x = 522; and seven lines from int16 limit to int16 limit that cross the screen.
3. `DRAW_LINE` with lengths 8, 10 and 0, and hidden in a `NOP` and an unknown packet, none of which may draw, mixed with `FILL_RECT`, `PLOT` and `SET_PALETTE` to check the order.
4. **Four throughput windows:** shallow corner to corner, steep drawn up and left, horizontal right to left, and one that starts 1,000 pixels off the screen (only its visible part is timed).
5. **Back-pressure:** a 65,536-point line, then 30 short lines sent right behind it without looking at `o_ready`.
6. **A starburst** after a `CLEAR`: 70 rays from (160, 120) to every 16th border pixel.
7. **500 random lines**, an `FB_CRC` every 50, with `PLOT`s, `FILL_RECT`s and unknown packets mixed in.

A wrong reply is reported with its group from `sim/line_labels.txt`. The first lines some typical bugs print:

- **Last point not drawn:** `FB_CRC reply 2 is a9a79db7, expected 5739fc8e. It covers: edge cases: single points ...`. `a9a79db7` is the untouched image: the single points drew nothing.
- **`>` instead of `>=` (or `<` for `<=`):** reply 5, `the eight octants, drawn outward from a centre`. Those lines include a tie.
- **Endpoints sorted to run left to right:** also reply 5, a different CRC.
- **Error term initialized to dx + dy ± 1:** `o_ready stayed low for 50 ms ... the next covers: edge cases: horizontal and vertical lines, both directions`. The two-pixel vertical line steps sideways at once, never meets (x1, y1), and runs forever. That is the danger of ending on `x == x1 && y == y1`: wrong stepping hangs the engine. A step counter ends the line anyway, but it doesn't make it right.
- **Steep lines stepped along x** (octant-swap form without the swap): reply 3, `horizontal and vertical lines`.
- **err only 16 bits wide:** `fence 2 waited 50 ms ... long lines at the int16 limits that cross the screen`. **dx or dy from a 16-bit subtraction:** reply 11, the same group.
- **`x <= 320` in the on-screen test:** `framebuffer write to address 76800`. **`x > 0`:** reply 2, the point at (0, 0) is missing. **Low bits only:** reply 2, the point at x = 522 appears at x = 10.
- **Lengths other than 9 accepted:** reply 12, the malformed-packet group.
- **Fetching bytes while a line runs:** `fence 2 waited 50 ms`.
- **Two clocks per point:** everything matches, then `FAIL: tb_gpu_line (throughput window 1: the line took 639 clocks from first to last write, more than 328: aim for one point per clock)`.

Keep running `tb_uart_gpu` and `tb_gpu_rect` with the new `gpu_model.py`. It's easy to reuse a register that `PLOT` or `FILL_RECT` still needs, and those tests, like item 3's mix, catch it.

## 7. The Arty top

Nothing in the top changes. Copy `projects/4.3-rect/` to `projects/4.4-line/`, set `PROJ := line` and `TESTS := tb_uart_gpu tb_gpu_rect tb_gpu_line`, and run `make`. The reference build took 36 seconds:

```
== line: resource use (xc7z020clg400-1) ==
  SLICE_LUTX:    2253/ 106400     2%
  SLICE_FFX:     653/ 106400     0%
  CARRY4:     144/  13300     1%
  ...
  RAMB18E1:       1/    280     0%
  RAMB36E1:      19/    140    13%
  Max frequency for clock 'clk_sys': 107.12 MHz (PASS at 50.00 MHz)
  Max frequency for clock 'clk_pix': 139.37 MHz (PASS at 25.00 MHz)
  Max frequency for clock 'clk_sys': 107.12 MHz (PASS at 50.00 MHz)
```

- Compared with Step 4.3 (1,715 LUTs, 526 flip-flops, 98 `CARRY4`), `DRAW_LINE` costs about 540 LUTs, 130 flip-flops and 46 carry chains: the endpoint, dx/dy and error registers, their adders and compares, and the on-screen test.
- `clk_sys` moved from 110 to 107 MHz. The critical path is still Chapter 4.3's sort-and-clamp chain in `EXEC`, not the line's step: one compare and a three-input add per clock is comfortable at 50 MHz.

## 8. On the board

Wiring and programming are as in Step 4.2: adapter TXD to JA pin 1, RXD to JA pin 2, GND to JA pin 5, monitor on HDMI TX, then `make prog`.

1. The Chapter 4.1 test image appears and LD0 lights.
2. Copy `host/line_test.py` next to `gpu_model.py`, and draw just the starburst:

    ```
    $ .venv/bin/python host/line_test.py /dev/ttyUSB0 --keep
    ```

    It sets a palette, clears to dark blue, and draws 70 rays from the centre to the border, one color per octant: red, orange, yellow, green, cyan, blue, violet, magenta, counter-clockwise from the right. Every ray should be one unbroken staircase from the centre to the border, the rays evenly spaced around the border, and each color in its own 45° sector. A ray that stops short, bends, or ends at the wrong place is a stepping bug, and gaps along the screen edge are a clipping bug. The script ends with `after the starburst: hardware CRC 1953c23b, Python CRC 1953c23b` and `PASS`.
3. Run the full test. It draws the starburst, then 500 random lines of the testbench's kinds, including ties and lines out to the int16 limits, with an `FB_CRC` after every 100. With `--seed 7` the byte count and every CRC below are what `gpu_model` computes, so your hardware must print them exactly. The time is roughly what 6,345 bytes take at 115,200 baud:

    ```
    $ .venv/bin/python host/line_test.py /dev/ttyUSB0 --seed 7
    sent 6345 bytes: a starburst and 500 random DRAW_LINEs (seed 7; 226 with a tie, 225 steep, 73 entirely off screen, 2543125 points stepped, 45065 on screen) in 0.56 s
      after the starburst: hardware CRC 1953c23b, Python CRC 1953c23b
      after line  100    : hardware CRC 7b1b5165, Python CRC 7b1b5165
      after line  200    : hardware CRC d74c2446, Python CRC d74c2446
      after line  300    : hardware CRC 36e0b724, Python CRC 36e0b724
      after line  400    : hardware CRC 5bb5a7b8, Python CRC 5bb5a7b8
      after line  500    : hardware CRC 9683637a, Python CRC 9683637a
    PASS: line_test
    ```

4. LD1 (overflow) stays dark. The 2.5 million points include about 40 lines that run near the int16 limits, up to 1.3 ms each, and the FIFO absorbs them without flow control.
5. Run it a few times without `--seed`. Each run prints its seed so a failure can be repeated, and names the first block that differs. If one does, `tb_gpu_line` should fail too. If no reply comes, press BTN0, check the wiring and the baud rate, and try again.

**Done when:** `tb_gpu_line`, `tb_gpu_rect` and `tb_uart_gpu` pass, the starburst looks right on the monitor, and `line_test.py` reports the hardware CRCs equal to Python's.

## Testbench requirements

**Modules you provide:** `uart_gpu #(CLK_HZ, BAUD)` with Chapter 4.2's ports and behavior and Chapter 4.3's `FILL_RECT`, all of whose requirements still hold (`tb_uart_gpu` and `tb_gpu_rect` still run), plus:

- Opcode `0x06` `DRAW_LINE`, length exactly 9: x0, y0, x1, y1 as little-endian signed 16-bit, then color. It writes color to every on-screen point (0 ≤ x ≤ 319, 0 ≤ y ≤ 239, full signed compare) visited by `gpu_model.line_points(x0, y0, x1, y1)`: the all-octant Bresenham of Section 2 starting at (x0, y0), err = dx + dy, x step if 2·err ≥ dy, y step if 2·err ≤ dx (dx = |x1 − x0|, dy = −|y1 − y0|), both endpoints included. Any formulation with identical pixels is fine. The endpoints must not be reordered. Wrong lengths are skipped and draw nothing.
- Lines up to 65,536 points must finish. Stepping off-screen points one per clock is enough: a fence gives up after 50 ms, and so does a flow-controlled send whose `o_ready` stays low that long.
- Commands run in order, and no bytes are fetched or lost while a line runs. In the back-pressure section about 340 bytes arrive right behind one 65,536-point line, without regard to `o_ready`.
- Throughput: in each of four windows, one line with N visible pixels may take at most N + 8 clocks from its first framebuffer write to its last. Setup, and any off-screen points before the first write, aren't counted.
- Draw ports as in Chapter 4.2: no write at an address of 76,800 or above, and no X on the write enables.

**Files from this chapter:** `tb_gpu_line.v` to `sim/`, `ref_line.py` to `sim/py/`, `gpu_model.py` (replacing Chapter 4.3's) and `line_test.py` to `host/`. It also needs Chapter 4.1's `ref_scanout.py`, `fb_test.hex` and `pal_test.hex`. Run `.venv/bin/python sim/py/ref_line.py sim` once to write `sim/line_stream.hex`, `line_replies.hex`, `line_fb.hex`, `line_pal.hex` and `line_labels.txt`, and commit them.

**Testbench:** `sim/tb_gpu_line.v`, top module `tb_gpu_line`, run from `build/sim/` by `make test` (about 35 seconds). It reads `../../rtl/common/fb_test.hex`, `pal_test.hex` and `../../sim/line_*`. It uses a 50 MHz clock and 2.5 Mbaud, and checks every reply byte, all 76,800 framebuffer bytes, all 256 palette entries, `o_overflow`, out-of-range and X writes, and the throughput windows. It prints one `PASS` or `FAIL` line. The watchdog is 200 ms simulated. By hand:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_gpu_line.vvp sim/tb_gpu_line.v
cd build/sim && vvp -n tb_gpu_line.vvp
```
