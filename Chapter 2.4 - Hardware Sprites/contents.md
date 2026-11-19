# Chapter 2.4 - Hardware Sprites

The tile engine from Chapter 2.3 draws anything that lines up with an 8x8 grid. A snake that moves one cell at a time fits that grid. A spaceship that glides one pixel per frame and flies over the background does not. Consoles and arcade boards solved this with **sprites**: a few small pictures that the hardware draws on top of the tiles, each at any pixel position the game asks for. This chapter adds four of them to the tile engine and uses them for a small shooter: a ship, two enemies and a bullet.

There is still no framebuffer. A sprite, like everything else on the Go Board, has to be computed as the beam passes. The trick that makes this cheap, **per-scanline sprite evaluation**, is the same one the NES used, and it also explains a limit every player of that console knows: too many sprites on one line, and some of them flicker or disappear.

## What a sprite is

Each of the four sprites is a **16x16 picture with 2 bits per pixel**, drawn in logical (320x240) pixels like everything else in this phase. Four registers describe it:

| Register | Width | Meaning |
| --- | --- | --- |
| x | 10 bits, two's complement | left edge, -512 to 511; the visible columns are 0 to 319 |
| y | 10 bits, two's complement | top edge, -512 to 511; the visible lines are 0 to 239 |
| image | 4 bits | which of 16 pictures in the sprite ROM to draw |
| enable | 1 bit | 0 hides the sprite |

The coordinates are signed so that a sprite can slide off any edge. At x = -6 only its right 10 columns show, and at y = 233 only its top 7 rows. A sprite at x = -16 or x = 320 is fully off-screen and draws nothing.

Three rules decide the color of each pixel:

- **Transparency.** Pixel value 0 is transparent: the sprite has no pixel there, and whatever is behind it shows through. Values 1 to 3 select a 3-entry **sprite palette**, `SPAL1` to `SPAL3`, separate from the tile palette.
- **Fixed priority.** Sprite 0 is in front of sprite 1, which is in front of 2, then 3. Where two sprites overlap, the lower-numbered sprite's opaque pixel wins, and its transparent pixels let the next sprite show through.
- **Sprites over tiles.** Any opaque sprite pixel covers the tile layer.

### The sprite file

This chapter supplies `sprites.hex`, 16 images of 16 rows. One row is one 32-bit word, leftmost pixel in bits 31:30, as in `tiles.hex` but twice as wide:

```
00000000 0003c000 00014000 00055000 ...   //  0 ship
```

| Images | Contents |
| --- | --- |
| 0 | ship |
| 1, 2 | enemy, two animation frames |
| 3 | bullet |
| 4 | explosion |
| 5-15 | test patterns: a striped top row and left column mark the corner, so a flipped or shifted sprite shows up in the frame test |

The ROM is 256 words of 32 bits, 8 Kbit, addressed by `{image, row}`. yosys builds it from two `SB_RAM40_4K`s side by side (each 256 x 16). As before, the read must be registered. The Python reference reads the same file, so edit the art as you like.

## Per-scanline evaluation

Drawing a sprite at pixel `(lx, ly)` needs one 2-bit value: row `ly - y`, column `lx - x` of its image. The obvious design reads the sprite ROM for every sprite on every pixel. That would mean four ROM reads per clock, and the block RAM has one read port.

The way out is to notice that all the pixels of one sprite on one line come from **a single 32-bit ROM word**, the sprite's row for that line. So split the work in two:

1. **During horizontal blanking**, decide which sprites cross the *next* line, and for each one read its row word from the ROM into a register that belongs to that sprite. This is **sprite evaluation**.
2. **During the visible line**, draw from those registers only. No memory is touched.

Horizontal blanking is 160 clocks long (x = 640 to 799), and evaluating one sprite takes one ROM read, so all four fit easily. Use the time to share hardware: evaluate one sprite per clock with a single subtractor and a single ROM port, a mux choosing which sprite's y and image to look at. For sprite `i` at clock `640 + i`:

```verilog
wire [9:0] row = ly_next - spr_y[i];        // 10-bit, wraps for negative values
wire       hit = spr_en[i] && row[9:4] == 0; // 0 <= row < 16, signed or not
```

`row[9:4] == 0` is the whole range check. If the sprite starts below the line, the difference is negative and its top bits are ones. Because every logical line and every y fit in 10 bits, the wraparound can never produce a false hit. On the next clock the ROM word for `{image, row[3:0]}` arrives; store it in sprite `i`'s register, or store zero if `hit` was false. A zero row is all transparent, so the drawing logic never needs to ask whether the sprite is on this line at all.

Two details trip up most first versions:

- **The next line.** Each logical line is drawn on two real lines, so the logical line after real line `y` is `(y + 1) >> 1`. And the line after 524 is line 0, not 525. Without that wrap, the first real line of every frame is drawn with the rows for logical line 262, so a sprite at y = 0 loses half of its top row. The frame test catches this.
- **ROM latency.** The word that arrives on clock `640 + i + 1` belongs to the sprite chosen on clock `640 + i`. Register the sprite number along with the read, the same pipeline alignment as in Chapter 2.2.

### Drawing the line with shift registers

The NES drew each evaluated sprite with a **shift register** loaded with the row and a counter loaded with the sprite's x. As the beam moves, the counter counts down; when it reaches zero the shift register starts shifting, and its top bits are the sprite's pixel. You can build the same thing with a comparator instead of a counter:

- When the logical x of the beam equals the sprite's x, set the sprite's `active` flag.
- While active, the pixel is the top 2 bits of the register, `sh[31:30]`. Shift left by 2 once per **logical** pixel, which is every second clock (on `x[0] == 1`).
- Shift zeros in. After 16 logical pixels the register is all zero, which is transparent, so no counter is needed to stop.
- Clear `active` when the next row is loaded.

**Negative x.** A sprite at x = -6 must already have shifted out 6 pixels when the line starts. The NES simply did not allow it: its sprites cannot hang off the left edge, and games hid the problem with an 8-pixel blank margin. You can do better. The last 32 clocks of horizontal blanking (x = 768 to 799) are, in logical pixels, positions -16 to -1 of the next line. Count them that way, start the shift register there, and a sprite at x = -6 shifts out its first 6 pixels while the beam is still blank. Evaluation finishes long before x = 768, so the row is loaded in time.

**Another style.** On an FPGA you do not have to shift. Keep the row in a plain register and pick the pixel by its column: `col = lx - x`, show `row_word[{~col[3:0], 1'b0} +: 2]` when `col` is 0 to 15 (the same 10-bit trick as for y). That handles every edge with no special case, at the price of a subtractor and a 16-to-1 mux per sprite instead of a comparator and a shift. Both styles pass the test; the shifter is the historical one, so try it first.

### Why there is a sprites-per-line limit

The NES held 64 sprites in its object memory but had only **8 shift registers**. Evaluation scanned all 64 for each line and kept the first 8 it found; a 9th sprite on that line was not drawn. Games that needed more rotated the priority every frame, so each sprite vanished only some of the time. That is the flicker you remember.

The limit is not about time. Between x = 640 and x = 768, where the shift registers start, this design could read more than a hundred rows from the ROM. The limit is **logic**: each sprite drawn at once needs its own 32-bit register, its own comparator and its own place in the priority mux, and every one of them is in use on every pixel. With four sprites and four registers this engine never runs out. If you want to feel the limit, try eight sprites in the sprite table with four registers: evaluation keeps the first four hits, in priority order, and the rest disappear on that line.

## Mixing with the tiles

Build the whole display as one module, `sprite_engine`, with the same ports as `tile_engine` plus the sprite registers. Inside, instantiate your `tile_engine` from Chapter 2.3 and put the sprites on top of its output.

The tile engine delivers a pixel some clocks after the beam position that produced it (two in Chapter 2.3, but it is your design and you know its number). The sprite pixel is ready earlier: the registers are already loaded, so it depends only on the current x. Delay the sprite's 2-bit result by the tile engine's latency, then choose:

```verilog
if (!t_de)             color <= 0;
else if (spix != 0)    color <= SPAL[spix];   // highest-priority opaque sprite
else                   color <= tile_color;
```

and delay `o_hsync`, `o_vsync` and `o_de` by the same total. The priority choice is a chain: `p0 != 0 ? p0 : p1 != 0 ? p1 : ...`. Mask with `de`: the shift registers run during the end of blanking, and the test checks that the output is black whenever `o_de` is low.

**When the positions change.** The game moves its sprites at the start of vertical blanking, exactly as in Pong, so every line of a frame sees the same positions and nothing tears. If your engine uses the sprite inputs directly, that is all you need. If it copies them into its own registers once per frame, do that late in vertical blanking: the testbench changes the inputs at the start of vertical sync (line 490), and the next frame must show the new positions.

## A small shooter

The game is yours to design. The reference uses this layout:

| Sprite | Use | Image |
| --- | --- | --- |
| 0 | bullet, enabled only while in flight | 3 |
| 1 | ship, near the bottom (y = 216) | 0 |
| 2, 3 | enemies, on two rows | 1 and 2 alternating every 16 frames, 4 while exploding |

Putting the bullet in sprite 0 keeps it in front of whatever it hits. Write `shooter_game` the way you wrote `pong_game`: all state in registers, updated once per frame on `i_tick`, sprite registers as outputs.

- **Ship.** Switch 1 and 2 move it 2 pixels per frame, clamped to 0..304.
- **Bullet.** Switch 3 fires when no bullet is in flight. It starts just above the ship and moves up 4 pixels per frame. It disappears once it has left the top of the screen.
- **Enemies.** They move right, 1 and 2 pixels per frame. At x = 320 they wrap to x = -16, so on the board you watch them slide off the right edge and back in from the left: a free test of off-screen clipping.
- **Hits.** Compare the bullet's position with each enemy's, as signed numbers: a hit when `|bx - ex| < 10` and `|by - ey| < 8`. On a hit, the bullet disappears, the enemy shows the explosion for 16 frames and then restarts at x = -16, and the score goes up by one.

Declare the positions `reg signed` and the constants `10'sd...`, or the comparisons silently become unsigned and a bullet at y = -4 counts as far below the screen.

The tile layer is the background. Since the tile engine's map can be loaded from `MAP_FILE`, make a `shooter_map.hex` with a few lines of Python: 1,200 bytes in hex, tile 1 (wall) along row 29 and tile `0x2e` (the "." glyph) scattered as stars, 0 elsewhere. Nothing writes the map while the game runs, so tie the write port to 0.

## The board

Make `projects/2.4-shooter/` with a `top_goboard.v` that connects:

- `vga_timing` to `sprite_engine` (pass `TILE_FILE("../../rtl/common/tiles.hex")`, `SPRITE_FILE("../../rtl/common/sprites.hex")` and `MAP_FILE("shooter_map.hex")`);
- Switch 1 to 3 through `synchronizer`s to `shooter_game`'s left, right and fire inputs;
- `i_tick` to the start of vertical blanking, `x == 0 && y == 480`;
- `shooter_game`'s sprite outputs to `sprite_engine`'s sprite inputs;
- the score to the 7-segment display in hex through two `hex_to_7seg`s.

As before, register `hsync`, `vsync` and the color one more clock before the pins.

On the board, check by hand:

1. The ship sits near the bottom over a starfield, with a wall row below it. The ship's transparent pixels show the stars behind it, not a solid square.
2. The ship moves smoothly with Switch 1 and 2 and stops flush at both edges.
3. Enemies slide off the right edge a column at a time and reappear from the left the same way, with no jump at x = 0.
4. Hold Switch 3: bullets fly up; a bullet that hits an enemy shows the explosion and adds one to the score.
5. Where sprites cross each other or the wall row, the lower-numbered sprite is in front and transparent pixels stay transparent.
6. Nothing flickers or tears, even with all four sprites on the same lines.

## Reading the resource report

For the reference design (the board top above, with the shift-register sprite engine), yosys 0.52 and nextpnr-ice40 report:

```
Info: Device utilisation:
Info:          ICESTORM_LC:     689/   1280    53%
Info:         ICESTORM_RAM:       7/     16    43%
Info:                SB_IO:      30/    112    26%
Info:                SB_GB:       6/      8    75%
...
Info: Max frequency for clock 'i_Clk$SB_IO_IN_$glb_clk': 83.81 MHz (PASS at 25.00 MHz)
```

yosys's `stat` gives 479 `SB_LUT4`, 209 `SB_CARRY`, 269 flip-flops and 7 `SB_RAM40_4K`. With `-noflatten`, the sprite logic itself (`sprite_engine` without its `tile_engine`) is 285 LUTs, 23 carries, 153 flip-flops and the 2 block RAMs of the sprite ROM. `shooter_game` is 162 LUTs and 140 carries, almost all of them signed comparisons, `tile_engine` 61 LUTs and 5 block RAMs, and `vga_timing` 29 LUTs.

The plan for this step warned that the design would come close to the HX1K's limit. The reference does not: it uses about half the chip. But look at where the cells go. Four sprites cost more than the whole tile engine, and most of that is the 128 bits of shift register, each needing a LUT to choose between "load", "shift" and "hold", plus a 10-bit comparator per sprite. That cost grows with every sprite you can draw on one line, so, as a rough estimate, eight sprites would take another 250 cells or so, and twelve would not fit next to the game. Memory is the other budget: 7 of 16 block RAMs are in use, and Chapter 2.5's 64x32 map needs one more.

Check the log for `mapping memory ... r_rom via $__ICE40_RAM4K_` for the sprite ROM, next to the tile engine's two memories. If that line is missing, the 8 Kbit ROM became logic, and the design will not place. The usual cause is reading it in more than one place: keep one address mux and one plain registered read.

## Testbench requirements

Provide in `rtl/common/`:

- **`sprite_engine.v`**: `sprite_engine #(TILE_FILE = "tiles.hex", SPRITE_FILE = "sprites.hex", MAP_FILE = "", PAL0, PAL1, PAL2, PAL3, SPAL1, SPAL2, SPAL3)` (palette entries 9-bit `{r, g, b}`; `PAL*` and `MAP_FILE` are passed to your `tile_engine`, `SPAL1`-`SPAL3` are the sprite colors for pixel values 1-3).
  - Inputs: `i_clk`, `i_x[9:0]`, `i_y[9:0]`, `i_de`, `i_hsync`, `i_vsync` (from a 640x480 `vga_timing`), the tile map write port `i_wr_en`, `i_wr_addr[10:0]`, `i_wr_data[7:0]` (as in Chapter 2.3), and the sprite registers `i_spr_x[39:0]`, `i_spr_y[39:0]` (sprite `n` in bits `[10n +: 10]`, logical top-left corner, two's complement), `i_spr_img[15:0]` (`[4n +: 4]`), `i_spr_en[3:0]`.
  - Outputs: `o_hsync`, `o_vsync`, `o_de` (delayed by your latency, 1 to 15 clocks) and `o_red[2:0]`, `o_grn[2:0]`, `o_blu[2:0]` lined up with them, black while `o_de` is low.
  - Four 16x16 sprites from `SPRITE_FILE` as described above: value 0 transparent, sprite 0 in front, all sprites over the tiles, clipped at every edge. The testbench changes the sprite inputs only at the start of vertical sync (line 490) and holds them for the next frame.
- **`sprites.hex`**: copy from this chapter.
- `tile_engine.v` and `tiles.hex` from Chapter 2.3, `vga_timing.v` from Chapter 1.1.

Copy from this chapter: `tb_frame_sprites.v` to `sim/`, `ref_sprites.py` to `sim/py/`. The frame test also uses `sim/frame_dump.v`, `sim/py/ppm.py` and `sim/py/compare.py` from Chapter 1.2, and `ref_sprites.py` imports `sim/py/ref_tiles.py` from Chapter 2.3 for the background. It reads `rtl/common/tiles.hex` and `rtl/common/sprites.hex`.

Run `make frame-sprites` (add `FRAME_TESTS += sprites` to the top-level Makefile). It takes a little over a minute. The testbench fills the map with every tile, then writes five frames, `build/frame_sprites_{corner,offscreen,overlap,hidden,abut}.ppm`: sprites at (0, 0) and the bottom-right corner, partly off all four edges, showing a single pixel, all four overlapping on the same lines, disabled and just off-screen, and side by side. It checks sync alignment and blanking, and `compare.py` checks each frame against `ref_sprites.py` pixel for pixel.

Board project: `projects/2.4-shooter/` with `top_goboard.v`, `shooter_game.v` (in `rtl/common/` if you like) and `shooter_map.hex` as described above.
