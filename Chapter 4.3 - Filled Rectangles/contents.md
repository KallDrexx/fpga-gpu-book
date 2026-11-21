# Chapter 4.3 - Filled Rectangles

Chapter 4.2's engine can clear the screen and plot single pixels. This chapter adds the first real drawing primitive, `FILL_RECT`. It is one more line in `EXEC` and two more states, but it brings in the three ideas that every later primitive reuses: signed coordinates that may lie off the screen, **clipping** them before any work is done, and an inner loop that writes **one pixel per clock** with an address that is stepped, not computed.

By the end of this chapter you will have:

- `rtl/common/cmd_engine.v` extended with `FILL_RECT` (opcode `0x05`),
- `host/gpu_model.py` replaced by this chapter's version, which models `FILL_RECT`,
- `sim/tb_gpu_rect.v` passing against `sim/py/ref_rect.py`, and Chapter 4.2's `tb_uart_gpu` still passing,
- `projects/4.3-rect/` on the board, and `host/rect_test.py` showing that the hardware CRC after 500 random rectangles equals Python's.

## 1. The command

| Opcode | Command | Length | Payload |
| --- | --- | --- | --- |
| `0x05` | `FILL_RECT` | 9 | x0, y0, x1, y1 (signed 16-bit, little-endian), color |

`FILL_RECT` writes `color` to every pixel (x, y) on the screen with

    min(x0, x1) <= x <= max(x0, x1)    and    min(y0, y1) <= y <= max(y0, y1)

That definition makes three choices:

- **Both corners are inclusive.** (x0, y0) and (x1, y1) are pixels of the rectangle. A rectangle from (5, 7) to (5, 7) is that one pixel, and x0 == x1 is a one-pixel-wide column. The other common convention, half-open [x0, x1), needs x1 = 320 to reach the right edge, a coordinate that isn't on the screen. Inclusive corners match `PLOT`'s single point, Chapter 4.4's line endpoints, and the vertices of Chapter 4.7's triangles, so an outline drawn with four `DRAW_LINE`s covers exactly the edge pixels of the filled rectangle. The cost is that an empty rectangle can't be expressed. A host that wants nothing drawn sends nothing.
- **The corners may come in either order.** (30, 20, 10, 40) is the same rectangle as (10, 20, 30, 40). A host that gets corners from moving objects, a drag box, or a bar chart with negative values doesn't have to sort them. The alternative, "inverted means empty", belongs with half-open corners and makes the drawing depend on the order the host happened to use. Sorting costs two 16-bit signed compares and some multiplexers, once per command.
- **Coordinates are signed 16-bit and anything off the screen is clipped away.** A rectangle partly off the screen draws its visible part. One entirely off the screen draws nothing and takes no time.

"Zero-size" in this chapter's tests therefore means x0 == x1 or y0 == y1. These are lines and single pixels, never nothing.

Two examples, byte by byte:

    05 09  0A 00 14 00 0D 00 16 00  07   (10, 20) to (13, 22), color 7: 4 x 3 pixels
    05 09  FB FF 64 00 4A 01 32 00  2A   (-5, 100) to (330, 50): draws x 0-319, y 50-100

A `FILL_RECT` with any length other than 9 is skipped like any other wrong-length packet.

## 2. Clip before the loop

The rectangle from (-32768, -32768) to (32767, 32767) is legal. It has 4.3 billion points. A loop that walked all of them and wrote only the visible ones would take 86 seconds at 50 MHz. Clip the rectangle to the screen first, and the same command is 76,800 writes, 1.5 ms. A rectangle entirely off the screen is rejected before the loop starts, in a clock or two.

In Python (this is `clip_rect` in `gpu_model.py`):

```python
xa, xb = min(x0, x1), max(x0, x1)                 # sort the corners
ya, yb = min(y0, y1), max(y0, y1)
if xb < 0 or xa > 319 or yb < 0 or ya > 239:      # entirely off the screen
    return None
return max(xa, 0), max(ya, 0), min(xb, 319), min(yb, 239)
```

After this step every value fits in 9 bits (x) or 8 bits (y), and the loop never sees a negative or off-screen coordinate. The order of sorting and clamping doesn't matter: clamping each corner first and then sorting gives the same rectangle, as long as the off-screen test looks at the original, unclamped corners (clamped, a rectangle entirely left of the screen becomes a column at x = 0).

Four traps, each of which this chapter's testbench catches:

- **Signed compares.** Declare the fields `wire signed [15:0]`, and compare them with plain integer constants (`w_xb < 0`, `w_xa > 319`) or with `16'sd319`. If one operand of a compare is unsigned, for example `9'd319`, Verilog compares both as unsigned, and -1 becomes 65,535: a rectangle from -1 to 5 then becomes 5 to 65,535.
- **Don't compare by subtracting.** `x1 - x0` in 16 bits wraps when the corners are far apart: 32767 - (-32768) is -1. Compare the corners directly.
- **Clamp to 319 and 239, not 320 and 240.** The corners are inclusive, so the last column is 319. Clamping x1 to 320 writes the first pixel of the next row, and on the bottom row it writes address 76,800, past the end of the framebuffer.
- **Reject with `<` and `>`, not `<=` and `>=`.** A rectangle that ends exactly at column 0 (xb == 0) or starts exactly at column 319 is on the screen.

## 3. One pixel per clock

With the clipped corners (xs, ys) and (xe, ye) in registers, the loop is two nested counters. The obvious address is `y*320 + x`. You saw in Chapter 4.1 that the beam doesn't need a multiplier because it moves one step at a time, and neither does the rectangle:

- Compute the address of the first pixel once, `ys*320 + xs`, as `(ys << 8) + (ys << 6) + xs`. Keep a copy as `row`, the address of the current row's first pixel.
- Each clock, write `color` at `addr`. Then, if x hasn't reached xe, add 1 to x and to `addr`.
- At the end of a row, if y has reached ye, the command is done. Otherwise add 1 to y, add 320 to `row`, set `addr` to the new `row`, and set x back to xs.

The row step is from the start of the row, not from where the address ended. Adding 320 to the end of the row shifts each row right by the rectangle's width and soon writes past the end of memory.

A rectangle of w x h pixels then takes w x h clocks plus a few clocks to set up. There's no bubble between rows, since the row change happens on the same clock as the last write of the row. A full-screen fill at 50 MHz takes 1.5 ms, the same as `CLEAR`, and is about 10% of a 60 Hz frame.

Other loop structures are fine as long as they keep that rate. Computing `y*320 + x` every clock also works, since multiplying by 320 is one adder. A free clock between rows costs 240 clocks over a full screen, which doesn't matter. The testbench measures the clocks from the first write of a full-screen `FILL_RECT` to its last and allows 76,800 + 4 x 240 + 16. Setup clocks before the first write aren't counted.

## 4. In the engine

`FILL_RECT` slots into Chapter 4.2's state machine like `CLEAR`:

| State | Does |
| --- | --- |
| `EXEC` | `OP_FILL_RECT: if (r_len == 8'd9)`: sort, reject or clamp, latch xs, xe, ys, ye and the color, go to `RECT_SETUP`. A rejected rectangle goes back to `FETCH` |
| `RECT_SETUP` | `addr = row = ys*320 + xs`, x = xs, y = ys, write the first pixel, go to `RECT` |
| `RECT` | one write per clock, as in Section 3, then back to `FETCH` |

The payload sits in the argument buffer as in Chapter 4.2: x0 in bytes 0-1, y0 in 2-3, x1 in 4-5, y1 in 6-7, color in byte 8. With `S_RECT_SETUP` and `S_RECT`, Chapter 4.2's six states become eight, and a 3-bit state register is full. Widen it to 4 bits now, since Chapters 4.4-4.8 add more states.

Because the engine fetches only in `FETCH`, a long rectangle holds back the FIFO exactly as `CLEAR` does, and commands stay in order. The testbench repeats Chapter 4.2's back-pressure test with a full-screen fill followed at once by 34 more rectangles.

**Timing.** Sorting, rejecting and clamping are a chain of 16-bit signed compares and multiplexers. In the reference, `EXEC` does all of it in one clock and `RECT_SETUP` computes the address. That chain is now the slowest path in the design, as Section 7 shows, but it still reaches twice the 50 MHz clock. If yours doesn't, add a state: register the sorted corners first, then reject and clamp.

`CLEAR` is now the same as `FILL_RECT 0 0 319 239`. You can run it through the rectangle loop and delete `S_CLEAR`, or leave it alone. Both are fine, and the tests don't care.

## 5. The Python model

Replace `host/gpu_model.py` with this chapter's version. It adds `FILL_RECT = 0x05` with length 9, the builder `fill_rect(x0, y0, x1, y1, c)`, `clip_rect()` from Section 2, and a counter `Gpu.rect_pixels`. Nothing else changes, so Chapter 4.2's `ref_cmd.py` still writes the same four files byte for byte, because its stream uses only opcodes `0x80`-`0xFF` as "unknown". Run `.venv/bin/python host/gpu_model.py` to run the self-check.

Copy `ref_rect.py` to `sim/py/` and run it once. Commit the five files it writes:

```sh
.venv/bin/python sim/py/ref_rect.py sim
```

    ref_rect: 6853 bytes, 600 FILL_RECTs (2421088 pixels), 25 FB_CRC replies -> sim/rect_*
      random: 500 (337 inverted, 175 with a negative corner, 82 zero width or height, 142 entirely off screen, 132 clipped)

## 6. Simulate: `tb_gpu_rect`

Copy `tb_gpu_rect.v` to `sim/`. It is Chapter 4.2's harness, so the stream goes through your real `uart_rx`, FIFO and `uart_tx` at 2.5 Mbaud, with new files and one new measurement:

```
$ make test TESTS="tb_uart_gpu tb_gpu_rect"
  PASS  tb_uart_gpu
  PASS  tb_gpu_rect
$ cat build/sim/tb_gpu_rect.log
tb_gpu_rect: full-screen FILL_RECT: 76800 writes in 76800 clocks from first to last (77776 allowed)
PASS: tb_gpu_rect (6853 bytes sent, 25 FB_CRC replies match, 2497899 pixel writes, full-screen fill 76800 clocks for 76800 pixels)
```

It takes about 40 seconds. The stream draws on top of Chapter 4.1's test image, whose pixels all differ, so a pixel written in the wrong place always shows:

1. `FB_CRC` of the untouched test image.
2. Edge cases in 11 groups, each followed by an `FB_CRC`: single pixels at the corners, small rectangles with the corners in all four orders, lines, rectangles touching x = 319 and y = 239, rectangles clipped on each side (x1 = 320, 330, 32767; x0 = -1, -20, -32768), inverted and clipped ones, rectangles just outside each edge, far outside, or with low bits that alias onto the screen (x = 522), whole-screen spans in one axis, and the full screen four ways, including -32768 to 32767.
3. `FILL_RECT`s with lengths 8, 10 and 0, and hidden inside a `NOP` and an unknown packet. None of them may draw. `PLOT` and `SET_PALETTE` are mixed in.
4. **Throughput window:** one full-screen `FILL_RECT` on its own, measured.
5. **Back-pressure:** a full-screen fill and 34 small rectangles right behind it, sent without looking at `o_ready`.
6. **500 random rectangles**, with an `FB_CRC` every 50, plus a few `PLOT`s, unknown packets, a `CLEAR` and a palette write.

A wrong reply is reported with the group it covers, from `sim/rect_labels.txt`. These are the first lines a few typical bugs print:

- **Corners treated as exclusive:** `FB_CRC reply 2 is a9a79db7, expected 5739fc8e. It covers: edge cases: single pixels at the four corners and the centre`. `a9a79db7` is the untouched test image, so the single-pixel rectangles drew nothing.
- **Rejecting `xb <= 0` instead of `xb < 0`:** also reply 2, with a different CRC. The pixel at (0, 0) is missing.
- **Inverted corners not sorted**, or **a row stride of 319:** reply 3, `small rectangles, corners in all four orders`.
- **Unsigned compares**, or **comparing by a 16-bit subtraction:** reply 7, `clipped on the left and top`.
- **x1 clamped to 320:** `framebuffer write to address 76800 (past 76,799)`, then reply 6, `clipped on the right and bottom`.
- **Clamping without rejecting:** reply 9, `touching an edge from outside, all off screen`. The rectangles left of the screen became a column at x = 0.
- **Accepting lengths other than 9:** reply 13, the malformed-packet group.
- **Fetching bytes while the loop runs:** `fence 2 waited 50 ms`. The parser lost its place.
- **Two clocks per pixel:** everything matches, then `FAIL: tb_gpu_rect (the full-screen FILL_RECT took 153599 clocks from first to last write, more than 77776: aim for one pixel per clock)`.

A rectangle that still loops over its off-screen part never finishes the -32768 to 32767 cases, and fails with a fence timeout.

Keep running `tb_uart_gpu` too. Adding an opcode must not break the old ones.

## 7. The Arty top

Nothing in the top changes. Copy `projects/4.2-cmd/` to `projects/4.3-rect/`, set `PROJ := rect` and `TESTS := tb_uart_gpu tb_gpu_rect`, and run `make`. The reference build took 31 seconds:

```
== rect: resource use (xc7z020clg400-1) ==
  SLICE_LUTX:    1715/ 106400     1%
  SLICE_FFX:     526/ 106400     0%
  CARRY4:      98/  13300     0%
  ...
  RAMB18E1:       1/    280     0%
  RAMB36E1:      19/    140    13%
  Max frequency for clock 'clk_sys': 110.30 MHz (PASS at 50.00 MHz)
  Max frequency for clock 'clk_pix': 138.37 MHz (PASS at 25.00 MHz)
  Max frequency for clock 'clk_sys': 110.30 MHz (PASS at 50.00 MHz)
```

- Compared with Chapter 4.2 (1,330 LUTs, 426 flip-flops), `FILL_RECT` costs about 385 LUTs and 100 flip-flops. The block RAMs are unchanged.
- `clk_sys` dropped from 169 MHz to 110 MHz. The new critical path starts at the argument buffer, goes through the signed compares of the sort and then the clamp, and ends at the registers `EXEC` loads. That is Section 4's chain, and it still has more than twice the margin needed.

## 8. On the board

Wiring and programming are as in Chapter 4.2: adapter TXD to JA pin 1, RXD to JA pin 2, GND to JA pin 5, monitor on the HDMI TX port, then `make prog`.

1. The Chapter 4.1 test image appears and LD0 lights.
2. Copy `host/rect_test.py` next to `gpu_model.py` and run it. It sets eight palette colors, clears, and sends 500 random rectangles of the same kinds as the testbench, with an `FB_CRC` after every 100. With `--seed 7` the byte count and every CRC below are what `gpu_model` computes, so your hardware must print them exactly. The time is roughly what 5,561 bytes take at 115,200 baud:

    ```
    $ .venv/bin/python host/rect_test.py /dev/ttyUSB0 --seed 7
    sent 5561 bytes: 500 FILL_RECTs (seed 7; 339 inverted, 175 with a negative corner, 63 zero width or height, 164 entirely off screen, 1726316 pixels) in 0.50 s
      after rectangle  100: hardware CRC 6252f696, Python CRC 6252f696
      after rectangle  200: hardware CRC 89498a3c, Python CRC 89498a3c
      after rectangle  300: hardware CRC 50fcff8f, Python CRC 50fcff8f
      after rectangle  400: hardware CRC c1e17d2d, Python CRC c1e17d2d
      after rectangle  500: hardware CRC e885a79c, Python CRC e885a79c
    PASS: rect_test
    ```

3. The screen shows overlapping colored rectangles on dark blue, many of them cut off at the edges. LD1 (overflow) stays dark.
4. Run it a few times without `--seed`. Each run prints its seed, so a failing drawing can be repeated. If a block differs, the script names the first block of 100. `tb_gpu_rect` should fail too, so run it.
5. If there's no reply at all, press BTN0 to reset the parser, check the wiring and the baud rate, and try again.

**Done when:** `tb_gpu_rect` and `tb_uart_gpu` pass, and `rect_test.py` reports the hardware CRCs equal to Python's after 500 random rectangles.

## Testbench requirements

**Modules you provide:** `uart_gpu #(CLK_HZ, BAUD)` with Chapter 4.2's ports and behavior, all of whose requirements still hold (`tb_uart_gpu` still runs), plus:

- Opcode `0x05` `FILL_RECT`, length exactly 9: x0, y0, x1, y1 as little-endian signed 16-bit, then color. It writes color to every on-screen pixel with min(x0,x1) <= x <= max(x0,x1) and min(y0,y1) <= y <= max(y0,y1). Both corners are inclusive, the corners may come in either order, and the result is clipped to 0-319 x 0-239. Wrong lengths are skipped and draw nothing.
- Clip before looping: rectangles reaching -32768 or 32767 must finish in about the time of their visible part. A fence gives up after waiting 50 ms.
- Commands run in order, and no bytes are fetched or lost while a rectangle fills. In the back-pressure section, about 385 bytes arrive during one full-screen fill without regard to `o_ready`.
- Throughput: a full-screen `FILL_RECT` may take at most 76,800 + 4 x 240 + 16 = 77,776 clocks from its first framebuffer write to its last. Setup before the first write isn't counted, and extra writes are allowed if the final pixels are right.
- Draw ports as in Chapter 4.2: no write at an address of 76,800 or above, and no X on the write enables.

**Files from this chapter:** `tb_gpu_rect.v` to `sim/`, `ref_rect.py` to `sim/py/`, `gpu_model.py` (replacing Chapter 4.2's) and `rect_test.py` to `host/`. It also needs Chapter 4.1's `ref_scanout.py`, `fb_test.hex` and `pal_test.hex`. Run `.venv/bin/python sim/py/ref_rect.py sim` once to write `sim/rect_stream.hex`, `rect_replies.hex`, `rect_fb.hex`, `rect_pal.hex` and `rect_labels.txt`, and commit them.

**Testbench:** `sim/tb_gpu_rect.v`, top module `tb_gpu_rect`, run from `build/sim/` by `make test` (about 40 seconds). It reads `../../rtl/common/fb_test.hex`, `pal_test.hex` and `../../sim/rect_*`. It uses a 50 MHz clock and 2.5 Mbaud, and checks every reply byte, all 76,800 framebuffer bytes, all 256 palette entries, `o_overflow`, out-of-range and X writes, and the throughput window. It prints one `PASS` or `FAIL` line. The watchdog is 400 ms simulated. By hand:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_gpu_rect.vvp sim/tb_gpu_rect.v
cd build/sim && vvp -n tb_gpu_rect.vvp
```
