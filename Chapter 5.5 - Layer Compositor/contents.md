# Chapter 5.5 - Layer Compositor

The picture now has four sources: the framebuffer, tile layer 0, tile layer 1 and the sprites. Chapters 5.3 and 5.4 stacked them in an order fixed in the hardware: framebuffer at the back, then layer 0, layer 1, and the sprites in front. That is fine for a demo and too rigid for a game:

- A player walking through a forest should pass **behind** the treetops and **in front of** the grass, and both live in the same tile layer. One order per layer cannot express that.
- A status bar drawn into the framebuffer should be in front of everything. But the framebuffer is at the back, and it has no transparent index, so moved to the front it would cover the whole screen.
- The tile layers and the sprites can be switched off, but the framebuffer cannot.

This chapter replaces the fixed order with a **compositor**: a small unit that looks at the four sources for one pixel and decides which one shows. The hardware is a few comparators and a multiplexer. The hard part is choosing the rule, because every later chapter's pictures depend on it.

By the end of this chapter you will have:

- a per-pixel **priority rule** for the four sources, with index 0 transparent in every one of them (the framebuffer too) and a **backdrop** colour behind everything,
- **priority bits** in the tile map entries (bits 11:10) and the sprite flags (bits 4:3), the fields Chapters 5.3 and 5.4 reserved,
- three commands (`0x30` to `0x32`) and capability bit 6 in `GET_INFO`,
- `sim/tb_frame_compositor.v`: eight frames compared pixel for pixel with the Python model,
- `host/compositor_demo.py`: all four sources on screen, switched on and off live while the player walks behind the trees.

## 1. The rule

For every pixel of the 320 x 240 view, each source offers a palette index:

| Source | Short name | Its index |
| --- | --- | --- |
| framebuffer (the front buffer) | `FB` | the pixel's byte |
| tile layer 0 | `L0` | the tile pixel |
| tile layer 1 | `L1` | the tile pixel |
| sprites | `SP` | the sprite pixel of Chapter 5.4's line buffer |

**Opaque.** A source is *opaque* at a pixel if it is enabled and its index is not 0. This now holds for the framebuffer too. For the sprites, use the `opaque` bit Chapter 5.4 already stores in the line buffer: a pixel a sprite drew is opaque even if its palette offset wrapped its index to 0.

**The key.** Each opaque pixel gets a **key** of two numbers, each 0 to 3:

1. the **layer priority** of its source, one number per source, set by `SET_PRIORITY`;
2. the **item priority** of the thing that drew it: the tile under the pixel (map entry bits 11:10) or the sprite that drew it (flags bits 4:3). The framebuffer's is always 0.

The pixel shows the opaque source with the **largest key**: compare layer priorities first, and item priorities only if the layer priorities are equal. If two keys are equal, the **later source in the order `FB`, `L0`, `L1`, `SP`** wins. If no source is opaque, the pixel shows palette entry `BACKDROP`.

**Power-up.** The layer priorities are `FB` 0, `L0` 1, `L1` 2, `SP` 3, the backdrop is entry 0 and the framebuffer is enabled. The four layer priorities differ, so the item priorities never matter, and the order is exactly that of Chapters 5.3 and 5.4. The one change, the framebuffer's transparent index 0, does not show either: where nothing else is opaque, it reveals the backdrop, palette entry 0, which is the colour the framebuffer's index 0 showed before. Only when the host sets another backdrop does the framebuffer's index 0 look different.

**What the item priority is for.** Give two sources the *same* layer priority, and the item priorities interleave them pixel by pixel. The forest: give `L1` and `SP` the same layer priority, give the tree tiles item priority 1, and leave the grass tiles and the player at 0. On a tree pixel the tile (1) beats the player (0). On a grass pixel the two tie at 0, and the tie goes to the later source, the sprites. One layer, two behaviours.

**What it does not do.** There is no blending: each pixel is exactly one source's colour. And sprites among themselves still follow Chapter 5.4: the lowest table index is in front **whatever their priority fields**, because the line buffer keeps only the front sprite's pixel. The compositor then uses that sprite's item priority against the other sources.

### Why this rule

Consoles have used three other rules:

- **Layer priorities only.** The simplest, and enough for a framebuffer with a HUD, but it cannot do the forest: a layer is one thing.
- **Item priority first, layer priority second.** Then a high-priority tile beats a sprite whatever the layers say. The price: nothing can be put in front of such a tile, not even a HUD, and the power-up order would no longer be Chapter 5.4's for maps or sprites with non-zero priority bits.
- **A rank table**, as on the SNES: a table gives a rank to every (source, priority bit) pair. Fully general, but eight or more registers to set and explain.

Layer first, item second sits in between. The layer priorities give a global order: a HUD in the framebuffer at layer priority 3, with everything else below 3, is always in front. The item bits refine the order locally, only between sources that share a layer priority, and are harmless otherwise. The cost: to interleave tree tiles with sprites, both must share one layer-priority level. This is also what the plan asked for: each layer has an enable and a priority.

## 2. The commands

`protocol.md` next to this chapter adds three rows to section 3, capability bit 6, the priority fields to sections 10 and 11, and a new section 12. Replace your `docs/protocol.md` with it.

| Op | Name | len | Payload |
| --- | --- | --- | --- |
| `30` | `SET_PRIORITY` | 1 | `{SP[1:0], L1[1:0], L0[1:0], FB[1:0]}`: the layer priority of each source. Default `E4` |
| `31` | `SET_BACKDROP` | 1 | the palette index shown where no source is opaque. Default 0 |
| `32` | `FB_ENABLE` | 1 | bit 0: 1 shows the framebuffer, 0 makes it transparent everywhere; bits 7:1 ignored. Default 1 |

- Lengths are exact: any other length is skipped whole and sets `BAD_LEN`. Every byte value is legal, so a well-formed packet never sets a flag. `0x33` to `0x3F` stay unknown (`BAD_OP`).
- **Capability bit 6** (`COMPOSITOR`): `gpu_link` now answers `GET_INFO` with `47 01 7F 02 40 01 F0 00 ER`. `uart_gpu` keeps `0F 00`.
- There are three separate **enables**: `SET_TILE_LAYER` (5.3), `SPRITE_CTRL` (5.4) and `FB_ENABLE`. Each decides only whether its source can be opaque.

**When they take effect.** Like the scroll registers and the sprite table, the three commands write **shadow** registers, copied to the **live** registers at the start of vertical blanking. A change that took effect half way down a frame would show the old order above that line and the new one below it. With shadows, everything the host sends during one frame lands together at the next blanking, which is what makes switching sources live free of glitches. The item priority bits are part of the data they sit in: a tile's is written into the map at once (Chapter 5.3's rules), a sprite's travels with the shadow sprite table.

## 3. The hardware

### 3.1 Where the mix sits

Each source already has a pipeline that ends in one palette index per pixel. The compositor becomes the last stage before the palette, replacing Chapter 5.4's priority multiplexer:

```
framebuffer RAM --> index -----(delay)--+
tile_layers     --> 2 x (index, item) --+--> compositor --> palette --> RGB
sprite_lines    --> {opaque, item, idx} +       ^
                      (delay)            live priorities, backdrop, FB enable
```

What arrives at the compositor in one clock must belong to one pixel. Chapter 5.4 already delays the framebuffer index and the sprite pixel to meet the tile output; that alignment stays. Two units must now hand over more than one merged index:

- **`tile_layers`** gives each layer's index separately (0 where that layer is disabled), plus the item priority of each layer's tile, taken from the same map entry as the tile number.
- **`sprite_lines`** stores the drawing sprite's item priority in the line buffer next to the index, so it comes out with the pixel.

Section 4 lists the changes unit by unit.

### 3.2 The compositor module

```
module compositor (i_clk_sys, i_cx_we, i_cx_sel[1:0], i_cx_wdata[7:0],     // settings, clk_sys
                   i_clk_pix, i_vblank,                                    // pixel clock, fb_scanout's o_vblank
                   i_fb[7:0], i_t0[7:0], i_tp0[1:0], i_t1[7:0], i_tp1[1:0], // the sources, aligned
                   i_sp[10:0],                                             // {opaque, item[1:0], index}
                   o_idx[7:0]);                                            // registered
```

**Settings.** `i_cx_sel` names the command: 0 `SET_PRIORITY`, 1 `SET_BACKDROP`, 2 `FB_ENABLE`; `i_cx_wdata` is its payload byte, valid in the clock of the `i_cx_we` strobe. Keep a shadow register for each (initial values `E4`, 0, 1) and copy them to the live registers on the rising edge of `i_vblank` synchronized into `clk_sys`: the same event, and the same kind of synchronizer, as the tile layers' latch. As there, the pixel logic on `clk_pix` reads the live registers without a synchronizer, because they change only at the start of blanking, when no visible pixel passes.

**Keys.** Give each source a 5-bit key `{opaque, layer[1:0], item[1:0]}`. With `opaque` as the top bit, any opaque source beats any transparent one; with the item bits at the bottom, the layer priority is compared first. "Largest key wins" then needs no special cases. Make a transparent source's **whole** key 0, not just its top bit:

```verilog
wire [4:0] k_l0 = (i_t0 != 8'd0) ? {1'b1, p_l0, i_tp0} : 5'd0;    // p_l0: live SET_PRIORITY bits 3:2
```

and likewise for the others (`FB` with item 0 and opaque only if `FB_ENABLE` is set; `SP` opaque from `i_sp[10]`, item `i_sp[9:8]`). The reason is simulation: the item bits of a transparent pixel can be X (an unwritten map entry), and one X bit in a comparison makes its result X, which spreads into every pixel. Chapter 4.1's and 4.5's testbenches never write a map, so with keys like `{v_l0, p_l0, i_tp0}` their pictures fill with X.

**A comparator tree.** Four keys need three comparisons, in two levels: compare `L0` with `FB` and `SP` with `L1` in parallel, then the two winners. The tie rule comes free if each comparison puts the later source on the left of a `>=`:

```verilog
wire l0_wins = (k_l0 >= k_fb);     wire [4:0] ka = l0_wins ? k_l0 : k_fb;    // index likewise
wire sp_wins = (k_sp >= k_l1);     wire [4:0] kb = sp_wins ? k_sp : k_l1;
wire b_wins  = (kb >= ka);         wire [4:0] kw = b_wins  ? kb   : ka;
```

(`kb` is `L1` or `SP`, both later than anything in `ka`.) The output register is `o_idx <= kw[4] ? winner_index : backdrop`. Three 5-bit comparators and two levels of multiplexers fit easily into the 40 ns of a pixel clock.

### 3.3 Latency

The output register adds **one clock** to the pixel pipeline, so lengthen `fb_scanout`'s delay chain for `o_hsync`, `o_vsync` and `o_de` by one. The reference measures 7 clocks from `i_de` to `o_de`, one more than in Chapter 5.4; the testbench measures it and accepts 1 to 24, as before.

## 4. Changes to the existing modules

1. **`cmd_engine`:** new outputs `o_cx_we`, `o_cx_sel[1:0]`, `o_cx_wdata[7:0]`. Default `o_cx_we` to 0 at the top of the clocked block like the other strobes. In the length table, `0x30`, `0x31` and `0x32` need length 1. In the execute state each is one line: strobe `o_cx_we`, set `o_cx_sel` to 0, 1 or 2, and `o_cx_wdata <= arg[0]`.
2. **`gpu_link`:** pass the three ports through and set `CAPS` to `16'h027F`. `uart_gpu` leaves the new outputs open.
3. **`tile_layers`:** replace `o_idx` with `o_idx0[7:0]`, `o_idx1[7:0]` (each 0 where its layer is disabled: the enables stay inside the unit) and `o_pri0[1:0]`, `o_pri1[1:0]` (map entry bits 11:10). In Chapter 5.3's pipeline the map entry is read a clock before the tile byte, so register its priority bits next to the tile byte; for layer 0, whose tile byte is saved for a clock while layer 1 is read, save its priority bits with it. A priority taken from the wrong stage shows as tile-shaped errors shifted by a pixel or two against the tile grid.
4. **`sprite_lines`:** line buffer entries and `o_pix` grow from 9 to 11 bits, `{opaque, item[1:0], index}`; the assembler carries the sprite's flags bits 4:3 through its write pipeline with the column and palette. The 1,024 x 11 buffer still fits one 18 Kbit block RAM (1,024 x 18). The gating by the global enable stays.
5. **`compositor`:** new, section 3.2.
6. **`fb_scanout`:** new inputs `i_cx_we`, `i_cx_sel[1:0]`, `i_cx_wdata[7:0]`; the compositor replaces the multiplexer between the delay chains and the palette (feed its `i_vblank` from `o_vblank`); the sync delay chain grows by one.

## 5. The Python model

`host/gpu_model.py` (a backward-compatible replacement) now has the compositor:

```python
import gpu_model as g
g.set_priority(fb=0, l0=1, l1=2, sp=3)    # SET_PRIORITY (these are the defaults)
g.set_backdrop(10)                        # SET_BACKDROP
g.fb_enable(True)                         # FB_ENABLE
g.map_entry(tile, flip_x, flip_y, pri=1)  # a map entry with item priority 1
g.sprite_pri(2)                           # flags bits 4:3, e.g. flags=g.SPRITE_ON | g.sprite_pri(2)
g.compose_pixel(sources, priority, backdrop)   # the rule for one pixel
gpu = g.Gpu(); gpu.vblank(); gpu.screen()      # screen() calls compose_pixel() for every pixel
```

`compose_pixel()` is section 1 in ten lines; read it. `Gpu.vblank()` latches the compositor settings with everything else, and the default capability word is `0x027F`. The self-check still passes: `.venv/bin/python host/gpu_model.py`.

## 6. Simulate

The testbench is Chapter 5.4's with the three new wires: `vga_timing`, `gpu_link` and `fb_scanout` connected as in `top_artyz7.v`, and an SPI master playing a stream. Copy `ref_compositor.py` to `sim/py/` (it imports `ref_layers.py` and `ref_sprite_table.py`) and `tb_frame_compositor.v` to `sim/`, write the stream once (commit `sim/compositor_stream.hex` and `sim/compositor_replies.hex`), add `FRAME_TESTS += compositor` and run:

```sh
.venv/bin/python sim/py/ref_compositor.py --stream sim
make frame-compositor
```

**Setup.** The palette; a framebuffer picture with transparent windows of index 0 (a hole in the middle, a horizontal band, a vertical band and a diagonal line); two half-empty tile layers whose tiles carry item priorities 0 to 3 (layer 1 in bands of 4 map columns, layer 0 in bands of 4 map rows); five images; 42 sprites: a grid of 32 whose four rows have item priorities 0 to 3, a sprite with palette 1 in the framebuffer hole (some of its pixels wrap to index 0 and must stay opaque), and two overlapping sprites with priorities 0 and 3.

**Eight consecutive frames**, `f0` to `f7`. As in the earlier chapters, the changes for `fN` are sent at line 100 of the frame before; some steps also send commands at line 500, after the latch, which must show one frame later.

| Frame | What happens | Tests |
| --- | --- | --- |
| f0 | nothing: power-up settings | the old order; **the item priorities are in the data and must change nothing** |
| f1 | priorities `FB` 3, `L0` 2, `L1` 1, `SP` 0 | framebuffer in front, its windows showing the layers; sprites behind everything |
| f2 | priorities 2, 2, 0, 2; backdrop 77 | three sources share a layer priority: item priorities, then the later source; the backdrop; the index-0 sprite pixels |
| f3 | default priorities, `FB_ENABLE 0`; `FB_ENABLE 1` at line 500 | the framebuffer gone, the backdrop under the layers; the line-500 enable not yet |
| f4 | priorities 0, 1, 2, 2 | the forest: `L1` and `SP` tie, tiles with a higher item priority hide sprites |
| f5 | both layers scroll; all 42 sprites rewritten with other item priorities | tile against sprite priorities in new places; the lower-index sprite stays in front of the other whatever their priorities |
| f6 | priorities 2, 2, 2, 2; backdrop 200 at line 500 | everything ties; the line-500 backdrop not yet |
| f7 | `FB_ENABLE` with the value `FE`; priorities 3, 3, 1, 2; `SET_TILE_LAYER 2` | the two top-priority sources are disabled (bit 0 of `FE` is 0; layer 0 off) and take no part; backdrop 200 now shows |

The testbench writes `build/frame_compositor_f0.ppm` to `f7.ppm`, sends `GET_INFO` at the end and expects `47 01 7F 02 40 01 F0 00 00` (the last byte proves that no command set a flag), and checks the latency and the sync alignment. About 5 minutes.

**The testbench does not look at the pixels.** `make frame-compositor` runs `compare.py` afterwards, and only that step catches a wrong picture.

**When it fails**, look at the first frame and pixel `compare.py` reports:

- f0 wrong where sources overlap: the power-up values, or item priorities used when the layer priorities differ.
- Large areas wrong in almost every frame, f0 included (the framebuffer in front at power-up): the two-bit fields of the priority byte read in the wrong order.
- The framebuffer's windows opaque in f1 (showing a colour instead of the layers behind them): the framebuffer's index 0 is not transparent.
- Tie pixels wrong in f2 or f6, in whole columns or rows of tiles: `>` instead of `>=`, or the source order.
- Tile-shaped errors in f2, f4 or f5, shifted against the tile grid by a pixel or two: the tile's priority bits come from the wrong pipeline stage.
- From f0 on, the sprite in the framebuffer hole has holes where its pixels wrapped to index 0: sprite opacity tested with `index != 0` instead of the line buffer's bit.
- f3 shows the framebuffer, f3 and f4 agree, or f6 shows backdrop 200: the shadow registers or the latch.
- f7 shows the framebuffer or layer 0 in front: `FB_ENABLE` reads more than bit 0, or a disabled layer still reaches the compositor.

**The other testbenches.** Everything from Chapter 5.4 still passes, with these edits:

- **`tb_frame_layers`** and **`tb_frame_sprite_table`**: the capability byte is now `7F`. In `sim/py/ref_layers.py` and `sim/py/ref_sprite_table.py`, change `0x3F` to `0x7F` in the `GET_INFO` assertion in `main()` and rerun both with `--stream sim` (this rewrites their `*_replies.hex`); change the `3F` in each testbench's failure message too. Their pictures do not change: that is what the defaults are for.
- **The new ports** can stay open in the older testbenches, as those of 5.3 and 5.4 did: `fb_scanout`'s in `tb_frame_scanout`, `tb_frame_flip`, `tb_flip_sync`, `tb_frame_layers` and `tb_frame_sprite_table`, `gpu_link`'s in `tb_gpu_spi`, `tb_link_arb` and the two frame tests. A floating `i_cx_we` reads as Z, which an `if` treats as false, so the settings keep their power-up values. (If your design lets the floating inputs reach a register some other way, tie `i_cx_we` to 0 in those testbenches.)
- **`tb_gpu_fuzz`**: this chapter ships replacements for `tb_gpu_fuzz.v` (capability byte `7F`, the only change) and `ref_fuzz.py` (`0x30` to `0x32` are known opcodes, with wrong-length rounds and random valid packets for them). Regenerate with `.venv/bin/python sim/py/ref_fuzz.py sim` and commit the new `sim/fuzz_*` files.

## 7. On the board

In `top_artyz7.v`, add three wires and connect the new ports:

```verilog
// gpu_link:    .o_cx_we(cx_we), .o_cx_sel(cx_sel), .o_cx_wdata(cx_wdata)
// fb_scanout:  .i_cx_we(cx_we), .i_cx_sel(cx_sel), .i_cx_wdata(cx_wdata)
```

The reference build (openXC7), with Chapter 5.4's for comparison:

| Resource | Chapter 5.4 | Chapter 5.5 | Of |
| --- | --- | --- | --- |
| LUTs | about 7,000 | about 7,060 | 106,400 |
| Flip-flops | about 2,200 | about 2,300 | 106,400 |
| RAMB36E1 | 54 | 54 | 140 |
| RAMB18E1 | 2 | 2 | 280 |
| DSP48E1 | 20 | 20 | 220 |

The compositor costs about 60 LUTs and 80 flip-flops (comparators, settings registers, the longer sync chain) and no memory: the two extra bits per line buffer entry fit the same block RAM. `clk_pix` closes above 150 MHz against 25. `clk_sys` closed at 68 MHz for one design built from this chapter and at 51 MHz for another, against 50 required. Its critical path is in the triangle rasterizer, not the compositor: placement shifts with any small change to the design, and the rasterizer's margin with it. If your build misses 50 MHz, look at that path first, not at the compositor.

Try the demo without the board first: `.venv/bin/python host/compositor_demo.py --model` runs the host code against the Python model and prints `PASS: compositor_demo (model)`; add `--ppm frame.ppm` to see a frame. (It imports Chapter 5.3's `layer_demo.py` and Chapter 5.4's `sprite_demo.py`, which must be in `host/`.) Then, with Chapter 5.1's wiring:

1. Program the bitstream. Nothing changes on screen: the defaults are the old order.
2. `.venv/bin/python host/spi_info.py` lists `COMPOSITOR` (`caps 027F`).
3. `.venv/bin/python host/compositor_demo.py --freq 5000000`. A scrolling landscape: a yellow HUD frame in front (the framebuffer), sky and hills (layer 0), ground and trees (layer 1), a ball (the player) walking left to right, behind each tree and in front of the ground, and six balls flying above. Every 2.5 seconds one thing changes: the HUD vanishes; layer 0 goes too and the magenta backdrop shows; the HUD and layer 0 come back and layer 1 (ground and trees) goes; layer 1 comes back and the sprites go; the sprites come back and the trees lose their item priority, so the player walks in front of them; finally layer 1 gets a higher layer priority than the sprites and the player goes behind all of it.
4. At the end the script prints the frame count and the error flags, which should be clear.
5. Experiments: set the player's `PRIORITY` to 1 and watch it step in front of the trees (equal item priorities: the sprites are the later source); make the three commands write the live registers directly and look for the order changing mid-frame; give layer 1 and the HUD both priority 3 and see which wins (layer 1, the later source).

## Testbench requirements

**Modules.**

- **`gpu_link`**: Chapter 5.4's ports plus outputs `o_cx_we`, `o_cx_sel[1:0]` (0 `SET_PRIORITY`, 1 `SET_BACKDROP`, 2 `FB_ENABLE`) and `o_cx_wdata[7:0]` (the payload byte): a one-clock strobe on `clk_sys` with the data in the same clock. `GET_INFO` replies `47 01 7F 02 40 01 F0 00 ER`.
- **Commands** `0x30` to `0x32` as in `protocol.md` sections 3 and 12: length exactly 1 (others skipped whole, `BAD_LEN`), no flag for any byte value; `0x33` to `0x3F` stay unknown.
- **`fb_scanout`**: Chapter 5.4's ports plus `i_cx_we`, `i_cx_sel[1:0]`, `i_cx_wdata[7:0]`, connected one to one to `gpu_link`'s. Any total latency from 1 to 24 clocks, with `o_hsync`, `o_vsync`, `o_de` delayed equally. How the compositor is built inside is free.
- **Video rules** (`protocol.md` section 12): index 0 transparent in every source, the framebuffer included (a sprite pixel is opaque if a sprite drew it); a disabled source is transparent; key = layer priority, then item priority (map entry bits 11:10, sprite flags bits 4:3, framebuffer 0); the largest key wins, ties to the later of `FB`, `L0`, `L1`, `SP`; the backdrop where nothing is opaque; sprites among themselves by table index. `SET_PRIORITY`, `SET_BACKDROP` and `FB_ENABLE` are copied to the live registers at the start of vertical blanking (the test accepts any copy complete after line 480 and before line 500); map and sprite-table rules as in sections 10 and 11. Power-up: priorities `E4`, backdrop 0, framebuffer on.
- **Python**: this chapter's `host/gpu_model.py` (with `raster_model.py`) and `host/compositor_demo.py`; `ref_compositor.py` imports `ref_layers.py` and `ref_sprite_table.py`.

**`tb_frame_compositor`** (about 5 minutes, `make frame-compositor` from the repo root). Plays `sim/compositor_stream.hex` over SPI at 5 MHz (checking `READY` before each SPI frame), dumps eight consecutive 640 x 480 frames at 8 bits per channel, and prints `PASS: tb_frame_compositor (...)`. It fails on a frame-dump geometry or X/Z error, unequal sync/`de` delay, a status byte without the `01` signature, a FIFO overflow, a `GET_INFO` reply other than `sim/compositor_replies.hex` (including any error flag), or a group of commands that missed the part of the frame it was meant for. It does **not** check pixels; the `compare.py` step that follows does.

**`tb_gpu_fuzz`**: Chapter 5.4's test with this chapter's `tb_gpu_fuzz.v` and `ref_fuzz.py`. **`tb_frame_layers`** and **`tb_frame_sprite_table`** need the capability edit of section 6.

**Files:** `protocol.md` to `docs/`; `tb_frame_compositor.v` and `tb_gpu_fuzz.v` to `sim/`; `ref_compositor.py` and `ref_fuzz.py` to `sim/py/`; `gpu_model.py` and `compositor_demo.py` to `host/`. Then:

```sh
.venv/bin/python sim/py/ref_layers.py --stream sim          # after the 0x7F edit
.venv/bin/python sim/py/ref_sprite_table.py --stream sim    # after the 0x7F edit
.venv/bin/python sim/py/ref_compositor.py --stream sim
.venv/bin/python sim/py/ref_fuzz.py sim
make frame-compositor
```
