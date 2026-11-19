# Chapter 2.2 - Font ROM and Text Mode

Pong drew everything with comparators: each object was a rectangle, and the color of a pixel came from a handful of `<` and `>=` tests. That stops working as soon as you want letters. A screen of text has 1,200 characters, each a small bitmap, and no set of comparators could describe them. You need memory: one memory that holds the shapes of the letters (the **font ROM**) and one that holds which letter goes in each place on the screen (the **character RAM**). This chapter builds both in the iCE40's block RAM and uses them to show a full screen of text.

The memories bring the main lesson of this chapter. Block RAM is synchronous: you give it an address on one clock edge and the data comes out after it. A pixel that needs two lookups in a row is ready two clocks after the beam position that asked for it, and everything that travels with the pixel has to be delayed to match.

## How text mode works

Text mode is how the IBM PC, the Apple II and most terminals drew their screens. The screen is a grid of character **cells**. Memory holds one byte per cell, the character code, and the hardware turns codes into pixels as the beam scans.

At this phase's resolution of 320x240 logical pixels, with 8x8 characters, the grid is 40 columns by 30 rows, so the character RAM holds 40 x 30 = 1,200 bytes. Each character is 8x8 logical pixels, which is 16x16 real pixels on the 640x480 output. That makes the address arithmetic pure bit slicing:

| Quantity | Range | Bits of the real 640x480 position |
| --- | --- | --- |
| Cell column | 0..39 | `x[9:4]` |
| Cell row | 0..29 | `y[8:4]` |
| Pixel column inside the glyph | 0..7 | `x[3:1]` |
| Pixel row inside the glyph | 0..7 | `y[3:1]` |

Bit 0 of each coordinate is dropped because every logical pixel is 2x2. The cells are stored row by row, so the character at column `c`, row `r` is at address `r * 40 + c`. The multiplication by 40 needs no multiplier: 40 = 32 + 8, so `r * 40` is `(r << 5) + (r << 3)`, one adder.

The **font** is a table of glyphs, one per character code. Each glyph is 8 bytes, one per row from top to bottom, and each bit is one pixel: 1 for the letter's color (the foreground), 0 for the background. The glyph for code `c`, row `gr` is at font address `c * 8 + gr`, which again is just `{c, gr}`. Here is the letter A from this chapter's font, with bit 7 as the leftmost pixel:

```
0x30  ..XX....
0x78  .XXXX...
0xCC  XX..XX..
0xCC  XX..XX..
0xFC  XXXXXX..
0xCC  XX..XX..
0xCC  XX..XX..
0x00  ........
```

The last row is empty and so is the right column. That gap is the spacing between letters, built into each glyph, so neighbouring characters never touch.

**Inverse video.** The font has 128 glyphs (codes 0x00 to 0x7F), but the character RAM holds bytes. Use the spare bit the way many terminals did: a code with bit 7 set draws glyph `code & 0x7F` with foreground and background swapped. That gives you highlighted text and a block cursor (an inverse space) for one XOR gate.

## The font file

This chapter supplies the font as `font8x8.hex`. The glyphs are Daniel Hepper's `font8x8_basic` (github.com/dhepper/font8x8), which is based on Marcel Sondaar's and IBM's public domain VGA fonts and is itself released into the **public domain**, so you can use and change it freely. The file covers codes 0x00 to 0x7F: all printable ASCII from 0x20 (space) to 0x7E (`~`). The control codes below 0x20 and 0x7F are blank. The original stores the leftmost pixel in bit 0; this file reverses each byte so that bit 7 is leftmost, which matches how you read the hex.

The format is the one `$readmemh` reads: hex numbers separated by whitespace, loaded into consecutive addresses, with `//` comments allowed. Each line holds one glyph:

```
30 78 cc cc fc cc cc 00   // 0x41 'A'
```

1,024 bytes in all. The Python reference for this chapter reads the same file, so if you edit a glyph, the hardware and the reference change together.

## Block RAM with `$readmemh`

The iCE40 HX1K has 16 block RAMs called `SB_RAM40_4K`, 4 Kbit each. Each can be shaped as 256x16, 512x8, 1024x4 or 2048x2. You rarely instantiate them by hand. Instead you write an array in a way yosys recognises, and it **infers** the block RAM for you. The pattern for a ROM is:

```verilog
reg [7:0] font [0:1023];
initial $readmemh(FONT_FILE, font);

always @(posedge i_clk)
    r_glyph <= font[font_addr];     // registered read: data appears after the edge
```

Two things make this work.

- **The read is registered.** The iCE40's block RAMs have no asynchronous read port. If you write `assign glyph = font[font_addr];`, yosys cannot use block RAM and builds the table out of logic cells instead. For this font that costs about 550 LUTs, over 40% of the chip; the writable character RAM would need 9,600 flip-flops and would not fit at all.
- **`$readmemh` in an `initial` block.** In simulation it fills the array at time zero. In synthesis yosys reads the file and puts its contents into the block RAM's initial values, so they are part of the bitstream and present the moment the FPGA configures. The path is relative to the directory where the tool runs, which is why the file name is a parameter.

The character RAM is the same, plus a write port in the same clock domain:

```verilog
always @(posedge i_clk)
    if (i_wr_en) chars[i_wr_addr] <= i_wr_data;
```

Give it an optional `TEXT_FILE` parameter for its initial contents, so the board can show a screen with nothing writing to it. A guarded load works in both iverilog and yosys:

```verilog
initial if (TEXT_FILE != "") $readmemh(TEXT_FILE, chars);
```

Nothing in this chapter writes the RAM while the board runs, but the testbench uses the write port to load its screens, and Chapter 2.3 builds on it.

## Pipeline alignment

Now follow one pixel through the hardware. On the clock where `vga_timing` says the beam is at `(x, y)`:

| Clock | What happens |
| --- | --- |
| 0 | Compute the cell address `row * 40 + col` from `x`, `y` and present it to the character RAM. |
| 1 | The character code comes out. Combine its low 7 bits with the glyph row to form the font address. |
| 2 | The glyph row byte comes out. Pick the bit for this pixel's column and choose the foreground or background color. |

The color for position `(x, y)` is ready two clocks after `vga_timing` showed `(x, y)`. By then `vga_timing` is already at `x + 2`. If you send the color out next to the current `hsync`, `vsync` and `de`, the whole picture lands two real pixels to the right of where it belongs, the first two pixels of every line show whatever was fetched during blanking, and the last two pixels of the text are cut off.

The fix is to **delay everything else by the same two clocks**, with a short chain of flip-flops for each signal that must stay lined up with the pixel:

- `hsync`, `vsync` and `de`, which leave the module next to the color;
- the glyph column `x[3:1]`, which is needed at clock 2 to pick the bit;
- the inverse-video bit (code bit 7), which comes out of the RAM at clock 1 and is needed at clock 2.

The glyph row `y[3:1]` needs no delay: `y` only changes between lines, during blanking, and the pipeline is far shorter than the blanking interval. Delaying it as well does no harm.

The module's outputs are then all "two clocks late" together, and the monitor cannot tell. The rule generalises to every pipeline you will build from here on: **count the clocks along each path, and delay every signal that travels beside the data by the same count.** The Phase 1 board tops already did this once, when they registered sync and color together before the pins.

A small extra point: during blanking, `x` runs up to 799 and `y` up to 524, so the cell address goes past 1,199. That is harmless, because those pixels are blanked, but in simulation an out-of-range read returns X. If the delays are wrong, that X can end up in a visible pixel. So if `frame_dump` reports an X color at pixel 0 of a line, you are looking at a pixel that was fetched during blanking: your `de` is not delayed enough.

**Why the frame dump catches what your eye doesn't.** If you delay `de` by one clock instead of two, every character moves one real pixel to the right: half a logical pixel. On a monitor this is almost invisible. The frame dump compares at full 640x480 resolution, so it shows up at once as thousands of differing pixels. This is the main reason the test for this chapter exists.

## Colors and blanking

Use two 9-bit parameters, `FG` and `BG`, packed as `{r, g, b}` with 3 bits each (so `9'o777` is white and `9'o001` is a dark blue). Output `FG` where the glyph bit (after the inverse XOR) is 1 and `BG` where it is 0. While the delayed `de` is low, output black. A background that is not black makes this easy to check: you can see exactly where the visible area ends.

## The board

Make a project `projects/2.2-text/` with a `top_goboard.v` that connects `vga_timing` to `text_mode`, ties the write port off (`i_wr_en = 0`), and registers the delayed `hsync`, `vsync` and color for one more clock before the pins, as in Phase 1. Since yosys runs inside the project directory, pass `FONT_FILE("../../rtl/common/font8x8.hex")` and a `TEXT_FILE` in the project, such as `screen.hex`.

Write `screen.hex` with a few lines of Python rather than by hand:

```python
cells = [0x20] * 1200                       # all spaces
def put(row, col, text, attr=0):
    for k, ch in enumerate(text):
        cells[row * 40 + col + k] = ord(ch) | attr
put(1, 1, "Hello from block RAM!")
put(3, 1, " inverse video ", 0x80)
with open("screen.hex", "w") as f:
    for r in range(30):
        f.write(" ".join(f"{v:02x}" for v in cells[r * 40:r * 40 + 40]) + "\n")
```

On the board, check by hand:

1. The monitor locks and shows your text, with no flicker and no garbage between characters.
2. Characters placed in all four corner cells (row 0 and row 29, column 0 and column 39) are complete. A missing column of pixels on the left or right edge means a misaligned pipeline.
3. A line with bit 7 set shows dark letters on a light bar.
4. Edit `screen.hex`, rebuild and reprogram: the new text appears, with no Verilog changed.

**Try it (optional):** connect your Chapter 0.4 `uart_rx` to the write port with a cursor that advances after each byte, and type on the screen from a serial terminal.

## Reading the resource report

For the reference design (the board top described above), yosys 0.52 and nextpnr-ice40 report:

```
Info: Device utilisation:
Info:          ICESTORM_LC:     130/   1280    10%
Info:         ICESTORM_RAM:       5/     16    31%
Info:                SB_IO:      12/    112    10%
Info:                SB_GB:       2/      8    25%
...
Info: Max frequency for clock 'i_Clk$SB_IO_IN_$glb_clk': 164.61 MHz (PASS at 25.00 MHz)
```

Five block RAMs, and very little logic: yosys's `stat` shows 72 `SB_LUT4`, 53 `SB_CARRY` and 40 flip-flops, mostly `vga_timing`'s counters, the address adder and the delay chains. Compare that with Pong's 640 logic cells. Text is cheap in logic because the work has moved into memory.

Check that the memories really became block RAM: the yosys log lists five `SB_RAM40_4K` cells. In the reference, the 1,024-byte font uses two of them in 1024x4 mode (each holds four bits of every byte), and the 1,200-byte character RAM uses three in 512x8 mode (1,536 bytes, of which 336 go unused). If `ICESTORM_RAM` shows 0 and the logic cell count explodes, one of your reads is not registered.

Five of sixteen block RAMs is a real share of this chip's memory. Chapters 2.3 to 2.5 add tile graphics, a palette and sprites, and they come out of the same 8 KB.

## Testbench requirements

Provide in `rtl/common/`:

- **`text_mode.v`**: `text_mode #(FONT_FILE = "font8x8.hex", TEXT_FILE = "", FG = 9'o777, BG = 9'o001)` with
  - inputs `i_clk`, `i_x[9:0]`, `i_y[9:0]`, `i_de`, `i_hsync`, `i_vsync` (straight from `vga_timing`, real 640x480 coordinates), and the write port `i_wr_en`, `i_wr_addr[10:0]` (cell `row * 40 + col`), `i_wr_data[7:0]`;
  - outputs `o_hsync`, `o_vsync`, `o_de` (the inputs delayed by your pipeline latency, at least 1 clock) and `o_red[2:0]`, `o_grn[2:0]`, `o_blu[2:0]` lined up with them: `FG`/`BG` (packed `{r, g, b}`) by glyph bit, swapped when code bit 7 is set, black while `o_de` is low.
  - Font ROM loaded from `FONT_FILE` with `$readmemh`; character RAM optionally from `TEXT_FILE`. Both reads registered, so they infer block RAM.
- **`font8x8.hex`**: copy from this chapter.
- `vga_timing.v` from Chapter 1.1.

Copy from this chapter: `tb_frame_text.v` to `sim/`, `ref_text.py` to `sim/py/`. They also use `sim/frame_dump.v`, `sim/py/ppm.py` and `sim/py/compare.py` from Chapter 1.2. `ref_text.py` reads `rtl/common/font8x8.hex`.

Run `make frame-text` (add `FRAME_TESTS += text` to the top-level Makefile). It takes about 20 seconds. The testbench loads two screens through the write port, writes `build/frame_text_{string,codes}.ppm`, and also checks that `o_hsync`, `o_vsync` and `o_de` share one delay and that the color is black outside `o_de`. `compare.py` then checks both frames against `ref_text.py` pixel for pixel. The testbench works with any pipeline latency from 1 to 15 clocks.

Board project: `projects/2.2-text/` with `top_goboard.v` and `screen.hex` as described above.
