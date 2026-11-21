# Chapter 2.3 - Tile Engine and Snake

Text mode drew the screen from two memories: a map that says which character goes in each cell, and a ROM with each character's shape. Nothing in that design cares that the shapes are letters. Swap the font for small pictures, give each pixel more than one bit, and you have a **tile engine**, the graphics hardware behind the NES, the Game Boy and most 1980s arcade boards. This chapter makes that change, then writes a game, Snake, whose logic draws by writing tile numbers into the map while the video hardware reads it.

The game brings two new ideas. The map now has two users at once, the game writing and the video reading, and block RAM with two ports is what lets them share it. And Snake needs to put food somewhere unpredictable, which in hardware means a **linear-feedback shift register**.

## 1. From characters to tiles

A tile is an 8x8 picture, exactly like a glyph, but with **2 bits per pixel**. Each 2-bit value is a color index into a **4-color palette**. One tile can then mix a background and three colors: brick and mortar, a snake's body and its highlights, a red apple with a green stem.

The screen geometry does not change from Chapter 2.2: 40x30 cells of 8x8 logical pixels, each logical pixel 2x2 real pixels. The map is still 1,200 bytes at address `row * 40 + col`, and the cell and pixel positions are still bit slices of `x` and `y`. What changes is the second memory:

| | Text mode (2.2) | Tile engine (2.3) |
| --- | --- | --- |
| Entries | 128 glyphs | 64 tiles |
| One row | 8 bits, 1 bit per pixel | 16 bits, 2 bits per pixel |
| Address | `{code[6:0], row}` | `{tile[5:0], row}` |
| Size | 1,024 x 8 bits | 512 x 16 bits (1 KB) |
| Pixel → color | bit ? FG : BG | palette[2-bit index] |

Each map byte holds a tile number in bits 5:0. Values 64 to 255 are reserved for later chapters (palette or flip bits, for example). This chapter's tests only write 0 to 63, so for now you can ignore bits 7:6.

A tile row is one 16-bit word. The leftmost pixel is in bits 15:14 and the rightmost in bits 1:0, so the hex reads left to right, two bits per pixel. Pixel `c` (0 = left) of a row word `w` is

```verilog
wire [1:0] pix = w[{~c, 1'b0} +: 2];   // c = 0 -> bits 15:14, c = 7 -> bits 1:0
```

The palette is four 9-bit `{r, g, b}` parameters, `PAL0` to `PAL3`. Index 0 is the background. A 4-entry `case` on `pix` is the whole palette lookup.

The pipeline is the one you built in 2.2 with a wider ROM. Clock 0 presents the map address, clock 1 brings the tile number and forms the tile address, clock 2 brings the row word, and the pixel is selected and colored. Delay `hsync`, `vsync`, `de` and the pixel column `x[3:1]` by the same two clocks. If you also register the palette output, everything moves to three clocks; the test accepts any latency from 1 to 15 as long as all of it lines up.

### The tile file

This chapter supplies `tiles.hex`, 64 tiles of eight 16-bit words each, one tile per line:

```
0080 0200 3ffc ffdf fff7 ffff 3ffc 0ff0   //  4 0x04 food
```

| Tiles | Contents |
| --- | --- |
| 0-4 | Snake: 0 empty, 1 wall, 2 body, 3 head, 4 food |
| 5-31 | test patterns, so that a wrong tile address shows up in the frame test |
| 32-63 | the font8x8 glyphs (public domain, from Chapter 2.2) for ASCII 0x20-0x3F in color 3, so tile `0x30 + d` is digit `d` |

Edit the art freely. The Python reference reads the same file, so the frame test keeps passing as long as the hardware draws what the file says.

## 2. Dual-port block RAM

In 2.2 nothing wrote the character RAM while the board ran. Now the game writes the map at any time while the video reads it every 16 pixels. Each `SB_RAM40_4K` on the iCE40 has a **read port and a separate write port**, each with its own address, its own clock and, for the write port, its own enable. Both run in the same clock edge. A RAM like this, with one port for writing and one for reading, is sometimes called "simple dual-port" or "pseudo dual-port". You get it by writing the two ports as two separate statements on one array:

```verilog
always @(posedge i_clk) if (i_wr_en) r_map[i_wr_addr] <= i_wr_data;  // the game's port
always @(posedge i_clk) r_tile <= r_map[map_addr];                   // the video's port
```

Three rules keep yosys mapping this to block RAM:

- **One write port and one read port per array.** Each port has one address expression. If you read the same array in two places with different addresses, yosys has to give it a second read port. On the iCE40 that means a second copy of the RAM, or worse, as you will see below.
- **The read is registered**, as in 2.2.
- **Keep the memory statements small and on their own.** Compute the address with a mux outside, then read in one plain statement. Do not hide a memory access in the middle of a large state machine.

What if both ports use the same address on the same clock? The read returns the **old** contents; the new value is there on the next clock. For the video this does not matter: at worst one pixel shows the tile from one frame earlier. It does matter for the game's own memories (see the hazard note below).

**When to write.** A write that lands while the beam is drawing that part of the screen shows half of the change in one frame and the rest in the next, the same tearing you avoided in Chapter 1.3. The fix is the same too: do the game's work at the start of vertical blanking. One Snake step writes a few cells in a few dozen clocks, and vertical blanking lasts 45 lines x 800 clocks = 36,000 clocks, so the screen is never caught half-updated.

## 3. Random numbers from an LFSR

Snake needs the food to appear somewhere the player cannot predict. Hardware cannot roll dice, but a **linear-feedback shift register** comes close at almost no cost. It is a shift register whose input bit is the XOR of a few of its own bits (the **taps**). With the right taps, an n-bit LFSR steps through all 2^n - 1 non-zero values in a fixed, scrambled order before it repeats. That is a **maximal-length** sequence. For 16 bits, one tap set is bits 15, 13, 12 and 10, which comes from the polynomial x^16 + x^14 + x^13 + x^11 + 1:

```verilog
wire fb = r[15] ^ r[13] ^ r[12] ^ r[10];
always @(posedge i_clk) if (i_en) r <= {r[14:0], fb};
```

Three facts to keep in mind:

- **Zero is a trap.** With XOR feedback, all zeros shifts in another zero forever. That is why the cycle has 2^16 - 1 states and not 2^16, and why the register must start from a non-zero `SEED`.
- **It is not random, only scrambled.** The same seed gives the same sequence every time. On the board the randomness comes from *when* you read it. If the LFSR steps every clock and the game samples it at moments that depend on the player, the result is unpredictable enough for a game.
- **Neighbouring values are related.** Each step is the last value shifted by one bit. Taking an x from one group of bits and a y from another is fine for food placement. It would not be good enough for cryptography.

Build `lfsr` as its own small module. `tb_lfsr` checks that it starts at `SEED`, holds while `i_en` is low, and visits all 65,535 non-zero values exactly once per period. Any maximal-length tap set passes. (An XNOR LFSR is also maximal, but its stuck state is all ones and it does pass through zero. The test expects the XOR form.)

**Placing food.** Take a candidate cell from the LFSR, for example x = `rnd[5:0]` and y = `rnd[10:6]`. Reject it if it lies outside the walls (x not in 1..38 or y not in 1..28) or if the cell is not empty, and try again on the next clock with the next LFSR value. About half the candidates are in range, so on an empty board this takes a few clocks. With a long snake it takes more tries, but it stays far inside the time limits below. Two traps:

- **The LFSR must keep stepping while you search.** If it is stopped, the same occupied cell comes back forever and the game hangs. A tempting way to add randomness is to pause the LFSR while a button is held, so the player's timing shifts the sequence. That is fine, but only outside the search: the reference design first paused it whenever a button was down, and `tb_snake_game` timed out on the first move made with a button held during the food search.
- **Read-during-write.** The search reads the game's copy of the map. If you start searching on the clock right after writing the new head cell, the read can still return that cell's old value, "empty", and the food lands on the head. Leave one clock between the last write and the first read, or check the candidate against the head position as well.

## 4. Snake's rules

The playfield is the whole 40x30 map. Row 0, row 29, column 0 and column 39 are wall (tile 1). The snake moves one cell per tick and grows by one cell for each food it eats. The test checks every rule here, so read them as the specification.

**Start.** After power-up and after each restart the game clears the whole map: walls on the border, empty (tile 0) inside. It then draws the snake, `START_LEN` cells long in row `START_Y`, with the head (tile 3) at (`START_X`, `START_Y`) and the body (tile 2) extending to the left. The snake heads right. Finally the game places one food (tile 4) at random on an empty cell. The length is `START_LEN` and the game is not over.

**A tick.** On each `i_tick`:

1. **Direction.** If a direction button is held (`i_up`, `i_down`, `i_left`, `i_right`, at most one at a time), the snake turns that way, unless it is the exact opposite of the current direction; a reversal is ignored. With no button held, the snake goes straight.
2. **Collision.** Look at the cell the head moves into. If it is wall or any part of the snake, the game is over: `o_game_over` goes to 1 and **the map does not change on that tick**. The check is made *before* the tail moves, so turning back into the cell the tail occupies right now also ends the game.
3. **Move.** Otherwise the old head cell becomes body, the new cell becomes head, and:
   - if the new cell had the food, the snake keeps its tail, the length goes up by one, and new food is placed at random on an empty cell;
   - otherwise the tail cell becomes empty and the length stays the same.

While the game is over, ticks do nothing. `i_restart` starts a new game, from game over or at any other time.

**The snake's memory.** The game needs two things the write-only map port cannot give it. To erase the tail it must know where the tail is, and to detect collisions it must know what is in a cell.

- Keep the snake's cells in a **ring buffer**: a RAM of `MAX_LEN` cell addresses with a head pointer and a tail pointer. A move writes the new head at `head + 1`, and a move without food reads the tail cell's address at `tail` and advances `tail`. Give `MAX_LEN` a default of 256: a 256 x 11-bit ring fits in one block RAM. Stop growing at `MAX_LEN`.
- Keep a **copy of the map** for collisions, 2 bits per cell (empty, blocked, food), in the game itself. The tile engine's read port is busy with the video, and this is the usual answer in hardware: when two units both need to read a memory, give each its own copy. 1,200 x 2 bits fits in one block RAM. An easy way to keep the copy in step is to write it from the game's own map write port.

A step is then a small state machine: compute the new head, read the copy, decide, write up to three cells (tail, old head, new head) one per clock, and place food if needed. Hold `o_busy` high from the clock that accepts the tick until the last write is done.

### Designing for test

The game must be testable without depending on how your LFSR works. Three things make that possible:

- **The map write port is an output** (`o_wr_en`, `o_wr_addr`, `o_wr_data`) that the top level connects to the tile engine. The testbench watches the same port, keeps its own copy of the map, and compares it with a model.
- **`i_food_load`** with `i_food_x`, `i_food_y` moves the food. On a one-clock pulse while the game is idle and running, the old food cell becomes empty and the food is drawn at (x, y). The testbench only loads food onto free cells. It uses this port to put the food on the snake's path, so the scripted moves are the same for any design. It still checks where *your* random food went each time: exactly one food tile on the map, on a free cell. One of its games grows the snake to 240 cells, so food placed without checking the map soon lands on the snake and fails.
- **`o_busy`** tells the testbench when a step has finished.

## 5. The board

Make `projects/2.3-snake/` with a `top_goboard.v` that connects:

- `vga_timing` to `tile_engine` (pass `TILE_FILE("../../rtl/common/tiles.hex")`), and `snake_game`'s map write port to `tile_engine`'s;
- the four buttons through `synchronizer`s to `i_up`, `i_down`, `i_left`, `i_right` (Switch 1 to 4);
- `i_tick` to a pulse at the start of vertical blanking (`x == 0 && y == 480`) every 8th frame, about 7.5 moves per second, and only while the game is not over;
- `i_restart` to the same pulse while the game is over and any button is held, so a new game starts at a blanking interval too;
- `i_food_load` to 0;
- the score, `o_length - 3`, to the 7-segment display in hex through two `hex_to_7seg`s.

As before, register `hsync`, `vsync` and the color one more clock before the pins.

On the board, check by hand:

1. The screen shows a brick border, a green snake heading right and one piece of food.
2. Each button turns the snake; pressing the opposite of its direction does nothing.
3. Eating food lengthens the snake by one, the score goes up by one, and new food appears on an empty cell. After a few foods, the positions look random.
4. Running into the wall or the body stops the game; pressing any button starts a new one.
5. Nothing tears or flickers when the snake moves.

## 6. Reading the resource report

For the reference design (the board top described above), yosys 0.52 and nextpnr-ice40 report:

```
Info: Device utilisation:
Info:          ICESTORM_LC:     658/   1280    51%
Info:         ICESTORM_RAM:       7/     16    43%
Info:                SB_IO:      30/    112    26%
Info:                SB_GB:       6/      8    75%
...
Info: Max frequency for clock 'i_Clk$SB_IO_IN_$glb_clk': 101.49 MHz (PASS at 25.00 MHz)
```

yosys's `stat` gives 468 `SB_LUT4`, 152 `SB_CARRY`, 234 flip-flops and 7 `SB_RAM40_4K`. Synthesizing with `-noflatten` splits it up: `tile_engine` is 75 LUTs and 5 block RAMs (3 for the 1,200-byte map, 2 for the 512 x 16 tile ROM), and `snake_game` is 377 LUTs, 97 carries and 2 block RAMs (the ring buffer and the map copy). The tile engine is barely bigger than text mode. The game logic is where the logic cells go.

Check the log for lines like `mapping memory ... r_map via $__ICE40_RAM4K_`, one for each of your four memories. If one is missing, the memory became flip-flops. An early version of this chapter's reference read its map copy from two states with different addresses and wrote it from inside the state machine. yosys built it from about 2,400 flip-flops, and nextpnr gave up at 6,476 logic cells, 505% of the chip. Moving each memory's reads and writes into their own small `always` blocks, with one address mux per port, brought it back to one block RAM.

Seven of sixteen block RAMs are now in use. The sprites in Chapter 2.4 take their graphics from the same 8 KB, and Chapter 2.5 grows the map to 64x32.

## Testbench requirements

Provide in `rtl/common/`:

- **`tile_engine.v`**: `tile_engine #(TILE_FILE = "tiles.hex", MAP_FILE = "", PAL0, PAL1, PAL2, PAL3)` (palette entries 9-bit `{r, g, b}`), with the same ports as Chapter 2.2's `text_mode`: inputs `i_clk`, `i_x[9:0]`, `i_y[9:0]`, `i_de`, `i_hsync`, `i_vsync` (from `vga_timing`), write port `i_wr_en`, `i_wr_addr[10:0]` (cell `row * 40 + col`), `i_wr_data[7:0]` (tile number in bits 5:0); outputs `o_hsync`, `o_vsync`, `o_de` (delayed by your latency, 1 to 15 clocks) and `o_red[2:0]`, `o_grn[2:0]`, `o_blu[2:0]` lined up with them, black while `o_de` is low. Tile ROM loaded from `TILE_FILE`, map optionally from `MAP_FILE`, both reads registered.
- **`lfsr.v`**: `lfsr #(SEED = 16'hACE1)(i_clk, i_en, o_value[15:0])`. `o_value` starts at `SEED`, steps once per clock while `i_en` is high, XOR feedback, maximal length (period 65,535, never 0).
- **`snake_game.v`**: `snake_game #(START_X = 20, START_Y = 15, START_LEN = 3, MAX_LEN = 256, SEED = 16'hACE1)` with inputs `i_clk`, `i_tick`, `i_up`, `i_down`, `i_left`, `i_right`, `i_restart`, `i_food_load`, `i_food_x[5:0]`, `i_food_y[4:0]` and outputs `o_wr_en`, `o_wr_addr[10:0]`, `o_wr_data[7:0]` (map writes, tile codes 0 empty, 1 wall, 2 body, 3 head, 4 food), `o_busy`, `o_game_over`, `o_length[10:0]`. It follows the rules above, and also:
  - `o_busy` starts at 1 and the game draws the first screen by itself. It goes high on the clock edge that accepts `i_tick`, `i_food_load` or `i_restart` (accepted only while `o_busy` is low; ticks and food loads also need the game not to be over) and stays high until the last map write for that command has happened. The map is written only while `o_busy` is high (or on the accepting edge).
  - `o_busy` falls within 5,000 clocks of a tick or food load, and within 20,000 clocks of a restart or power-up.
  - The testbench holds a direction button from 2 clocks before the tick until `o_busy` falls, so the game may sample it at any time in that window.
- **`tiles.hex`**: copy from this chapter.
- `vga_timing.v` from Chapter 1.1. Your `snake_game` uses your `lfsr`.

Copy from this chapter: `tb_lfsr.v`, `tb_snake_game.v` and `tb_frame_tiles.v` to `sim/`, `ref_tiles.py` to `sim/py/`. The frame test also uses `sim/frame_dump.v`, `sim/py/ppm.py` and `sim/py/compare.py` from Chapter 1.2; `ref_tiles.py` reads `rtl/common/tiles.hex`.

Run:

- `make sim-tb_lfsr` and `make sim-tb_snake_game` (add both to your test list so `make test` runs them). The Snake test takes a few seconds. It plays five scripted games: eating, a reversal, biting the body, turning into the tail cell, hitting the wall, and a 240-cell snake.
- `make frame-tiles` (add `FRAME_TESTS += tiles` to the top-level Makefile). It takes about 20 seconds, writes `build/frame_tiles_{all,scene}.ppm` (all 64 tiles, then a Snake screen written during vertical blanking), checks sync alignment and blanking, and compares both frames with `ref_tiles.py` pixel for pixel.

Board project: `projects/2.3-snake/` with `top_goboard.v` as described above.
