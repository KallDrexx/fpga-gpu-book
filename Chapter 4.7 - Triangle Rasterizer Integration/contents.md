# Chapter 4.7 - Triangle Rasterizer Integration

Chapter 4.6 built `tri_raster`: give it three corners and it streams out the covered pixels. This chapter gives it a command, `DRAW_TRI`, and connects it to the command engine of Chapters 4.2-4.5, so a host can draw a filled triangle with one 15-byte packet.

The rasterizer already clips, accepts either winding and follows the fill rule, so the engine only starts it and turns each pixel's x and y into an address. The new questions are about the whole system: does the engine wait for the rasterizer, do all pixels land in the back buffer, and does the fill rule still give **one write per pixel** once a real memory sits behind it?

By the end of this chapter you will have:

- `rtl/common/cmd_engine.v` extended with `DRAW_TRI` (opcode `0x08`) and one `tri_raster` inside it,
- `host/gpu_model.py` replaced by this chapter's version, which models `DRAW_TRI` with Chapter 4.6's `raster_model`,
- `sim/tb_gpu_tri.v` passing: six meshes of triangles drawn into a *counting buffer* write every pixel exactly once, and hundreds of random `DRAW_TRI`s match the model by framebuffer and `FB_CRC`,
- Chapters 4.2 to 4.6's testbenches still passing,
- `projects/4.7-tri/` on the board, with `host/tri_demo.py` showing rotating triangles and the hardware CRC equal to Python's.

## 1. The command

| Opcode | Command | Length | Payload |
| --- | --- | --- | --- |
| `0x08` | `DRAW_TRI` | 13 | x0, y0, x1, y1, x2, y2 (signed 16-bit, little-endian), color |

`DRAW_TRI` writes `color` to every pixel of `raster_model.tri_pixels(v0, v1, v2, 320, 240)`. That is Chapter 4.6's definition, unchanged: either winding, integer sample points, the top-left rule, nothing for zero area, and only the part inside 0 ≤ x ≤ 319, 0 ≤ y ≤ 239. Like every drawing command it draws into the back buffer (Chapter 4.5), and a packet with the wrong length (12 or 14, say) is skipped.

Two examples, byte by byte:

    08 0D  00 00 00 00  00 00 04 00  04 00 00 00  09     (0,0) (0,4) (4,0), color 9: 10 pixels
    08 0D  EC FF 32 00  1E 00 14 00  1E 00 5A 00  05     (-20,50) (30,20) (30,90), color 5: 1455 pixels, clipped at x = 0

At 115,200 baud the 15 bytes take 1.3 ms, which is about as long as the engine needs for a 250 x 250 bounding box. For most triangles the serial link, not the rasterizer, is the limit.

## 2. Wiring `tri_raster` into the engine

Clipping needs nothing new. `tri_raster` clamps its bounding box to `SCREEN_W` x `SCREEN_H` (320 x 240 here) before it scans, so every pixel it emits is on screen and the engine does not test x and y again. A huge triangle therefore costs at most the screen: 76,800 clocks, 1.5 ms, the same as a `CLEAR`.

The engine's side of the handshake is short:

| State | Does |
| --- | --- |
| `EXEC` | `OP_DRAW_TRI: if (r_len == 8'd13)`: copy the color to the write data, pulse `r_tri_start` for one clock, go to `TRI` |
| `TRI` | wait for `o_done`, then go to `FETCH` |
| any state | when the rasterizer's `o_valid` is high, write the pixel at `o_x`, `o_y` |

The corners are already in the payload registers: `r_arg[0]` and `[1]` are x0, `[2]` and `[3]` are y0, and so on to `[12]`, the color. Chapter 4.2's 16-byte argument buffer holds all 13. `TRI` is the twelfth state, so the 4-bit state register is still enough.

```verilog
reg         r_tri_start = 1'b0;
wire        w_tri_valid, w_tri_done;
wire [15:0] w_tri_x, w_tri_y;

tri_raster #(.SCREEN_W(320), .SCREEN_H(240)) u_tri (
    .i_clk(i_clk), .i_start(r_tri_start),
    .i_x0({r_arg[1],  r_arg[0]}),  .i_y0({r_arg[3],  r_arg[2]}),
    .i_x1({r_arg[5],  r_arg[4]}),  .i_y1({r_arg[7],  r_arg[6]}),
    .i_x2({r_arg[9],  r_arg[8]}),  .i_y2({r_arg[11], r_arg[10]}),
    .o_busy(), .o_valid(w_tri_valid), .o_x(w_tri_x), .o_y(w_tri_y), .o_done(w_tri_done));
```

In the engine's clocked block, `r_tri_start <= 1'b0;` goes with the other defaults at the top, so it is a one-clock pulse. The new pieces:

```verilog
// in S_EXEC:
OP_DRAW_TRI: if (r_len == 8'd13) begin
    o_fb_wdata  <= r_arg[12];          // the color, held for every pixel
    r_tri_start <= 1'b1;               // tri_raster latches the corners on this clock
    r_state     <= S_TRI;
end
...
S_TRI: if (w_tri_done) r_state <= S_FETCH;
...
// after the case statement, so it wins over the defaults above:
if (w_tri_valid) begin
    o_fb_we   <= 1'b1;
    o_fb_addr <= {w_tri_y[7:0], 8'd0} + {w_tri_y[7:0], 6'd0} + w_tri_x[8:0];   // y*320 + x
end
```

The address is Chapter 4.4's y·256 + y·64 + x, with no multiplier. The low bits are enough because `tri_raster` never emits a pixel outside the frame.

**Why the engine waits in `TRI`.** There is one draw port, an `FB_CRC` queued behind a `DRAW_TRI` must see all its pixels, and `tri_raster` ignores an `i_start` while busy, so a second `DRAW_TRI` started early would be lost. So the engine **fetches no bytes while the rasterizer runs**. That is Chapter 4.3's back-pressure rule: the FIFO fills, `o_ready` drops, and the host waits. A 1.5 ms triangle lets about 385 bytes arrive at the test baud, which the 512-byte FIFO holds.

The last pixel's write lands the clock after its `o_valid`, about when the engine returns to `FETCH`. Fetching and executing the next command takes several clocks, so nothing can write before it.

**The draw buffer needs nothing new.** `o_fb_buf` is still `~o_flip_req`, one wire for every command, so `DRAW_TRI` writes the back buffer for free.

**Reset.** `BTN0` resets the parser. If the engine left `TRI` on a reset, the rasterizer would keep writing for up to 1.5 ms while the next commands ran, and the next `DRAW_TRI`'s start would be ignored. Treat `TRI` like Chapter 4.5's `FLIP` wait: a reset leaves it alone, and the rasterizer finishes by itself.

```verilog
if (i_rst) begin
    if (r_state != S_FLIP && r_state != S_TRI) r_state <= S_FETCH;
    r_phase <= 2'd0;
end
```

## 3. Throughput

Inside the scan, `tri_raster` tests one bounding-box pixel per clock, and a covered pixel becomes a write in that clock:

    clocks = setup + (clamped box width) x (clamped box height)

A box is at least twice the triangle's area, so at most half of the clocks write. That is fine: a 100 x 100 triangle takes 10,000 clocks, 0.2 ms.

Chapter 4.6's testbench allowed any pixel rate (2 x box + 1,000 clocks). `DRAW_TRI` must keep this phase's goal of one pixel per clock, so `tb_gpu_tri` times four triangles from first write to last and allows the clamped box plus 2 clocks per box row plus 16. Setup, however long, comes before the first write and isn't counted, so a serial setup passes, and so does a clock or two lost at each row change. A design that spends two clocks per box pixel fails. A smarter traversal that skips empty runs finishes sooner and passes too.

| Triangle | Box | Writes | First to last write (reference) | Allowed |
| --- | --- | --- | --- | --- |
| inside the screen | 291 x 221 = 64,311 | 31,190 | 63,578 clocks | 64,769 |
| huge, clockwise, mostly off screen | the screen, 76,800 | 76,800 | 76,800 clocks | 77,296 |
| thin sliver | 301 x 5 = 1,505 | 450 | 827 clocks | 1,531 |
| small | 13 x 11 = 143 | 49 | 110 clocks | 181 |

## 4. Shared edges in a real framebuffer

Chapter 4.6 checked, with pixel lists, that triangles sharing an edge cover each pixel once. In a framebuffer the two mistakes look different:

- **A gap** (neither triangle owns the edge) shows the old background as a one-pixel crack along the edge.
- **A double hit** (both own it) is invisible when both triangles have the same color. It is still wrong: with two colors the result depends on drawing order, the blending of later phases would blend the pixel twice, and it wastes a write.

So comparing the final picture with Python can miss both. The testbench counts instead: next to every framebuffer byte it keeps a **counting buffer**, an integer that goes up by one at every write to that byte. After drawing a whole mesh it checks that

- every pixel of the back buffer was written **exactly once**: no gap and no double hit,
- no pixel of the other buffer was written at all: nothing went to the front buffer.

This ignores order and timing, so a broken fill rule can't hide, and the first failing pixel's coordinates are printed.

Each mesh is a grid of quads, every quad split into two triangles along a random diagonal, sent in random order with random winding and corner order:

1. **Squares on the exact screen**, 20 x 20 pixels, no jitter. Every edge is horizontal, vertical or exactly 45 degrees, so many pixels lie exactly on an edge.
2. **Jittered quads** extending 24 pixels past every screen edge, so clipping happens on all four sides and at the corners.
3. **Huge jittered triangles**, thousands of pixels across, mostly off screen.
4. **A patch of slivers** in the middle of the screen. It covers only part of the screen, so the check is "no pixel twice", and the final framebuffer comparison shows nothing leaked outside.
5. **An int16-limit mesh**, corners at ±32768 and a vertex near the screen centre.
6. **Two triangles** from (−32768, −32768) to (32767, 32767): one diagonal across the whole screen.

A `FLIP` follows each mesh, so meshes land in both buffers.

## 5. The Python model

Replace `host/gpu_model.py` with this chapter's version. It needs Chapter 4.6's `host/raster_model.py` in the same folder. It adds `DRAW_TRI = 0x08` with length 13, the builder `draw_tri(x0, y0, x1, y1, x2, y2, c)`, `tri_covered()` (a call to `raster_model.tri_pixels`), and two counters, `Gpu.tri_count` and `Gpu.tri_drawn`. Nothing else changes. Run the self-check:

```sh
.venv/bin/python host/gpu_model.py
```

    gpu_model self-check ok

Check that Chapters 4.2 to 4.5 still get the same files. These commands rewrite the files already in `sim/` byte for byte, so `git status` stays clean:

```sh
for n in cmd rect line flip_sync; do .venv/bin/python sim/py/ref_$n.py sim; done
```

Copy `ref_tri_draw.py` to `sim/py/` and run it once (about 15 seconds; the model tests every box pixel the slow way). Commit the six files it writes:

```sh
.venv/bin/python sim/py/ref_tri_draw.py sim
```

    ref_tri_draw: 17257 bytes, 1122 DRAW_TRIs (2178569 pixels), 37 FB_CRC replies, 8 FLIPs -> sim/tri_*
      meshes (triangles, pixels): 384/76800, 176/76800, 12/76800, 140/5758, 12/76800, 2/76800
      488 clockwise in meshes and random; random: 300 (24 degenerate, 55 empty, 84 with a corner off screen but drawing)

## 6. Simulate: `tb_gpu_tri`

Copy `tb_gpu_tri.v` to `sim/`. It is Chapter 4.4's harness (real serial at 2.5 Mbaud, its own memory on your draw ports, every reply checked) with two framebuffers, the counting buffer of Section 4, and a stand-in for the scanout: `i_flip_ack` follows `o_flip_req` four clocks later.

```
$ make test TESTS="tb_uart_gpu tb_gpu_rect tb_gpu_line tb_flip_sync tb_tri_raster tb_gpu_tri"
  PASS  tb_uart_gpu
  PASS  tb_gpu_rect
  PASS  tb_gpu_line
  PASS  tb_flip_sync
  PASS  tb_tri_raster
  PASS  tb_gpu_tri
$ cat build/sim/tb_gpu_tri.log
tb_gpu_tri: throughput window 1: 31190 writes in 63578 clocks from first to last (64769 allowed)
tb_gpu_tri: throughput window 2: 76800 writes in 76800 clocks from first to last (77296 allowed)
tb_gpu_tri: throughput window 3: 450 writes in 827 clocks from first to last (1531 allowed)
tb_gpu_tri: throughput window 4: 49 writes in 110 clocks from first to last (181 allowed)
PASS: tb_gpu_tri (17257 bytes sent, 37 FB_CRC replies match, 2263486 pixel writes, 6 counting checks, 4 throughput windows at one box pixel per clock)
```

`tb_gpu_tri` takes two to three minutes. The stream, in order:

1. `FB_CRC` of the untouched test image.
2. **Directed triangles** in nine groups, each followed by an `FB_CRC`: Chapter 4.6's worked triangle in every corner order; a 4x4 square from two triangles at three places, one in the far corner; triangles touching the screen edges and corners, partly off screen, and hidden just off screen; degenerate ones; slivers and one-pixel triangles; right triangles and exact 45-degree edges; corners at the int16 limits.
3. **Malformed and ordered:** `DRAW_TRI` with lengths 12, 14 and 0, and hidden inside a `NOP` and an unknown packet (none may draw); then `DRAW_TRI` mixed with `FILL_RECT`, `PLOT`, `DRAW_LINE` and `SET_PALETTE`, overlapping, with no fence between them.
4. **Four throughput windows** (Section 3).
5. **Back-pressure:** a screen-sized triangle, then a full-screen `FILL_RECT`, 20 short triangles and an `FB_CRC`, all sent without looking at `o_ready`. An engine that fetched during the rasterizer would run the rectangle in the middle of it.
6. **Six meshes** (Section 4), each checked by the counting buffer and followed by a `FLIP`.
7. **300 random triangles** of every kind above, an `FB_CRC` every 50, older commands and two `FLIP`s mixed in.

At the end it compares both framebuffers and the palette with the model. A wrong reply is reported with its group from `sim/tri_labels.txt`. Typical failures and their first lines:

- **Bias 0 on every edge** (`>= 0`): `FB_CRC reply 2 ... directed: Chapter 4.6's worked triangle`, then `counting check 1 fails ... 10603 pixels were written more than once (double hits); the first is x=20 y=0, written 3 times`.
- **Bias −1 on every edge** (`> 0`): the same reply, and `11136 pixels were never written (gaps); the first is x=0 y=0`.
- **Winding not normalized:** `41110 pixels were never written (gaps)`, and the throughput windows with clockwise triangles show `0 writes in -1 clocks`.
- **Box one row short at the bottom:** `FB_CRC reply 3 ... a 4x4 square`, `320 pixels were never written (gaps); the first is x=0 y=239`.
- **Wrong stride** (321 here): `framebuffer write to address 77035 (past 76,799)`.
- **x address from 8 bits only:** `15360 pixels were never written (gaps); the first is x=256 y=0`, and as many written twice, because x ≥ 256 wraps to the start of the row.
- **A 32-bit area or edge value:** `FB_CRC reply 10 ... corners at the int16 limits whose edge values need 33 bits`.
- **The engine doesn't wait in `TRI`:** `FB_CRC reply 3` is wrong, then `framebuffer write to address 76800`: the next commands interleave with the rasterizer's pixels.
- **Triangles written to the front buffer:** `write to the buffer on the screen (buffer 0) at address 0`.
- **Two clocks per box pixel:** everything matches, then `FAIL: tb_gpu_tri (throughput window 1: the triangle took 127155 clocks from first to last write, more than 64769: aim for one box pixel per clock)`.

Keep running Chapters 4.2 to 4.6's testbenches too: the new payload registers and state are easy to break. `tb_uart_gpu`, `tb_gpu_rect` and `tb_gpu_line` check the older commands, `tb_flip_sync` that `FLIP` still stalls and swaps only in blanking, and `tb_tri_raster` the rasterizer alone. `make frame-flip` still passes too. The older testbenches leave some `uart_gpu` ports unconnected, and `iverilog -Wall` warns about them; that is expected.

## 7. The Arty top

Nothing in the top changes. Copy `projects/4.5-flip/` to `projects/4.7-tri/`, set `PROJ := tri`, add `$(ROOT)/rtl/common/tri_raster.v` to `SRCS`, and set `TESTS := tb_uart_gpu tb_gpu_rect tb_gpu_line tb_flip_sync tb_tri_raster tb_gpu_tri`. Run `make`.

**A yosys trap.** This is the first time `tri_raster` goes through synthesis. If you gave its outputs initial values in one list, `output reg [15:0] o_x = 0, o_y = 0,`, openXC7's yosys stops with `SystemVerilog interface in module port list cannot have a default value` (Icarus accepts it). Declare each initialized output on its own: `output reg [15:0] o_x = 16'd0, output reg [15:0] o_y = 16'd0,`.

The reference build took 2 minutes 30 seconds (Chapter 4.5's took 45):

```
== tri: resource use (xc7z020clg400-1) ==
  SLICE_LUTX:    4873/ 106400     4%
  SLICE_FFX:     1290/ 106400     1%
  CARRY4:     364/  13300     2%
  ...
  DSP48E1:      19/    220     8%
  ...
  RAMB18E1:       1/    280     0%
  RAMB36E1:      38/    140    27%
  Max frequency for clock 'clk_pix': 149.34 MHz (PASS at 25.00 MHz)
  Max frequency for clock 'clk_sys': 64.15 MHz (PASS at 50.00 MHz)
```

- Compared with Chapter 4.5 (2,266 LUTs, 672 flip-flops, 137 `CARRY4`, 1 `DSP48E1`), `DRAW_TRI` costs about 2,600 LUTs, 620 flip-flops, 227 carry chains and 18 DSP blocks, nearly all in `tri_raster`. Your numbers depend on how you wrote it: the reference computes the starting edge values and their row-start copies as separate products, which is why it uses more DSP blocks than Chapter 4.6's estimate of 8.
- `RAMB36E1` is still 38. The rasterizer adds no memory.
- **`clk_sys` reaches 64 MHz**, so 50 MHz passes, but with much less margin than Chapter 4.5's 95 MHz. The critical path now runs through `tri_raster`'s setup: a 17-bit subtraction, a multiplier and a compare. Always read the `clk_sys` line of `make report`; a `FAIL at 50.00 MHz` means the hardware may misbehave.

**If `clk_sys` fails,** spend more setup clocks; nothing else depends on how many there are, and the testbenches don't count them.

1. **Register the differences before the multipliers.** Compute x2 − x0, y1 − y0 and the others, and (xmin − x0) and the like, in extra states, so each multiplier takes its inputs straight from flip-flops. In the reference this raised `clk_sys` from 64 to 71 MHz and cut the DSP blocks from 19 to 9.
2. **One shared multiplier, serial setup.** Step through the products one per clock. Setup grows by a few tens of clocks, invisible next to a 15-byte packet, and the DSP count drops to a handful.

## 8. On the board

Wiring and programming are as in Chapter 4.2: adapter TXD to JA pin 1, RXD to JA pin 2, GND to JA pin 5, monitor on HDMI TX. Put `SW0` down (double buffered), run `make prog`, and the Chapter 4.1 test image appears. Without a board, `tri_demo.py --model` prints the CRCs the hardware must reproduce:

```
$ .venv/bin/python host/tri_demo.py --model --frames 4
frame   0: CRC d805f844
frame   1: CRC 6eb40a0a
frame   2: CRC 246b99ea
frame   3: CRC 2a5c62d7
```

1. Run

    ```
    $ .venv/bin/python host/tri_demo.py /dev/ttyUSB0
    rotating triangles: SW0 must be down, DRAW_TRI into the back buffer, FLIP after each picture, 10 s
    ...
    PASS: tri_demo (N frames checked)
    ```

    On a dark blue screen a big red triangle at the left turns clockwise, with a small white triangle on it turning twice as fast the other way. A yellow square at the right turns the other way, and a green triangle sweeps across the top, leaving at the right edge and coming back at the left. A turn takes two seconds. Each picture is built in the back buffer and `FLIP`ped, so there is no flicker or half-drawn frame.

2. **Look at the edges.** The yellow square is two triangles sharing a diagonal, and the diagonal must be invisible at every angle. A dark one-pixel line along it that comes and goes is a gap: the fill rule is wrong for that slope. The green triangle should be cut cleanly at the screen edges; anything wrapping to the other side is an address bug.
3. **Check the CRCs.** Each frame (about 85 bytes) the script sends `FB_CRC`, `FLIP` and another `FB_CRC`; the second reply comes after the swap and covers the previous picture, so waiting for it paces the script to about 59 frames per second. The script records every reply and afterwards compares every tenth frame with the model (`--check-every 1` checks all, at about 0.13 s of Python per frame):

    ```
    frame    0: hardware CRC d805f844, Python CRC d805f844
    frame   60: hardware CRC f3ee33de, Python CRC f3ee33de
    ```

    If a CRC differs, the script prints the first frame that did. `tb_gpu_tri` should have failed too; run it.
4. LD1 (overflow) stays dark, because the script waits for each frame's replies before sending the next.
5. If no reply comes, press BTN0, check the wiring and the baud rate, and try again.

**Done when:** `tb_gpu_tri` passes, `tb_uart_gpu`, `tb_gpu_rect`, `tb_gpu_line`, `tb_flip_sync` and `tb_tri_raster` still pass, the rotating triangles look right on the monitor with an invisible diagonal in the square, and `tri_demo.py` reports hardware CRCs equal to Python's.

## Testbench requirements

**Modules you provide** (in `rtl/common/`, found by `-y rtl/common`): `uart_gpu #(CLK_HZ, BAUD)` with Chapter 4.5's ports and behavior, all of whose requirements still hold, plus:

- Opcode `0x08` `DRAW_TRI`, length exactly 13: x0, y0, x1, y1, x2, y2 as little-endian signed 16-bit, then color. It writes color, into the back buffer (`o_fb_buf`), to every pixel in `gpu_model.tri_covered(...)`: Chapter 4.6's definition (integer sample points, normalized to positive area, top-left rule, zero area draws nothing, bounding box clamped to 0 ≤ x ≤ 319 and 0 ≤ y ≤ 239). Corners are full signed 16 bits, including triangles whose edge values need 33 bits. Wrong lengths are skipped and draw nothing.
- **Every pixel is written exactly once** by one `DRAW_TRI`, and **no pixel of the buffer on the screen** is ever written (`i_flip_ack` names it). The testbench counts writes per address.
- Commands run in order, and no bytes are fetched or lost while a triangle is drawn. In the back-pressure section about 310 bytes arrive right behind one screen-sized triangle, without regard to `o_ready`.
- Any triangle must finish: a fence gives up after 50 ms, and so does a flow-controlled send whose `o_ready` stays low that long.
- Throughput: in each of four windows, one triangle whose bounding box clamped to the screen has B pixels in R rows may take at most B + 2R + 16 clocks from its first framebuffer write to its last. Setup, and anything before the first write, isn't counted.
- Draw ports as in Chapter 4.2: no write at an address of 76,800 or above, and no X on the write enables.
- `tri_raster` as in Chapter 4.6 (`tb_tri_raster` still passes).

**Files from this chapter:** `tb_gpu_tri.v` to `sim/`, `ref_tri_draw.py` to `sim/py/`, `gpu_model.py` (replacing Chapter 4.5's) and `tri_demo.py` to `host/`. They also need Chapter 4.6's `raster_model.py`, Chapter 4.1's `ref_scanout.py`, `fb_test.hex` and `pal_test.hex`. Run `.venv/bin/python sim/py/ref_tri_draw.py sim` once to write `sim/tri_stream.hex`, `tri_replies.hex`, `tri_fb0.hex`, `tri_fb1.hex`, `tri_pal.hex` and `tri_labels.txt`, and commit them. The stream uses Chapter 4.4's entries plus new ones, listed at the top of `tb_gpu_tri.v`: `2004` / `2005` / `2006` zero the write counters / check every back-buffer pixel was written exactly once / check none was written twice, and `4HHH` before a `3NNN` supplies the high bits of a window limit above 4,095.

**Testbench:** `sim/tb_gpu_tri.v`, top module `tb_gpu_tri`, run from `build/sim/` by `make test` (two to three minutes). It reads `../../rtl/common/fb_test.hex`, `pal_test.hex` and `../../sim/tri_*`. It uses a 50 MHz clock and 2.5 Mbaud, models two framebuffers with a write counter per byte and an acknowledge four clocks behind `o_flip_req`, and checks every reply byte, both framebuffers, all 256 palette entries, the six counting checks, writes to the front buffer, `o_overflow`, out-of-range and X writes, and the throughput windows. It prints one `PASS` or `FAIL` line. The watchdog is 2 s simulated. By hand:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_gpu_tri.vvp sim/tb_gpu_tri.v
cd build/sim && vvp -n tb_gpu_tri.vvp
```
