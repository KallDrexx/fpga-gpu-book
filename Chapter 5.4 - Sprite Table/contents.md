# Chapter 5.4 - Sprite Table

Chapter 4.8's `BLIT` copies an image into the framebuffer. For a moving object that is expensive: every frame the old picture must be erased (by redrawing what was under it) and the new one blitted, and the host has to remember the background. Twenty moving objects cost thousands of pixel writes per frame.

Consoles use **sprites** instead. A sprite is never written into memory. The video hardware keeps a small table that says "image A at (x, y)" and adds the sprites to the picture as it scans out each line. Nothing has to be erased, because nothing was drawn. Moving a sprite means changing four bytes of the table.

By the end of this chapter you will have:

- a **sprite table** of 64 entries of 8 bytes, with images from Chapter 4.8's asset RAM, 16 x 16 pixels each,
- **double-buffered line buffers**: the next line is assembled while the current one is on screen,
- at most **16 sprites per line**, with an exact rule for which ones are dropped,
- three commands (`0x20` to `0x22`) and capability bit 5 in `GET_INFO`,
- `sim/tb_frame_sprite_table.v`: eight frames compared pixel for pixel with the Python model,
- `host/sprite_demo.py`: 32 bouncing sprites, each moved by a 4-byte rewrite.

## 1. The table

64 entries, numbered 0 to 63, of 8 bytes each. 16-bit fields are little endian, as everywhere in the protocol.

| Byte | Field | Meaning |
| --- | --- | --- |
| 0-1 | `X` | signed 16-bit: screen x of the sprite's left column |
| 2-3 | `Y` | signed 16-bit: screen y of the sprite's top row |
| 4 | `ASSET` | bits 3:0: the asset that holds the image (bits 7:4 ignored) |
| 5 | `PALETTE` | bits 3:0: palette offset (bits 7:4 ignored) |
| 6 | `FLAGS` | bit 0 `FLIP_X`, bit 1 `FLIP_Y`, bit 2 `ENABLE`, bits 4:3 `PRIORITY`, bits 7:5 reserved |
| 7 | | reserved |

Positions are in the 320 x 240 view, like `BLIT` coordinates. They may be negative or past the edges; the sprite is clipped. `PRIORITY` is for Chapter 5.5's compositor: it is stored and has no effect yet. Write it, and all reserved and ignored bits, as 0.

The position comes first on purpose: moving a sprite rewrites bytes 0 to 3 and leaves the image, palette and flags alone. A game moves most of its sprites every frame, so this is the command that sets the host's traffic: 32 moving sprites cost 32 x 7 = 224 bytes per frame.

At power-up the table is all zeros, so `ENABLE` is 0 and no sprite shows.

## 2. The image

A sprite is always **16 x 16 pixels**: the first 256 bytes of its asset, row by row, pixel (`px`, `py`) at byte `py * 16 + px`. Upload it with Chapter 4.8's `UPLOAD_ASSET id, 16, 16, ...`.

A fixed size keeps the hardware simple: the row in the sprite is one subtraction (`line - Y`, 0 to 15), the address is a shift, and every sprite costs the same time (section 6). Larger objects are built from several sprites. The price is one rule: a sprite whose asset does not exist or is not exactly 16 x 16 is **not drawn**. That is not an error, and the sprite still takes one of the 16 slots on its lines (section 5).

**Palette.** A non-zero image byte `i` is drawn as palette entry `(i + 16 * PALETTE) mod 256`. If the art uses indices 1 to 15 and the palette holds 15-color ramps at 16, 32, 48, ..., one image can be shown in 16 color schemes.

**Transparency.** An image byte of 0 is transparent, as for `BLIT`. The test is on the image byte, before the offset is added. A non-zero byte whose sum wraps to exactly 0 is drawn, opaque, in palette entry 0. This corner case is why a line buffer entry needs a ninth bit (section 4).

**Flips.** `FLIP_X` draws image column `15 - px` at column `px`; `FLIP_Y` does the same for rows.

## 3. The commands

`protocol.md` next to this chapter adds the three rows to section 3, capability bit 5, and a new section 11 on sprites. Replace your `docs/protocol.md` with it.

| Op | Name | len | Payload |
| --- | --- | --- | --- |
| `20` | `SET_SPRITE` | 9 | id, then the 8 bytes of the entry |
| `21` | `MOVE_SPRITE` | 5 | id, x (16-bit), y (16-bit): table bytes 0 to 3 only |
| `22` | `SPRITE_CTRL` | 1 | bit 0: 1 shows the sprites, 0 hides them all; bits 7:1 ignored |

- **Ids** of 64 and more: the command does nothing and sets no flag (the length was right; the same rule as a tile layer above 1).
- **Lengths** are exact. Any other length is skipped whole and sets `BAD_LEN`. `0x23` to `0x2F` stay unknown opcodes (`BAD_OP`).
- **Capability bit 5** (`SPRITES`): `gpu_link` now answers `GET_INFO` with `47 01 3F 02 40 01 F0 00 ER`. `uart_gpu` keeps `0F 00`.

There is no bulk command: a full table is 64 `SET_SPRITE` packets of 11 bytes, 704 bytes, sent once.

**When a write takes effect.** If table writes went straight to the table the video reads, a write in the middle of a frame would affect only the lines still to come: a sprite moved from y = 100 to y = 200 while the beam is at line 150 would show in both places, or in neither, or in two halves. The host cannot avoid this, because it does not know where the beam is when its packet runs. So, as for the scroll registers of Chapter 5.3, the commands write a **shadow** table (and a shadow global enable), and at the start of vertical blanking the shadow is copied to the **live** table that the video reads. Every frame is drawn from one table. Several writes to one entry in a frame: the last one counts.

The copy is not instant. The table is 4 Kbit, so it is copied one entry per clock: 64 `clk_sys` clocks, about 1.3 microseconds after `o_vblank` rises. A sprite command that executes during those 1.3 microseconds may land in either frame, and a `SET_SPRITE` or `MOVE_SPRITE` whose bytes the copy cuts through can show, for that one frame, with some bytes old and some new (the next frame is right). Making the copy wait for the command engine would need a handshake between the two; instead the protocol tells hosts not to have sprite commands executing in the first 5 microseconds of blanking. Send a frame's updates during the visible part of the previous frame, as the test does, or at least 5 microseconds after `VSYNC` rises, as the demo does.

`UPLOAD_ASSET` is **not** buffered. Replacing an image while a sprite uses it shows the new image from the next line built. Upload while no enabled sprite uses the asset, or accept the tear.

## 4. Line buffers

The video needs a pixel every 40 ns, which is far too little time to search 64 table entries. So each line is prepared in advance in a **line buffer**: a small memory with one entry per logical pixel (320) holding the sprite pixels of that line. While the beam reads one line buffer, the hardware fills the other with the next line; then they swap. This is double buffering again, one line at a time.

The 320 x 240 view is shown at 2 x 2, so logical line `L` occupies display lines 2L and 2L + 1. A line buffer is therefore read twice, and the next one has **two display lines** (64 microseconds) to be built:

| Display lines | The beam shows | Being built |
| --- | --- | --- |
| 0-1 | line 0 (buffer 0) | line 1, into buffer 1 |
| 2-3 | line 1 (buffer 1) | line 2, into buffer 0 |
| ... | | |
| 476-477 | line 238 (buffer 0) | line 239, into buffer 1 |
| 478-479 | line 239 (buffer 1) | nothing |
| 480-522 | blanking | nothing |
| 523-524 | blanking | line 0, into buffer 0 |

Line `L` lives in buffer `L mod 2`, so the buffer the beam reads is `i_y[1]`. Lines are built from the live table. The copy at line 480 is long finished when line 0 is built in lines 523-524, so the whole new frame sees the new table.

An entry is **9 bits**, `{opaque, index}`; `opaque` is 0 where no sprite drew. Both buffers fit in one 1,024 x 9 memory (two halves of 512), written on `clk_sys` and read on `clk_pix` like the framebuffer: one 18 Kbit block RAM.

## 5. Building one line

The **assembler** runs on `clk_sys` (50 MHz). When the beam reaches a build slot of the table above, it:

1. **Clears** the 320 entries of the target buffer (it still holds the line before last).
2. **Selects.** Reads the 64 live entries in order. Entry `n` is *on line `L`* if it is enabled and `Y <= L < Y + 16` (`Y` signed). It stores the index of each one found, and stops storing after 16.
3. **Draws** the selected sprites from the **last found to the first**. For each, the image row is `ry = L - Y` (`15 - ry` with `FLIP_Y`); for each of its 16 columns it reads the image byte and, if it is not 0 and the column `X + px` is 0 to 319, writes `{1, byte + 16 * PALETTE}` there. A sprite whose asset cannot be a sprite is skipped here.

Drawing the lowest index last puts it **in front**, and overlap needs no extra logic: later writes overwrite earlier ones. This is Chapter 2.4's rule, sprite 0 in front.

### The 16-per-line limit

> Of the sprites on a line, the 16 with the **lowest table index** are drawn. The others are **dropped for that line**.

- The decision is made line by line. A sprite that is 17th on some of its lines may be 16th on others, so a crowded line shows a sprite cut off: some rows drawn, others missing.
- Only **enabled** sprites are on a line. A disabled one takes no slot.
- **X and the asset do not matter for counting.** A sprite completely off the screen sideways, or one whose asset cannot be drawn, still takes a slot, because the selection looks only at `ENABLE` and `Y`. Park unused sprites with `ENABLE` clear or `Y` off the screen.
- It is a table-order rule, not a screen-order one: keep the player's ship at a low index and it never disappears.

There is no overflow flag in the protocol.

## 6. Time budget

A build slot is two display lines: 1,600 `clk_pix` clocks, 3,200 `clk_sys` clocks. The worst case is 16 drawable sprites on the line, at one pixel per clock with the steps one after another:

| Step | `clk_sys` clocks |
| --- | --- |
| Clear 320 entries | 320 |
| Select: 64 entries, plus 2 for the read latency | 66 |
| Draw 16 sprites: per sprite 1 to fetch the entry, 1 to latch it, 16 pixels, 1 to move on | 16 x 19 = 304 |
| Drain the write pipeline | about 5 |
| **Total** | **about 700** |

That is 22% of the slot. So 16 is a choice, not a limit the clock forces: even 64 sprites on a line would take 320 + 66 + 64 x 19, about 1,600 clocks. A small limit keeps the selection list small and the build short, and it is the kind of rule (8 or 16 per line) the consoles that invented sprites had to live with. Keep it in a `localparam` and change it to see what the tests say. The design would even fit on `clk_pix`; it runs on `clk_sys` because the asset RAM and the table writes are already there, so only the line buffer crosses clock domains.

## 7. The command engine

All three commands write the shadow side, which lives in the sprite unit (section 8). New ports on `cmd_engine` and `gpu_link`, all on `clk_sys`:

| Group | Ports |
| --- | --- |
| table byte write | outputs `o_sp_we`, `o_sp_addr[8:0]` (`id * 8 + byte`), `o_sp_wdata[7:0]` |
| global enable | outputs `o_spc_we`, `o_spc_en` |
| asset RAM, second read port | input `i_ar_addr[14:0]`, output `o_ar_data[7:0]`, 1-clock read |
| asset lookup | input `i_as_id[3:0]`, outputs `o_as_base[14:0]`, `o_as_ok`; combinational |

`o_as_base` is the asset's first byte in the asset RAM (the `t_base` that `BLIT` already uses), and `o_as_ok` is 1 if the asset exists and is 16 x 16. The lookup sits in the engine because the engine owns the asset table. Add `0x20`, `0x21`, `0x22` to the `known`/`len_ok` table (lengths 9, 5, 1) and set `CAPS` to `16'h023F` in `gpu_link`. In `uart_gpu`, leave the new outputs open and tie `i_ar_addr` and `i_as_id` to 0.

**The table writes.** `SET_SPRITE`'s 9 payload bytes fit in `arg[]`, so the packet arrives through the ordinary path and the execute state sees it whole. One 8-bit port writes one byte per clock, so add a state that writes `spn` bytes (8 for `SET_SPRITE`, 4 for `MOVE_SPRITE`) on consecutive clocks:

```verilog
S_SPW: begin
    o_sp_we <= 1'b1; o_sp_addr <= {arg[0][5:0], spj[2:0]}; o_sp_wdata <= arg[spj + 4'd1];
    spj <= spj + 4'd1;
    if (spj + 4'd1 == spn) st <= S_FETCH;
end
```

`MOVE_SPRITE` thus copies payload bytes 1 to 4 (x, y) to table bytes 0 to 3. `SPRITE_CTRL` needs no state: one strobe of `o_spc_we` with `o_spc_en = arg[0][0]`. Default both strobes to 0 at the top of the clocked block, like the others. The state ends after at most 8 clocks, so the protocol's "every state ends" rule holds.

**The second read port.** The blitter reads the asset RAM with `a_rdata <= amem[a_addr]`. Add an independent registered read, `s_rdata <= amem[i_ar_addr]`, and drive `o_ar_data` from it. The RAM now has one write port and two read ports on one clock. yosys still maps it onto the same 8 RAMB36E1: one port of each block serves the writes and the blitter's reads (which never happen at the same time), the other serves the sprites.

## 8. The sprite unit

Put the sprite hardware in one module, `sprite_lines`, inside `fb_scanout` next to `tile_layers`:

| Port | Direction | Meaning |
| --- | --- | --- |
| `i_clk_sys` | in | engine clock: everything except the line buffer read |
| `i_wr_we`, `i_wr_addr[8:0]`, `i_wr_data[7:0]` | in | shadow table byte writes |
| `i_en_we`, `i_en` | in | shadow global enable |
| `o_ar_addr[14:0]`, `i_ar_data[7:0]` | out, in | asset RAM read port |
| `o_as_id[3:0]`, `i_as_base[14:0]`, `i_as_ok` | out, in, in | asset lookup |
| `i_clk_pix`, `i_x`, `i_y`, `i_vblank` | in | beam, and `fb_scanout`'s `o_vblank` |
| `o_pix[8:0]` | out | `{opaque, index}` for the pixel, 1 clock after `i_x` |

`fb_scanout` brings the write, enable and asset signals out as ports, to be wired to `gpu_link` one to one in the top level and the testbench:

```
i_sp_we, i_sp_addr[8:0], i_sp_wdata[7:0], i_spc_we, i_spc_en,      (clk_sys)
o_ar_addr[14:0], i_ar_data[7:0], o_as_id[3:0], i_as_base[14:0], i_as_ok
```

**The tables.** The shadow is 64 entries of 64 bits written a byte at a time (byte `addr[2:0]` of entry `addr[8:3]`), for example eight 64 x 8 memories with their own write enables. The live table is 64 x 64, so the assembler reads a whole entry per clock. On the rising edge of `i_vblank` synchronized into `clk_sys` (the same event as the tile layers' latch), a counter copies shadow entry `i` to live entry `i` for `i` = 0 to 63 (allow one clock of read latency), and the global enable is copied.

**Starting a build.** The assembler is on `clk_sys`, the beam on `clk_pix`. Two things cross: "start now" and the line number. On `clk_pix`, on each display line that starts a slot (`i_y` = 0, 2, ..., 476 build lines 1 to 239; `i_y` = 523 builds line 0), store the target line in a register at, say, `i_x == 5`, and **toggle** a flip-flop at `i_x == 10`. On `clk_sys`, pass the toggle through the two-flop `synchronizer`; a change (`tick ^ tick_delayed`) is the start. A toggle, unlike a one-clock pulse, crosses correctly whatever the clock ratio. The line number was written 5 `clk_pix` clocks before the toggle and holds for 1,600, so it is stable when `clk_sys` reads it. The build takes about 700 `clk_sys` clocks (350 `clk_pix`), so the start can be anywhere in the first display line of the slot.

**The assembler** (about 100 lines in the reference) is a state machine: idle, clear, select, then per sprite fetch, latch, 16 pixels, next, and a final drain. Points to get right:

- The selection test on a live entry `lv` (`Y` is bits 31:16, `ENABLE` bit 50) needs enough bits. `line` is 0 to 239 and `Y` anything from -32,768 to 32,767:

```verilog
wire signed [15:0] ey = lv[31:16];
wire signed [17:0] dy = $signed({10'd0, line}) - $signed({{2{ey[15]}}, ey});
wire hit = lv[50] && dy >= 0 && dy < 16;
```

- Selection reads one entry per clock and the data arrives a clock later, so carry the index of the entry being tested alongside it.
- Pixel address: `base + ry * 16 + (FLIP_X ? 15 - px : px)`. For a 4-bit `px`, `15 - px` is `~px`, and `ry` is `dy[3:0]` or `~dy[3:0]`. The screen column `X + px` needs 17 signed bits.
- The image byte arrives two clocks after the address leaves (one register out, one in the RAM), so carry the column and palette through two pipeline registers and write the line buffer when the byte arrives (column 0 to 319, byte not 0). The pipeline runs one pixel per clock with no handshake; writes happen in drawing order, so "last write wins" is right.
- The line buffer read on `clk_pix`: `o_pix <= en ? lb[{i_y[1], i_x[9:1]}] : 9'd0`. Gating with the live global enable hides the uninitialized buffer at power-up. The live enable changes only at the start of blanking, so this side needs no synchronizer.

**Mixing.** The sprites go in front of everything for now: framebuffer, layer 0, layer 1, sprites. In `fb_scanout` the choice of palette index gets one more level:

```verilog
wire [7:0] idx = sp[8] ? sp[7:0] : ((ovl != 8'd0) ? ovl : fb_idx_delayed);
```

`sp` must be the sprite value of the same pixel as the other two. `o_pix` is registered once after `i_x`, like the framebuffer read, so pass it through the same delay chain as the framebuffer index. The total latency does not change. Chapter 5.5 replaces this line with a compositor.

## 9. The Python model

`host/gpu_model.py` (a backward-compatible replacement) now models the sprites:

```python
import gpu_model as g
g.set_sprite(id, x, y, asset, palette=0, flags=g.SPRITE_ON)   # SET_SPRITE packet
g.move_sprite(id, x, y)                                         # MOVE_SPRITE
g.sprite_ctrl(True)                                             # SPRITE_CTRL
g.sprite_entry(x, y, asset, palette, flags)                     # the 8 table bytes
gpu = g.Gpu()
gpu.vblank()            # shadow table and enable -> live, as at the start of blanking
gpu.sprite_lines(line)  # the table indexes drawn on a line, lowest first
gpu.screen()            # one frame: framebuffer, layers 0 and 1, then the sprites
```

`sprite_lines()` is the selection rule in a few lines of Python; read it. `screen()` draws each line's sprites back to front. The default capability word is now `0x023F`. The self-check still passes: `.venv/bin/python host/gpu_model.py`.

## 10. Simulate

The testbench is shaped like Chapter 5.3's: `vga_timing`, `gpu_link` and `fb_scanout` wired as in `top_artyz7.v`, now with the sprite ports, and an SPI master playing a command stream. Copy `ref_sprite_table.py` to `sim/py/` (it imports Chapter 5.3's `ref_layers.py` for the palette, tiles and maps) and `tb_frame_sprite_table.v` to `sim/`, write the stream once (commit the two `sim/sprite_table_*.hex` files), add `FRAME_TESTS += sprite_table` and run:

```sh
.venv/bin/python sim/py/ref_sprite_table.py --stream sim
make frame-sprite_table
```

(The name is `sprite_table` because Chapter 2.4 already has `ref_sprites.py` and `frame-sprites`.)

**Setup.** Palette, a framebuffer picture, two sparse tile layers, four 16 x 16 images, an 8 x 8 image (asset 4, which cannot be a sprite) and scene A in the table. Asset 5 is never uploaded. Image 3 uses indices 232 to 255, so palette offsets wrap it past 255, sometimes to exactly 0.

**Eight consecutive frames**, `f0` to `f7`. As in Chapter 5.3, the changes for frame `fN` are sent at line 100 of the frame before. Some steps also send commands at line 500, in the blanking **after** the latch; those must show one frame later.

| Frame | What happens | Tests |
| --- | --- | --- |
| f0 | scene A: 28 entries | sprites on all four corners and edges, some with one row or column visible (x = 319, x = -15, y = 239), some completely off; `FLIP_X`, `FLIP_Y`, both; four overlapping; palettes 1, 3 and 15 with wraparound; the 8 x 8 asset and the missing one (not drawn); one entry without `ENABLE` |
| f1 | 11 `MOVE_SPRITE` and 2 `SET_SPRITE` at line 100; one `MOVE_SPRITE` at line 500 | moves to the edges and back from off-screen; a sprite moved twice (last counts); moves keep flips and palette; ignored bits 7:4 of `ASSET` and `PALETTE`; writes during the visible part do not tear the frame in progress; the line-500 move does **not** show yet |
| f2 | nothing sent | the line-500 move shows now |
| f3 | all 64 entries rewritten (`SET_SPRITE` x 64) | the line limit, below |
| f4 | `SPRITE_CTRL` with bit 0 clear and bits 7:1 set; `SPRITE_CTRL 1` at line 500 | no sprites, tile layers intact |
| f5 | layer scrolls and 20 `MOVE_SPRITE` | sprites back (the line-500 enable); sprites do not scroll with the layers |
| f6 | 32 `MOVE_SPRITE` to random places | the demo's traffic; clipping at random |
| f7 | ids 64, 200 and 255; `SPRITE_CTRL 0xFF` | out-of-range ids change nothing and set no flag; bits 7:1 ignored |

Scene B (f3) exercises the limit:

- **Entries 0 to 19** in one row at y = 40. Entry 7 is disabled (no slot). Entry 3 is at x = -100 and entry 11 names the missing asset 5: both take a slot and draw nothing. So 19 sprites are on the line, entries 0 to 16 except 7 get the 16 slots (entry 16, at x = 256, is drawn), and 17 to 19 are dropped.
- **Entries 20 to 36**: 17 sprites at y = 100, 10 pixels apart so they overlap. The 17th is dropped, and the lower index is in front.
- **Entries 37 to 56**: a staircase, each sprite one pixel lower than the one before, with 16 or more on many lines.
- **Entries 57 to 63** at y = 160, inside the staircase: on lines 160 to 164 a different number of them fits on each line, so they are cut off at different rows.

The testbench writes `build/frame_sprite_table_f0.ppm` to `f7.ppm`, sends `GET_INFO` at the end and expects `47 01 3F 02 40 01 F0 00 00` (the last byte proves the ignored ids set no flag), and checks the latency and sync alignment as before. About 6 minutes.

**The testbench does not look at the pixels.** `make frame-sprite_table` runs `compare.py` afterwards, and only that step catches a wrong picture: a broken design prints `PASS: tb_frame_sprite_table` and then `FAIL: compare sprite_table`.

**When it fails**, look at the first frame and pixel `compare.py` reports:

- Row at y = 40 in f3 wrong (a 17th sprite, or entry 16 missing): the limit, or a disabled, off-screen or undrawable entry counted wrongly.
- Overlaps in the wrong order: the draw loop runs the wrong way.
- Old sprite pixels left behind, a trail under moving sprites: the line buffer is not cleared.
- Mirror images wrong, or a flipped sprite 1 pixel off: `FLIP_X`/`FLIP_Y`, or `15 - px` written as `16 - px`.
- Boxes around sprites: transparency ignored. Single wrong pixels in image 3: transparency tested on the sum instead of the byte.
- Sprites at the left or top edge missing or wrapped: the clipping, or `Y` or `X` treated as unsigned or too narrow.
- Wrong colors: the offset is `16 * PALETTE` and wraps; `PALETTE` and `ASSET` use bits 3:0 only.
- f1 shows the line-500 move, or the lower part of f0 already shows a line-100 move: the shadow latch.
- f1 sprites change flips or images: `MOVE_SPRITE` writes more than 4 bytes.
- f4 shows sprites: `SPRITE_CTRL` reads more than bit 0.

**The other testbenches.** Everything from Chapter 5.3 and before still passes. Older testbenches leave the new ports open: `fb_scanout` in `tb_frame_scanout`, `tb_frame_flip`, `tb_flip_sync` and `tb_frame_layers`; `gpu_link` in `tb_gpu_spi`, `tb_link_arb` and `tb_frame_layers`. Icarus warns about the dangling inputs; with the global enable at 0 the sprites stay off. (If your sprite unit lets a floating input reach a register, for example `en <= i_spc_we ? i_spc_en : en`, the pictures fill with X: tie that test's new inputs to 0.) Two files of Chapter 5.3 change because the capability byte goes from `1F` to `3F`:

- **`tb_frame_layers`**: in `sim/py/ref_layers.py`, change `0x1F` to `0x3F` in the `GET_INFO` assertion in `main()`, then rerun `.venv/bin/python sim/py/ref_layers.py --stream sim` (it rewrites `sim/layers_replies.hex`). Change the `1F` in `tb_frame_layers.v`'s failure message too.
- **`tb_gpu_fuzz`**: this chapter ships replacements for `tb_gpu_fuzz.v` (capability byte `3F`; `i_ar_addr` and `i_as_id` tied to 0) and `ref_fuzz.py` (`0x20` to `0x22` are known opcodes; wrong-length and out-of-range-id rounds for them). The fuzz test cannot see the table, but it checks that the parser consumes the packets and sets the right flags. Regenerate with `.venv/bin/python sim/py/ref_fuzz.py sim` and commit the new `sim/fuzz_*` files.

## 11. On the board

In `top_artyz7.v`, wire the sprite ports of `gpu_link` to those of `fb_scanout`:

```verilog
// gpu_link
.o_sp_we(sp_we), .o_sp_addr(sp_addr), .o_sp_wdata(sp_wdata), .o_spc_we(spc_we), .o_spc_en(spc_en),
.i_ar_addr(ar_addr), .o_ar_data(ar_data), .i_as_id(as_id), .o_as_base(as_base), .o_as_ok(as_ok)
// fb_scanout
.i_sp_we(sp_we), .i_sp_addr(sp_addr), .i_sp_wdata(sp_wdata), .i_spc_we(spc_we), .i_spc_en(spc_en),
.o_ar_addr(ar_addr), .i_ar_data(ar_data), .o_as_id(as_id), .i_as_base(as_base), .i_as_ok(as_ok)
```

The reference build (openXC7):

| Resource | Used | Of |
| --- | --- | --- |
| LUTs | about 6,900 | 106,400 |
| Flip-flops | about 2,200 | 106,400 |
| RAMB36E1 | 54 | 140 |
| RAMB18E1 | 2 | 280 |
| DSP48E1 | 20 | 220 |

The sprite unit adds one RAMB18E1, the line buffer (the other one was already there in Chapter 5.3). The shadow table, the live table and the selection list are small enough that yosys puts them in distributed (LUT) RAM. The RAMB36E1 count stays at 54: the asset RAM's second read port uses the blocks' second port and costs nothing. `clk_sys` closes at about 58 to 62 MHz against 50 required, `clk_pix` above 110 MHz against 25.

First try the demo without the board: `.venv/bin/python host/sprite_demo.py --model` runs the host code against the Python model and prints `PASS: sprite_demo (model)`; add `--ppm sprites.ppm` to see a frame. Then, with Chapter 5.1's wiring (FT232H on Pmod JB):

1. Program the bitstream. HDMI shows what it did before: sprites are off at power-up.
2. `.venv/bin/python host/spi_info.py` lists `SPRITES` (`caps 023F`).
3. `.venv/bin/python host/sprite_demo.py --freq 5000000`. After about a second of uploading, 32 balls and rings in four colors bounce around, sliding in and out over the edges with no trail and no flicker. Each frame of moves is 224 bytes, about 0.4 ms of SPI at 5 MHz.
4. At the end the script prints the frame count and the error flags, which should be clear.
5. Experiments: set `FLIP_X` on every second sprite; stack 17 sprites on one line and watch the 17th vanish; make table writes immediate (bypass the shadow) and watch moving sprites shear at the beam line. Put it back afterwards.

## Testbench requirements

**Modules.**

- **`gpu_link`**: Chapter 5.3's ports plus outputs `o_sp_we`, `o_sp_addr[8:0]` (entry * 8 + byte), `o_sp_wdata[7:0]`, `o_spc_we`, `o_spc_en` (one-clock strobes on `clk_sys` with the data in the same clock); input `i_ar_addr[14:0]` with output `o_ar_data[7:0]` (the asset RAM byte, one clock after the address); input `i_as_id[3:0]` with combinational outputs `o_as_base[14:0]` (the asset's first byte) and `o_as_ok` (the asset exists and is 16 x 16). `GET_INFO` replies `47 01 3F 02 40 01 F0 00 ER`.
- **Commands** `0x20` to `0x22` as in `protocol.md` sections 3 and 11: exact lengths (others skipped whole, `BAD_LEN`); ids of 64 and more ignored without a flag; `MOVE_SPRITE` changes table bytes 0 to 3 only; `SPRITE_CTRL` reads bit 0 only. `0x23` to `0x2F` stay unknown.
- **`fb_scanout`**: Chapter 5.3's ports plus `i_sp_we`, `i_sp_addr[8:0]`, `i_sp_wdata[7:0]`, `i_spc_we`, `i_spc_en`, `o_ar_addr[14:0]`, `i_ar_data[7:0]`, `o_as_id[3:0]`, `i_as_base[14:0]`, `i_as_ok`, connected one to one to `gpu_link`'s. Any total latency from 1 to 24 clocks, with `o_hsync`, `o_vsync`, `o_de` delayed equally. How the sprite unit is built inside is free.
- **Video rules** (`protocol.md` section 11): 64 entries; 16 x 16 images from 16 x 16 assets; color `(i + 16 * PALETTE) mod 256`, image byte 0 transparent; flips; clipping on all four sides; a sprite is on a line if enabled and `Y <= L < Y + 16`, the 16 lowest indices on a line are drawn, the lowest in front; order framebuffer, layer 0, layer 1, sprites. The table and global enable are copied to the live ones at the start of vertical blanking (the test accepts any copy that is complete after line 480 and before line 500); tile and asset writes are immediate. Power-up: no sprites.
- **Python**: this chapter's `host/gpu_model.py` (with `raster_model.py`) and `host/sprite_demo.py`; `ref_sprite_table.py` imports `ref_layers.py`.

**`tb_frame_sprite_table`** (about 6 minutes, `make frame-sprite_table` from the repo root). Reads `sim/sprite_table_stream.hex` and `sim/sprite_table_replies.hex`, plays the stream over SPI at 5 MHz (checking `READY` before each frame), dumps eight consecutive 640 x 480 frames at 8 bits per channel, and prints `PASS: tb_frame_sprite_table (...)`. It fails on a frame-dump geometry or X/Z error, unequal sync/`de` delay, a status byte without the `01` signature, a FIFO overflow, a wrong `GET_INFO` reply (including any error flag), or a group of commands that missed the part of the frame it was meant for. It does **not** check pixels; the `compare.py` step that follows does.

**`tb_gpu_fuzz`**: Chapter 5.3's test with this chapter's `tb_gpu_fuzz.v` and `ref_fuzz.py`. **`tb_frame_layers`** needs the capability edit of section 10.

**Files:** `protocol.md` to `docs/`; `tb_frame_sprite_table.v` and `tb_gpu_fuzz.v` to `sim/`; `ref_sprite_table.py` and `ref_fuzz.py` to `sim/py/`; `gpu_model.py` and `sprite_demo.py` to `host/`. Then:

```sh
.venv/bin/python sim/py/ref_layers.py --stream sim      # after the 0x3F edit
.venv/bin/python sim/py/ref_sprite_table.py --stream sim
.venv/bin/python sim/py/ref_fuzz.py sim
make frame-sprite_table
```
