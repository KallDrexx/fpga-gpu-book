# Chapter 4.1 - Framebuffer Scanout

Everything you have put on a screen so far was computed while the beam passed: Pong's paddles were compares against the beam position, and the tile engine looked up a tile and a row just in time for each pixel. That works for a few objects, but a line or a triangle can't be drawn that way. From now on the picture lives in memory. A **framebuffer** holds one byte for every pixel. A drawing engine writes into it whenever it likes, and a **scanout reader** reads it out in beam order, 60 times a second, whether or not anything changed.

This chapter builds the read side: the framebuffer RAM, preloaded with a test image, the scanout reader, and the palette that turns each byte into a color. Step 4.2 adds the drawing side.

By the end of this chapter you will have:

- `rtl/common/framebuffer.v`: a 76,800-byte true dual-port RAM with one port per clock,
- `rtl/common/palette.v`: 256 RGB888 colors, written on one clock and read on another,
- `rtl/common/fb_scanout.v`: both RAMs and the reader that shows the 320x240 buffer as 640x480,
- `rtl/common/fb_test.hex` and `pal_test.hex`: the test image, made by this chapter's `ref_scanout.py`,
- `sim/tb_frame_scanout.v` passing under `make frame-scanout`: the simulated frame is the test image scaled 2x, exactly,
- `projects/4.1-scanout/`: the test image on an HDMI monitor.

```
         clk_sys (50 MHz)                                 clk_pix (25 MHz)
  draw port (Step 4.2) ──> port A ┌─────────────┐ port B <── address <── vga_timing x, y
                                  │ framebuffer │                          │
                                  │ 320x240x8   │ ──> index               │ hsync, vsync, de
                                  └─────────────┘        │                 │
  palette writes ───────> write  ┌─────────────┐ read <─┘                 delay to match
                                  │ palette     │                          │
                                  │ 256 x RGB888│ ──> red, green, blue ──> hdmi_tx (Step 3.4)
                                  └─────────────┘
```

## 1. A framebuffer and its budget

Phase 4 settles on 320x240 logical pixels, each shown as a 2x2 block of the 640x480 output, and 8 bits per pixel. A pixel's byte is not a color but a **palette index**: it picks one of 256 colors, each a full 24-bit RGB value. One buffer is

    320 x 240 = 76,800 bytes = 614,400 bits

Store pixel (x, y) at address `y*320 + x`. Row 0 fills addresses 0-319, row 1 starts at 320, and the bottom-right pixel is at 76,799. The address needs 17 bits.

That is far too much for flip-flops and far too much for distributed RAM (LUTs used as memory). It goes in **block RAM**. The Z7-20's programmable logic has 140 `RAMB36E1` blocks. Each holds 36 Kbit: 32 Kbit of data plus 4 Kbit of parity bits, which you can use as a ninth data bit per byte. Each `RAMB36E1` can also be used as two independent `RAMB18E1` halves, so the same silicon counts as 280 of those.

A block's shape is set by its port width. The useful 36 Kbit shapes are 32K x 1, 16K x 2, 8K x 4, 4K x 9, 2K x 18 and 1K x 36. For bytes, 4K x 9 is the natural shape: 4,096 bytes per block, ninth bit unused. So

    76,800 / 4,096 = 18.75  ->  19 RAMB36E1 per buffer (14% of the chip)

Two buffers for Step 4.5 take 38 blocks, about a quarter of the chip. Phase 5's tile and sprite memories and Phase 6's depth buffer fit in the rest.

## 2. The framebuffer RAM

### One port per clock

The drawing engine runs on the 50 MHz `clk_sys` from Step 3.2, so that it can draw one pixel per clock with time to spare. Scanout must run on the 25 MHz `clk_pix`, one pixel per clock, because that is the rate the monitor takes them. The memory sits between two clock domains.

Step 2.5's `uart_ppu` kept everything in one clock and wrote VRAM in the same domain as the reader. Here, block RAM solves the problem for you: each `RAMB36E1` has two completely independent ports, A and B, each with its own clock, address, data and write enable. That is a **true dual-port** RAM. The memory cells are shared, but no signal crosses from one clock domain to the other in the fabric, so you need no synchronizers. The only thing to avoid is writing an address on one port in the same instant the other reads it: the write still succeeds, but that one read returns an invalid byte (UG473's word for it). For a framebuffer that is a single wrong pixel for one frame, which no one can see. Step 4.5's double buffering removes even that.

Give the two ports these jobs:

| Port | Clock | Used by | Does |
| --- | --- | --- | --- |
| A | `clk_sys` 50 MHz | the drawing engine (4.2-4.8), `FB_CRC` (4.2) | writes, and reads back |
| B | `clk_pix` 25 MHz | the scanout reader | reads only |

Port A needs its read path now even though nothing uses it yet: Step 4.2's `FB_CRC` command reads the whole buffer through it to checksum it.

### Writing it so yosys infers block RAM

You don't instantiate `RAMB36E1` by hand. You write an ordinary Verilog array with registered reads, and yosys's `memory_libmap` pass recognizes the pattern and builds it out of block RAM. The whole module is this:

```verilog
module framebuffer #(parameter INIT_FILE = "") (
    input  wire        i_clk_a,
    input  wire        i_we_a,
    input  wire [16:0] i_addr_a,
    input  wire [7:0]  i_wdata_a,
    output reg  [7:0]  o_rdata_a,
    input  wire        i_clk_b,
    input  wire [16:0] i_addr_b,
    output reg  [7:0]  o_rdata_b
);
    reg [7:0] mem [0:76799];
    initial if (INIT_FILE != "") $readmemh(INIT_FILE, mem);

    always @(posedge i_clk_a)
        if (i_we_a) begin
            mem[i_addr_a] <= i_wdata_a;
            o_rdata_a     <= i_wdata_a;      // write-first: a write returns the new byte
        end else
            o_rdata_a     <= mem[i_addr_a];

    always @(posedge i_clk_b)
        o_rdata_b <= mem[i_addr_b];
endmodule
```

Each port has exactly one clock, and each read is registered: the data for an address presented before a rising edge is on `o_rdata` after that edge. That one clock of **read latency** is the price of block RAM and shapes the scanout pipeline in Section 3. The `$readmemh` in an `initial` block becomes the blocks' `INIT_xx` values, so the image is in the bitstream and appears at power-up, no loading needed. `INIT_FILE` is a parameter because the path depends on where the tool runs (Section 8).

Three things from the real synthesis runs are worth knowing.

**How yosys splits it.** Yosys built this RAM from 19 `RAMB36E1` in 4K x 9 mode, each holding 4,096 consecutive bytes. The top five address bits pick the block: on a write they gate the block's write enable, and on a read they drive a 19-way multiplexer in LUTs that picks which block's output to use. Yosys's description of the block also has the 64K x 1 **cascade** mode, in which two `RAMB36E1` chain together through dedicated wires, but its cost model preferred 4K x 9 here. A naive split into 32K x 1 slices would have needed 24 blocks.

**Write-first, not read-first.** The obvious way to write port A is

```verilog
always @(posedge i_clk_a) begin
    if (i_we_a) mem[i_addr_a] <= i_wdata_a;
    o_rdata_a <= mem[i_addr_a];          // read-first: a write returns the OLD byte
end
```

It simulates perfectly, and yosys does not put it in block RAM. Xilinx documents address-collision problems for read-first ports whose clocks are unrelated, so yosys's description of the 7-series block only allows read-first when both ports share one clock. With two clocks it fell back to distributed RAM: 4,800 `RAM128X1D` cells, 25,011 LUTs, 23% of the chip, and nextpnr was still placing after four minutes. The same RAM with one clock, or with port A written as above (write-first), or with port A never reading while it writes ("no change"), maps to 19 blocks. After every build, check `build/yosys.log` for

    mapping memory top_artyz7.u_scan.u_fb.mem via $__XILINX_BLOCKRAM_TDP_

`TDP` is true dual-port. `LUTRAM` in that line means the inference failed.

**Unused ports disappear.** If you tie port A's write enable to 0 and leave `o_rdata_a` unconnected, yosys removes port A and treats the framebuffer as a ROM. A test build like that used 17 blocks and turned the palette into plain logic. It works, but it isn't the RAM that Step 4.2 needs, so this chapter's top keeps port A alive (Section 8).

## 3. The scanout reader

### The address

`vga_timing` gives the beam position `x` (0-799) and `y` (0-524). For a visible beam pixel the framebuffer pixel is `(x/2, y/2)`, so the address is

    addr = (y/2)*320 + x/2

`x/2` is just `x[9:1]`, wiring and no logic. The multiply is the part to avoid. Here it happens to be cheap anyway, because 320 = 256 + 64 and `(y/2)*320` is `(y/2 << 8) + (y/2 << 6)`, one adder. But a general multiplier per pixel is the wrong habit: at another width, or in Phase 5's tile layers, it costs a DSP block or a long carry chain. The beam moves one step at a time, so step the address with it instead:

- Keep a register `line_base`, the address of the first pixel of the current framebuffer row.
- The address of the pixel under the beam is `line_base + x[9:1]`.
- At the end of each line, if `y` is odd, add 320: the next beam line starts a new framebuffer row. If `y` is even, leave `line_base` alone: the next beam line repeats the row. That is **line doubling**.
- During vertical blanking (`y >= 480`), set `line_base` to 0, ready for the next frame.

**Pixel doubling** comes free from `x[9:1]`: beam pixels 0 and 1 both read framebuffer pixel 0, 2 and 3 read pixel 1, and so on.

A common variation keeps a single running address instead: add 1 after every odd beam pixel, and at the end of each visible line subtract 320 (repeat the row) or don't (move on). It uses fewer adder bits. Either is fine. What matters in both is that the frame rewinds in blanking. A reader that doesn't rewind still shows the first frame after power-up correctly, because the registers start at 0, and then walks off the end of the memory. That is why the testbench never dumps the first frame.

During horizontal blanking `x` keeps counting to 799, so the address runs past the end of the row, and on the last visible lines past 76,799. Those reads return garbage that nobody looks at, because `de` is low. You don't need to gate the address.

### Latency and the syncs

The reference reader has three register stages between the beam and the output:

| Clock edge | What happens |
| --- | --- |
| 1 | the framebuffer latches `line_base + x/2` and returns the index |
| 2 | the palette latches the index and returns 24 bits of color |
| 3 | an output register holds red, green, blue for `hdmi_tx` |

So the color for beam position (x, y) comes out three clocks after `vga_timing` showed (x, y). If `hsync`, `vsync` and `de` left at once, every pixel would land three places to the right of where the monitor thinks it is, and the first three pixels of each line would be lost in the porch. Delay all three through the same number of flip-flops as the color. This is exactly what the Phase 2 engines did, with a different count. Your latency can differ. One RAM read can be skipped by computing the address a clock early, and the output register is optional. The testbench measures the latency and accepts any value from 1 to 24, as long as all five outputs share it.

Give the sync delay registers an initial value of 1 (syncs are active low) and `de` an initial value of 0. They are not checked, but the monitor then sees no fake sync pulse at power-up.

## 4. The palette

256 entries of 24 bits is 6,144 bits: small. Make it its own little RAM, `palette.v`, with the same shape as the framebuffer but simpler:

- a write port on `clk_sys` (`i_wr_en`, `i_wr_addr[7:0]`, `i_wr_data[23:0]`), for Step 4.2's `SET_PALETTE` command. It is unused by this chapter's hardware but tested in simulation,
- a registered read port on `clk_pix` (`i_rd_addr[7:0]`, `o_rd_data[23:0]`),
- `INIT_FILE` for `$readmemh`. Each line is one entry, six hex digits `RRGGBB`.

Red is bits 23:16, green 15:8 and blue 7:0, in the file and on the port. Keep that order everywhere: the `SET_PALETTE` packet and the Python model in Step 4.2 use it too.

Left alone, yosys puts this RAM in one `RAMB18E1`, in simple dual-port mode (one write port, one read port, up to 36 bits wide). That builds, but in the reference build nextpnr reported a 0.04 ns hold violation on that block's write data, with a flip-flop placed right next to it (nextpnr-xilinx doesn't repair hold violations). Two other placement seeds passed. A memory this small is just as happy in LUTs, so ask for distributed RAM:

```verilog
(* ram_style = "distributed" *) reg [23:0] mem [0:255];
```

Yosys then uses 32 `RAM64M` cells (128 LUTs), writing on `clk_sys` and reading through your `clk_pix` output register, and saves a block RAM. Iverilog ignores the attribute. Check the yosys log line again: it should say `$__XILINX_LUTRAM_SDP_` for `u_pal.mem`.

## 5. `fb_scanout`: putting it together

`fb_scanout` holds one `framebuffer`, one `palette` and the reader. Its ports are the interface Steps 4.2-4.5 build on:

```verilog
module fb_scanout #(
    parameter FB_FILE  = "",          // $readmemh file for the framebuffer
    parameter PAL_FILE = ""           // and for the palette
) (
    // draw side, clk_sys
    input  wire        i_clk_sys,
    input  wire        i_fb_we,
    input  wire [16:0] i_fb_addr,     // y*320 + x
    input  wire [7:0]  i_fb_wdata,
    output wire [7:0]  o_fb_rdata,    // mem[i_fb_addr], one clk_sys later
    input  wire        i_pal_we,
    input  wire [7:0]  i_pal_addr,
    input  wire [23:0] i_pal_wdata,   // {R, G, B}
    // scanout side, clk_pix
    input  wire        i_clk_pix,
    input  wire [9:0]  i_x,           // from vga_timing
    input  wire [9:0]  i_y,
    input  wire        i_de,
    input  wire        i_hsync,
    input  wire        i_vsync,
    output wire        o_hsync,       // delayed to match the color
    output wire        o_vsync,
    output wire        o_de,
    output wire [7:0]  o_red,
    output wire [7:0]  o_grn,
    output wire [7:0]  o_blu
);
```

The outputs may be `reg` or `wire`, as your pipeline needs. Color outside `o_de` doesn't matter: `hdmi_tx` sends control symbols then, and the testbench doesn't look.

## 6. The test image

The test image comes from `sim/py/ref_scanout.py` from this chapter, which is also the Python reference for the frame test. Copy it into `sim/py/` and write the two hex files once:

```sh
.venv/bin/python sim/py/ref_scanout.py --hex rtl/common
```

That writes `rtl/common/fb_test.hex` (76,800 lines, two hex digits each, address order) and `rtl/common/pal_test.hex` (256 lines of `RRGGBB`). Commit them: the hardware build loads them too. The image is built to fail loudly when something is slightly off:

- the background index steps by 1 per pixel in x and by 3 per row in y, so a reader one pixel early or late, a wrong stride or a missing line repeat changes almost every pixel,
- a 1-pixel white border and four 8x8 corner markers (red top left, green top right, blue bottom left, yellow bottom right) show the first and last row and column, mirroring, and the very last byte,
- a 1-pixel checkerboard, which must show as clean 2x2 squares,
- the 16 named palette colors as bars, and all 256 entries as a 16x16 swatch. Every ramp entry (16-255) has red, green and blue all different, so swapped channels show.

## 7. Simulate: `make frame-scanout`

Copy `tb_frame_scanout.v` into `sim/` and add `FRAME_TESTS += scanout` to the top-level Makefile. It uses Chapter 1.2's harness unchanged, with `frame_dump #(BITS=8)`. It drives `fb_scanout` directly from a `vga_timing` on a 25 MHz clock, with no TMDS in the way, and runs the draw port on its own 50 MHz clock.

```
$ make frame-scanout
...
PASS: tb_frame_scanout (latency 3 clocks, 444 draw-port writes; build/frame_scanout_{image,edit}.ppm written)
  ok    frame_scanout_edit.ppm: 640x480 match
  ok    frame_scanout_image.ppm: 640x480 match
PASS: compare scanout (2 frames match exactly)
```

It takes about 15 seconds. The testbench:

1. lets frame 0 pass, so a reader that forgets to rewind fails,
2. dumps frame 1 as `image`: the preloaded picture, which must equal the hex file scaled 2x,
3. in the vertical blanking after it, writes three palette entries and 444 pixels through the `clk_sys` port, then reads 74 addresses back through `o_fb_rdata` and checks them,
4. dumps frame 2 as `edit`: the border is now orange, with a cross through the middle and new corner pixels,
5. measures the latency from `i_de` to `o_de` and checks, on every clock, that `o_hsync`, `o_vsync` and `o_de` are the inputs delayed by exactly that much.

The frame dumpers' `vsync` input is `o_vsync | ~armed`, with `armed` set on line 10 of frame 0. That is the lesson from Chapter 2.2: a delay register that starts at 0 looks like a vsync pulse.

When `compare.py` reports a mismatch, it names the first bad pixel. Open `build/frame_scanout_image.ppm` next to `build/ref_scanout_image.ppm` and look at the corners first. With x off by one, only the right edge looks wrong at first glance, but every pixel is half a block late. If lines aren't doubled, the image is squashed into the top half, followed by garbage. A wrong stride slants the picture. Red and blue swapped turns the top-left marker blue.

## 8. The Arty top

`projects/4.1-scanout/top_artyz7.v` is Step 3.4's color-bar top with `color_bars` replaced:

- `clk_gen` makes `clk_pix`, `clk_ser` and, for the first time, uses `clk_sys`,
- `vga_timing` on `clk_pix` feeds `fb_scanout`, whose delayed syncs and 8-bit color go straight to `hdmi_tx`,
- `led[0]` shows `o_locked`.

`fb_scanout`'s outputs are already registered, so the top needs no extra stage before `hdmi_tx`. If yours ends in a palette read, add one register stage in the top, like Step 3.4.

Yosys runs in the project directory, so the hex paths are relative to it: `FB_FILE("../../rtl/common/fb_test.hex")` and `PAL_FILE("../../rtl/common/pal_test.hex")`, the same rule as Step 3.5's tile files. A wrong path makes yosys stop with an error.

To keep the draw port alive (Section 2) and give it a hardware check, add a few lines on `clk_sys`. Bring `sw[0]` and `sw[1]` in through `synchronizer`s clocked by `clk_sys`, then:

- while `sw[0]` is up, a counter steps through addresses 0 to 76,799 and writes the low byte of each address into the framebuffer. That paints diagonal stripes over the image in 1.5 ms,
- while `sw[1]` is up, a free-running 8-bit counter writes `{n, 8'h00, ~n}` into palette entry `n`, a red-to-blue ramp,
- `led[1]` is the XOR of all eight bits of `o_fb_rdata`, so the read port is used.

Add `rtl/common/synchronizer.v`, `framebuffer.v`, `palette.v` and `fb_scanout.v` to the project's `SRCS`.

## 9. Build and read the report

Run `make` in `projects/4.1-scanout/`. The reference build took 25 seconds:

```
== scanout: resource use (xc7z020clg400-1) ==
  SLICE_LUTX:     852/ 106400     0%
  SLICE_FFX:     192/ 106400     0%
  CARRY4:      46/  13300     0%
  ...
  OSERDESE2:       8/    200     4%
  BUFGCTRL:       3/     32     9%
  MMCME2_ADV:       1/      4    25%
  RAMB36E1:      19/    140    13%
  Max frequency for clock 'clk_sys': 190.62 MHz (PASS at 50.00 MHz)
  Max frequency for clock 'clk_pix': 145.75 MHz (PASS at 25.00 MHz)
```

- **`RAMB36E1` 19** is the framebuffer, as Section 1 predicted. No `RAMB18E1`: the palette is in LUTs.
- **`BUFGCTRL` 3**: `clk_pix`, `clk_ser` and `clk_sys`. A build with the draw port tied off has only two, because `clk_sys` then drives nothing.
- **`clk_sys` reaches 190 MHz.** Its critical path in `build/nextpnr.log` is the decode from the top address bits to the 19 blocks' write enables. `clk_pix`'s ends in a TMDS encoder, as in Phase 3. Both have lots of room.
- In `build/yosys.log`, look for the two `mapping memory` lines (`BLOCKRAM_TDP` for the framebuffer, `LUTRAM_SDP` for the palette) and, near the end, `19 RAMB36E1` and `32 RAM64M`.

## 10. On the board

Connect the monitor to HDMI OUT, set both switches down (toward the buttons) and run `make prog`. Then check:

1. The monitor reports 640x480 at 60 Hz, as in Step 3.4, and LD0 is lit.
2. Diagonal bands of blue, green and orange fill the screen. The 1-pixel white border shows on all four edges. If your monitor crops (overscan), use its "1:1" or "just scan" setting.
3. The corner markers are red top left, green top right, blue bottom left and yellow bottom right. If red and blue are swapped, check the channel order between the palette and `hdmi_tx`, which simulation would also have caught.
4. The checkerboard near the top left is a fine, even pattern with no smearing, and the 16 bars at the bottom left are black, white, red, green, blue, yellow, cyan, magenta, grey, orange, navy, dark green, maroon, mint, lavender and brown.
5. Flip `sw[1]` up: the background and the swatch turn into a red-to-blue ramp at once. The border and bars change too, because their entries are rewritten. The image itself is untouched.
6. Flip `sw[0]` up: diagonal stripes replace the image at once. Put both switches down again. The stripes stay, because the framebuffer is RAM. `make prog` brings the test image back.

**Done when:** `make frame-scanout` passes (both frames match the Python reference exactly), and the test image appears on the monitor with all four corners and the border visible.

## Testbench requirements

**Modules you provide** (in `rtl/common/`, found by `-y rtl/common`):

- `fb_scanout #(FB_FILE = "", PAL_FILE = "")`, with the ports in Section 5:
  - Framebuffer 320x240x8, address `y*320 + x`, preloaded with `$readmemh(FB_FILE)` (two hex digits per line); palette 256 x 24 bits `{R[23:16], G[15:8], B[7:0]}`, preloaded with `$readmemh(PAL_FILE)`.
  - `clk_sys` port: `i_fb_we` writes `i_fb_wdata` to `i_fb_addr` on a rising edge; `o_fb_rdata` shows the byte at the address presented before a rising edge, after that edge (one clock of latency; reads never overlap writes in the test). `i_pal_we` writes `i_pal_wdata` to entry `i_pal_addr`. Writes are visible to scanout in the next frame.
  - `clk_pix` side: from `vga_timing`'s `x`, `y`, `de`, `hsync`, `vsync` (640x480, 800x525 timing). Beam pixel (x, y) with `de` high shows the palette color of framebuffer pixel (x/2, y/2), after a latency L of 1-24 clocks. `o_hsync`, `o_vsync` and `o_de` must be `i_hsync`, `i_vsync` and `i_de` delayed by the same L on every clock, after power-up. The color outside `o_de` is not checked.
  - The reader must work in every frame, not just the first after power-up.
- `vga_timing` from Chapter 1.1, unchanged, plus whatever modules `fb_scanout` uses (`framebuffer`, `palette`).

**Files from this chapter:** `tb_frame_scanout.v` to `sim/`, `ref_scanout.py` to `sim/py/`. Chapter 1.2's `frame_dump.v`, `ppm.py`, `compare.py` and `frame.mk`, unchanged. Run `.venv/bin/python sim/py/ref_scanout.py --hex rtl/common` once to write `rtl/common/fb_test.hex` and `pal_test.hex`, and add `FRAME_TESTS += scanout` to the top-level Makefile.

**Testbench:** `sim/tb_frame_scanout.v`, top module `tb_frame_scanout`, run from the repo root by `make frame-scanout` (about 15 seconds; `+vcd` writes a very large `.vcd`). `clk_pix` 25 MHz, `clk_sys` 50 MHz, not edge-aligned. It loads the hex files from `rtl/common/` and checks the read-back against its own copy. It dumps frames 1 and 2 after power-up to `build/frame_scanout_{image,edit}.ppm`, writing between them during vertical blanking: palette entries 1 = `FF8000`, 254 = `0080FF`, 255 = `80FF00`; index 254 at the four corner pixels; index 255 on row 120, x = 40-279, and on column 161, y = 20-219. It prints one `PASS` or `FAIL` line, and `compare.py` then checks both frames against `ref_scanout.py` pixel for pixel. By hand:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb_frame_scanout.vvp sim/tb_frame_scanout.v
vvp -n build/tb_frame_scanout.vvp
.venv/bin/python sim/py/ref_scanout.py build
.venv/bin/python sim/py/compare.py --set build scanout
```
