# Chapter 5.3 - Hardware Tile Layers

So far the GPU makes pictures by writing pixels into a framebuffer. A scrolling game background drawn that way needs 76,800 pixel writes every frame. Consoles solved this long ago: keep the background as a small map of tiles, and let the video hardware build the picture while it scans the screen. Chapter 2.5 did this on the Go Board. This chapter does it again on the Arty, with two layers, 256 colors and a 512 x 512 pixel world, and puts it behind the SPI protocol.

By the end of this chapter you will have:

- two tile layers, each a 64 x 64 map of 16-bit entries, drawn during scanout with no framebuffer memory,
- one shared 16 KB tile graphics RAM (256 tiles of 8 x 8 pixels at 8 bits per pixel),
- per-layer scroll in X and Y with wraparound, latched at vertical blanking so a frame never tears,
- four new commands (`0x10` to `0x13`) and capability bit 4 in `GET_INFO`,
- `sim/tb_frame_layers.v`: ten frames at different scroll offsets, compared pixel for pixel with a Python model,
- `host/layer_demo.py`: a two-layer parallax scene.

## 1. Two layers, each its own world

A **tile** is an 8 x 8 picture. A **map** says which tile goes where. The picture on the screen is the map drawn with its tiles, shifted by a **scroll** position.

Each layer has its own 64 x 64 map, so drawn at 8 x 8 pixels per tile it is a **world** of 512 x 512 pixels. The screen shows a 320 x 240 window of it (doubled to 640 x 480 on the display, like the framebuffer) whose top-left corner is at world position (`sx`, `sy`). Screen pixel (X, Y) shows world pixel

```
wx = (sx + X) mod 512        wy = (sy + Y) mod 512
```

The world **wraps**: scroll past the right edge and you come back in at the left. Since 512 is a power of two, the "mod" is just the low 9 bits of the sum.

Both layers share the tile graphics, so a map entry only has to name a tile. A 16-bit entry has room for more:

| Bits | Name | Meaning |
| --- | --- | --- |
| 7:0 | `TILE` | tile number, 0 to 255 |
| 8 | `FLIP_X` | mirror the tile left to right |
| 9 | `FLIP_Y` | mirror the tile top to bottom |
| 15:10 | | reserved: write 0 |

Flips are cheap: the pixel position inside the tile, `px` or `py` (0 to 7), becomes `7 - px`, which for a 3-bit number is just `~px`. One tile can then serve as four.

The tile graphics are 64 bytes per tile, row by row: the byte for pixel (`px`, `py`) of tile `t` is at address `t * 64 + py * 8 + px`. Each byte is an index into the 256-entry palette the framebuffer already uses. **Index 0 is transparent.**

## 2. Layering and transparency

The order is fixed, back to front:

1. the front framebuffer (always opaque),
2. layer 0,
3. layer 1.

Each layer pixel that is not index 0 hides what is behind it. Think of layer 0 as distant hills and layer 1 as the ground and trees in front. A layer that is switched off counts as index 0 everywhere. (Chapter 5.5 replaces this fixed order with a compositor.)

## 3. Memory budget

| Memory | Bits | RAMB36E1 |
| --- | --- | --- |
| Tile graphics, 16,384 x 8 | 128 Kbit | 4 |
| Map 0, 4,096 x 16 | 64 Kbit | 2 |
| Map 1, 4,096 x 16 | 64 Kbit | 2 |
| **Layers total** | | **8** |

Chapter 5.2's design used 46 of the 140 RAMB36 blocks (two framebuffers and the asset RAM), so the layers bring it to 54.

All three memories are written by the command engine on `clk_sys` and read by the video logic on `clk_pix`, like the framebuffer: one clock on each port of a dual-port RAM.

## 4. The commands

Chapter 5.2 reserved `0x10` to `0x1F` for tile layers. This chapter defines four of them. The updated spec is [`protocol.md`](protocol.md) next to this chapter: new rows in the opcode table, a section 10 on tile layers, capability bit 4. Replace your `docs/protocol.md` with it.

| Op | Name | len | Payload |
| --- | --- | --- | --- |
| `10` | `SET_TILE_LAYER` | 1 | mask: bit 0 shows layer 0, bit 1 shows layer 1 |
| `11` | `SET_SCROLL` | 5 | layer, x (16-bit), y (16-bit) |
| `12` | `UPLOAD_TILES` | 2 + n | offset (16-bit), n data bytes, n up to 253 |
| `13` | `WRITE_TILE_MAP` | 3 + 2k | layer, offset (16-bit, in entries), k entries of 16 bits, k up to 126 |

The rules:

- **Size limits.** A payload is at most 255 bytes, so one `UPLOAD_TILES` carries at most 253 tile bytes (just under four tiles) and one `WRITE_TILE_MAP` at most 126 entries (almost two map rows). A full 16 KB tile set is 65 packets; a full map is 33. Each packet is at most 257 bytes, so one fits in each SPI frame that `READY` allows.
- **Scroll is modulo 512.** `SET_SCROLL` takes any 16-bit value and uses the low 9 bits, so `-1` means 511.
- **Layer numbers.** Only 0 and 1 exist. For a layer above 1, `SET_SCROLL` and `WRITE_TILE_MAP` do nothing. The length was right, so no flag is set (the same rule as a `BLIT` of an unknown asset).
- **Off the end.** `UPLOAD_TILES` bytes for addresses 16,384 and up are dropped, byte by byte, and so are `WRITE_TILE_MAP` entries from 4,096 up. They must not wrap to the start of the RAM, or a slightly wrong offset would corrupt tile 0.
- **Wrong lengths.** `SET_TILE_LAYER` needs exactly 1 and `SET_SCROLL` exactly 5. `UPLOAD_TILES` needs at least 2, and `WRITE_TILE_MAP` at least 3 and an odd number (whole entries). Anything else is skipped whole and sets `BAD_LEN`.
- **Capability bit 4** (`TILES`). `gpu_link` now answers `GET_INFO` with `47 01 1F 02 40 01 F0 00 ER`. Phase 4's `uart_gpu` keeps `0F 00`: its engine accepts the new commands, but nothing is connected to its tile ports (section 5).

### When a command takes effect

The video logic uses the scroll values on every pixel. If a command changed one in the middle of a frame, the lines above the change would be drawn with the old scroll and the lines below with the new: a visible **tear**. So scroll and enable are **double buffered**:

- `SET_SCROLL` and `SET_TILE_LAYER` write **shadow registers**, which the video does not use.
- At the **start of vertical blanking** the GPU copies all the shadows into the **live registers** the video uses. These stay fixed for the whole next frame.

A host may send several `SET_SCROLL` in a frame (the last one counts) and at any time: the change shows from the next frame on. Everything sent before the same blanking takes effect together, so the two layers move in step.

Map and tile writes are not buffered: they go straight into the RAMs, so a write during the visible part of a frame can show in the lines still to be drawn. That is fine while loading a level, or for map areas that are off screen. When the screen shows the area being written, send the writes during vertical blanking (the `VSYNC` pin and the `VBLANK` status bit tell you when).

Power-up state: both layers disabled, both scrolls 0, the memories undefined. Enable a layer only after its tiles and map are written.

## 5. The command engine

The commands fit into the engine of Chapter 5.2 as three small additions.

**The ports.** `cmd_engine` and `gpu_link` get these outputs, all on `clk_sys`. Each `_we` is a one-clock write strobe, with the data registered and valid in the same clock:

| Group | Ports |
| --- | --- |
| tile map | `o_tm_we`, `o_tm_layer`, `o_tm_addr[11:0]`, `o_tm_wdata[15:0]` |
| tile graphics | `o_tg_we`, `o_tg_addr[13:0]`, `o_tg_wdata[7:0]` |
| scroll | `o_sc_we`, `o_sc_layer`, `o_sc_x[8:0]`, `o_sc_y[8:0]` |
| enable | `o_en_we`, `o_en[1:0]` |

`gpu_link` passes them through and sets the engine's `CAPS` to `16'h021F`. `uart_gpu` leaves the engine's new outputs unconnected and keeps `CAPS` at `000F`.

**The short two.** `SET_TILE_LAYER` and `SET_SCROLL` use the ordinary path: the payload lands in `arg[]`, and the execute state checks the length and pulses the strobe:

```verilog
8'h10: if (len == 1) begin o_en_we <= 1'b1; o_en <= arg[0][1:0]; end
8'h11: if (len == 5 && arg[0] <= 1) begin
           o_sc_we <= 1'b1; o_sc_layer <= arg[0][0];
           o_sc_x <= {arg[2][0], arg[1]}; o_sc_y <= {arg[4][0], arg[3]};
       end
```

Taking only bit 0 of the high byte is the "modulo 512". In the `known`/`len_ok` table, `0x10` needs 1 and `0x11` needs 5, and `0x12` and `0x13` are known with `len_ok` always false: as with `UPLOAD_ASSET`, only a packet with a wrong length reaches the execute state. Default the new strobes to 0 at the top of the clocked block, like `o_fb_we`.

**The streamed two.** The engine keeps only 16 argument bytes, and these packets carry up to 253. So, like `UPLOAD_ASSET` in Chapter 4.8, they are **streamed**: each payload byte is acted on as it arrives, in the state that consumes payload bytes. Whether a packet can be streamed is known from its length byte, before any data:

```verilog
wire tg_stream = (op == 8'h12) && (len >= 2);
wire tm_stream = (op == 8'h13) && (len >= 3) && len[0];     // 3 + an even number
```

For payload byte `n` (the payload counter you already have):

- **`UPLOAD_TILES`:** byte 0 is the offset's low byte (kept in `arg[0]`). At byte 1 load a 17-bit address `tg_a <= {1'b0, dbyte, arg[0]}`. Every later byte is data: write it at `tg_a[13:0]` if `tg_a < 16384` (`tg_a[16:14] == 0`), then add 1 to `tg_a`. With 17 bits the address cannot wrap to 0 within one packet.
- **`WRITE_TILE_MAP`:** bytes 0 to 2 are layer and offset; at byte 2 load `tm_a` from `arg[1]` and the byte just read. After that, an odd `n` is an entry's low byte (keep it) and an even `n` is its high byte, which completes the entry: strobe `o_tm_we` if the entry number is below 4096 and the layer is 0 or 1, with data `{dbyte, low}` (little endian: low byte first).
- After the last byte (`n + 1 == len`) go back to expecting an opcode.

A frame start that cuts a streamed packet short behaves as for `UPLOAD_ASSET` (spec section 4): the bytes that arrived stay written and `TRUNC` is set. For the map only whole entries count. The Python model does the same, so the fuzz test stays exact.

## 6. The pixel pipeline

For every pixel the video side has to:

1. add the scroll to the beam position and find the tile column and row,
2. read the **map** entry for that tile,
3. read the **tile graphics** byte for that tile number and pixel position (with flips),
4. pick the front layer that is not transparent, and look the index up in the palette.

A block RAM read takes one clock, so steps 2 and 3 are two pipeline stages in a row. The map address comes from the beam position and the live scroll:

```verilog
wire [8:0]  wx = i_x[9:1] + sx;                  // 2 screen pixels per logical pixel
wire [8:0]  wy = i_y[9:1] + sy;
wire [11:0] map_addr = {wy[8:3], wx[8:3]};       // row * 64 + column
```

The scroll is added **before** the map lookup, so the same sum picks both the tile and the pixel inside it. The low bits `wx[2:0]` and `wy[2:0]` are `px` and `py`; delay them one clock to meet the map entry.

### Two layers, one tile RAM

Each pixel needs two tile reads, one per layer, but the tile RAM has one read port (the other is the write side). There are three ways out:

- **Two copies of the tile RAM,** one per layer, written together. Simple, but it costs 4 more RAMB36.
- **Prefetch a line** of each layer into a line buffer. More logic and memory.
- **Time multiplex.** Every logical pixel lasts **two** `clk_pix` clocks, so one read port can serve layer 0 in the first and layer 1 in the second.

We use the third: no extra RAM and a few flip-flops. The two maps are separate RAMs, so both are read in the same clock. Their address changes only every second clock, so their outputs stay valid for two clocks. Follow one logical pixel P that starts at an even `i_x` = k:

| `clk_pix` | Map RAM outputs | Tile RAM address | Tile RAM output | Output register |
| --- | --- | --- | --- | --- |
| k | (previous pixel) | | | |
| k+1 | P, both layers | P, layer 0 | | |
| k+2 | P (same address at k+1) | P, layer 1 | P, layer 0: save it | |
| k+3 | next pixel | next pixel, layer 0 | P, layer 1: mix with the saved layer 0 | loaded at the end of k+3 |
| k+4, k+5 | | | | holds P |

The layer select is `i_x[0]` delayed one clock (0 = layer 0) and, one stage later, delayed two. The output register loads only every second clock, so it holds each pixel for both of its clocks. The mix is the fixed order of section 2:

```verilog
o_idx <= (en[1] && t1 != 0) ? t1 : (en[0] ? t0 : 8'd0);     // t1 layer 1, t0 layer 0 (saved)
```

Put this in a module `tile_layers` with the write ports of section 5, the pixel clock, the beam position and an 8-bit output `o_idx` that is **0 where no layer shows a pixel**. In this pipeline `o_idx` is ready 4 clocks after `i_x`. The testbench only checks the final picture, so any latency works.

### Merging with the framebuffer

Instantiate `tile_layers` inside `fb_scanout` and choose per pixel:

```verilog
wire [7:0] idx = (ovl != 8'd0) ? ovl : fb_idx_delayed;     // ovl = tile_layers' o_idx
```

The two indexes must refer to the same pixel, so delay the framebuffer index by the difference in latency. In the reference `fb_scanout` of Chapter 4.5 the framebuffer index is ready 1 clock after `i_x`, against 4 for the layers, so it gets 3 more registers. The palette lookup and output register follow as before, and `o_hsync`, `o_vsync` and `o_de` get the same extra delay to stay aligned. The reference's total latency goes from 3 to 6 clocks.

`fb_scanout` gets the write ports of section 5 as new inputs:

```
i_tm_we, i_tm_layer, i_tm_addr[11:0], i_tm_wdata[15:0],
i_tg_we, i_tg_addr[13:0], i_tg_wdata[7:0],
i_sc_we, i_sc_layer, i_sc_x[8:0], i_sc_y[8:0], i_en_we, i_en[1:0]
```

### Latching at vertical blanking

Keep both the shadow registers (written by the strobes) and the live ones on `clk_sys`. `fb_scanout` already produces `o_vblank` (registered, high from line 480). Synchronize it into `clk_sys` with the two-flop `synchronizer`, and copy shadow to live on its rising edge:

```verilog
always @(posedge i_clk_sys) if (vb_sys && !vb_d) begin sx0 <= sh_x0; ... en <= sh_en; end
```

The pixel logic on `clk_pix` then reads the live registers directly, with no synchronizer. Is that safe? A synchronizer is for a signal that can change at any moment relative to the clock that reads it. The live registers change at one known time, a few `clk_sys` clocks after line 480 begins. The pixel pipeline does sample them at that moment, and a flip-flop there may catch a mix of old and new bits, or even go metastable. But nothing it computes during blanking is shown. The next visible pixel is at line 0, 45 lines (36,000 pixel clocks) later, and by then the registers have long been steady. A value that never changes while it is being used needs no synchronizer.

Why not copy in the `clk_pix` domain instead? Then the shadow registers would be the ones crossing clocks, and they change whenever a command arrives. A `SET_SCROLL` landing in the clock of the copy could be half copied.

Any latch point inside vertical blanking is correct, as long as it leaves a few clocks before line 0 for the copy to settle. The testbench accepts all of them. The rule it checks: a change sent during a frame shows in the next frame, never in the one in progress.

## 7. The Python model

`host/gpu_model.py` (a backward-compatible replacement) now models the layers:

```python
import gpu_model as g
g.set_tile_layer(mask)            g.set_scroll(layer, x, y)           # packet builders
g.upload_tiles(offset, data)      g.tile_packets(data)                # all of data as packets
g.write_tile_map(layer, offset, entries)    g.map_packets(layer, entries)
g.map_entry(tile, flip_x, flip_y)
gpu = g.Gpu()
gpu.vblank()     # shadow -> live, as the hardware does at the start of blanking
gpu.screen()     # one frame of 76,800 palette indices: framebuffer, layer 0, layer 1
```

`g.layer_pixel(tiles, tmap, sx, sy, x, y)` defines sections 1 and 2 in a few lines; read it. The model also follows the rules of section 4: dropped bytes and entries, ignored layer numbers, wrong lengths, and the streamed writes a cut packet leaves behind. Its self-check still passes: `.venv/bin/python host/gpu_model.py`.

## 8. Simulate

The testbench is a small board: `vga_timing`, `gpu_link` and `fb_scanout` wired as in `top_artyz7.v`, with an SPI master that plays a stream of commands. Copy `ref_layers.py` to `sim/py/` and `tb_frame_layers.v` to `sim/`, write the stream once (commit `sim/layers_stream.hex` and `sim/layers_replies.hex`), add `FRAME_TESTS += layers` to your Makefile, and run:

```sh
.venv/bin/python sim/py/ref_layers.py --stream sim
make frame-layers
```

What the stream does:

1. **Setup.** All 256 palette entries, a framebuffer picture (stripes and lines, then `FLIP`), 256 tiles and both maps. Tile 0 is empty; some tiles are checkerboards, half transparent or hollow frames. Every pixel's color depends on the tile and on `x` and `y` separately, so a flipped tile, a one-pixel shift or a wrong tile changes the picture. Layer 1 is half empty so layer 0 shows through it, and layer 0 has holes that show the framebuffer.
2. **Packets that must be partly or wholly ignored:** `SET_SCROLL` and `WRITE_TILE_MAP` for layer 2, a map write of 4 entries that starts 2 entries before the end, and a tile upload of 8 bytes that starts 4 bytes before the end. A design that wraps, or writes layer 2 into layer 0, changes tile 0 or the first map entries, and that shows on screen.
3. **Ten consecutive frames**, `f0` to `f9`. The changes for frame `fN` are sent at line 100 of frame `f(N-1)`, which must therefore show **none** of them. A design that applies scroll at once tears `f(N-1)`.

| Frame | Layer 0 scroll | Layer 1 scroll | Enabled | Tests |
| --- | --- | --- | --- | --- |
| f0 | 0, 0 | 0, 0 | both | the basic picture |
| f1 | 7, 13 | 0, 0 | both | odd offsets, layers independent |
| f2 | 7, 13 | 301, 0 | both | X wraps at world 512 inside the view; a first write replaced before blanking |
| f3 | -1, -1 (511, 511) | 301, 299 | both | negative values; wrap in X and Y |
| f4 | 264, 264 | 511, 511 | both | two writes in a frame, the last counts |
| f5 | 100, 50 | 5, 6 (hidden) | layer 0 only | enable bit; layer 1 moved while hidden |
| f6 | 1, 1 | 5, 6 | layer 1 only | the other enable bit |
| f7 | | | none | the framebuffer alone |
| f8 | 320, 240 | 5, 6 | both | scroll by whole tiles; a map row and a tile rewritten during the blanking before it |
| f9 | 509, 3 (sent as 1533 and -509) | 2, 510 (sent as 1534) | both | scroll values reduced modulo 512 |

The testbench dumps the ten frames to `build/frame_layers_f0.ppm` to `f9`. At the end it sends `GET_INFO` and checks the 9 bytes `47 01 1F 02 40 01 F0 00 00`; the last byte proves that every packet was well formed and that the ignored ones set no flag. It measures the latency (any value from 1 to 24 clocks is fine) and checks that the three sync outputs are delayed by the same amount. It takes about 5 minutes (16 video frames and 35 KB over SPI at 5 MHz).

**The testbench does not look at the pixels.** Like every frame test since Chapter 1.2, it prints `PASS` as long as the frames have the right shape. `make frame-layers` then runs `ref_layers.py build` and `compare.py --set build layers`, and only that last step says whether the pictures are right. A broken design typically prints `PASS: tb_frame_layers` and then `FAIL: compare layers`.

**When it fails**, look at the first frame and pixel that `compare.py` reports:

- The right picture shifted by 8 or a multiple: the map address, or row and column swapped.
- Whole tiles wrong in places: map entry byte order, or the flip bits swapped.
- Right at `f0` and wrong from `f1` or `f2`: the wrap. A world that wraps at 320 instead of 512 shows from `f1`.
- `f(N-1)` already shows part of `f(N)`'s scroll, from line 100 down: the latch.
- Layer 0 in front of layer 1, or layer 1's holes showing black: the mix.
- Everything doubled or half-size: `i_x[9:1]` or `i_y[9:1]` was forgotten.
- Only the top-left corner or tile 0 wrong: an off-the-end write wrapped, or a layer-2 write went into layer 0.

**The other testbenches.** `tb_frame_flip`, the command-processor tests and the SPI tests still pass. In `tb_frame_flip` the new inputs of `fb_scanout` can stay unconnected (Icarus warns about "dangling input" ports, and the layers stay off), and that test measures the latency, so 6 is fine.

`tb_gpu_fuzz` from Chapter 5.2 does need a change, so this chapter ships replacements for both its files:

- **`tb_gpu_fuzz.v`** differs from Chapter 5.2's in one line: the expected capability byte in the flag test is now `1F`. Without it the old test fails at `GET_INFO` byte 2.
- **`ref_fuzz.py`** now treats `0x10` to `0x13` as known: its "unknown opcode" rounds use only opcodes that are still unknown, its wrong-length rounds include the four new commands (and check the tile length rules one packet at a time, between flag clears, since flags are sticky), and the valid rounds send random tile commands (in and out of range, some cut short). The fuzz test cannot see the tile ports, but it does check that the parser consumes these packets correctly and sets the right flags.

Copy both to `sim/` and `sim/py/`, regenerate the stream with `.venv/bin/python sim/py/ref_fuzz.py sim` (commit the new `sim/fuzz_*` files) and run it as in Chapter 5.2.

## 9. On the board

Build the Arty project as in Chapter 5.2, with the `fb_scanout` tile ports wired to the `gpu_link` outputs in `top_artyz7.v`:

```verilog
.i_tm_we(tm_we), .i_tm_layer(tm_layer), .i_tm_addr(tm_addr), .i_tm_wdata(tm_wdata),
.i_tg_we(tg_we), .i_tg_addr(tg_addr), .i_tg_wdata(tg_wdata),
.i_sc_we(sc_we), .i_sc_layer(sc_layer), .i_sc_x(sc_x), .i_sc_y(sc_y), .i_en_we(en_we), .i_en(en)
```

The reference build:

| Resource | Used | Of |
| --- | --- | --- |
| LUTs | about 6,200 | 106,400 |
| Flip-flops | about 1,900 | 106,400 |
| RAMB36E1 | 54 | 140 |
| DSP48E1 | 20 | 220 |

`clk_sys` closes at about 65 to 70 MHz against 50 required, and `clk_pix` far above its 25 MHz. The 8 new RAMB36 are the tile graphics and the two maps of section 3.

First try the demo without the board: `.venv/bin/python host/layer_demo.py --model` runs the same host code against the Python model and prints `PASS: layer_demo (model)`. Add `--ppm scene.ppm` to see the scene.

Then, with the wiring of Chapter 5.1 (FT232H on Pmod JB):

1. Program the bitstream. HDMI should show the Chapter 4 test image as before, since the layers are off at power-up.
2. `.venv/bin/python host/spi_info.py` should list `TILES` among the capabilities (`caps 021F`).
3. `.venv/bin/python host/layer_demo.py --freq 5000000`. After about a second of uploading you should see blue sky with clouds, green hills and a brown ground strip with small trees. For the next 10 seconds the hills drift 1 pixel every 2 frames and the ground 2 pixels per frame: that difference is the parallax. The motion should be smooth, with no shearing, no flicker and no jump when a layer wraps (the ground repeats every 256 frames). Ctrl-C stops it early.
4. At the end the script prints the frame count and the error flags, which should be clear.
5. Experiments: send `set_tile_layer(1)` or `set_tile_layer(2)` in `layer_demo.py` to see one layer alone; give the far layer a vertical scroll; set `FLIP_X` on every map entry. To see what tearing looks like, move the latch in your RTL to a fixed line in the middle of the frame (`i_y == 240`) and rerun: the hills shear at that line while they move. Put it back afterwards.

**Done when:** `make frame-layers` passes (all ten frames, including the ones scrolled across the wrap, match the Python model exactly), the updated `tb_gpu_fuzz` and the earlier testbenches still pass, and `layer_demo.py` shows the two layers scrolling smoothly at different speeds on the monitor.

## Testbench requirements

**Modules.**

- **`gpu_link`**: all ports of Chapter 5.2, plus the outputs `o_tm_we`, `o_tm_layer`, `o_tm_addr[11:0]`, `o_tm_wdata[15:0]`, `o_tg_we`, `o_tg_addr[13:0]`, `o_tg_wdata[7:0]`, `o_sc_we`, `o_sc_layer`, `o_sc_x[8:0]`, `o_sc_y[8:0]`, `o_en_we`, `o_en[1:0]`, all one-clock strobes on `clk_sys` with the data valid in the same clock. `GET_INFO` replies `47 01 1F 02 40 01 F0 00 ER`.
- **Commands** `0x10` to `0x13` exactly as in `protocol.md` sections 3 and 10: lengths (wrong ones skipped whole with `BAD_LEN`), scroll modulo 512, a layer above 1 ignored without a flag, data past the end of a memory dropped without a flag and without wrapping, streamed writes as the data arrives (a cut packet leaves what arrived), map entries low byte first.
- **`fb_scanout`**: its Chapter 4.5 ports plus `i_tm_we`, `i_tm_layer`, `i_tm_addr`, `i_tm_wdata`, `i_tg_we`, `i_tg_addr`, `i_tg_wdata`, `i_sc_we`, `i_sc_layer`, `i_sc_x`, `i_sc_y`, `i_en_we`, `i_en` (widths as above), all on `clk_sys`. `o_vblank` is unchanged. Any total latency from 1 to 24 clocks; `o_hsync`, `o_vsync` and `o_de` are the inputs delayed by that same latency.
- **Video rules.** Sections 1 and 2: a 320 x 240 view shown at 2 x 2, a 512 x 512 world that wraps, flips, index 0 transparent, order framebuffer then layer 0 then layer 1, the palette shared with the framebuffer. Scroll and enable are latched once per frame at a point inside vertical blanking and constant for the whole frame; tile and map writes take effect at once. Power-up: layers disabled, scroll 0.
- **`host/gpu_model.py`** from this chapter (it needs `raster_model.py` next to it) and `host/layer_demo.py`. The tiles, maps, palette and expected frames all come from `ref_layers.py`.

**`tb_frame_layers`** (about 5 minutes): run from the repo root by `make frame-layers`. Reads `sim/layers_stream.hex` and `sim/layers_replies.hex` and uses only the ports above. It sends 156 SPI frames at 5 MHz (checking `READY` before each), dumps ten consecutive frames at 640 x 480 and 8 bits per channel, and prints `PASS: tb_frame_layers (...)`. It fails on: a frame-dump geometry or X/Z error; `o_hsync`/`o_vsync`/`o_de` not delayed equally; a status byte without the `01` signature; a FIFO overflow; a `GET_INFO` reply other than the expected bytes (including any error flag); a group of commands that did not finish in the part of the frame it was meant for. It does **not** check pixels: `make frame-layers` then compares the ten frames with `ref_layers.py`'s, and only that `compare.py` step catches a wrong picture.

**`tb_gpu_fuzz`**: Chapter 5.2's test, with this chapter's `tb_gpu_fuzz.v` and `ref_fuzz.py` (section 8).

**Files from this chapter:** `protocol.md` to `docs/`; `tb_frame_layers.v` and `tb_gpu_fuzz.v` (replacing Chapter 5.2's) to `sim/`; `ref_layers.py` and `ref_fuzz.py` (replacing Chapter 5.2's) to `sim/py/`; `gpu_model.py` and `layer_demo.py` to `host/`. Add `FRAME_TESTS += layers`.

By hand:

```sh
.venv/bin/python sim/py/ref_layers.py --stream sim
.venv/bin/python sim/py/ref_fuzz.py sim
make frame-layers
```
