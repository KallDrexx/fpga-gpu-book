# Chapter 1.3 - Moving Objects

So far every picture has been a fixed function of the beam position: the same `x` and `y` always give the same color. A game needs things that move. This chapter adds the first piece of *state* that the picture depends on, the position of a square, and changes it once per frame so the square bounces around the screen.

That sounds like a small step, and in logic it is. The real lesson is about *when* the state may change. Get that wrong and the picture tears, and no amount of testing a single frame will show it. So the testbench for this chapter dumps two consecutive frames and checks the square moved by exactly the right amount between them.

By the end you will have:

- `bouncing_square`, a module that keeps a square's position, moves it by (DX, DY) once per frame, bounces it off the four edges, and draws it;
- `tb_frame_motion.v` and `ref_motion.py` (supplied), a frame-dump test that plugs into the Chapter 1.2 harness as `make frame-motion`;
- a board project with a square gliding smoothly around the monitor.

## 1. Two speeds of logic

A graphics design has two kinds of logic that run at very different rates.

**Pixel logic** runs every clock, 25 million times a second. It answers one question: what color is the pixel at (`x`, `y`) right now? It must answer in one clock, every clock, with no exceptions. `color_bars` and `test_pattern` were pure pixel logic.

**Game logic** runs once per frame, about 60 times a second. It decides where things are: it moves the square, checks for collisions, and in later chapters reads buttons and keeps score. It changes registers that the pixel logic reads.

The pixel logic only *reads* the game state. The game logic only *writes* it, and only at one moment in the frame. Keeping the two apart this way is the pattern behind every beam-racing design in Phase 2, and it has two benefits:

- **The game logic has time.** It runs once per frame, so it could in principle take thousands of clocks per update. Here it needs only one, but Pong and Snake will be glad of the room.
- **The pixel logic sees a consistent world.** Every pixel of a frame is drawn from the same game state, so the frame is a snapshot of one moment.

## 2. Why mid-frame changes tear the picture

The monitor draws the frame from top to bottom over about 16 ms. If the square's position changes while the beam is halfway down, the lines above the beam were drawn with the old position and the lines below with the new one. The square on screen is not one square at all, but the top of one and the bottom of another:

```
    update while the beam is here
             |
   line 238  ####....        old position
   line 239  ####....
   line 240  ..####..   <--  new position from here down
   line 241  ..####..
```

This is **tearing**. With a slow square it looks like a jagged edge that flickers. With fast motion, or with several objects updated at different times, whole objects look broken or appear twice. It also gets worse in a subtle way when the game state is more than one register. If `x` is updated on one clock and `y` on another, a frame can be drawn with a new `x` and an old `y`: a position the object never had.

The cure is to change the game state only while nothing is being drawn. **Vertical blanking** is the gap between the last visible line (479) and the first line of the next frame. It is 45 lines, or 36,000 clocks (1.44 ms), during which `de` is low on every pixel. Anything that changes during blanking is invisible, and the next frame is drawn entirely from the new state.

This chapter updates at the **start** of vertical blanking: the first clock of line 480. Any point in blanking would avoid tearing, and the testbench accepts any, but the start leaves the most time before the next frame for game logic that takes many clocks. The same idea returns at full scale in Chapter 4.5, where swapping whole framebuffers outside blanking tears in exactly the same way.

## 3. The frame tick

The game logic needs to know when the start of blanking is. `vga_timing` already provides the beam position, so it is one comparison:

```verilog
wire w_frame = (i_x == 0) && (i_y == V_VISIBLE);
```

This is a **tick**: a signal that is high for exactly one clock per frame. Use it as the enable of the game-state registers (`if (w_frame) ...` inside the clocked block), just as `o_wrap` enabled the next counter in Phase 0.

Both halves of the comparison matter. Without `i_x == 0` the tick is high for the whole of line 480, 800 clocks, and the square moves 800 steps per frame. Testing `i_y >= V_VISIBLE` instead of `==` moves the square once per blanking line, 45 times a frame. Both bugs are easy to write and both are caught by the testbench.

Why not use `vsync` as the tick? You could detect its falling edge, but that comes 10 lines later (line 490) and needs an edge detector. The `x`/`y` comparison is simpler and does not depend on sync polarity.

## 4. Moving and bouncing

The square is SIZE x SIZE pixels. Its state is four registers:

- `r_sq_x`, `r_sq_y`: the top-left corner, 10 bits each;
- `r_right`, `r_down`: one direction bit per axis, both starting at 1 (moving right and down).

The two axes are completely independent. Write the rule for one axis, then copy it for the other with `y`, DY, `V_VISIBLE` and `r_down`.

### The bounce rule

The largest legal position along x is `MAX_X = H_VISIBLE - SIZE`, where the square's right edge touches column `H_VISIBLE - 1`. On every tick:

| Moving | Condition | New position | New direction |
| --- | --- | --- | --- |
| Right | `x + DX >= MAX_X` | `MAX_X` (touching the right edge) | left |
| Right | otherwise | `x + DX` | right |
| Left | `x <= DX` | `0` (touching the left edge) | right |
| Left | otherwise | `x - DX` | left |

In words: move by DX, but never past the edge. A move that would reach or cross the edge puts the square exactly on the edge and reverses the direction in the same tick, so the next frame already moves away. The square touches every edge exactly, never leaves the screen, and never pauses on an edge for a frame.

The testbench and the Python model both follow this table exactly. Other bounce rules exist (reflecting the overshoot, or letting the square stick for a frame), and they look almost the same on a monitor, which is the point: only a bit-exact test tells you whether you built the rule you meant.

### Traps in the arithmetic

- **Unsigned subtraction wraps.** Registers are unsigned, so `x - DX` with `x = 1` and `DX = 3`, stored back in 10 bits, is 1022, not -2, and a test like `x - DX < 0` is never true. That is why the table compares `x <= DX` *before* subtracting.
- **Off-by-one at the far edge.** The square covers columns `x` to `x + SIZE - 1`. The right edge is reached when `x + SIZE - 1 == H_VISIBLE - 1`, which is `x == H_VISIBLE - SIZE`. Writing `H_VISIBLE - SIZE - 1`, or `>` for `>=`, leaves a 1-pixel gap or makes the square stop one step early.
- **Width of the sum.** `x + DX` can exceed 10 bits on a larger screen. A Verilog sum used in a comparison is computed at the width of its widest operand, and a parameter is 32 bits, so the comparison itself is safe; but if you store the sum in a wire first, make it 11 bits.

### Power-up

Like the rest of this book, the module has no reset port. Give each register its starting value where it is declared, from parameters: `reg [9:0] r_sq_x = START_X;`. The iCE40 loads these at configuration, and the simulation starts from them.

## 5. Drawing the square

The pixel side is one test per pixel: is the beam inside the square?

```
inside = de && (x >= sq_x) && (x < sq_x + SIZE) && (y >= sq_y) && (y < sq_y + SIZE)
```

Draw white (all channels `3'b111`) inside and black outside. Note the `<` on the far edges: with SIZE = 32 and `sq_x = 100`, the square covers columns 100 to 131, which is 32 columns.

Keep this part combinational, as `test_pattern` was. The color then lines up with `de`, `hsync` and `vsync` from the same cycle, and the board top adds one output register stage for all of them as before.

Unlike the test patterns, the comparisons here are against registers rather than constants, so each one costs a real comparator and `sq_x + SIZE` an adder. This is where the logic cell count starts to climb, and in Chapter 2.1 you will count these comparators carefully.

## 6. The module

Put it all in `rtl/common/bouncing_square.v`:

- Parameters: `H_VISIBLE`, `V_VISIBLE` (screen size), `SIZE`, `DX`, `DY`, `START_X`, `START_Y`.
- Inputs: the clock and the beam position from `vga_timing` (`i_x`, `i_y`, `i_de`).
- Outputs: 3 bits per color channel, and the current position `o_sq_x`, `o_sq_y`.

The position outputs are not needed on the board. They let the testbench check the game logic directly, every clock, instead of finding the square in the picture. It is a good habit to expose a design's internal state this way when it costs nothing: yosys removes the unused outputs in the board build.

## 7. The test

`tb_frame_motion.v` follows the pattern Chapter 1.2 set out. It has two parts.

**Full-size frames.** One `vga_timing` drives two `bouncing_square` instances with SIZE = 32, DX = 3, DY = 2. The *free* square starts at (100, 80), in open space. The *bounce* square starts at (602, 444), near the bottom-right corner. Each gets two `frame_dump` instances, with `SKIP(0)` and `SKIP(1)`, so the simulation writes two consecutive frames of each:

| File | Square at | What it shows |
| --- | --- | --- |
| `frame_motion_free1.ppm` | (103, 82) | after one update |
| `frame_motion_free2.ppm` | (106, 84) | moved by exactly (DX, DY) |
| `frame_motion_bounce1.ppm` | (605, 446) | one step from the corner |
| `frame_motion_bounce2.ppm` | (608, 448) | clamped: touching the right and bottom edges |

`ref_motion.py` contains the bounce rule as a short Python function, draws the same four frames, and `compare.py` checks them pixel for pixel. A square drawn one pixel too wide, or a corner one pixel short of the edge, shows up as a thin strip of magenta in the diff image.

**Why "after one update".** `vga_timing` starts at the top-left pixel, so the simulation begins with frame 0 being drawn. Its blanking starts at line 480 (the first update), and its `vsync` follows at line 490. `frame_dump` with `SKIP(0)` waits for that `vsync`, so frame 1 is drawn after one update, and frame 2 after two. The testbench checks this assumption and says so if your `vga_timing` starts somewhere else.

**Many frames, quickly.** A full-size square takes about 200 frames to cross the screen, and simulating 200 frames of 640x480 takes minutes. So the testbench also runs a second `vga_timing` with tiny parameters (48x36 visible, 2,240 clocks per frame) and two small squares on it. While the big frames are captured, the small screen runs about 550 frames and the squares bounce off its edges about 150 times. One small square uses steps that land exactly on the edges, the other steps that overshoot and must be clamped. This only works because `bouncing_square` takes the screen size as parameters; a 640 written into the logic fails here.

**What is checked every clock.** For all four squares, the testbench keeps its own model of the bounce rule and advances it at the start of each vertical blanking. It then checks:

- At the top-left pixel of each frame, `o_sq_x`/`o_sq_y` match the model. A wrong step, a swapped DX and DY, or a bounce one frame early or late fails here.
- Through all 480 visible lines the position does not change. An update in the middle of the frame fails here, with the beam position at which it changed. Changing it anywhere in vertical blanking is allowed.
- The color is black whenever `de` is low.
- On the small screens, every visible pixel is white exactly inside the square and black outside.

The full run takes about 25 seconds. When it fails, the first lines of output name the square, the frame number, the beam position and both the position the design reported and the one expected.

## 8. The board project

Create `projects/1.3-bouncing-square/`. Its `top_goboard.v` is the Chapter 1.1 top with `color_bars` replaced by `bouncing_square` (default parameters): `vga_timing`, the square, one register stage for the syncs and color, and the pin mapping. Set its `TESTS` to `tb_vga_timing tb_frame_motion`, and add `FRAME_TESTS += motion` to the top-level Makefile so `make frame` also checks the pixels.

The reference design used to write this chapter reports:

| Resource | Used | Available |
| --- | --- | --- |
| Logic cells (ICESTORM_LC) | 234 | 1,280 (18%) |
| Block RAM | 0 | 16 |
| I/O pins | 12 | 112 |
| Global buffers | 2 | 8 |

Fmax is about 130 MHz. The second global buffer carries the frame tick: it is the clock enable of every game-state flip-flop (a fanout of 21), and nextpnr promotes high-fanout enables to a global net. Much of the growth over Chapter 1.1 comes from the adders and comparators against register values in the inside test.

### On the monitor

Program the board and check by hand:

1. **Smooth motion.** A white 32x32 square moves diagonally at 3 pixels per frame across and 2 down, about 180 pixels per second. It takes about 3.4 seconds to cross the screen. The motion should be even, with no stutter or jumps.
2. **Clean bounces.** At each edge the square touches the edge of the picture (after auto-adjust) and turns back. It never leaves the screen and never stops on an edge.
3. **No tearing.** Watch the square's left and right sides while it moves. They should stay straight vertical lines, never with a step part-way down.

Then, as an experiment, make it tear on purpose. Change the tick to fire on line 240 instead of 480 and set DX to 16. Whenever the square crosses the middle of the screen, its top and bottom halves are drawn 16 pixels apart. Run `make frame-motion` with the same change: the full-size squares report "position changed while the picture was being drawn" at `y=240` (the small screen has no line 240, so its squares never move and fail too). Put the tick back before you commit.

When something is wrong:

| Symptom | Likely cause |
| --- | --- |
| Square does not move | Tick never fires: compare against `V_VISIBLE`, not a line past the frame's end; check `i_x == 0` |
| Square streaks or flies off at once | Tick high for more than one clock per frame |
| Square goes off an edge and reappears on the other side | Unsigned wraparound: comparing after subtracting, or a missing `>=` check |
| Gap between the square and an edge at the bounce | Off-by-one in `MAX_X`/`MAX_Y` |
| Square tears or jitters, but the test passes | The board top differs from the testbench: for example the square fed from a different `x`, `y` than the timing generator, or the color registered differently from the syncs |

## Testbench requirements

Copy `tb_frame_motion.v` from this chapter into `sim/` and `ref_motion.py` into `sim/py/`. They use the Chapter 1.2 harness (`frame_dump.v` in `sim/`, `ppm.py` and `compare.py` in `sim/py/`, `frame.mk` included from the top-level Makefile) unchanged. Add `FRAME_TESTS += motion` to the top-level Makefile.

You provide:

- **`rtl/common/vga_timing.v`** from Chapter 1.1, unchanged. It must start at x = 0, y = 0 (initial values of 0), which the testbench checks.
- **`rtl/common/bouncing_square.v`**, module `bouncing_square`
  - Parameters (integer, defaults shown): `H_VISIBLE=640`, `V_VISIBLE=480`, `SIZE=32`, `DX=3`, `DY=2`, `START_X=100`, `START_Y=80`.
  - Ports: `input i_clk`, `input [9:0] i_x`, `input [9:0] i_y`, `input i_de`, `output [2:0] o_red`, `output [2:0] o_grn`, `output [2:0] o_blu`, `output [9:0] o_sq_x`, `output [9:0] o_sq_y`.
  - Behavior: the square's top-left corner starts at (`START_X`, `START_Y`) moving right and down, with no reset port. The position changes only during vertical blanking (`i_y >= V_VISIBLE`), once per frame, by the bounce rule in Section 4 (the start of blanking, `i_x == 0 && i_y == V_VISIBLE`, is the intended point). `o_sq_x`/`o_sq_y` show the current position. Color is `3'b111` on all channels inside the square, zero outside it and whenever `i_de` is low, combinational from the inputs and the position registers.

Run:

```
make frame-motion
```

which prints the testbench's `PASS` line, one `ok`/`FAIL` line for each of the four frames, and `PASS: compare motion (4 frames match exactly)`. By hand, from the repository root:

```
mkdir -p build
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_frame_motion.v && vvp -n build/tb.vvp
.venv/bin/python sim/py/ref_motion.py build
.venv/bin/python sim/py/compare.py --set build motion
```

The simulation takes about 25 seconds. Frames go to `build/frame_motion_{free1,free2,bounce1,bounce2}.ppm` and references to `build/ref_motion_*.ppm`. Add `+vcd` to `vvp` for a waveform in `build/tb_frame_motion.vcd`; it is large.
