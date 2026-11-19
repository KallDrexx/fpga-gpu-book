# Chapter 2.5 - UART-Driven Tiles and Scrolling

Until now every picture on the Go Board came from inside the FPGA: a map loaded from a hex file, or a game written in Verilog. This chapter hands the screen to the PC. The tile map, the tile graphics, the palette, the sprite positions and a new pair of **scroll registers** all become registers that a Python script writes over the UART, using the packet protocol you built in Step 0.5.

The payoff is the idea this whole phase has been building towards. The script sends a 64x32 map once, about half a second of traffic. After that it moves the picture at 60 frames per second by sending **two scroll bytes per frame**. The FPGA does the rest as the beam scans. Sending changes instead of pixels is how every later part of this project talks to the GPU, from the SPI link in Phase 5 to the 3D commands in Phase 6.

## Hardware scrolling

The map grows from 40x30 cells to **64x32 cells**, 512x256 logical pixels, larger than the 320x240 screen. The screen becomes a window onto the map, and two registers, **scroll X** (0 to 511) and **scroll Y** (0 to 255), say where the window's top-left corner sits. Screen pixel (lx, ly) shows map pixel

```
mx = (lx + scroll_x) mod 512        my = (ly + scroll_y) mod 256
```

When the window runs off the right edge of the map it continues from the left edge, and the same for top and bottom. This is **wraparound**: the map is a cylinder in both directions, and a game can scroll forever by redrawing the columns just out of sight.

The hardware is one adder per axis, placed **before** the tile lookup. Everything you did in Chapter 2.3 then works on (mx, my) instead of on the beam position:

```verilog
wire [8:0] mx = i_x[9:1] + i_scroll_x;    // 9 bits: wraps at 512 by itself
wire [7:0] my = i_y[8:1] + i_scroll_y;    // 8 bits: wraps at 256 by itself
wire [10:0] map_addr = {my[7:3], mx[8:3]}; // row * 64 + column
// tile row = my[2:0], pixel column = mx[2:0]
```

This is why the map is 64x32 and not 40x30 or 50x40. With power-of-two sizes, "mod 512" is simply the carry you do not keep, and "row times 64 plus column" is two bit fields put side by side. A 40-column map would need a compare and subtract for the wrap and a multiplier (or the shift-and-add from 2.3) for the address.

Two mistakes are common here, and the frame test catches both:

- **Scrolling after the lookup.** If the cell comes from (mx, my) but the pixel inside the tile still comes from the beam, `x[3:1]` and `y[3:1]`, the screen moves in 8-pixel jumps and every tile is cut in the wrong place. All four of (column, row, pixel column, pixel row) must come from the sums. The pixel column also has to travel down the pipeline to the stage where the tile row arrives, exactly as `x[3:1]` did in 2.3.
- **Wrapping in the wrong place.** Computing the column as a 10-bit sum and then adding it to `row * 64` makes column 64 spill into column 0 of the *next* row. Past the seam, where the window shows the map's left edge again, the picture is then one row too low. Keep each sum at its own width and the wrap is correct by construction.

The adder adds a carry chain in front of the map read. At 25 MHz there is plenty of time for it, but you can also register (mx, my) first and accept one more clock of latency. The tests accept any latency from 1 to 15 clocks.

### Indices, then colors

There is a second change to the tile layer, and the reason for it is in the resource report at the end of this chapter. The palette is now writable, so it lives in registers instead of parameters. If each layer looks up its own color, as 2.3 and 2.4 did, those registers feed two wide multiplexers on every pixel, and on the HX1K that alone costs about a hundred logic cells.

The NES did it the other way round, and so should you. Each layer produces only a **color index**: the tile layer 2 bits, the sprite layer 2 bits. The mixer picks one index, `{1, sprite}` when a sprite pixel is opaque and `{0, tile}` otherwise, and a **single palette lookup** at the end of the pipeline turns that 3-bit index into a color. An 8-entry table read once per clock is a block RAM, and a block RAM costs no logic cells. So `scroll_tiles`, this chapter's tile layer, outputs `o_pix[1:0]` instead of red, green and blue, and the colors appear only at the very end. The read is registered like any block RAM read, so it adds one clock of latency; delay the syncs to match.

## Growing the register protocol

Step 0.5 gave you two packets, `W addr data` and `R addr`, and 16 eight-bit registers. That is enough to light LEDs, but this chapter needs 2,048 map bytes, 1,024 bytes of tile graphics, a palette, four sprites and the scroll offsets. There are two standard ways to reach that much memory through a small protocol:

- **A wider address.** Change the packet to `W addr_hi addr_lo data`. Every byte becomes directly addressable, and the decoder is a compare on 16 bits. The price is 4 bytes on the wire for every byte stored, and every host script from Step 0.5 needs a new packet format.
- **An address register and a data port.** Keep the packets exactly as they are. Two registers hold a pointer into a larger space, and writing a third register stores the byte at the pointer and **increments the pointer**. A bulk load is then "set the pointer once, then write DATA again and again": 3 bytes on the wire per byte stored. This is how the NES picture processor (PPUADDR and PPUDATA) and the TMS9918 and Sega video chips were loaded, and it is the choice here.

### The register map

The 16 registers of Step 0.5, extended:

| Addr | Name | Meaning |
| --- | --- | --- |
| 0x00 | LEDS | bits 3:0 drive the four LEDs, as in Step 0.5 |
| 0x01 | SEG | shown in hex on the 7-segment display, as in Step 0.5 |
| 0x02 | SCROLL_X | scroll X, bits 7:0 |
| 0x03 | SCROLL_Y | scroll Y, 0 to 255. Writing it also **commits** the frame's update (see below) |
| 0x04 | SCROLL_XH | bit 0 is scroll X bit 8 |
| 0x05 | ADDR_LO | VRAM pointer, bits 7:0 |
| 0x06 | ADDR_HI | VRAM pointer, bits 11:8 (in bits 3:0) |
| 0x07 | DATA | write: VRAM[pointer] = data, then pointer = pointer + 1 |
| 0x08-0x0F | | unused: writes ignored, reads return 0 |

Registers 0x00 to 0x04 read back what was last written; addresses above 0x0F read 0, as before. Reads of 0x05 to 0x07 are up to you (the reference returns the current pointer, and 0 for DATA); the tests do not read them. Reading VRAM back is not supported: the video owns the map's read port.

### The VRAM map

Behind ADDR and DATA is a 4 KB address space, called VRAM after the video memory of the consoles it imitates:

| VRAM address | Contents | Format |
| --- | --- | --- |
| 0x000-0x7FF | tile map, 64x32 | cell (col, row) at `row * 64 + col`; tile number in bits 5:0, bits 7:6 reserved |
| 0x800-0xBFF | tile graphics, 64 tiles | row r of tile t at `0x800 + 16t + 2r`: first byte bits 15:8 (the left four pixels), next byte bits 7:0, same pixel order as `tiles.hex` |
| 0xC00-0xC0F | palette, 8 entries | entry n at `0xC00 + 2n`: first byte bit 0 is color bit 8, next byte color bits 7:0, of the 9-bit `{r, g, b}`. Entries 0-3 color the tiles, 4 is unused, 5-7 are sprite colors 1-3 |
| 0xC10-0xC2F | sprites, 8 bytes each | sprite n at `0xC10 + 8n`: X bits 9:8 (in bits 1:0), X bits 7:0, Y bits 9:8, Y bits 7:0, ATTR (bit 7 enable, bits 3:0 image), 3 unused bytes |
| 0xC30-0xFFF | unused | writes ignored |

Every multi-byte value is **big-endian, high byte first**, so a host can write a 16-bit value with Python's `struct.pack(">H", v)`, and a sprite's X and Y with `">hh"`: a two's complement 16-bit value whose low 10 bits are exactly the 10-bit signed coordinate of Chapter 2.4. The hardware may take a value when its low byte arrives; for the palette, the reference keeps the high byte in a 1-bit latch and writes all 9 bits at once on the low byte. That is a standard trick for 16-bit registers on an 8-bit bus, and it is why the order is part of the specification. Sprite coordinates are screen coordinates: sprites do not scroll with the map.

The map and the tile graphics are written straight into their block RAMs through the write port you already have; the tile graphics are now a RAM with a write port instead of a ROM, still loaded from `tiles.hex` at power-up. Byte writes into a 16-bit row need either two 8-bit memories side by side or one memory with a byte mask; yosys maps either to block RAM. The sprite graphics stay a ROM from `sprites.hex`.

One packet layer, three kinds of destination: a decoder on the pointer sends each DATA byte to the map, the tile RAM, the palette or the sprite table. Keep that decoder outside the memory statements, as in 2.3.

### Is the FIFO still needed?

In Step 0.5 the FIFO absorbed bursts while the parser was busy. Here the parser takes a byte in two or three clocks, and a byte arrives every 2,170 clocks at 115,200 baud, so the FIFO never holds more than one byte. The one place the parser waits is a `READ` reply while `uart_tx` is still sending the previous reply, and since every reply needs two incoming bytes, the transmitter is always free in time. So this chapter replaces the FIFO with a **one-byte holding register**: set on the receiver's `o_valid`, cleared when the parser takes the byte. The resource report shows why that matters.

## When writes take effect

Chapter 1.3 taught that state which changes during a frame tears the picture. This chapter has two kinds of state, and they need different rules.

**Memory contents take effect at once.** A map, tile or palette byte goes straight into its RAM, and the next pixel that reads it uses it. Change a map cell while the beam is drawing it and that frame shows half the old tile and half the new. For a map drawn once at start-up that does not matter, and for small changes during play it is usually invisible. Phase 5 deals with streaming large map changes.

**Per-frame state is double-buffered.** Scroll and sprite positions change every frame, and a scroll change halfway down the screen shears the whole picture. So every one of these registers has two copies. The parser writes the **shadow** copy. The video reads the **active** copy, and the active copy is loaded from the shadow at one fixed point in vertical blanking.

**Commit on SCROLL_Y.** Copying every frame is not quite enough, because the host is not synchronized to the beam. Its writes for one frame arrive over about half a millisecond and can straddle the copy point. For most registers a split update shows one frame with half of the change, which nobody notices. But a scroll X crossing 256 needs SCROLL_XH and SCROLL_X to change together: copied in between, one frame shows the window 256 pixels away. The fix is to make the host's last write mean "the frame's update is complete". Here that is **SCROLL_Y**: writing it sets a commit flag, and at the next start of vertical blanking, if the flag is set, the hardware copies the whole set and clears the flag:

```verilog
if (vblank_start && r_commit) begin
    o_scroll_x <= {r_sxh, r_sx};  o_scroll_y <= r_sy;  /* ...and the sprites */
    r_commit   <= 1'b0;
end
if (wrote_scroll_y) r_commit <= 1'b1;   // later statement wins on a tie
```

So a host always ends a frame's update with SCROLL_Y, even when only a sprite moved and Y has not changed. A group cut by the copy point then simply waits one more frame instead of tearing.

**Where the shadows live.** The scroll shadows are 17 flip-flops. The sprite shadows would be 100, and flip-flops are the budget that runs out first on this chip (see below). Since the shadow copy is only written by the parser and read by the copy, one byte at a time, it can live in a small block RAM, laid out like the VRAM region it belongs to. At commit time a 5-bit counter walks the 32 bytes and loads the active sprite registers, one byte per clock, in 32 clocks of the 36,000 that vertical blanking lasts. The active registers stay flip-flops, because the sprite engine from 2.4 compares all four x positions with the beam on every pixel. Copying every frame without the commit flag, at any point inside blanking, is also a valid design; the tests accept both, and every group of writes they send ends with SCROLL_Y and completes inside the visible lines.

## The memory budget

The plan for this step promised "2 KB map, 1 KB tile graphics, well inside the 8 KB budget". Here is the whole budget, counted the way the chip counts it, in 512-byte `SB_RAM40_4K` blocks:

| Memory | Size | Blocks |
| --- | --- | --- |
| tile map, 64x32 | 2,048 x 8 bits | 3 (see below) |
| tile graphics, now writable | 512 x 16 bits = 1 KB | 2 |
| sprite graphics, ROM (2.4) | 256 x 32 bits = 1 KB | 2 |
| palette | 8 x 9 bits | 1 |
| sprite shadow table | 32 x 8 bits | 1 |
| total | | **9 of 16 (4.5 KB of 8 KB)** |

Two things in this table are worth noticing. The map needs 16 Kbit, four blocks, but yosys builds it from three. The video reads only bits 5:0 of each map byte, so yosys drops the two reserved bits from the memory and stores 2,048 x 6 bits. When you use bits 7:6 for something (flip bits, a second palette), the fourth block comes back. And the palette and the sprite table use 16 and 32 bytes of VRAM but cost a whole 512-byte block each. Block RAM comes in fixed-size pieces; a tiny memory is cheap in logic but not in blocks. With seven blocks free, memory is not what limits this design. Logic is.

## Does 60 Hz fit through 115,200 baud?

A UART byte is 10 bits on the wire (start, 8 data, stop), so 115,200 baud carries **11,520 bytes per second**, or **192 bytes per frame** at 60 frames per second. Each packet is 3 bytes:

| Update | Packets | Bytes | Time | Share of a frame |
| --- | --- | --- | --- | --- |
| scroll only: SCROLL_X, SCROLL_Y | 2 | 6 | 0.52 ms | 3.1% |
| scroll crossing X = 0 or 256: add SCROLL_XH | 3 | 9 | 0.78 ms | 4.7% |
| move all four sprites (ADDR twice, 32 DATA) and scroll | 36 | 108 | 9.4 ms | 56% |
| redraw the whole map | 2,050 | 6,150 | 534 ms | 32 frames |
| send the screen as pixels (320 x 240 x 9 bits) | | 86,400 | 7.5 s | 450 frames |

The scroll-only update uses 3% of the link; a frame could carry thirty times more. Sending the picture itself would take seven and a half seconds per frame. That gap, a factor of more than ten thousand, is the reason consoles had tile maps and scroll registers, and the reason this GPU will take commands instead of pixels.

One more detail shows up on the monitor. The Go Board's frame rate is 25 MHz / 420,000 = **59.52 Hz**, not 60. A script that sends exactly 60 updates per second sends one extra update about every two seconds, and that frame jumps 2 pixels instead of 1. You can see it as a tiny hitch in a slow scroll. `--fps 59.52` gets closer, but the PC's clock and the board's crystal still drift apart. The real fix is to synchronize the host to the display, for example with a frame counter register the host polls, or a byte the FPGA sends at every vertical blank. That is a good exercise, and it is what the Phase 5 protocol does.

## The board

Make `projects/2.5-scroll/` with a `top_goboard.v` that connects:

- `vga_timing` to `uart_ppu` (pass `TILE_FILE("../../rtl/common/tiles.hex")` and `SPRITE_FILE("../../rtl/common/sprites.hex")`, `CLK_HZ(25_000_000)`, `BAUD(115_200)`);
- `i_UART_RX` and `o_UART_TX` to the UART pins;
- `o_reg0[3:0]` to the four LEDs and `o_reg1` to the 7-segment display through two `hex_to_7seg`s, as in Step 0.5.

As before, register `hsync`, `vsync` and the color one more clock before the pins.

Inside `uart_ppu` you need: `uart_rx`, the holding register, the parser and register file (call it what you like; the reference calls it `ppu_regs`), `uart_tx`, and the picture: `scroll_tiles` under the sprite logic from Chapter 2.4, then the index mix and the palette RAM. The easiest way to reuse your sprite engine is to copy `sprite_engine.v` into a new module (the reference calls it `ppu`), swap its `tile_engine` for `scroll_tiles`, and replace its color mix with the index mix.

Copy `scroll_demo.py` from this chapter to `host/`. With the board programmed, run it and check by hand:

1. `python3 host/scroll_demo.py /dev/ttyUSB1` prints `map drawn: 6306 bytes in 0.55 s` (about half a second), and LEDs 1 and 3 are lit: the script's link check wrote LEDS = 0x5 and read it back.
2. The screen shows a starfield with block labels (two digits in every 8x4 block of the map), a snake, and the ship sprite in the middle, and the map scrolls diagonally, one pixel per frame, smoothly.
3. The wall around the map's edge slides past without a jump, with the map continuing seamlessly on the other side: that is the wraparound. A seam that skips a row or a column means the wrap is wrong.
4. The ship stays in the middle while the map moves under it, and the stars show through its transparent pixels.
5. `--dx 1 --dy 0` scrolls horizontally across a point where scroll X bit 8 changes (X = 256 or X = 0) about every 4 seconds with no flash of the wrong picture; that is the commit on SCROLL_Y doing its job.
6. Press Ctrl-C. The script reports about 60 frames per second, about 6.0 bytes per frame and the link about 3% busy. The picture stops and stays: the scroll registers hold their last value.
7. Run it again with `--tiles rtl/common/tiles.hex` after editing a tile in the file: the new art appears without rebuilding the bitstream.

`scroll_demo.py` is short and worth reading: it builds the setup as one long byte string (palette, map, sprites, each a pointer and a run of DATA packets), sends it once, then loops on a `time.perf_counter()` deadline, sending SCROLL_XH only when bit 8 changes and SCROLL_Y last.

## Reading the resource report

For the reference design (the board top above, without the FIFO), yosys 0.52 and nextpnr-ice40 report:

```
Info: Device utilisation:
Info:          ICESTORM_LC:     944/   1280    73%
Info:         ICESTORM_RAM:       9/     16    56%
Info:                SB_IO:      32/    112    28%
Info:                SB_GB:       6/      8    75%
...
Info: Max frequency for clock 'i_Clk$SB_IO_IN_$glb_clk': 89.41 MHz (PASS at 25.00 MHz)
```

yosys's `stat` gives 623 `SB_LUT4`, 103 `SB_CARRY`, 491 flip-flops and 9 `SB_RAM40_4K`. With `-noflatten`:

| Module | LUTs | Carries | Flip-flops | Block RAMs |
| --- | --- | --- | --- | --- |
| sprites, index mix, palette (`ppu` without `scroll_tiles`) | 303 | 21 | 168 | 3 |
| `ppu_regs`: parser, registers, shadows, commit | 177 | 13 | 220 | 1 |
| `scroll_tiles` | 87 | 15 | 49 | 5 |
| `uart_rx` + `uart_tx` | 79 | 15 | 53 | 0 |
| `vga_timing` | 29 | 47 | 20 | 0 |

This design did not fit at first, and the story of getting it to fit is the real lesson of the report. The first version kept Step 0.5's FIFO, held the palette and the sprite shadows in flip-flops, and looked up colors in each layer. It needed 1,261 logic cells, 98% of the chip, and nextpnr stopped with `Unable to find legal placement for all cells`. Moving the sprite shadows into a block RAM, dropping the FIFO and a few smaller savings brought it to 1,052 cells (82%), and placement *still* failed. Only after the palette moved into a block RAM, behind the index mix, did it drop to 944 cells and place, with timing to spare. Putting the FIFO back into that final design gives 1,056 cells and the same placement failure.

Why does a design at 82% not place? Because a logic cell is not a free-standing unit. The HX1K's cells come in tiles of eight, and **all eight flip-flops in a tile share one clock enable and one set/reset signal**. A register with its own enable, one per palette entry or per sprite field, needs a tile of its own for its flip-flops, however few there are; the other cells in that tile can only take logic without flip-flops. A design full of small registers with different enables runs out of tiles long before it runs out of cells. The practical limit on this chip is well below 100%; in this design, about 80% was already too much. When nextpnr fails like this, count your flip-flops and their enables before you look anywhere else, and move anything that is read one entry at a time into block RAM.

Look at the table again with that in mind. The sprite logic from Chapter 2.4 is still the largest single block, mostly its four 32-bit shift registers. The register file is next, and 100 of its 220 flip-flops are the active sprite registers that the sprite engine needs in parallel. Everything else together is about a third of the chip. If you want more on the Go Board, the next things to move are the active sprite registers: the NES kept its sprite table in RAM and read it during evaluation, one sprite at a time, which is the design Phase 5 builds on the Arty Z7, where these limits go away.

Check the log for `mapping memory ... via $__ICE40_RAM4K_` lines: one each for the map, the tile RAM, the sprite ROM, the palette and the sprite shadow table. An 8-entry memory is so small that yosys builds it from flip-flops unless told otherwise, and that puts the hundred cells back. Write `(* ram_style = "block" *)` in front of the palette's declaration and check that its line appears.

## Testbench requirements

Provide in `rtl/common/`:

- **`scroll_tiles.v`**: `scroll_tiles #(TILE_FILE = "tiles.hex")`.
  - Inputs: `i_clk`, `i_x[9:0]`, `i_y[9:0]`, `i_de`, `i_hsync`, `i_vsync` (from a 640x480 `vga_timing`); the VRAM write port `i_wr_en`, `i_wr_addr[11:0]`, `i_wr_data[7:0]` (map at 0x000-0x7FF, tile rows at 0x800-0xBFF, high byte at the even address; ignore 0xC00 and up); `i_scroll_x[8:0]`, `i_scroll_y[7:0]`.
  - Outputs: `o_hsync`, `o_vsync`, `o_de` (delayed by your latency, 1 to 15 clocks) and `o_pix[1:0]`, the color index lined up with them (any value while `o_de` is low).
  - Tile RAM loaded from `TILE_FILE` at power-up. The testbench changes the scroll inputs only at the start of vertical sync (line 490) and holds them for the next frame: use them directly, or copy them on line 491 or later.
- **`uart_ppu.v`**: `uart_ppu #(CLK_HZ = 25_000_000, BAUD = 115_200, TILE_FILE = "tiles.hex", SPRITE_FILE = "sprites.hex")`.
  - Inputs: `i_clk`, `i_rx` (serial in, idle high), `i_x[9:0]`, `i_y[9:0]`, `i_de`, `i_hsync`, `i_vsync`.
  - Outputs: `o_tx` (serial out), `o_hsync`, `o_vsync`, `o_de` (latency 1 to 15), `o_red[2:0]`, `o_grn[2:0]`, `o_blu[2:0]` (black while `o_de` is low), `o_reg0[7:0]`, `o_reg1[7:0]` (registers 0x00 and 0x01).
  - Step 0.5 packets and the register and VRAM maps above; sprites as in Chapter 2.4 (16x16, `sprites.hex`, value 0 transparent, sprite 0 in front, clipped at the edges), drawn over the scrolled tiles with palette entries 5-7.
  - Must work at 32 clocks per bit (the test runs at `BAUD = 781_250`). `o_reg0` and `o_reg1` must change within 4 bit times of a packet's last byte, and a read reply must start within 200 bit times.
  - A group of writes ending with SCROLL_Y that completes inside the visible lines of a frame must not change that frame, and must show in the next one.
- `vga_timing.v` (Chapter 1.1), `uart_rx.v` and `uart_tx.v` (Chapter 0.4), `tiles.hex` (Chapter 2.3), `sprites.hex` (Chapter 2.4), and whatever modules your `uart_ppu` uses.

Copy from this chapter: `tb_frame_scroll.v` and `tb_frame_ppu.v` to `sim/`, `ref_scroll.py` and `ref_ppu.py` to `sim/py/`, `scroll_demo.py` to `host/`. The frame tests also use `sim/frame_dump.v`, `sim/py/ppm.py` and `sim/py/compare.py` from Chapter 1.2. `ref_scroll.py` imports `ref_tiles.py` (Chapter 2.3) and `ref_ppu.py` imports `ref_sprites.py` (Chapter 2.4); they read `rtl/common/tiles.hex` and `rtl/common/sprites.hex`.

Add `FRAME_TESTS += scroll ppu` to the top-level Makefile, then run:

- `make frame-scroll` (about 50 seconds). The testbench writes the whole map and redraws tile 7 through the write port, then dumps `build/frame_scroll_{origin,fine,wrapx,wrapy,corner}.ppm` at scroll (0, 0), (3, 5), (300, 17), (101, 200) and (511, 255), checks that the syncs share one delay, and `compare.py` checks each frame against `ref_scroll.py` pixel for pixel.
- `make frame-ppu` (about two minutes). Everything goes over the serial line: LEDS and SEG, the palette, a redrawn tile 5, all 2,048 map bytes, three sprites and the scroll, then read-back of registers 0x00-0x04. It dumps `build/frame_ppu_{a,b,c}.ppm`: the scene at scroll (300, 200); the next frame, during which it moves sprite 0 partly over sprite 3, enables sprite 3 and scrolls to (37, 250) from line 100 on, which must still show the first scene; and the frame after, which must show the new one.

Board project: `projects/2.5-scroll/` with `top_goboard.v` as described above; host script `host/scroll_demo.py` (pyserial).
