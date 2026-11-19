# Chapter 1.2 - Frame-Dump Harness

Chapter 1.1 proved the timing is right: the testbench counted sync pulses and `de` cycles. It did not check the picture itself. A monitor can show you a picture, but it cannot tell you that column 639 is missing, that every pixel is shifted right by one, or that one band of a gradient is four lines too tall. A monitor scales, filters and crops what it gets, so it hides exactly the bugs you most need to find.

This chapter builds the tool that every later graphics step uses to check its output. A simulation runs one frame and saves every visible pixel to an image file. A Python script draws what that frame *should* look like and compares the two pixel by pixel. When they differ, it names the first wrong pixel.

By the end you will have:

- `frame_dump`, a reusable simulation component that captures a frame from any video bus and writes it as a PPM image (supplied with this chapter);
- `ppm.py`, `compare.py` and `ref_patterns.py`, the Python half of the harness (supplied);
- `test_pattern`, your module that draws a checkerboard, a gradient, a 1-pixel border and the Chapter 1.1 color bars;
- a `make frame` target that simulates, draws the references and compares them;
- a board project that shows each pattern, with all four border lines visible on the monitor.

## 1. Writing files from a testbench

Verilog testbenches can open, write and close files with system tasks that work much like C's `stdio`:

| Task | What it does |
| --- | --- |
| `fd = $fopen("build/x.ppm", "wb")` | Opens a file and returns a handle (an `integer`). Returns 0 on failure, typically because the directory does not exist. Mode `"w"` is text, `"wb"` binary; on Linux they behave the same, but say what you mean. |
| `$fwrite(fd, "fmt", args...)` | Writes formatted text, with the same format codes as `$display` but no newline added. `$fdisplay` adds one. |
| `$fclose(fd)` | Flushes and closes the file. Forget it and the end of the file may be missing. |
| `$sformat(str, "fmt", args...)` | Formats into a `reg` vector, for building file names. |

Two format codes matter here. `%0d` prints a decimal number with no padding (plain `%d` pads to the width of the variable). `%c` writes the low 8 bits of a value as one raw byte, including byte 0. That is how you write binary data: `$fwrite(fd, "%c%c%c", r, g, b)` writes three bytes.

**Strings are vectors.** A string in Verilog is just a vector with 8 bits per character, so a file name can be a parameter: `parameter FILE = "build/frame.ppm"`. There is one trap. A shorter string assigned to a wider vector is padded with zero bytes *on the left*, and some tasks then see an empty or garbled name. This bites when you choose between strings with `?:` (the result is as wide as the longest one) or store them in a fixed-width `reg`. Pass each file name as its own literal and you avoid it.

These tasks exist only in simulation. Synthesis ignores or rejects them, which is why the code in this chapter lives in `sim/`, never in `rtl/common/`.

## 2. The PPM image format

PPM (Portable PixMap) is the simplest color image format there is, and that is why the harness uses it. A file is a short text header followed by the pixels:

```
P6
640 480
7
<640 x 480 x 3 bytes: R G B for each pixel, top line first, left to right>
```

- `P6` means binary pixels. `P3` is the same image with the pixel values written as decimal text, which is larger but readable in a text editor.
- `640 480` is width then height.
- `7` is the **maxval**, the value that means full brightness. The Go Board has 3 bits per channel, so its values run from 0 to 7, and the file stores them exactly as the hardware produces them. Later, with 8 bits per channel, maxval becomes 255.
- After maxval comes exactly one whitespace byte, then the raw data with one byte per channel (two bytes if maxval is over 255).

The pixel order is the scan order of the monitor: line 0 left to right, then line 1, and so on. A frame dump can therefore write pixels in the order they arrive. Image viewers (`eog`, `feh`, GIMP, most file browsers) open PPM files directly and scale maxval 7 up to full brightness for display.

Storing the raw 3-bit values, rather than scaling them to 0..255, keeps comparisons exact. There is no rounding step that Verilog and Python could do differently.

## 3. Capturing a frame

The capture logic is `frame_dump.v`, supplied with this chapter. Every later chapter depends on its exact behavior, so it is given rather than left to you. Read it through: it is about 130 lines, mostly comments, and uses only the tasks above. (For practice, write your own first and then compare it.)

It watches a **video bus**, the same signals that leave your design for the monitor:

```verilog
frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(0),
             .FILE("build/frame_patterns_border.ppm"))
    u_dump (.i_clk(clk), .i_vsync(vs), .i_de(de),
            .i_red(r), .i_grn(g), .i_blu(b),
            .o_done(done), .o_error(error));
```

Three design decisions are worth understanding, because later chapters rely on them.

**It finds the frame from the signals, not from your counters.** `frame_dump` never looks at `x` or `y`. It waits for `vsync` to go low (SKIP + 1 times), then treats the next run of `de` cycles as line 0, the run after that as line 1, and so on. This is how a monitor sees your design, and it means the dumper works on any design whose outputs look like VGA, including designs where `x` and `y` are buried several pipeline stages deep. It is also why a `de` that is one cycle late shows up: the dumper captures exactly what `de` says is visible.

**It samples like a flip-flop.** Inputs are read on the rising edge of `i_clk`, so it sees the values that were stable just before the edge. Feed it the same signals that go to the output pins, from the same pipeline stage, and the dump is exactly what the monitor receives.

**It checks shape, and always writes a valid image.** Every line must have exactly WIDTH `de` cycles, there must be exactly HEIGHT lines before the next `vsync`, and no pixel may be `x`. Any violation prints a `frame_dump:` line and raises `o_error`. The file is still written at full size (missing pixels black, extras dropped), so even a broken frame opens in a viewer and can be compared.

`SKIP` lets one simulation dump several consecutive frames: two instances with `SKIP(0)` and `SKIP(1)` capture frames 1 and 2. You will use exactly that in Chapter 1.3 to check that a moving square moved.

### The testbench

`tb_frame_patterns.v` is the frame-dump testbench for this chapter. It is a template for every later one, and has three parts:

1. The design under test: one `vga_timing` and four `test_pattern` instances, one per pattern.
2. One `frame_dump` per output, each with its own file name `build/frame_patterns_<label>.ppm`.
3. A blanking check (every output black whenever `de` is low, which the dumpers cannot see), a watchdog (three frame times), and a verdict: `PASS` once every dumper reports `o_done` with no `o_error` and no blanking error.

Notice what the `PASS` means. The testbench checks the frame's *shape* and the blanking. Whether the *pixels* are right is a separate question, answered in Python. Since `vga_timing` starts at the top left, the first full frame begins after the first `vsync`, so the simulation covers almost two frames, about 810,000 clocks. Expect 15 seconds or so.

## 4. The reference images in Python

The other half of the harness is a **golden model**: a Python program that draws the frame the hardware should produce. Python is the right tool because the reference must be *obviously* correct. A border in numpy is four lines that read like the specification; the same border in Verilog has comparisons and bit widths that can be off by one. If both are written from the same specification and agree on every pixel, you can trust both.

### Setting up

numpy is needed now; Pillow and pyserial come in later chapters. Use a virtual environment in the repository root so the system Python is untouched:

```
python3 -m venv .venv
.venv/bin/pip install numpy pillow pyserial
```

Add `.venv/` to `.gitignore`, and run the scripts with `.venv/bin/python` (or set `PYTHON` in the Makefile, below).

### Images are arrays

An image is a numpy array of shape `(height, width, 3)`: `img[y, x]` is the `(r, g, b)` of one pixel. Note the order: row first. Drawing becomes array slicing and boolean masks:

```python
img = blank(640, 480)                   # all black
img[0, :] = (7, 7, 7)                   # top line
Y, X = np.mgrid[0:480, 0:640]           # X[y, x] == x, Y[y, x] == y
img[(X // 32) % 2 == 0] = (0, 0, 7)     # every other 32-pixel column, blue
```

`np.mgrid` gives you the coordinates of every pixel at once, which is the Python version of the `x` and `y` your hardware gets from `vga_timing`. Most patterns are then one line, written almost the way the hardware computes them.

### The supplied scripts

- **`ppm.py`** reads (P6 and P3) and writes (P6) PPM files as numpy arrays, and makes blank images. It needs only numpy.
- **`ref_patterns.py OUTDIR`** draws the four reference images and writes `OUTDIR/ref_patterns_<label>.ppm`. It has one function per frame and a `FRAMES` table mapping each label to its function.
- **`compare.py`** compares two images, or a whole set with `--set DIR NAME`. In set mode it pairs every `DIR/ref_NAME_<label>.ppm` with `DIR/frame_NAME_<label>.ppm`.

`compare.py` reports the **first mismatched pixel in scan order**: the earliest pixel the hardware got wrong. That is usually the most useful one, since later errors often follow from it. It also prints how many pixels differ and the range of rows and columns they span, and writes a diff image `diff_NAME_<label>.ppm` with every wrong pixel in magenta over a dimmed copy of the reference. A typical failure reads:

```
  FAIL  frame_patterns_border.ppm: 956 of 307200 pixels differ; first at (x=638, y=1):
        got (7, 7, 7), expected (0, 0, 0); differences span x=638..639, y=1..478;
        diff image build/diff_patterns_border.ppm
FAIL: compare patterns (1 of 4 frames differ: border)
```

That points straight at the bug: the right edge is drawn one column too far left. The script prints exactly one line starting with `PASS` or `FAIL` and exits with status 0 or 1, so make can stop on it.

`--tolerance N` accepts pixels whose channels differ by at most N. Everything in Phases 1 to 5 must match exactly (tolerance 0). Tolerance is for Phase 6, where 3D shading is checked against a floating-point model.

Each script sets `sys.dont_write_bytecode` before importing `ppm`, and the Makefile also sets `PYTHONDONTWRITEBYTECODE=1`, so Python never leaves `__pycache__` directories in `sim/py/`.

## 5. The test patterns

Now the part you build. `test_pattern` is purely combinational, like `color_bars`: it takes `x`, `y`, `de` and a 2-bit pattern number, and outputs 3 bits per channel. Each pattern is chosen to catch a different kind of mistake.

| `i_pattern` | Pattern | Exact definition (at 640x480) | What it catches |
| --- | --- | --- | --- |
| 0 | Checkerboard | 32x32 squares. White (all channels 7) where `x[5] == y[5]`, so the top-left square is white; black elsewhere. 20 x 15 squares. | Swapped or wrong coordinate bits; x and y mixed up. |
| 1 | Gradient | Red = `x / 80` (0 to 7 left to right), green = `y / 60` (0 to 7 top to bottom), blue = 7 - red. | Off-by-one band edges; wrong bit order on a channel. |
| 2 | Border | White on line 0, line 479, column 0 and column 639; black everywhere else. | Off-by-one in the visible area, the most common timing bug. |
| 3 | Color bars | The Chapter 1.1 `color_bars` output. | Nothing new; it keeps the old picture available. |

Every pattern is black when `de` is low, as in Chapter 1.1.

Hints:

- The checkerboard is a single XNOR of two bits. Powers of two are free in hardware; that is why the squares are 32 pixels.
- The gradient needs `x / 80` and `y / 60`. You already built the comparison chain for `x / 80` in `color_bars`; the `y` chain has the same shape. Do not write `/`.
- For the border, write the comparisons against 639 and 479 directly from the table, then check them against the definition of `de`. The last visible column is 639, not 640.
- Pattern 3 should instantiate your `color_bars` module rather than copy it.

Write it, then run `make frame`. If a frame fails, open the diff image next to the frame dump. Most bugs are obvious at a glance once you can see exactly which pixels are wrong.

## 6. Wiring it into make

`frame.mk` (supplied) adds the frame rules. Copy it to the repository root and add one line to the top-level Makefile, after its variables:

```make
PYTHON = .venv/bin/python
include frame.mk
```

Also add `-y sim` to the iverilog flags your Phase 0 Makefile uses for `make test` and `make sim-<tb>`, so any testbench can find `frame_dump`. (`-y` only searches for modules that are otherwise undefined, so the testbenches in `sim/` are not affected.)

`make frame-NAME` does four things, stopping at the first failure:

1. Deletes old frames, references and diffs for NAME, so a stale file can never pass.
2. Compiles and runs `sim/tb_frame_NAME.v`, and checks that it printed `PASS`.
3. Runs `sim/py/ref_NAME.py build` to draw the references.
4. Runs `compare.py --set build NAME`.

`make frame` runs `frame-NAME` for every NAME in `FRAME_TESTS`. This chapter adds `patterns`. Consider making `make test` depend on `frame` as well, so one command checks everything.

### How later chapters plug in

The harness is built around one naming convention. To add a frame test called NAME, a later chapter supplies (or asks you to write) two files:

- `sim/tb_frame_NAME.v`: instantiates the design and one `frame_dump` per frame of interest, writing `build/frame_NAME_<label>.ppm`, and prints `PASS` or `FAIL`.
- `sim/py/ref_NAME.py`: draws each frame and writes `build/ref_NAME_<label>.ppm`.

and adds `FRAME_TESTS += NAME`. Nothing else changes. Some examples of what is coming:

- **1.3, moving objects:** two dumpers with `SKIP(0)` and `SKIP(1)`; the Python model draws the square at its start position and at the position one step later.
- **2.2, text mode:** the Python model reads the same font file as the font ROM and draws the same string.
- **2.4 and 5.x, sprites and layers:** the model gets more complex, but the comparison stays the same. At that point the reference drawing code becomes a real golden model of each rendering block, kept in `sim/py/` and reused between chapters.
- **4.x, the framebuffer:** BITS becomes 8 and maxval 255. The harness is unchanged.
- **6.x, 3D:** `--tolerance` comes into use.

As designs grow, put the video output of each graphics step in one portable module (for example `pong`, with `o_hsync`, `o_vsync`, `o_de` and color outputs). The frame-dump testbench then instantiates that one module, and `top_goboard.v` stays a thin pin mapping around the same module. The dump sees what the monitor sees.

## 7. The board project

Create `projects/1.2-test-patterns/`. Its `top_goboard.v` follows Chapter 1.1: instantiate `vga_timing` and `test_pattern`, register the syncs and color through one output stage, and map the color buses to the `o_VGA_*` pins. For pattern selection, pass the four buttons through `synchronizer` and keep a 2-bit register: pressing switch 1 to 4 selects pattern 0 to 3. Bounce does not matter here, since every bounce selects the same pattern. Start at power-up on the border (pattern 2), since that is the one to check on the monitor.

Set the project's `TESTS` to `tb_vga_timing tb_color_bars`. Frame tests such as `tb_frame_patterns` run through `make frame-patterns` from the repository root, not through `TESTS`: they read and write files by root-relative paths.

The reference design used to write this chapter reports:

| Resource | Used | Available |
| --- | --- | --- |
| Logic cells (ICESTORM_LC) | 224 | 1,280 (17%) |
| Block RAM | 0 | 16 |
| I/O pins | 16 | 112 |
| Global buffers | 1 | 8 |

Fmax is about 150 MHz. The two comparison chains for the gradient account for much of the growth over Chapter 1.1.

### On the monitor

Program the board (`make prog`), then check by hand:

1. **Border (power-up).** Press the monitor's auto-adjust once. Then look for all four white lines, one at each edge of the picture. Each must be one pixel wide and touch the edge, with no black gap between line and edge.
2. **Checkerboard (switch 1).** 20 squares across, 15 down, white square top left. Edges sharp, squares square.
3. **Gradient (switch 2).** Blue at top left, red at top right, cyan bottom left, yellow bottom right, 8 x 8 even blocks.
4. **Color bars (switch 4)**, as in Chapter 1.1.

When something is wrong:

| Symptom | Likely cause |
| --- | --- |
| One edge missing, and `make frame` fails | Off-by-one in `test_pattern` or `vga_timing`; read the first mismatch and the diff image |
| An edge missing, but `make frame` passes | The board top differs from the testbench: for example `test_pattern` fed from a different `x`, `y` or `de` than the one the testbench uses, or the color passing through more (or fewer) registers than the syncs |
| Right or bottom edge missing, `make frame` passes, top matches testbench | The monitor is cropping (overscan) or is not adjusted; auto-adjust again, or try its "1:1" or "just scan" mode |
| Gap between a border line and the screen edge | Monitor position; auto-adjust again |
| Gradient blocks uneven or colors in the wrong corners | Channel bits reversed in the pin mapping (bit 2 is the most significant) |

## Testbench requirements

Copy from this chapter: `tb_frame_patterns.v` and `frame_dump.v` into `sim/`; `ppm.py`, `compare.py` and `ref_patterns.py` into `sim/py/`; `frame.mk` into the repository root, included from the top-level Makefile. Add `-y sim` to the iverilog flags. Install numpy (`python3 -m venv .venv && .venv/bin/pip install numpy pillow`) and set `PYTHON = .venv/bin/python`.

You provide:

- **`rtl/common/vga_timing.v`** and **`rtl/common/color_bars.v`** from Chapter 1.1, unchanged.
- **`rtl/common/test_pattern.v`**, module `test_pattern` (combinational)
  - Ports: `input [9:0] i_x`, `input [9:0] i_y`, `input i_de`, `input [1:0] i_pattern`, `output [2:0] o_red`, `output [2:0] o_grn`, `output [2:0] o_blu`.
  - Patterns as in the table in Section 5: 0 checkerboard, 1 gradient, 2 border, 3 color bars. All outputs zero when `i_de` is low.

Run everything with:

```
make frame
```

which prints the testbench's `PASS` line, one `ok`/`FAIL` line per frame, and a final `PASS: compare patterns (4 frames match exactly)`. By hand, from the repository root:

```
mkdir -p build
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_frame_patterns.v && vvp -n build/tb.vvp
.venv/bin/python sim/py/ref_patterns.py build
.venv/bin/python sim/py/compare.py --set build patterns
```

The simulation takes about 15 seconds. Frames go to `build/frame_patterns_{checker,gradient,border,bars}.ppm`, references to `build/ref_patterns_*.ppm`, and diff images (on failure) to `build/diff_patterns_*.ppm`. Add `+vcd` to `vvp` for a waveform in `build/tb_frame_patterns.vcd`; it is large.
