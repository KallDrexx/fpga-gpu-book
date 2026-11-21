# Chapter 1.1 - VGA Timing Generator

Phase 0 gave you a build flow, a test flow and a serial port. Phase 1 puts a picture on a monitor. This chapter builds the part every later graphics step depends on: a timing generator that tells the monitor where the beam is and tells your logic which pixel to draw next. It ends with eight color bars on the screen.

By the end you will have:

- `vga_timing`, a portable module that produces `hsync`, `vsync`, `de` (display enable) and the current pixel coordinates `x`, `y`;
- `color_bars`, a small combinational module that turns `x` into eight vertical bars;
- a board project that drives the Go Board's VGA connector, and a monitor that locks onto it.

## 1. How a monitor scans

VGA comes from the CRT era. An electron beam swept the screen left to right, one line at a time, top to bottom. At the end of each line the beam had to fly back to the left edge, and at the bottom of the frame it had to fly back to the top. The beam was switched off during both flybacks, so those periods carry no picture. They are called **blanking**.

The signal on the cable mirrors that motion. It has five wires that matter here:

- **Red, green, blue**: analog voltages, 0 V (black) to 0.7 V (full brightness), sampled continuously along the line.
- **HSYNC**: a pulse once per line that tells the monitor "start the flyback to the left now".
- **VSYNC**: a pulse once per frame that says "start the flyback to the top now".

There is no pixel clock on the cable. The monitor works out the timing from the sync pulses alone, measures their periods to guess the video mode, and then samples the color wires at its own guess of the pixel rate. That is why the sync timing has to be exact while the color is forgiving: a monitor that sees the wrong sync period shows "Out of range" or nothing at all.

Modern LCD monitors have no beam, but they still accept VGA and read it the same way, so the timing rules have not changed.

## 2. The 640x480 at 60 Hz mode

You generate one pixel per clock. Each line is a fixed number of clocks, and each frame is a fixed number of lines. Both are split into the same four regions:

1. **Visible (active) area**: pixels you see.
2. **Front porch**: a short blank gap after the visible area.
3. **Sync pulse**: the sync signal is asserted.
4. **Back porch**: another blank gap before the next visible area.

The porches existed to give the CRT's deflection circuits time to settle, and to keep the picture away from the edges of the tube. For 640x480 at 60 Hz the numbers are:

| | Visible | Front porch | Sync | Back porch | Total |
| --- | --- | --- | --- | --- | --- |
| Horizontal (clocks) | 640 | 16 | 96 | 48 | **800** |
| Vertical (lines) | 480 | 10 | 2 | 33 | **525** |

Both sync pulses are **active low**: the signal sits high and drops to 0 for the pulse. Polarity is part of how monitors tell modes apart, so getting it backwards can make a monitor refuse the signal even if every count is right.

A few numbers follow from the table, and the testbench checks each of them:

- An `hsync` pulse every 800 clocks, low for 96.
- A `vsync` pulse every 800 x 525 = **420,000** clocks, low for 2 lines = 1,600 clocks.
- 640 x 480 = **307,200** visible pixels per frame.

**The clock.** The standard pixel clock is 25.175 MHz. The Go Board has a 25 MHz oscillator, and that is close enough: the frame rate becomes 25,000,000 / 420,000 = 59.52 Hz instead of 59.94 Hz, and the line rate 31.25 kHz instead of 31.47 kHz. Monitors have a tolerance window far wider than that 0.7% error, so you use the board clock directly with no PLL.

## 3. Designing the timing generator

### Two counters

The whole generator is two counters:

- `x` counts clocks across a line: 0, 1, ..., 799, then back to 0.
- `y` counts lines: it advances by one each time `x` wraps, and runs 0 to 524 before wrapping.

A useful convention, and the one this book uses, is to start each count at the first visible pixel. Then `x` and `y` are also the coordinates of the pixel being drawn, and the visible area is simply `x < 640` and `y < 480`. Everything else follows from comparisons:

- `de` is high when `x < 640 && y < 480`.
- `hsync` is low when `x` is in the range [656, 752): that is, after 640 visible clocks and 16 clocks of front porch, for 96 clocks.
- `vsync` is low when `y` is in [490, 492).

Ten bits hold either count (2^10 = 1024 > 800), so both are `[9:0]`. Your existing `counter` module already wraps at a maximum, and you can chain two of them through `o_wrap` if you like. Writing the two counters directly in `vga_timing` is just as clear and is only a few lines.

### Parameters, not magic numbers

Give the module eight parameters, defaulting to the table above:

```verilog
module vga_timing #(
    parameter H_VISIBLE = 640, parameter H_FRONT = 16, parameter H_SYNC = 96, parameter H_BACK = 48,
    parameter V_VISIBLE = 480, parameter V_FRONT = 10, parameter V_SYNC = 2,  parameter V_BACK = 33
) ( ... );
```

Derive the totals and the sync start and end positions from them with `localparam`. This pays off twice. It is easy to check (every number in the logic traces back to one row of the table), and Phase 3 reuses this module for HDMI where the same idea applies. The testbench also runs a second copy with small, odd parameters to catch any 640 or 800 written directly into the logic.

### Keep everything in step

The most important property of the generator is that `x`, `y`, `de`, `hsync` and `vsync` all describe **the same clock cycle**. If `de` is high, the `x` on the same cycle is a visible column. If `hsync` is low, `x` is somewhere in 656 to 751.

The simplest way to get this is to register only the two counters and compute `de`, `hsync` and `vsync` combinationally from them (`assign`). The testbench requires this alignment, and checks it on every cycle.

You may register the three flags instead (it removes a little combinational logic from their paths). If you do, you must compare against the *next* value of the counters, or delay `x` and `y` by one cycle too, so that everything still lines up. A `de` that is one cycle late is the classic bug here: the picture shifts one pixel and the frame dump in Chapter 1.2 will show it.

### Power-up state

Earlier modules in this book have no reset, and this one does not need one either. The counters free-run, and wherever they start they reach a correct frame within one frame time. Do give the registers an initial value (`reg [9:0] r_x = 0;`). The iCE40 loads that value at configuration, and in simulation it keeps the outputs from being `x` forever. The testbench fails if it sees an unknown value on any output after the first couple of clocks.

## 4. Color on the Go Board

The Go Board's VGA connector is driven by a simple resistor DAC: **3 bits per channel**, 9 bits in total, so 512 colors. In the pin file these are `o_VGA_Red_0` to `o_VGA_Red_2`, `o_VGA_Grn_0..2` and `o_VGA_Blu_0..2`, with bit 2 the most significant. The sync outputs are `o_VGA_HSync` and `o_VGA_VSync`.

One rule matters more than any other: **color must be black outside the visible area**. Monitors measure the black level during blanking. If you send color in the porches, the monitor's idea of black shifts and the picture looks washed out or tinted, or the monitor fails to lock at all. Gate every color output with `de`.

### Color bars

Eight bars of 80 pixels fill 640 columns. Using only full-on and full-off for each channel gives exactly the eight colors a 1-bit-per-channel display can show. The classic test-pattern order, left to right, is:

| Bar | x range | Color | R G B |
| --- | --- | --- | --- |
| 0 | 0-79 | white | 1 1 1 |
| 1 | 80-159 | yellow | 1 1 0 |
| 2 | 160-239 | cyan | 0 1 1 |
| 3 | 240-319 | green | 0 1 0 |
| 4 | 320-399 | magenta | 1 0 1 |
| 5 | 400-479 | red | 1 0 0 |
| 6 | 480-559 | blue | 0 0 1 |
| 7 | 560-639 | black | 0 0 0 |

"1" means the channel's 3 bits are `3'b111`, and "0" means `3'b000`. Two hints:

- Finding the bar number needs a divide by 80, which is not a power of two. A chain of seven comparisons (`x < 80`, `x < 160`, ...) is simple and synthesizes to very little. Do not use `x / 80` and hope.
- Look at the R, G and B columns as functions of the bar number's three bits. Each channel is the inverse of one of those bits, so once you have the bar number the color is three single-bit expressions.

Make `color_bars` purely combinational, with `x` and `de` in and nine color bits out. It then adds no delay, and its output lines up with `hsync` and `vsync` from the same cycle.

## 5. The board project

Create `projects/1.1-vga-bars/` with a `Makefile` and a `top_goboard.v`, as in Phase 0. The thin `top_goboard.v` has `i_Clk` and the eleven VGA outputs as ports, and does three things:

1. Instantiates `vga_timing` and `color_bars`.
2. Registers the two syncs and nine color bits in one flip-flop stage before they leave the chip.
3. Maps the color buses onto the individual `o_VGA_*` pins.

Step 2 is good practice for any signal that leaves the FPGA. Combinational outputs can glitch as their inputs settle, and a glitch on a sync wire is a false sync pulse to the monitor. A register on each output removes glitches. Because all eleven signals pass through the same stage, they stay aligned with each other.

**Check your pin file.** The VGA pins on the Go Board are: HSync 26, VSync 27; Red 36, 37, 40; Green 29, 30, 33; Blue 28, 41, 42 (bit 0 first in each list). Compare your `boards/goboard/goboard.pcf` with this list and with the board schematic. If a pin number is wrong, nextpnr may stop with `package does not have a pin named ...`. Worse, the design may build and simply show the wrong colors.

### What the build should report

The reference design used to write this chapter gives the following after `make report`:

| Resource | Used | Available |
| --- | --- | --- |
| Logic cells (ICESTORM_LC) | 119 | 1,280 (9%) |
| Block RAM | 0 | 16 |
| I/O pins | 12 | 112 |
| Global buffers | 1 | 8 |

Fmax comes out near 190 MHz, far above the 25 MHz needed. About half of those logic cells are carry chains that yosys uses for the comparators. Your number will differ a little depending on how you write the comparisons. If yours is several times larger, look for a stray multiply or divide.

## 6. Bringing it up on the monitor

Program the board (`make prog`) with a VGA monitor connected, then check:

1. **The monitor locks.** It shows a picture rather than "No signal" or "Out of range". If the monitor has an info menu, it should report 640x480 at about 60 Hz (some show 59 Hz).
2. **Eight bars**, in the order of the table, white on the left and black on the right, each one-eighth of the width.
3. **The picture is stable**: no rolling, no tearing, no jitter at the bar edges.
4. **Edges.** Use the monitor's "auto adjust" button once. The white bar should touch the left edge, and the blue bar should end at seven-eighths of the width.

When it does not work:

| Symptom | Likely cause |
| --- | --- |
| "No signal" | Sync pins wrong or not toggling, or the bitstream did not load |
| "Out of range" / wrong mode | Wrong totals, or a sync with the wrong polarity |
| Picture rolls vertically | `vsync` period or pulse wrong; check `y` wrap |
| Bars wrong colors | Color pins swapped in the `.pcf` or in the top-level mapping |
| Washed-out or tinted image | Color not blanked outside `de` |
| Image shifted sideways | Porches swapped (front and back porch exchanged) |

The testbench catches all of the timing causes, so run it before you reach for the monitor.

## Testbench requirements

Copy `tb_vga_timing.v` and `tb_color_bars.v` from this chapter into `sim/`. You provide:

- **`rtl/common/vga_timing.v`**, module `vga_timing`
  - Parameters (integer, defaults shown): `H_VISIBLE=640`, `H_FRONT=16`, `H_SYNC=96`, `H_BACK=48`, `V_VISIBLE=480`, `V_FRONT=10`, `V_SYNC=2`, `V_BACK=33`.
  - Ports: `input i_clk`, `output o_hsync`, `output o_vsync`, `output o_de`, `output [9:0] o_x`, `output [9:0] o_y`.
  - Requirements: `o_x`/`o_y` start at the first visible pixel and wrap at the totals. `o_de`, `o_hsync` and `o_vsync` match `o_x`/`o_y` in the same cycle. Both syncs are active low. No outputs are unknown after power-up (use initial values; there is no reset port).
- **`rtl/common/color_bars.v`**, module `color_bars` (combinational)
  - Ports: `input [9:0] i_x`, `input i_de`, `output [2:0] o_red`, `output [2:0] o_grn`, `output [2:0] o_blu`.
  - Requirements: eight 80-pixel bars in the order of the table in Section 4, each channel `3'b111` or `3'b000`, and all zero when `i_de` is low.

Run them with `make sim-tb_vga_timing` and `make sim-tb_color_bars`, or both inside `make test`. By hand:

```
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -o build/tb.vvp sim/tb_vga_timing.v && vvp -n build/tb.vvp
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -o build/tb.vvp sim/tb_color_bars.v && vvp -n build/tb.vvp
```

`tb_vga_timing` simulates about three frames (roughly 1.3 million clocks), so expect it to take 10-15 seconds. Add `+vcd` for a waveform, but the file is large. The project Makefile should list both in `TESTS`.
