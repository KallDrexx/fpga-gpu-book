# Chapter 4.8 - Sprite Blitter

Chapter 4.7 drew shapes from numbers: three corners in, a triangle out. Games and user interfaces mostly draw something else, small pictures that were made once and are shown over and over: a ball, a ship, a font glyph. The usual name is a **sprite**, and copying one into the framebuffer is a **blit** (block transfer).

The serial link is the problem. A 16 x 16 sprite is 256 bytes. At 115,200 baud the link carries about 11,500 bytes per second, so twenty sprites per frame would need 5 KB and take 0.4 s: two frames per second. The fix is to send each picture **once**, keep it in GPU memory, and afterwards say only which picture goes where. A `BLIT` packet is 8 bytes, so twenty of them are 160 bytes, about 14 ms.

By the end of this chapter you will have:

- `rtl/common/cmd_engine.v` extended with a 32 KB **asset RAM**, an **asset table**, `UPLOAD_ASSET` (opcode `0x09`) and `BLIT` (opcode `0x0A`),
- `host/gpu_model.py` replaced by this chapter's version, which models both,
- `sim/tb_gpu_blit.v` passing: odd-sized images uploaded in one or many packets, then hundreds of blits (partly and fully off screen, all flips, transparent and opaque, overlapping) match the model by framebuffer and `FB_CRC`,
- Chapters 4.2 to 4.7's testbenches still passing,
- `projects/4.8-blit/` on the board, with `host/blit_demo.py` animating 20 sprites after a single upload, using only `BLIT` commands to draw each frame, and the hardware CRC equal to Python's.

## 1. Asset RAM and the asset table

An **asset** is a w x h image of palette indices: one byte per pixel, stored row by row, top row first. That is the framebuffer's format, so a blit is a copy with a few rules, and the same palette colors it.

The assets live in a **32 KB asset RAM**: 32,768 bytes, which is 8 block RAMs of the Z7-20 (a `RAMB36E1` holds 4 KB of 8-bit data). The size comes from Phase 5's memory budget, which keeps this RAM for the sprite images of the scene engine. The two framebuffers already use 38 block RAMs, so the whole design needs 46 of the 140.

The host does not choose addresses. It names an image with a small number, its **asset id** (0 to 15), and the GPU keeps a table:

| id | valid | base (15 bits) | w (8 bits) | h (8 bits) | size = w x h (16 bits) |
| --- | --- | --- | --- | --- | --- |
| 0 | 1 | 0 | 80 | 40 | 3,200 |
| 1 | 1 | 3,200 | 1 | 1 | 1 |
| 2 | 0 | | | | |

`base` is where the image starts in the asset RAM. Sixteen entries are a few hundred bits, so flip-flops or a small distributed RAM hold the table. A `next_free` register points at the first unused byte of the asset RAM. It starts at 0.

Width and height are one byte each, so 1 to 255. The whole image has to fit in 32,768 bytes, so the largest possible are 128 x 255 (32,640 bytes) or 181 x 181. A 255 x 255 image can never fit.

## 2. `UPLOAD_ASSET`

| Opcode | Command | Length | Payload |
| --- | --- | --- | --- |
| `0x09` | `UPLOAD_ASSET` | 5 + n | id, w, h, offset (16-bit, little-endian), then n data bytes, 0 ≤ n ≤ 250 |

A payload is at most 255 bytes (Chapter 4.2), so a 3,200-byte image goes up in several packets. The scheme here is the simplest one that works: **every packet says which image it belongs to and where its bytes go.** `id`, `w` and `h` repeat in every packet, and `offset` counts bytes from the start of the image. Packet k of an 80 x 40 image carries 250 bytes at offset 250k; the last one is shorter. There is no separate "begin" or "end" command: the packet with offset 0 creates the asset.

The GPU decides what a packet does from its header alone, checking the rows of this table from the top:

| Packet | Effect |
| --- | --- |
| length under 5 | ignored |
| w = 0 and h = 0, n = 0 | **reset**: every asset is forgotten and `next_free` = 0 (the RAM bytes are not erased) |
| w = 0 or h = 0 (any other), id ≥ 16, or offset + n > w x h | ignored |
| offset = 0, asset `id` exists and its w x h equals this w x h | **replace in place**: same base, the table takes the new w and h; the data goes to base |
| offset = 0, otherwise, and `next_free` + w x h ≤ 32,768 | **allocate**: base = `next_free`, `next_free` moves up by w x h; the data goes to base |
| offset = 0, otherwise | **refused** (the RAM is full): nothing changes, an old asset `id` stays as it was |
| offset > 0, asset `id` exists with exactly this w and h | **continue**: the data goes to base + offset |
| offset > 0, otherwise | ignored |

"Ignored" means the payload is read and nothing changes. Three consequences:

- **Space is only given back by a reset.** An image replaced by one of another size stays in the RAM unused. Re-uploading the same size, even as another shape (7 x 13 for 13 x 7), costs nothing.
- **A refused upload is harmless.** Its later packets name a w and h that the table doesn't hold for that id, so they write nothing.
- **Order doesn't matter after the first packet.** Re-sending a lost packet is safe, and an image can even go up backward: allocate with a header-only packet (offset 0, n = 0), then send the data from the last packet to the first.

**Allocated is not the same as written.** After an allocation the image holds whatever the RAM held before. Upload every byte of an image before you blit it. The testbench never blits half an image, because those pixels are not defined, and the Python model counts any such read (`Gpu.blit_unwritten`) so the stream generator can check.

Two examples, byte by byte:

    09 09  03 02 02 00 00  01 00 02 03          asset 3, 2 x 2, offset 0, 4 data bytes [1 0 / 2 3]
    09 37  01 64 03 FA 00  ...50 data bytes...  asset 1, 100 x 3, offset 250: the last 50 of its 300 bytes

The first allocates a 4-byte asset at `next_free`. The second continues asset 1, whose first 250 bytes went up in a packet that began `09 FF 01 64 03 00 00` (length 255 = 5 + 250, offset 0). The second packet's length is 5 + 50 = 55 = `0x37`, and `FA 00` is offset 250.

`host/gpu_model.py` has `upload_asset(id, w, h, data, offset)` for one packet, `upload_packets(id, w, h, data)` for a whole image (a list of packets, 250 bytes each), and `asset_reset()`.

### In the engine

The engine's 16-byte argument buffer can't hold 250 data bytes, and doesn't need to. It stores the five header bytes as usual and checks them as soon as the fifth arrives, because the length byte already told it n. After that each data byte goes straight from the FIFO to the RAM:

| State | Does |
| --- | --- |
| `BYTE`, payload byte 4 (the fifth) of an `UPLOAD_ASSET` of length ≥ 5 | go to `UPH` instead of `FETCH` |
| `UPH` | apply the table above. For a packet that writes: set `up_addr` (the first RAM address) and `up_ok`; update the table and `next_free`. Otherwise clear `up_ok`. If n = 0, the packet is done |
| `BYTE`, payload byte 5 or later | `if (up_ok)` write the byte at `up_addr` and add 1; either way the byte is consumed, and the last one ends the packet |

`UPH` needs w x h. One 8 x 8 multiplier is cheap (a DSP block, or a few dozen LUTs), but keep it out of `UPH`'s path: w and h arrived bytes earlier, so `r_size <= r_arg[1] * r_arg[2];` on every clock leaves `UPH` only a register to read.

```verilog
// the asset RAM: one write port, one read port on the same address
reg [7:0] amem [0:32767];
always @(posedge i_clk) begin
    if (a_we) amem[a_addr] <= a_wdata;
    a_rdata <= amem[a_addr];            // read-first: one clock after the address
end
```

Write it that way (registered read, no reset) and `yosys` maps it onto eight `RAMB36E1`. Uploads write it and blits read it, through the same address, and the two never overlap, because the engine runs one command at a time.

## 3. `BLIT`

| Opcode | Command | Length | Payload |
| --- | --- | --- | --- |
| `0x0A` | `BLIT` | 6 | id, x, y (signed 16-bit, little-endian), flags |

`BLIT` copies asset `id` into the **back buffer** (Chapter 4.5) with its top-left corner at (x, y). Sprite pixel (sx, sy), with 0 ≤ sx < w and 0 ≤ sy < h, goes to screen position (x + sx, y + sy), and shows the image pixel that the flags pick:

| Flag bit | Name | Meaning |
| --- | --- | --- |
| 0 | `FLIP_X` | mirror left to right: screen column x + sx shows image column w − 1 − sx |
| 1 | `FLIP_Y` | mirror top to bottom: screen row y + sy shows image row h − 1 − sy |
| 2 | `OPAQUE` | draw index 0 as well |
| 3-7 | | ignored |

- **Transparency.** Color index **0 is transparent**: such a pixel is not written, and the framebuffer keeps what was there. That is what lets a round ball sit on a background. `OPAQUE` turns it off, for a solid rectangle such as a background tile, or when you really want index 0. A fixed index and a flag keep the asset format simple, and cost one of the 256 palette entries.
- **Flips.** An image of the rows `[1 0 2 / 3 4 0]` shows as `[2 0 1 / 0 4 3]` with `FLIP_X`, as `[3 4 0 / 1 0 2]` with `FLIP_Y`, and as `[0 4 3 / 2 0 1]` with both. A flipped pixel keeps its transparency, and the size on screen never changes.
- **Clipping.** A sprite may lie partly or entirely off any edge. Only pixels with 0 ≤ X ≤ 319 and 0 ≤ Y ≤ 239 are written, as with `PLOT`. Compute positions with 17 bits: x + sx can reach 32,767 + 254.
- **Unknown assets.** An id that is not in the table (never uploaded, refused, forgotten by a reset, or 16 or more) draws nothing. A packet with a length other than 6 is skipped, like any wrong-length packet.
- **Order.** Sprites are drawn in the order they arrive, so a later `BLIT` lands on top of an earlier one and shows it through its own transparent pixels. One `BLIT` writes a screen pixel at most once.

Two examples, byte by byte, for the 2 x 2 asset 3 uploaded above:

    0A 06  03  0A 00 14 00  00      asset 3 at (10, 20), no flags: index 1 at (10, 20), 2 at (10, 21), 3 at (11, 21)
    0A 06  03  FF FF FF FF  07      asset 3 at (-1, -1), both flips and OPAQUE: only (0, 0) is on screen, and it shows image pixel (0, 0), index 1

`host/gpu_model.py` has `blit(id, x, y, flags)` and the flag constants `FLIP_X`, `FLIP_Y` and `OPAQUE`. Its `blit_points(w, h, x, y, flags)` yields every sprite pixel as (X, Y, source index): that function is the definition of the flips.

## 4. One pixel per clock

The loop runs two counters over the sprite and, for every pixel, reads the asset RAM, tests the pixel and writes the framebuffer. The flips can be handled without any extra arithmetic in the loop.

**Read the asset in order, and walk the screen backward.** Whatever the flags, the engine reads the image's bytes from `base` up to `base + w x h − 1`: the source address is a counter that only ever adds 1. A flip changes the *destination* instead:

| Flag | Start at | Step |
| --- | --- | --- |
| none | X = x, Y = y | X + 1 along a row, then Y + 1 |
| `FLIP_X` | X = x + w − 1 | X − 1 along a row |
| `FLIP_Y` | Y = y + h − 1 | Y − 1 for each new row |

Check it: with `FLIP_X`, the first byte of a row, image column 0, lands at X = x + w − 1, the right end, which is where column 0 belongs when mirrored, and the last byte, column w − 1, lands at X = x. Since a `BLIT` writes every screen pixel at most once, the order in which they land doesn't matter.

Each clock the `BLIT` state handles one pixel:

```verilog
S_BLIT: begin
    a_addr  <= sa;                      // ask the RAM for the next source byte
    sa      <= sa + 1;
    q1_v    <= 1'b1;                    // this pixel is now in the pipeline
    q1_on   <= (cxp >= 0) && (cxp <= 319) && (cyp >= 0) && (cyp <= 239);
    q1_da   <= {cyp[7:0], 8'd0} + {cyp[7:0], 6'd0} + cxp[8:0];     // y*320 + x
    if (ix == b_w - 1) begin            // last pixel of a row
        ix <= 0;  cxp <= b_xs;  cyp <= b_fy ? cyp - 1 : cyp + 1;
        iy <= iy + 1;
        if (iy == b_h - 1) r_state <= S_FETCH;
    end else begin
        ix <= ix + 1;  cxp <= b_fx ? cxp - 1 : cxp + 1;
    end
end
```

`cxp` and `cyp` are the screen position, signed 17-bit, and `b_xs` remembers where each row starts. The address is Chapter 4.4's two shifts and an add; it is only used when the pixel is on screen, so the low bits are enough.

The states before it set this up. `EXEC`, for a `BLIT` of length 6 whose id is valid in the table, copies the entry's base, w and h and the flags into `b_` registers and goes to `BSET`; any other `BLIT` goes back to `FETCH`. `BSET` sets `sa = base`, `ix = iy = 0`, `cyp = FLIP_Y ? y + h − 1 : y`, and `cxp = b_xs = FLIP_X ? x + w − 1 : x`.

With `UPH`, `BSET` and `BLIT` the engine has 15 states, so Chapter 4.3's 4-bit state register still fits. If your design needs two more, widen it: a state number of 16 in a 4-bit register silently becomes 0 (`FETCH`), and neither iverilog nor yosys stops you.

**The RAM answers one clock late.** A block RAM gives the byte for an address on the clock after the address was presented, so a pixel takes three clocks to travel, and a new one enters every clock:

| Clock | The pixel that entered at clock 0 |
| --- | --- |
| 0 | `a_addr <= sa`; its screen address and on-screen flag go into `q1` |
| 1 | the RAM has latched the byte (`a_rdata`); the screen address moves to `q2` |
| 2 | `if (q2_v && q2_on && (a_rdata != 0 || b_opq))` write `a_rdata` at the screen address |

`q1_v <= 1'b0;` goes with the defaults at the top of the clocked block, so `q1_v` is 1 only on clocks when `S_BLIT` ran. The rest goes after the case statement, where it wins over those defaults:

```verilog
q2_v <= q1_v;  q2_on <= q1_on;  q2_da <= q1_da;
if (q2_v && q2_on && (a_rdata != 8'd0 || b_opq)) begin
    o_fb_we <= 1'b1;  o_fb_addr <= q2_da;  o_fb_wdata <= a_rdata;
end
```

That `if` is the transparency test: a zero index (without `OPAQUE`) never raises `o_fb_we`. The last writes land two clocks after the loop ends. Fetching and executing even the shortest next packet takes five clocks or more, so nothing else touches the framebuffer or the RAM in between.

**Cost.** The loop spends one clock on every sprite pixel, **including the ones that are off screen or transparent**. That is simple and costs at most 32,640 clocks (0.65 ms) for the biggest possible image. Skipping the off-screen part would need a second clipping calculation; the testbench allows it if you are curious.

**Back-pressure.** As in Chapter 4.7, the engine **fetches nothing while a `BLIT` runs**, and the FIFO takes the bytes that arrive meanwhile: the biggest `BLIT` lets about 160 bytes arrive at the test baud. `o_fb_buf` is still `~o_flip_req`, so a `BLIT` writes the back buffer with no new logic.

**Reset.** Leave `S_BLIT` (and `S_BSET`) out of Chapter 4.7's reset exception: `BTN0` sends them back to `FETCH` and the rest of the sprite is not drawn. Unlike the rasterizer, nothing keeps running afterwards except the two pixels already in the pipeline, so the next command starts cleanly.

## 5. The Python model

Replace `host/gpu_model.py` with this chapter's version. It still needs Chapter 4.6's `host/raster_model.py`. It adds:

- the opcodes `UPLOAD_ASSET = 0x09` and `BLIT = 0x0A`, the flag constants, `ASSET_RAM = 32768`, `N_ASSETS = 16`,
- the builders `upload_asset`, `upload_packets`, `asset_reset` and `blit`, and `blit_points`,
- in `Gpu`: the asset RAM `ram`, the table `assets` (id → base, w, h), `next_free`, and counters `blit_count`, `blit_drawn`, `upload_rejected` and `blit_unwritten`. `Gpu.upload()` is Section 2's table in 30 lines, the definition your hardware must match.

Nothing else changes, so older streams give the same files. Run the self-check:

```sh
.venv/bin/python host/gpu_model.py
```

    gpu_model self-check ok

Check that Chapters 4.2 to 4.7 still get the same files. These commands rewrite the files already in `sim/` byte for byte, so `git status` stays clean (the last one takes about 15 seconds):

```sh
for n in cmd rect line flip_sync tri_draw; do .venv/bin/python sim/py/ref_$n.py sim; done
```

Copy `ref_blit.py` to `sim/py/` and run it once. Commit the six files it writes:

```sh
.venv/bin/python sim/py/ref_blit.py sim
```

    ref_blit: 78248 stream entries, 78164 bytes, 675 BLITs (650 of an uploaded asset, 462112 pixels), 49 images uploaded, 54 FB_CRC replies, 4 FLIPs -> sim/blit_*
      random phase: 49 fully off screen, 96 partly off, 13 unknown ids, 9 re-uploads (0 refused); refused uploads in all: 11

## 6. Simulate: `tb_gpu_blit`

Copy `tb_gpu_blit.v` to `sim/`. It is Chapter 4.7's harness: real serial at 2.5 Mbaud, two framebuffers with write counters on your draw ports, every reply checked. The asset RAM is *inside* your design, so the testbench sees an upload only through what a later `BLIT` draws. That is the right test: it checks what the host can observe.

```
$ make test TESTS="tb_uart_gpu tb_gpu_rect tb_gpu_line tb_flip_sync tb_tri_raster tb_gpu_tri tb_gpu_blit"
  PASS  tb_uart_gpu
  PASS  tb_gpu_rect
  PASS  tb_gpu_line
  PASS  tb_flip_sync
  PASS  tb_tri_raster
  PASS  tb_gpu_tri
  PASS  tb_gpu_blit
$ cat build/sim/tb_gpu_blit.log
tb_gpu_blit: throughput window 1: 2743 writes in 3199 clocks from first to last (3296 allowed)
tb_gpu_blit: throughput window 2: 1 writes in 1 clocks from first to last (19 allowed)
tb_gpu_blit: throughput window 3: 218 writes in 254 clocks from first to last (273 allowed)
tb_gpu_blit: throughput window 4: 240 writes in 240 clocks from first to last (781 allowed)
tb_gpu_blit: throughput window 5: 800 writes in 1580 clocks from first to last (2116 allowed)
tb_gpu_blit: throughput window 6: 8590 writes in 11520 clocks from first to last (33166 allowed)
PASS: tb_gpu_blit (78164 bytes sent, 54 FB_CRC replies match, 1073087 pixel writes, 10 counting checks, 6 throughput windows at one sprite pixel per clock)
```

`tb_gpu_blit` takes about three minutes. The stream, in order:

1. **Directed uploads and blits** in 17 groups, each followed by an `FB_CRC`:
   - an 8 x 8 image in all four flips, transparent and opaque, and in all eight flag combinations on a background, once with the reserved flag bits set;
   - 1 x 1 assets (one of index 0) at the four corners and just off screen;
   - the odd sizes 13 x 7, 25 x 10 (exactly one 255-byte packet), 17 x 15 (250 + 5 bytes), 255 x 1, 1 x 255, 80 x 40 (13 packets), and 40 x 50 sent **backward**;
   - one asset clipped one pixel at a time across each edge, others across every corner;
   - ten overlapping sprites, with transparency showing what is under;
   - assets replaced in place by another shape of the same size, and by a bigger image, with every other asset checked to be intact;
   - **malformed** uploads and blits (every "ignored" row of Section 2's table, a reset with data, a 255 x 255 image, `BLIT` of unknown ids and with lengths 0, 5 and 7, and packets hidden inside a `NOP` and an unknown opcode), after which the assets must still draw as before;
   - `BLIT` mixed with `FILL_RECT`, `PLOT`, `DRAW_TRI`, `DRAW_LINE` and `SET_PALETTE` with no fences, then `FLIP`s and `BLIT`s into the other buffer;
   - a **reset**, after which every old id draws nothing and the freed RAM takes new assets.
2. **Five throughput windows,** each one `BLIT` alone: 80 x 40 on screen, 1 x 1, 255 x 1 flipped, 1 x 255 partly off the top, 40 x 50 partly off the left. The limit is w x h + 2h + 16 clocks from the first write to the last, like Chapter 4.7's B + 2R + 16 with the whole sprite as the box: one clock per sprite pixel, on screen or not, and a little spare per row. Setup isn't counted, and a design that skips off-screen pixels finishes sooner.
3. **Ten counting checks:** the write counters are zeroed, one `BLIT` runs (flips, transparent and opaque, on and off screen), and no pixel may have been written twice, nor any of the front buffer.
4. **A full asset RAM.** After a reset, a 255 x 255 upload is refused. A 128 x 255 image goes up in 131 packets (32,640 bytes) and a 16 x 8 one fills the last 128 bytes. Then new ids are refused, a smaller image for id 8 is refused (it needs new space), and its continuing packets do nothing: the two stored images still draw. An 8 x 16 image replaces the 16 x 8 one in place. The sixth throughput window is the 128 x 255 asset partly off screen. **Back-pressure:** the biggest `BLIT` (32,640 clocks), a full-screen `FILL_RECT` and 20 more `BLIT`s, sent without looking at `o_ready`. An engine that fetched during the blit would run the rectangle in the middle of it. Then another reset.
5. **Random:** 12 assets of random sizes (1 to 255 wide, many odd, some thin; 0, 20 or 50 % transparent; a third uploaded backward), then 340 random `BLIT`s: on screen, partly off any edge or corner, fully off, at the int16 limits, with random flags, a few of unknown ids. Nine re-uploads (same size in place, same size another shape, new size) and older commands are mixed in, with an `FB_CRC` every 50 and two `FLIP`s. At the end every asset is blitted in all eight flag combinations, which shows that no later upload damaged an earlier one.

At the end it compares both framebuffers and the palette with the model. A wrong reply is reported with its group from `sim/blit_labels.txt`. Typical failures and their first lines:

- **`FLIP_X` starts one pixel too far right** (X = x + w instead of x + w − 1): `FB_CRC reply 2 is 42a7ffd5, expected ef8b691b. It covers: asset 0 (8x8, single packet): the four flips, transparent and opaque`.
- **Transparency ignored** (index 0 written): the same reply 2, and `6837 framebuffer bytes wrong at the end; first ... (x=186 y=0): 00, expected 71`.
- **`OPAQUE` ignored:** reply 2 again, `first ... (x=55 y=0): 01, expected 00`.
- **Clip test `X <= 320`:** `framebuffer write to address 76800 (past 76,799)`, then `FB_CRC reply 7 ... asset 3 clipped one pixel at a time across every edge`. With **`Y <= 238`** instead, one bottom row is missing: `FB_CRC reply 4 ... 1x1 assets at the four corners and just off screen`.
- **Wrong source stride** (a byte skipped at the end of each row): `FB_CRC reply 2 is xxxxxxxx`, because it reads RAM that no upload wrote.
- **Offset ignored** (continuing packets written at base): `FB_CRC reply 5 ... assets 3 (13x7), 4 (25x10, one 255-byte packet), 5 (17x15, two packets)`, the first group with a multi-packet image.
- **Every new asset placed at address 0**, or `next_free` never moved up: the 1 x 1 assets overwrite each other, so `FB_CRC reply 4 ... 1x1 assets at the four corners`, and then almost every reply.
- **`BLIT` length not checked:** `FB_CRC reply 12 ... malformed UPLOAD_ASSET and BLIT packets`.
- **Two clocks per sprite pixel:** everything matches, then `FAIL: tb_gpu_blit (throughput window 1: the BLIT took 6397 clocks from first to last write, more than 3296: aim for one sprite pixel per clock)`.

The testbench accepts any pixel order and any latency: a design that walks the screen forward and reads the source backward for flips, with one more pipeline stage, passes the same stream. Keep running Chapters 4.2 to 4.7's testbenches too: the new states and the argument buffer are easy to break. `make frame-flip` still passes too. The older testbenches leave some `uart_gpu` ports unconnected, and `iverilog -Wall` warns about them; that is expected.

## 7. The Arty top

Nothing in the top changes: the asset RAM and the table are inside `cmd_engine`, and `uart_gpu`'s ports are the ones from Chapter 4.7. Copy `projects/4.7-tri/` to `projects/4.8-blit/`, set `PROJ := blit`, add `tb_gpu_blit` to `TESTS`, and run `make`. There is no new source file.

The reference build took 1 minute 36 seconds:

```
== blit: resource use (xc7z020clg400-1) ==
  SLICE_LUTX:    6039/ 106400     5%
  SLICE_FFX:    1517/ 106400     1%
  CARRY4:     433/  13300     3%
  ...
  DSP48E1:      20/    220     9%
  ...
  RAMB18E1:       1/    280     0%
  RAMB36E1:      46/    140    32%
  Max frequency for clock 'clk_pix': 134.14 MHz (PASS at 25.00 MHz)
  Max frequency for clock 'clk_sys': 69.93 MHz (PASS at 50.00 MHz)
```

- **`RAMB36E1` went from 38 to 46.** The eight new ones are the asset RAM. If you see 38 and thousands of extra LUTs instead, `yosys` did not recognize your RAM as a block RAM. It needs a registered read (`a_rdata <= amem[a_addr]` in a clocked block) and no reset. A read through a `wire` or `assign` makes 262,144 flip-flops or LUT-RAM bits, and the build fails or crawls.
- **About 1,150 more LUTs, 230 more flip-flops, 70 more carry chains and 1 more DSP block** than Chapter 4.7. The DSP block is the w x h multiplier; the LUTs are mostly the 16-entry table (kept in distributed RAM) with its comparisons, and the 17-bit position arithmetic. Your numbers depend on how you wrote it.
- **`clk_sys` reaches 70 MHz** (Chapter 4.7: 64 MHz). The rasterizer is still the slowest path. Read the `clk_sys` line of `make report` anyway.

## 8. On the board

Wiring and programming are as in Chapter 4.2: adapter TXD to JA pin 1, RXD to JA pin 2, GND to JA pin 5, monitor on HDMI TX. Put `SW0` down (double buffered), run `make prog`, and the Chapter 4.1 test image appears. Without a board, `blit_demo.py --model` prints the CRCs the hardware must reproduce:

```
$ .venv/bin/python host/blit_demo.py --model --frames 4
frame   0: CRC 8f288218
frame   1: CRC 50355587
frame   2: CRC 881a7fbb
frame   3: CRC 36397794
```

1. Run

    ```
    $ .venv/bin/python host/blit_demo.py /dev/ttyUSB0
    uploading 6612 bytes of images (once) ...
    animating 20 sprites: only BLIT commands per frame (36 of them), FLIP after each picture, 10 s
    ...
    PASS: blit_demo (N frames checked)
    ```

    The upload takes about 0.6 s, and the script checks it with an `FB_CRC` that must still be the blank screen's: the images only went into the asset RAM. Then a field of dark blue squares appears, with 20 sprites moving over it: five red balls with a white highlight, five green arrows, five pink flags and five blue crosses. They were sent once and are never sent again. Each frame is 16 opaque `BLIT`s of one 80 x 60 tile, which cover the whole picture (so every frame starts clean), then the 20 sprites, then `FLIP`.

2. **Look at the flips.** A sprite is mirrored left to right when it moves left, and top to bottom when it moves up. The arrows must always point the way they travel, the flags' poles stay behind them, and a ball's highlight jumps to the other side whenever it turns. A sprite that turns into the wrong mirror image has the wrong flip.
3. **Look at the edges.** Sprites leave through the right edge and come back through the left one a piece at a time, and bounce a few pixels past the top and bottom. Cut-off pieces must be clean. A piece that wraps to the other side of the screen is an address bug; a sprite that vanishes the moment its corner leaves is a clipping bug.
4. **Look at the background.** The squares must be seamless where the 16 tiles meet. A seam shows a source-stride or clipping bug in the opaque `BLIT`.
5. **Check the CRCs.** Each frame is 288 bytes of `BLIT`s (36 x 8), then `FB_CRC`, `FLIP` and `FB_CRC` as in Chapter 4.7. The link is the limit: the 294 bytes take 25 ms at 115,200 baud, so the `FLIP` misses the next vertical blanking and waits for the one after, and the script shows a new picture every second monitor frame, 30 per second. (With `BAUD` raised to 921,600 in the top and `--baud 921600`, if your adapter supports it, it reaches 60.) Sending the same 20 sprites as pixels would take about 9 KB, 0.8 s per frame. The script records every reply and afterwards compares every tenth frame with the model (`--check-every 1` for all):

    ```
    frame    0: hardware CRC 8f288218, Python CRC 8f288218
    frame   60: hardware CRC e6020cc8, Python CRC e6020cc8
    frame  120: hardware CRC dd6290e1, Python CRC dd6290e1
    ```

    If a CRC differs, the script prints the first frame that did. `tb_gpu_blit` should have failed too; run it.
6. LD1 (overflow) stays dark, because the script waits for each frame's replies.
7. If no reply comes, press BTN0, check the wiring and the baud rate, and try again.

**Done when:** `tb_gpu_blit` passes, `tb_uart_gpu`, `tb_gpu_rect`, `tb_gpu_line`, `tb_flip_sync`, `tb_tri_raster` and `tb_gpu_tri` still pass, the 20 sprites move cleanly with the right flips and edges on the monitor after one upload and only `BLIT`s each frame, and `blit_demo.py` reports hardware CRCs equal to Python's.

## Testbench requirements

**Modules you provide** (in `rtl/common/`, found by `-y rtl/common`): `uart_gpu #(CLK_HZ, BAUD)` with Chapter 4.7's ports and behavior, all of whose requirements still hold (the asset RAM is inside; the ports do not change), plus:

- **Opcode `0x09` `UPLOAD_ASSET`**, length 5 + n with 0 ≤ n ≤ 250: `id, w, h, offset (16-bit little-endian)`, then n data bytes. It behaves as Section 2's table, which is `gpu_model.Gpu.upload`: reset, ignore, replace in place (same w x h), allocate at the first free byte, refuse when the 32,768-byte RAM is full (changing nothing), continue (same w and h). Ids 0 to 15. Space is never reused except by a reset.
- **Opcode `0x0A` `BLIT`**, length exactly 6: `id, x, y (signed 16-bit), flags`. It writes the back buffer (`o_fb_buf`) as `gpu_model.blit_points` says: pixel (sx, sy) goes to (x + sx, y + sy) and reads image column w − 1 − sx with `FLIP_X` (bit 0), row h − 1 − sy with `FLIP_Y` (bit 1); index 0 is not written unless `OPAQUE` (bit 2) is set; only 0 ≤ X ≤ 319 and 0 ≤ Y ≤ 239 are written; flag bits 3 to 7 are ignored; an id that is not an asset draws nothing. Positions are full signed 16 bits.
- **No pixel is written twice** by one `BLIT`, and **no pixel of the buffer on the screen** is written (`i_flip_ack` names it). The testbench counts writes per address after ten single `BLIT`s.
- Commands run in order, and no bytes are fetched or lost while a `BLIT` runs. In the back-pressure section about 180 bytes arrive right behind the biggest `BLIT` (32,640 sprite pixels), without regard to `o_ready`. Uploads of 255-byte packets arrive back to back at 2.5 Mbaud.
- Throughput: in each of six windows, one `BLIT` of a w x h asset may take at most w x h + 2h + 16 clocks from its first framebuffer write to its last, in any pixel order. Setup, and anything before the first write, isn't counted.
- Draw ports as in Chapter 4.2: no write at an address of 76,800 or above, and no X on the write enables.
- The stream never blits an asset that is not completely uploaded, so bytes no upload wrote may hold anything.

**Files from this chapter:** `tb_gpu_blit.v` to `sim/`, `ref_blit.py` to `sim/py/`, `gpu_model.py` (replacing Chapter 4.7's) and `blit_demo.py` to `host/`. They also need Chapter 4.6's `raster_model.py`, Chapter 4.1's `ref_scanout.py`, `fb_test.hex` and `pal_test.hex`. Run `.venv/bin/python sim/py/ref_blit.py sim` once to write `sim/blit_stream.hex`, `blit_replies.hex`, `blit_fb0.hex`, `blit_fb1.hex`, `blit_pal.hex` and `blit_labels.txt`, and commit them. The stream uses Chapter 4.7's entries, including the `3NNN` window limits with their `4HHH` prefixes.

**Testbench:** `sim/tb_gpu_blit.v`, top module `tb_gpu_blit`, run from `build/sim/` by `make test` (about three minutes: the stream is 78,000 bytes). It reads `../../rtl/common/fb_test.hex`, `pal_test.hex` and `../../sim/blit_*`. It uses a 50 MHz clock and 2.5 Mbaud, models two framebuffers with a write counter per byte and an acknowledge four clocks behind `o_flip_req`, and checks every reply byte, both framebuffers, all 256 palette entries, the ten counting checks, writes to the front buffer, `o_overflow`, out-of-range and X writes, and the throughput windows. It prints one `PASS` or `FAIL` line. The watchdog is 2 s simulated. By hand:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_gpu_blit.vvp sim/tb_gpu_blit.v
cd build/sim && vvp -n tb_gpu_blit.vvp
```
