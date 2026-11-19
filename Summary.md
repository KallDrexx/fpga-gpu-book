# FPGA Graphics Pipeline Learning Plan

Nov 18, 2026 · @Matthew Shapiro

## Overview

The plan takes you from blinking an LED to a small GPU that takes drawing commands over SPI from a microcontroller and renders shaded 3D. It has 41 steps of one to two days each, roughly 65 working days in total, split into seven phases numbered to match the steps:

- **Phase 0, Foundations (Go Board):** toolchain, simulation, inputs, UART.
- **Phase 1, Video basics (Go Board):** VGA timing, dumping frames to images, motion.
- **Phase 2, Retro arcade games (Go Board):** Pong, a tile engine, Snake, hardware sprites, scrolling driven over UART.
- **Phase 3, Move to the Arty Z7:** open-source Xilinx flow, clocking, HDMI out, porting the games.
- **Phase 4, Framebuffer and 2D rasterization:** command processor, rectangles, lines, double buffering, triangles, sprite blitter.
- **Phase 5, Scenes and the MCU protocol:** SPI link, tile layers, sprite table, compositing, streamed scrolling, display lists.
- **Phase 6, Basic 3D:** fixed-point math, interpolation, depth buffer, vertex transform, culling, a rotating shaded model.

Every step ends with a **Done when** check you can see for yourself: a self-checking testbench that prints PASS, a simulated frame saved as an image and compared against a Python reference, or a picture on the monitor.

**The verification harness you'll reuse everywhere.** You build it in Step 1.2 and extend it in every later phase.

- Icarus Verilog testbenches that check results and end with one `PASS` or `FAIL` line, run with `make test`.
- A frame-dump testbench that runs one full frame and writes it to a PPM image, which any image viewer opens.
- A Python golden model (numpy and Pillow) for each rendering block. A test passes when the Verilog frame matches the Python frame pixel for pixel, or within a stated tolerance for 3D.
- From Phase 4 on, a `FB_CRC` command that returns a checksum of the framebuffer, so you can check the real hardware against the Python model too.
- Optional: Verilator plus SDL2 to watch a design run live in a window.

**Ground rules that save you later**

- Keep board-specific code in a thin `top_goboard.v` or `top_artyz7.v`. Everything else stays portable.
- Commit each step with its testbench. Rerunning `make test` then shows at once when a new step breaks an old one.
- Read the resource report (LUTs, block RAM, DSPs) after every build. On the Go Board you will run out, and knowing your budget is part of the lesson.

&#91;embedded content: finished GPU architecture · step numbers on each block\]

Assets, tiles and meshes are uploaded once through the same command path. After that, each frame carries only scroll offsets, sprite positions, a few draw commands or a matrix. The numbers on each block are the steps that build it.

## Hardware and toolchain

The Go Board is too small for a framebuffer, so it teaches beam-racing graphics. The Arty Z7 has enough block RAM for framebuffers, tile layers and a depth buffer, all without touching DDR.

|  | Nandland Go Board | Arty Z7-20 |
| --- | --- | --- |
| FPGA | iCE40 HX1K, VQ100 | Zynq XC7Z020 |
| Logic | 1,280 logic cells | 85K logic cells |
| Block RAM | 64 Kbit (8 KB) | 4.9 Mbit (about 560 KB usable as bytes) |
| Multipliers | none | 220 DSP slices |
| Clock into fabric | 25 MHz | 125 MHz (pin H16) |
| Video out | VGA connector | HDMI source port, wired straight to the fabric |
| USB serial | reaches the FPGA | wired to the processor side, not the fabric |
| Open toolchain | yosys, nextpnr-ice40, IceStorm | yosys, nextpnr-xilinx, Project X-Ray (openXC7) |

Sources: [Go Board specs](https://www.cnx-software.com/2015/12/08/nandland-go-board-is-a-50-board-for-learning-fpga-vhdl-verilog-programming-crowdfunding/), [Arty Z7 reference manual](https://reference.digilentinc.com/programmable-logic/arty-z7/reference-manual), [Zynq-7000 family table](https://www.design-reuse.com/exit/?urlid=418), [openXC7](https://github.com/openXC7).

**What these limits mean for the plan**

- **No framebuffer on the Go Board.** A 640x480 screen at 1 bit per pixel is 37.5 KB, more than four times the chip's RAM. Every Go Board picture is computed pixel by pixel as the beam scans, the way Pong-era and NES-era hardware worked.
- **No PLL needed on the Go Board.** 25 MHz is close enough to the 25.175 MHz VGA standard that monitors accept it. Sources disagree on whether the VQ100 package has a usable PLL, so the plan never relies on one.
- **Check the Go Board schematic for VGA color depth.** It has only a few resistor-DAC bits per channel, so design with a small palette.
- **Buy a 3.3 V USB-serial adapter for the Arty Z7.** The board's own USB-UART reaches only the processor, so the fabric needs an FTDI or CP2102 adapter (or a Digilent Pmod USBUART) on a Pmod pin. It also stands in for the MCU until Phase 5.
- **Stay in block RAM on the Arty Z7.** The DDR3 sits behind the processor and needs processor configuration first. The Z7-20's block RAM holds every buffer in this plan at once, 2D layers and 3D depth buffer included.
- **You generate HDMI yourself.** The HDMI data and clock pairs connect straight to the fabric, so Step 3.3 writes the TMDS encoder.

**Tools to install**

- **OSS CAD Suite** (YosysHQ's prebuilt bundle): yosys, nextpnr-ice40, IceStorm (`icepack`, `iceprog`), Icarus Verilog, Verilator, GTKWave and openFPGALoader in one download.
- **openXC7** for the Arty Z7: yosys `synth_xilinx`, then nextpnr-xilinx, then Project X-Ray's `fasm2frames` and `xc7frames2bit`. A [worked Arty Z7-20 blinky](https://hub.mender.io/t/building-and-packaging-fpga-bitstreams-for-the-zynq-7000-without-vivado/8323) shows the full Makefile. Program over JTAG with openFPGALoader.
- **Python 3** with numpy, Pillow and pyserial for golden models, image comparison and host scripts.
- **Fallback:** if a Xilinx primitive (MMCM, OSERDESE2) misbehaves under openXC7, build that one design in free Vivado. That tells a tool bug from your bug; then return to the open flow.

**Parts to have on hand**

- A monitor with VGA input (or an active VGA-to-HDMI adapter), and one with HDMI that accepts 640x480.
- The USB-serial adapter above.
- A cheap 8-channel logic analyzer that works with sigrok/PulseView, for debugging UART and SPI.
- A USB-SPI bridge such as an FT232H board (optional), to test the SPI link from a PC before the MCU code exists.

## Phase 0: Foundations on the Go Board (about 6 days)

This phase ends with a working build and test flow and a UART register interface, the seed of the GPU's command port.

### 0.1 Toolchain and blink (1 day)

- **Learn:** module structure, `always @(posedge clk)`, counters, the `.pcf` pin file, the flow yosys, `nextpnr-ice40 --hx1k --package vq100`, `icepack`, `iceprog`.
- **Build:** a counter that blinks an LED at 1 Hz from the 25 MHz clock; each switch lights its LED. Wrap it all in a Makefile (`make prog`).
- **Done when:** the LED blinks 60 times in 60 seconds on a stopwatch, and `make clean prog` rebuilds from nothing.

### 0.2 Simulation workflow (1 day)

- **Learn:** testbenches, blocking versus nonblocking assignments, `initial`, `$dumpvars`, reading waveforms in GTKWave.
- **Build:** a parameterized counter and a hex-to-7-segment decoder, and a testbench that checks all 16 digits against a table.
- **Done when:** `make test` prints PASS, and the board's 7-segment display counts 00 to FF at one count per second.

### 0.3 Inputs done right (1 day)

- **Learn:** metastability, the two-flop synchronizer, debouncing, edge detection.
- **Build:** a button that adds exactly one to the display per press.
- **Done when:** the testbench feeds a bouncy press (random glitches over 5 ms) and sees exactly one increment, and 20 real presses show 0x14.

### 0.4 UART transmit and receive (1–2 days)

- **Learn:** state machines, baud timing (115,200 baud is about 217 clocks per bit at 25 MHz), sampling at mid-bit.
- **Build:** a UART echo that also shows the last received byte on the display.
- **Done when:** a loopback testbench (transmitter into receiver) passes for all 256 byte values, and a pyserial script sends 10,000 random bytes and gets every one back.

### 0.5 FIFO and register protocol (1 day)

- **Learn:** inferring block RAM, full and empty flags, a simple packet parser.
- **Build:** a FIFO behind the UART receiver and a parser for two packets: `WRITE addr data` and `READ addr`, which replies with the value. Map register 0 to the LEDs.
- **Done when:** the testbench fills and drains the FIFO with flags checked at every count, and a Python script sets the LEDs and reads back every register it wrote.

## Phase 1: Video signal basics (about 4 days)

This phase ends with a stable 640x480 picture and the frame-dump harness that verifies every later graphics step.

### 1.1 VGA timing generator (1–2 days)

- **Learn:** how a monitor scans. At 640x480 and 60 Hz, a line is 800 clocks (640 visible, 16 front porch, 96 sync, 48 back porch) and a frame is 525 lines (480 visible, 10, 2, 33). Both sync pulses are active low.
- **Build:** a module that outputs `hsync`, `vsync`, `de` (display enable) and the current `x`, `y`. Drive color bars from `x`.
- **Done when:** the testbench measures an `hsync` period of 800 clocks with a 96-clock pulse, a `vsync` period of 420,000 clocks with a 2-line pulse, and exactly 307,200 `de` cycles per frame. On the board, the monitor locks and shows eight bars.

### 1.2 Frame-dump harness (1 day)

- **Learn:** file output from testbenches (`$fopen`, `$fwrite`), the PPM image format, and comparing images in Python.
- **Build:** a testbench that runs one frame and writes every `de` pixel to `frame.ppm`, and a `compare.py` that diffs it against an expected image and prints the first mismatched pixel. Patterns: checkerboard, gradient, and a 1-pixel border on all four edges.
- **Done when:** `make frame` matches the Python-drawn reference exactly, and all four border lines are visible on the monitor. A missing edge means an off-by-one in your timing, the most common bug at this stage.

### 1.3 Moving objects (1 day)

- **Learn:** keeping slow "game logic" (once per frame) apart from fast pixel logic, and why changing positions mid-frame tears the picture.
- **Build:** a square that bounces off the screen edges, updated only at the start of vertical blanking.
- **Done when:** a testbench dumps two consecutive frames and checks the square moved by exactly (dx, dy), and the board shows smooth motion with no tearing.

## Phase 2: Retro arcade games, racing the beam (about 9 days)

This phase ends with a tile-and-sprite engine that scrolls smoothly while the PC sends only a few bytes per frame. That idea, sending changes instead of pixels, carries through the rest of the project.

All Go Board graphics here render at 320x240 (each logical pixel drawn 2x2), so a screen is 40x30 tiles of 8x8.

### 2.1 Pong (2 days)

- **Learn:** per-pixel comparators ("is this pixel inside the ball?"), a game-state machine (serve, play, point scored), reading LUT usage in the nextpnr report.
- **Build:** two button-driven paddles, a ball, wall and paddle collisions, scores on the 7-segment display.
- **Done when:** a testbench forces ball positions and checks each outcome (bounce off paddle, bounce off wall, point scored), and the game is playable on the board.

### 2.2 Font ROM and text mode (1–2 days)

- **Learn:** block RAM loaded with `$readmemh`, and pipeline alignment. A RAM read takes one clock, so you fetch ahead and delay `hsync`, `vsync` and `de` by the same number of clocks.
- **Build:** an 8x8 font ROM and a 40x30 character RAM (1,200 bytes). Show a screen of text.
- **Done when:** a frame dump of a test string matches Python's rendering of the same font file pixel for pixel, with no 1-pixel shift.

### 2.3 Tile engine and Snake (2 days)

- **Learn:** dual-port block RAM (the game writes one port, the video reads the other), a linear-feedback shift register for random numbers.
- **Build:** turn text mode into a tile engine (8x8 tiles, 2 bits per pixel, 4-color palette). Snake's game logic writes the tile map.
- **Done when:** a testbench plays a scripted sequence of moves and checks the map (snake length, food eaten, self-collision ends the game), and Snake is playable on the board.

### 2.4 Hardware sprites (2 days)

- **Learn:** per-scanline sprite evaluation, the way the NES did it. During horizontal blanking, find which sprites cross the next line and load their bitmap rows into shift registers. This is why old consoles had a sprites-per-line limit.
- **Build:** four 16x16 sprites, 2 bits per pixel, with a transparent color and fixed priority over the tile layer. Make a small shooter: ship, enemy, bullet.
- **Done when:** frame dumps with sprites at x = 0, partly off-screen and overlapping each other all match the Python model. On the boacamerd the sprites move without flicker. Expect to be near the HX1K's LUT limit.

### 2.5 UART-driven tiles and scrolling (2 days)

- **Learn:** hardware scrolling. Add scroll offsets to the screen coordinates before the tile lookup; with a 64x32 map, power-of-two sizes make the wraparound free.
- **Build:** expose the tile map, palette, sprite positions and scroll X/Y as registers over the Step 0.5 protocol. Memory: 2 KB map, 1 KB tile graphics, well inside the 8 KB budget.
- **Done when:** a Python script draws a map once, then scrolls it at 60 frames per second by writing only the 2 scroll bytes each frame over a 115,200-baud link.

## Phase 3: Moving to the Arty Z7 (about 7 days)

This phase ends with your Phase 2 games running unchanged over HDMI, built entirely with open-source tools.

### 3.1 openXC7 bring-up (1 day)

- **Learn:** the openXC7 flow, `.xdc` constraint files, programming the fabric over JTAG with openFPGALoader. The part is `xc7z020clg400-1`.
- **Build:** a 1 Hz blink from the 125 MHz clock, plus buttons and switches to LEDs.
- **Done when:** the LED blinks 60 times a minute, and one `make prog` rebuilds and loads from a clean checkout.

### 3.2 Clock generation (1 day)

- **Learn:** the MMCM (`MMCME2_BASE`), the `LOCKED` signal, reset synchronizers, why each clock domain gets its own reset.
- **Build:** from 125 MHz, make a 25 MHz pixel clock, a phase-aligned 125 MHz serial clock (5x pixel, for 10-bit DDR output), and a 50 MHz system clock for the drawing engine in Phase 4.
- **Done when:** counters in each domain drive LEDs that blink at the expected ratios, and a `LOCKED` LED stays lit.

### 3.3 TMDS encoder (1–2 days, simulation only)

- **Learn:** DVI's 8b/10b TMDS coding: transition minimizing, DC balancing with a running disparity counter, and the four control symbols sent during blanking.
- **Build:** the encoder, plus a decoder in Verilog or Python to check it.
- **Done when:** a testbench round-trips all 256 values and long random streams through encode and decode, and the running disparity stays within bounds.

### 3.4 HDMI (DVI) output (1–2 days)

- **Learn:** `OSERDESE2` in 10:1 DDR mode (a master and slave pair), `OBUFDS` differential outputs. The clock channel just sends `1111100000`.
- **Build:** Step 1.1's timing generator feeding three TMDS encoders and serializers. Show color bars.
- **Done when:** the monitor shows the bars and its info menu reports 640x480 at 60 Hz. If nothing appears, build the same design in Vivado once to separate a tool problem from a design problem.

### 3.5 Port the Go Board games (1 day)

- **Learn:** keeping cores portable behind board-specific top modules.
- **Build:** Pong and the Step 2.5 tile engine on the Arty Z7, now with 8 bits per color channel and the UART on a Pmod pin through your USB-serial adapter.
- **Done when:** every Phase 1 and 2 testbench passes unchanged, and both games run over HDMI.

## Phase 4: Framebuffer and 2D rasterization (about 13 days)

This phase ends with a command-driven 2D renderer: clear, rectangles, lines, triangles and sprite blits into a double-buffered framebuffer, each checked pixel for pixel against Python.

**Design choices for this phase**

- **Resolution and color:** 320x240, shown 2x on the 640x480 output, at 8 bits per pixel through a 256-entry RGB888 palette. One buffer is 76,800 bytes; two use about a quarter of the Z7-20's block RAM.
- **Two clock domains:** the drawing engine runs on the 50 MHz system clock and scanout on the 25 MHz pixel clock. A true dual-port block RAM with one port per clock connects them safely.
- **Throughput:** at one pixel per clock, a 60 Hz frame allows about 830,000 pixel writes, so a full clear (1.5 ms) is cheap.

### 4.1 Framebuffer scanout (1–2 days)

- **Build:** the framebuffer RAM preloaded with a test image, a scanout reader that doubles each pixel and line, and the palette lookup.
- **Done when:** the simulated frame matches the source image scaled 2x exactly, and the image appears on the monitor.

### 4.2 Command processor (2 days)

- **Learn:** packet framing, back-pressure, a FIFO between the transport and the engine.
- **Build:** packets of `[opcode][length][payload]` arriving over UART. First opcodes: `NOP`, `SET_PALETTE`, `CLEAR color`, `PLOT x y color`, and `FB_CRC`, which returns a CRC-32 of the framebuffer so you can check real hardware against Python.
- **Done when:** a testbench sends a byte stream and checks framebuffer contents, and a host script plots 1,000 random pixels and the hardware CRC equals the Python CRC.

### 4.3 Filled rectangles (1 day)

- **Build:** `FILL_RECT x0 y0 x1 y1 color`, clipped to the screen, writing one pixel per clock.
- **Done when:** 500 random rectangles, including off-screen, negative, zero-size and inverted corners, match the Python model in simulation and by CRC on hardware.

### 4.4 Lines (1–2 days)

- **Learn:** Bresenham's algorithm, which uses only integer adds and compares. It is practice for the edge stepping in triangles.
- **Build:** `DRAW_LINE x0 y0 x1 y1 color`.
- **Done when:** lines in all eight octants, plus horizontal, vertical and single-point lines, match a Python Bresenham with the same tie-breaking, and a starburst looks right on screen.

### 4.5 Double buffering and vsync (1 day)

- **Build:** two buffers and a `FLIP` command that swaps the scanout buffer only during vertical blanking; drawing stalls until the swap happens. Add a `VSYNC` output pin for the host.
- **Done when:** the testbench shows swaps occur only in blanking, and animated rectangles that tear with one buffer are clean with two.

### 4.6 Triangle rasterizer, the math (2 days, simulation)

- **Learn:** edge functions. For edge (x0, y0) to (x1, y1), E(x, y) = (x − x0)(y1 − y0) − (y − y0)(x1 − x0). A pixel is inside when all three edges agree in sign, with the top-left fill rule for pixels exactly on an edge. Stepping one pixel in x or y changes E by a constant, so the inner loop is additions only.
- **Build:** bounding-box traversal with incremental edge functions.
- **Done when:** Verilog and a Python model agree on 10,000 random triangles in a 64x64 test frame, including degenerate and clockwise ones.

### 4.7 Triangle rasterizer, integration (1–2 days)

- **Build:** `DRAW_TRI` with screen clipping of the bounding box and either winding order accepted.
- **Done when:** a shared-edge test passes: a grid of triangles drawn into a counting buffer in simulation writes every pixel exactly once, with no gaps or double hits. A rotating triangle looks right on the monitor.

### 4.8 Sprite blitter (2 days)

- **Learn:** keeping assets resident in GPU memory so the host uploads them once.
- **Build:** a 32 KB asset RAM, `UPLOAD_ASSET id w h data`, and `BLIT id x y flags` with a transparent color index, X and Y flip, and clipping.
- **Done when:** random blits, including partly off-screen ones, match the Python model, and on hardware 20 animated sprites run after one upload, using only `BLIT` commands each frame.

## Phase 5: Scenes, layers, scrolling and the MCU protocol (about 14 days)

This phase ends with a parallax side-scroller driven by an MCU over SPI using under 1 KB per frame. The MCU uploads assets once, then sends only changes: scroll offsets, sprite positions, a few draw commands, and new map columns as they scroll into view. A 1 MHz SPI link carries about 2 KB per frame at 60 Hz, so the budget leaves headroom even for a slow MCU.

**Memory budget on the Z7-20.** Block RAM comes in 36 Kbit blocks that each hold 4 KB of byte-wide data, so the Z7-20 has about 560 KB usable.

| Buffer | Size |
| --- | --- |
| Framebuffer layer, 2 x 320x240 at 8 bpp | 150 KB |
| Sprite and asset RAM | 32 KB |
| Tile graphics, 256 tiles of 8x8 at 8 bpp | 16 KB |
| Two tile maps, 64x64 entries of 16 bits | 16 KB |
| Sprite table and line buffers | about 2 KB |
| **Total** | **about 216 KB** |

That leaves about 340 KB free, so the Phase 6 depth buffer fits in the same build as all the 2D layers.

### 5.1 SPI slave transport (2 days)

- **Learn:** oversampling a slow external clock. Synchronize `SCK`, `MOSI` and `CS` into the 50 MHz domain with two flops each and detect `SCK` edges there. This is simple and safe for `SCK` up to about 6 MHz.
- **Build:** an SPI mode-0 slave feeding the same command FIFO as the UART, so the parser doesn't care which link a byte came from. Add a `READY` output (FIFO nearly full, the MCU must wait) and the `VSYNC` pin from Step 4.5. `MISO` returns a status byte and replies.
- **Done when:** a testbench with an SPI master model replays the Phase 4 test streams and produces identical framebuffers. On hardware, a USB-SPI bridge or the MCU sends a test and the CRC matches; check the waveforms on the logic analyzer.

### 5.2 Protocol specification and hardening (1 day)

- **Build:** a written spec: packet framing, opcode table, byte order, coordinate formats, a `GET_INFO` command returning version and capabilities, and error flags. Raising `CS` always resets the parser, so the MCU can resynchronize after any error.
- **Done when:** a fuzz testbench sends random garbage followed by valid packets, and after each `CS` toggle the GPU recovers and draws correctly; an assertion fails if the parser ever hangs.

### 5.3 Hardware tile layers (2 days)

- **Build:** two background layers, each a 64x64 map of 8x8 tiles at 8 bits per pixel, with per-layer scroll X/Y and wraparound. Like Step 2.5, they render during scanout and cost no framebuffer memory.
- **Done when:** frame dumps at several scroll offsets, including across the wrap point, match the Python model, and a two-layer parallax demo scrolls smoothly on screen.

### 5.4 Sprite table (2 days)

- **Learn:** double-buffered line buffers: while one scanline displays, the next is assembled.
- **Build:** a 64-entry sprite table (x, y, asset id, palette, flip, priority) with at most 16 sprites per line, rendered into the line buffers.
- **Done when:** frame dumps match the Python model, including what happens past the per-line limit, and the host moves 32 sprites by rewriting 4 bytes each.

### 5.5 Layer compositor (1 day)

- **Build:** a per-pixel priority mix of the two tile layers, the framebuffer layer and the sprites. Index 0 is transparent; each layer has enable and priority bits.
- **Done when:** a scene with every layer overlapping matches the Python compositor, and toggling layers live works on screen.

### 5.6 Streaming large maps (2 days)

- **Learn:** the NES and Genesis trick: the map in GPU memory is only a window onto a much larger level. When the scroll crosses a tile boundary, the host writes just the newly exposed column.
- **Build:** `WRITE_TILE_COLUMN` and `WRITE_TILE_ROW` (64 entries, 128 bytes each).
- **Done when:** a simulation scrolls through a 256-tile-wide test level and every frame matches the reference, and on hardware a long level scrolls at under 200 bytes per frame.

### 5.7 Display lists (1–2 days)

- **Build:** store a command sequence in GPU RAM, run it with `RUN_LIST id` or automatically every vsync, and patch single words with `WRITE_LIST_WORD`.
- **Done when:** a HUD redraws every frame while the host sends only the changed score digits.

### 5.8 2D capstone: side-scroller (2 days)

- **Build:** a parallax background, 20 or more sprites and a HUD, driven by the MCU over SPI. Log the bytes sent each frame.
- **Done when:** it runs at 60 Hz under 1 KB per frame, and replaying the logged byte stream into the simulator produces the same frames.

## Phase 6: Basic 3D (about 14 days)

This phase ends with a rotating, smoothly shaded model where the MCU sends only one transform matrix per frame (about 48 bytes). The mesh is uploaded once; the GPU transforms vertices, projects them, culls back faces, and rasterizes with a depth buffer.

**Design choices for this phase**

- **Shading through palette ramps:** split the 256-color palette into 8 ramps of 32 shades. A triangle has a base ramp and the rasterizer interpolates only an intensity, so you keep 8-bit pixels and get smooth Gouraud shading.
- **Memory:** render at 320x240. 3D reuses the two Phase 4 color buffers and adds only a 16-bit depth buffer (150 KB), so the build stays under 400 KB and the tile and sprite layers remain available for a HUD or background behind the 3D scene.
- **Throughput:** a 200-triangle model averaging 500 pixels per triangle is 100,000 pixels, about 2 ms at 50 MHz.

### 6.1 Fixed-point math and multipliers (1–2 days)

- **Learn:** Q formats (for example Q16.16 positions, Q1.15 matrix entries), rounding and saturation, pipelined multiply-accumulate, checking that yosys mapped it to DSP48 slices.
- **Build:** a pipelined signed multiply-accumulate unit.
- **Done when:** it matches a Python fixed-point model bit for bit on 100,000 random inputs, and the synthesis report shows DSP slices used.

### 6.2 Divider and reciprocal (1–2 days)

- **Learn:** why division is expensive in hardware. A sequential divider produces one bit per clock; a reciprocal table plus one Newton step is faster.
- **Build:** one of the two, signed, with a `busy` and `done` handshake.
- **Done when:** it is bit-exact against Python for random inputs and edge cases (tiny divisors, negatives, the largest values).

### 6.3 Attribute interpolation (2 days)

- **Learn:** per-triangle setup. One division by the triangle's area gives the change in each attribute per pixel step in x and y; the inner loop then adds, just like the edge functions.
- **Build:** extend the Step 4.7 rasterizer to interpolate intensity (for the palette ramp) and depth.
- **Done when:** frame dumps match a Python model using the same fixed-point math exactly, and a triangle with three different vertex intensities shows a smooth gradient on screen.

### 6.4 Depth buffer (2 days)

- **Learn:** read-compare-write pipelines and the hazard when neighboring pixels hit the same address, and clearing depth in the same pass as color.
- **Build:** a 16-bit depth buffer with compare-and-write, and a performance counter for pixels per clock.
- **Done when:** two intersecting triangles show the correct intersection line against the Python model, and the counter reports the expected throughput.

### 6.5 Vertex transform unit (2–3 days)

- **Build:** a vertex buffer (for example 1,024 vertices of three 16-bit coordinates), `UPLOAD_VERTICES`, `SET_MATRIX` (3x4), and a pipeline that multiplies, divides for perspective (x′ = f·x / z, y′ = f·y / z) and maps to screen coordinates plus depth. It reuses Steps 6.1 and 6.2.
- **Done when:** 1,000 random vertices under random matrices match a Python fixed-point model bit for bit, and that model stays within 1 pixel of a floating-point numpy version.

### 6.6 Primitive assembly and culling (2 days)

- **Build:** an index buffer and `DRAW_MESH`, which fetches three transformed vertices per triangle. Cull back faces by the sign of the triangle's area (the edge function again). Reject any triangle with a vertex behind the near plane; the bounding-box clip already handles the screen edges.
- **Done when:** a cube shows at most three faces from any angle, and a testbench counts the expected number of culled triangles for known orientations.

### 6.7 3D capstone (2 days)

- **Build:** store a normal per vertex and compute lighting (normal dot light direction) on the GPU with the multiply-accumulate unit. Render a cube, then an icosphere, then a model of about 200 triangles.
- **Done when:** the MCU sends only a matrix per frame, the model rotates at 30 frames per second or better, and a simulated frame matches a Python reference renderer for the same model and matrix.

## Stretch goals and resources

**Stretch goals, roughly in order of payoff**

- Affine texture mapping (PlayStation 1 style), then perspective-correct texturing with a per-pixel divide.
- Proper near-plane clipping instead of rejecting triangles.
- Formal verification of the FIFO and command parser with SymbiYosys.
- Per-line scroll registers for raster effects (wavy water, split-screen HUD).
- Alpha blending and additive sprites.
- A DDR3 framebuffer through the Zynq processor side, for 640x480 at 16 bits per pixel. This needs processor configuration first, either with Vivado or the open GenZ generator presented at FOSDEM 2025.

**Learning resources**

- [Project F](https://projectf.io/): FPGA graphics tutorials covering beam racing, framebuffers, lines, triangles and Verilator/SDL simulation, on iCE40 and Arty boards. The closest match to this plan.
- [Getting Started with FPGAs](https://nostarch.com/gettingstartedwithfpgas) by Russell Merrick, the Go Board's designer, and [nandland.com](https://nandland.com/) for Go Board projects including VGA and UART.
- [ZipCPU](https://zipcpu.com/) for testbench discipline, FIFOs and formal verification.
- [fpga4fun HDMI](https://www.fpga4fun.com/HDMI.html) for a compact TMDS encoder to compare with yours.
- [Scratchapixel: Rasterization](https://www.scratchapixel.com/lessons/3d-basic-rendering/rasterization-practical-implementation/overview-rasterization-algorithm.html) for edge functions, the top-left rule and perspective-correct interpolation.
- [openXC7](https://github.com/openXC7) and its toolchain installer, plus the [Arty Z7-20 openXC7 walkthrough](https://hub.mender.io/t/building-and-packaging-fpga-bitstreams-for-the-zynq-7000-without-vivado/8323).
- [Arty Z7 reference manual](https://reference.digilentinc.com/programmable-logic/arty-z7/reference-manual) for pinouts, clocks and HDMI wiring.
