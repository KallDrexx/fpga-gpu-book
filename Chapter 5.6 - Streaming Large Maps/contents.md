# Chapter 5.6 - Streaming Large Maps

A tile layer's map holds 64 x 64 tiles, a world of 512 x 512 pixels. A side-scroller's level is often 200 tiles wide or more. It does not have to fit: the screen shows only about 40 columns of the level at a time, so the map only has to hold the part of the level around the camera. As the camera moves, the host overwrites the columns that have left the screen with the ones about to enter. This is how the NES and the Mega Drive scrolled levels many screens wide with a few kilobytes of video memory. This chapter adds two commands that make it cheap, and the host code that decides what to send.

By the end of this chapter you will have:

- two commands, `WRITE_TILE_COLUMN` (`0x40`) and `WRITE_TILE_ROW` (`0x41`), each writing 64 map entries of one layer in one packet, and capability bit 7 in `GET_INFO`,
- `host/level_stream.py`: a small planner that says which columns and rows to write for each scroll step,
- `sim/tb_frame_streaming.v`: fourteen frames of a scroll through a 256-tile-wide level, compared pixel for pixel with a Python model,
- `host/stream_demo.py`: a long level scrolled over SPI at under 200 bytes per frame.

## 1. The map as a ring

Chapter 5.3's hardware already does the hard part: the world **wraps**. Screen pixel (X, Y) shows world pixel `((sx + X) mod 512, (sy + Y) mod 512)`, which lies in map column `((sx + X) >> 3) mod 64` and map row `((sy + Y) >> 3) mod 64`.

So keep **level column `c` in map column `c mod 64`**, and level row `r` in map row `r mod 64`, and send the scroll in *level* pixels: `SET_SCROLL` with `sx = 1728` is legal, because the hardware keeps only the low 9 bits. The hardware then shows exactly the level at (`sx`, `sy`), as long as the columns and rows on screen are loaded. The map has become a **ring** of 64 columns: level columns `c0` to `c0 + 63` are loaded, and level column `c0 + 64` goes into the same map column as `c0`, so moving the window one column to the right means overwriting column `c0` with column `c0 + 64`.

```
level columns:   ... 60 61 62 63 64 65 66 ...
map columns:     ... 60 61 62 63  0  1  2 ...        (c mod 64)
```

Nothing in the hardware treats the wrap specially: level columns 63 and 64 are neighbours on the screen in map columns 63 and 0. Only the host knows where the **seam** is, the point in the ring where the newest loaded column meets the oldest. The columns on either side of the seam are 63 columns apart in the level, so the seam must never be on the screen.

## 2. When to write

A view at `sx` touches level columns `sx >> 3` to `(sx + 319) >> 3`: 40 columns when `sx` is a multiple of 8, **41** otherwise (a partial tile at each side). Vertically it touches 30 rows, or **31**. The ring holds 64 of each, so at least **23 columns and 33 rows** are spare.

Two rules from Chapter 5.3 decide when a write may happen:

1. The scroll is shadowed. A `SET_SCROLL` sent during frame N-1 latches at the next vertical blanking and takes effect in frame N.
2. Map writes are **not** shadowed. A write sent during frame N-1 can show in the lines of frame N-1 that have not been drawn yet.

So send the writes for frame N during frame N-1, together with its `SET_SCROLL`, and make sure they are complete before the blanking. This is the **one frame ahead** rule. The writes must not change anything frame N-1 shows. A new column `c` (scrolling right) overwrites level column `c - 64`, which must lie left of frame N-1's view. Put the other way round:

> **The view on screen and the next view must fit in the ring together:** from the first column of one to the last column of the other is at most 64 columns, and likewise for rows.

A view is at most 41 columns wide, so a step of up to **23 tiles (184 pixels) in X** and **33 tiles (264 pixels) in Y** always meets the rule, in any direction and at any sub-tile offset. (Depending on `sx mod 8`, the exact limit is 185 to 192 pixels.) Both views are inside the 64 loaded columns, so the seam, which is just outside them, is never on screen.

You may also write **further ahead** than one frame: load columns before the view needs them, as long as the ring still holds the view on screen. That sends no fewer bytes in total, but it lets a host move a write to a quieter frame.

When a step needs new columns *and* new rows, write the **columns first**, then the rows, and fill each new row from the columns the ring holds *after* the column writes. A new column is filled using the old set of rows, so where it crosses a row that is about to be replaced it briefly holds a stale entry. That entry is off screen (it is in a column frame N-1 does not show), and the row write replaces it before frame N.

### The bytes

A column is a 132-byte packet: opcode, length, layer, column number and 64 entries of 2 bytes. A camera moving `v` pixels per frame to the right needs a column every `8 / v` frames, `16.5 * v` bytes per frame on average, plus a 7-byte `SET_SCROLL` per layer. At 2 pixels per frame that is 33 bytes a frame. Redrawing the screen through the framebuffer would cost 76,800.

The average is not the whole story: the bytes come in lumps. A frame that writes nothing sends only its scrolls (14 bytes for two layers), and a frame that writes one column or row sends 146. A frame that needs a column and a row at once would send 278, which is why the demo of section 9 never lets that happen. (Chapter 5.3's `WRITE_TILE_MAP` could write a column too, but it writes consecutive entries, so a column takes 64 one-entry packets, 448 bytes. That is why the column gets a command of its own.)

## 3. The commands

Chapter 5.2 reserved `0x40` to `0x4F` for streamed maps. This chapter defines two. The updated spec is [`protocol.md`](protocol.md) next to this chapter: two rows in section 3, capability bit 7 in section 8, a new section 13. Replace your `docs/protocol.md` with it.

| Op | Name | len | Payload |
| --- | --- | --- | --- |
| `40` | `WRITE_TILE_COLUMN` | 130 | layer, column (0 to 63), 64 entries of 16 bits |
| `41` | `WRITE_TILE_ROW` | 130 | layer, row (0 to 63), 64 entries of 16 bits |

The rules:

- **Entries** have the format of Chapters 5.3 and 5.5 (tile, flips, priority), low byte first. Entry `k` of a column goes to map row `k`, map entry `k * 64 + column`. Entry `k` of a row goes to map column `k`, map entry `row * 64 + k`. The 12-bit map address is just two 6-bit numbers **concatenated**: `{k, column}` for a column, `{row, k}` for a row.
- **Length** is exactly 130. Any other length is skipped whole and sets `BAD_LEN`, as for every fixed-length command. The length is the packet's second byte, so the engine knows before the first entry whether to write.
- **Numbers that name nothing.** A layer above 1, or a column or row above 63, makes a correct-length packet write nothing and set **no** flag: the rule Chapter 5.3 set for a tile command naming a layer that does not exist. The number is **not** wrapped: 64 does not mean 0, so a host that forgets its modulo does not silently overwrite column 0.
- **Immediate and streamed**, like `WRITE_TILE_MAP`: the entries are written as they arrive. A packet cut short by a frame start leaves the whole entries that arrived written and sets `TRUNC`.
- **Capability bit 7** (`TILE_STREAM`): `gpu_link` now answers `GET_INFO` with `47 01 FF 02 40 01 F0 00 ER`. `uart_gpu` keeps `0F 00`.
- `0x42` to `0x4F` stay unknown (`BAD_OP`).

## 4. The command engine

**Nothing changes outside `cmd_engine`.** The commands drive the tile-map write port of Chapter 5.3 (`o_tm_we`, `o_tm_layer`, `o_tm_addr[11:0]`, `o_tm_wdata[15:0]`), so `gpu_link`, `fb_scanout`, `tile_layers` and every testbench keep their ports. In `gpu_link` only the capability word changes, to `16'h02FF`.

In `cmd_engine`, `WRITE_TILE_MAP` is already streamed: its payload bytes are handled as they leave the FIFO, in the state that reads payload bytes, and a well-formed packet never reaches the execute state. Add a second streamed case of the same kind:

1. **Length table.** `0x40` and `0x41` are known opcodes that are never valid in the execute state (like `0x12` and `0x13`): only a packet whose length is not 130 gets there, and it sets `BAD_LEN`.
2. **Stream condition.** A packet streams if `op` is `0x40` or `0x41` **and** `len == 130`: a wire from the opcode and length registers.
3. **Bytes.** Payload bytes 0 and 1 land in `arg[0]` (layer) and `arg[1]` (number) as usual and stay there for the whole packet. From byte 2 on they come in pairs: at an even `n` save the low byte; at an odd `n` you have the high byte and write entry `k = (n >> 1) - 1`.
4. **Write.** Strobe `o_tm_we` only if `arg[0] <= 1` **and** `arg[1] < 64`, with `o_tm_layer = arg[0][0]`, `o_tm_wdata = {high, low}`, and the address `{k[5:0], arg[1][5:0]}` for `0x40` or `{arg[1][5:0], k[5:0]}` for `0x41`. At the last byte (`n + 1 == len`) go back to reading opcodes, as `WRITE_TILE_MAP` does.

The map's write port on `clk_sys` is used only by the engine, at most one entry per two payload bytes, so nothing competes for it.

## 5. The host: `level_stream.py`

The commands only move bytes. The bookkeeping is the hard part: which columns does the next view need, and what goes in them? `host/level_stream.py` keeps it for one layer:

```python
s = StreamedLayer(layer, level, width, height)   # level(c, r) -> map entry, 0 outside
packets = s.load(sx, sy)      # 64 column writes: the ring for a view at (sx, sy)
packets = s.plan(sx, sy)      # what to send now for the NEXT frame's view at (sx, sy)
```

The object remembers `c0` and `r0`, the first level column and row the ring holds, and the view on screen. `plan()` compares the next view with the ring and returns the new columns, then the new rows. A new column holds the level rows `r0` to `r0 + 63` **in map row order**: entry `k` is the level row whose number is `k mod 64`. Read the one-line expression for it in `column()`; most bugs in streaming code live in an index like that one. Rows are the same with columns and rows exchanged, using the column window after it moved.

Before returning, `plan()` asserts the rule of section 2 (the column or row about to be overwritten is not on screen) and that the view stays inside the level. A layer whose level is exactly 64 rows high can pass `wrap_y=True`: it wraps vertically by itself and only its columns are streamed.

Call `plan()` once per frame, and send its packets followed by the `SET_SCROLL`s of the same view, in one go. `stream_demo.py` does this in `frame_packets()`.

## 6. The Python model

`host/gpu_model.py` (a backward-compatible replacement) knows the two commands and the new capability word, `0x02FF`:

```python
import gpu_model as g
g.write_tile_column(layer, column, entries)    # exactly 64 entries
g.write_tile_row(layer, row, entries)
gpu = g.Gpu()                                  # gpu.tmap[layer][row * 64 + column]
```

`Gpu.line_stream()` is section 3 in a dozen lines. The self-check still passes: `.venv/bin/python host/gpu_model.py` (it needs Chapter 4.6's `host/raster_model.py` next to it, as before).

## 7. Simulate

A game would scroll through the 256-tile level at 2 pixels per frame: 864 frames. The simulation takes about 20 seconds per video frame, so that would take five hours. Instead the test **takes big steps**: about 157 pixels (20 tiles) per frame, close to the 23-tile limit, so the level goes by in 11 frames. Every column of the level is still streamed in, about 20 column packets per step, layer 0's ring is rewritten three times over, and the hard cases (the view crossing from map column 63 to 0, writes landing one tile beyond the visible edge, rows and columns in the same step) come up in every frame instead of every few hundred. A one-pixel step is not in the test; `stream_demo.py --model` covers it.

The testbench is Chapter 5.5's: `vga_timing`, `gpu_link` and `fb_scanout` wired as in `top_artyz7.v`, and an SPI master playing a stream of commands. Copy `ref_streaming.py` to `sim/py/` (it imports `ref_layers.py` and `host/level_stream.py`), `tb_frame_streaming.v` to `sim/` and `level_stream.py` to `host/`. Write the stream once (commit `sim/streaming_stream.hex` and `sim/streaming_replies.hex`), add `FRAME_TESTS += streaming` and run:

```sh
.venv/bin/python sim/py/ref_streaming.py --stream sim
make frame-streaming
```

**Setup.** The palette, a framebuffer picture (seen through the holes in the tiles), the first 64 tiles of Chapter 5.3's graphics, and two levels. Layer 0's is 256 x 96 tiles, every cell different from its neighbours, about one in seven empty; it is loaded as 64 columns. Layer 1's is 96 x 64 tiles, 60% empty, and is loaded as 64 **rows**, so both commands are used on both layers. Then packets that must change nothing: column and row writes for layers 2 and 3, for columns 64 and 200 and for rows 64 and 130 (a design that wraps the number or takes the layer from one bit changes a column or row near the top left of f0); five packets of the wrong length (payloads of 128, 129, 131, 0 and 1 bytes) and a reserved `0x42`. A `GET_INFO` must then show `BAD_LEN | BAD_OP`, and `CLEAR_ERRORS` clears them.

**Fourteen consecutive frames**, `f0` to `f9` and `fa` to `fd` (hex digits). The writes for frame `fN` and its two `SET_SCROLL`s are sent from line 100 of the frame before. Layer 1 moves at a quarter of layer 0's speed in X and 1.5 times in Y, wrapping in its 64 rows.

| Frame | Layer 0 scroll | Writes | Tests |
| --- | --- | --- | --- |
| f0 | 0, 0 | none | the loaded rings |
| f1 | 157, 0 | none | the view still fits in the ring: only the scroll moves |
| f2 to f7 | 314, 40 to 1099, 265 | 16 to 20 columns each (layer 1: 0 to 5) | new columns over the oldest; the view crosses from map column 63 to 0 |
| f8 to fa | 1256, 310 to 1570, 400 | 19 to 20 columns and 5 to 6 rows | the rows wrap too: level rows 64 and up replace rows 0 and up |
| fb | 1728, 528 | 19 columns, 16 rows | the end of the level (column 255, row 95); the largest step |
| fc | 1536, 400 | none | back to the left: the ring still holds those columns |
| fd | 1380, 240 | 20 columns leftwards, 2 rows upwards | writing in the other direction |

The steps carry up to 5.3 KB, about 270 lines of SPI time at 5 MHz, so they finish long before the blanking at line 480. The testbench fails if one does not (a symptom of `READY` held low). The send line is `MID_LINE` in `ref_streaming.py`; setting `STREAM_MID_LINE=160` in the environment of both `--stream` and `make` moves it, a legal variant that must also pass.

The testbench dumps `build/frame_streaming_f0.ppm` to `fd.ppm`, sends a last `GET_INFO` and expects `47 01 FF 02 40 01 F0 00 00`: no flag, so every streamed packet was well formed. It checks the latency and sync alignment as before. It takes about 7 minutes. As in earlier chapters it does not look at the pixels: `compare.py` does. `ref_streaming.py` draws each frame twice, once with the model and once directly from the level functions with no map or ring, and stops if they disagree, so the planner and the model cannot agree on a wrong picture.

**When it fails**, look at the first frame and pixel `compare.py` reports:

- Wrong from f0, nearly everywhere: column and row addresses exchanged, an entry `k` that lands in row or column `k + 1` (the picture of a layer shifted by one tile), or the entry's two bytes swapped (the tile grid right, the tiles wrong).
- f0 right but a later frame wrong along a tile column or row: an off-by-one in your own copy of the planner (`ref_streaming.py`'s own check stops a wrong planner before the stream is written).
- f0 wrong in one column or row of tiles near the top left (map column 0 or 8, row 0, 1 or 2): a packet that must change nothing wrote (layer 2 or 3, number 64, 130 or 200), or a wrong-length packet was streamed.
- The testbench itself fails with X/Z pixels from f0, or one layer's tiles show in the other: `o_tm_layer` taken from the wrong place for one command (layer 1 is loaded only by rows, so if rows go to layer 0, layer 1's map is never written).
- `GET_INFO` replies differ: the capability word, or a flag that should (or should not) be set.

**The other testbenches.** Everything from Chapter 5.5 still passes, with these edits:

- **`tb_frame_layers`, `tb_frame_sprite_table` and `tb_frame_compositor`**: the capability byte is now `FF`. In `main()` of `sim/py/ref_layers.py`, `sim/py/ref_sprite_table.py` and `sim/py/ref_compositor.py`, change `0x7F` to `0xFF` in the `GET_INFO` assertion, and rerun the three with `--stream sim` (this rewrites their `*_replies.hex`). Change the `7F` in the three testbenches' failure messages to `FF`. Their pictures do not change.
- **No new ports**, so no tie-offs.
- **`tb_gpu_fuzz`**: this chapter ships replacements for `tb_gpu_fuzz.v` (capability byte `FF`, the only change) and `ref_fuzz.py` (`0x40` and `0x41` are known opcodes, with wrong-length rounds, valid packets with numbers in and out of range, and a cut packet). Regenerate with `.venv/bin/python sim/py/ref_fuzz.py sim` and commit the new `sim/fuzz_*` files.

## 8. On the board

Nothing changes in `top_artyz7.v`. The reference build (openXC7):

| Resource | Chapter 5.5 | Chapter 5.6 | Of |
| --- | --- | --- | --- |
| LUTs | about 7,060 | about 7,100 | 106,400 |
| Flip-flops | about 2,300 | about 2,300 | 106,400 |
| RAMB36E1 | 54 | 54 | 140 |
| RAMB18E1 | 2 | 2 | 280 |
| DSP48E1 | 20 | 20 | 220 |

The new commands cost 30 to 40 LUTs in the engine and no memory. `clk_pix` closes above 150 MHz against 25. `clk_sys` closed at 58 MHz in one build of this chapter's design and at 67 MHz in another, against 50 required; as in Chapter 5.5 the critical path is in the triangle rasterizer, and placement moves it around from build to build.

## 9. The demo

`host/stream_demo.py` scrolls a 256 x 96-tile level of ground, grass and trees on layer 1 while a small far layer of hills (written once, never streamed) moves at half speed on layer 0. The camera moves right 2 pixels per frame and follows the terrain up and down by at most 1 pixel per frame. Each frame the script calls `plan()`, sends the packets and both `SET_SCROLL`s right after `VSYNC` rises, and counts the bytes.

The camera adds one trick: when a step would expose a new column and a new row in the same frame, it delays the vertical step by one frame. The view is a pixel late for one frame, which nobody sees, and no frame ever needs more than one 132-byte packet.

Try it without the board first. `.venv/bin/python host/stream_demo.py --model` scrolls through the whole level (864 frames), checks after every frame that each tile of the view is in the ring where the screen will look for it, and prints:

```
864 frames: 46.5 bytes per frame on average, at most 146, at least 14
first load of 64 columns: 8448 bytes; spread over 864 frames that adds 9.8 per frame
PASS: stream_demo (model)
```

Check the numbers. A column is needed every 4 frames (33 bytes a frame), a row at most every 8 frames (16.5), and the two scrolls are 14: at most 63.5 on average. The run averages 46.5 because the first 23 columns are already loaded and the terrain is mostly flat (192 columns and 21 rows in all). 651 frames carry only the scrolls (14 bytes) and 213 carry one packet as well (146 bytes): **every frame is under 200 bytes**. Without the camera's trick 16 frames would carry a column and a row, 278 bytes. The script fails if any frame reaches 200. (It imports Chapter 5.3's `layer_demo.py`, which must be in `host/`. Add `--ppm frame.ppm` to see the last frame.)

Then, with Chapter 5.1's wiring:

1. Program the bitstream. Nothing changes on screen.
2. `.venv/bin/python host/spi_info.py` lists `TILE_STREAM` (`caps 02FF`).
3. `.venv/bin/python host/stream_demo.py --freq 5000000`. For about 14 seconds the ground, grass and trees of a 2,048-pixel level roll by, rising and falling with the terrain, with the hills behind at half speed. There should be no seam, no tile popping in at the right edge and no tear. The script prints the bytes per frame and `PASS: stream_demo`.
4. Experiments: remove the camera's trick and see the largest frame become 278; raise `SPEED` to 12 (1.5 columns a frame, 198 bytes on average before the scrolls) and see the script fail; raise it to 190 and watch `plan()`'s assertion fire; send each frame's packets one frame late and watch the right edge; skip the assertion and overwrite a column still on screen, and watch it change in the middle of a frame.

## Testbench requirements

**Modules.**

- **`gpu_link`**: Chapter 5.5's ports, unchanged; `GET_INFO` replies `47 01 FF 02 40 01 F0 00 ER`. **`fb_scanout`** and everything inside it: unchanged.
- **Commands** `0x40` `WRITE_TILE_COLUMN` and `0x41` `WRITE_TILE_ROW` as in `protocol.md` sections 3 and 13: length exactly 130 (any other length skipped whole, nothing written, `BAD_LEN`); payload layer, number, 64 little-endian entries; entry `k` of a column to map entry `k * 64 + number`, of a row to `number * 64 + k`; a layer above 1 or a number above 63 writes nothing and sets no flag (no wrapping); entries written immediately through the tile-map port as they arrive; a packet cut by a frame start leaves the whole entries written and sets `TRUNC`; `0x42` to `0x4F` unknown (`BAD_OP`).
- **Video rules**: those of Chapters 5.3 to 5.5, unchanged (scroll modulo 512, latched in vertical blanking).
- **Python**: this chapter's `host/gpu_model.py` (with Chapter 4.6's `raster_model.py`), `host/level_stream.py` and `host/stream_demo.py` (with Chapter 5.3's `layer_demo.py`); `ref_streaming.py` imports `ref_layers.py` and `level_stream.py`.

**`tb_frame_streaming`** (about 7 minutes, `make frame-streaming` from the repo root). Plays `sim/streaming_stream.hex` over SPI at 5 MHz (checking `READY` before each SPI frame), dumps fourteen consecutive 640 x 480 frames at 8 bits per channel, and prints `PASS: tb_frame_streaming (...)`. It fails on a frame-dump geometry or X/Z error, unequal sync/`de` delay (any latency from 1 to 24 clocks is accepted), a status byte without the `01` signature, a FIFO overflow, a step not sent completely before blanking, or `GET_INFO` replies other than `sim/streaming_replies.hex` (`... 00 06` after the malformed packets, `... 00 00` at the end). It does **not** check pixels; the `compare.py` step that follows does.

**`tb_gpu_fuzz`**: Chapter 5.5's test with this chapter's `tb_gpu_fuzz.v` and `ref_fuzz.py`. **`tb_frame_layers`, `tb_frame_sprite_table` and `tb_frame_compositor`** need the capability edit of section 7.

**Files:** `protocol.md` to `docs/`; `tb_frame_streaming.v` and `tb_gpu_fuzz.v` to `sim/`; `ref_streaming.py` and `ref_fuzz.py` to `sim/py/`; `gpu_model.py`, `level_stream.py` and `stream_demo.py` to `host/`. Add `FRAME_TESTS += streaming`. Then:

```sh
.venv/bin/python sim/py/ref_layers.py --stream sim          # after the 0xFF edits
.venv/bin/python sim/py/ref_sprite_table.py --stream sim
.venv/bin/python sim/py/ref_compositor.py --stream sim
.venv/bin/python sim/py/ref_streaming.py --stream sim
.venv/bin/python sim/py/ref_fuzz.py sim
make frame-streaming
```
