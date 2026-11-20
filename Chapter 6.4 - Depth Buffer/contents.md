# Chapter 6.4 - Depth Buffer

Chapter 6.3 gave every pixel of a shaded triangle a depth, and then threw it away: triangles simply overwrite each other in drawing order. That is the painter's algorithm, and it is wrong whenever the order that is right changes from pixel to pixel. Two triangles that pass through each other cannot be ordered at all; neither is "behind" the other.

The fix is a **depth buffer** (a z-buffer): remember, for every screen pixel, the depth of whatever was drawn there last, and draw a new pixel only if it is nearer. The picture then no longer depends on the order the triangles arrive in, except where depths are exactly equal.

By the end of this chapter you will have:

- a `depth_unit` (a 16-bit, 320x240 depth buffer in block RAM with a read-compare-write pipeline) inside the command engine,
- five new commands, `SET_DEPTH`, `CLEAR_DEPTH`, `CLEAR_ALL`, `GET_STATS` and `CLEAR_STATS` (`0x61`-`0x65`), and capability bit 11,
- three performance counters (pixels produced, pixels written, clocks) that tell you pixels per clock,
- `host/gpu_model.py` with a bit-exact depth buffer, and `docs/protocol.md` with a new section 16,
- `tb_depth_unit` passing (the read-after-write hazard, tested head on), `tb_depth_stats` passing (the counters), and `make frame-depth` passing (six frames, two intersecting triangles among them, identical to the model),
- the older tests still passing with the edits of Section 9.

## 1. The depth test

Chapter 6.3 fixed the convention: `z` is an unsigned 16-bit number, **smaller is nearer**, 0 is the nearest depth and 65,535 the farthest. The depth buffer holds one `z` per pixel, and the clear value is 65,535: nothing is farther than that.

A shaded pixel with depth `z` at a place where the buffer holds `old`:

    pass  =  z < old                 draw the pixel; store z

The comparison is **strict**: equal depths fail, so of two pixels with the same depth the one drawn first stays. The other choice, `z <= old`, lets the last one win ties. Neither is wrong, but the choice is visible wherever two triangles have equal depths, so the model, the hardware and every test use `<`. A side effect is handy: drawing the same triangle again changes nothing.

**What decides the picture.** Everything is decided pixel by pixel from the interpolated depth, an integer that Chapter 6.3's model defines exactly. So "the intersection line" is not computed anywhere: it is the border between the pixels where one triangle's rounded depth is smaller and those where the other's is. Where the two are equal the triangle drawn first wins, so drawing the pair in the other order changes exactly those pixels; `ref_depth.py` checks that.

**Modes.** `SET_DEPTH` (`0x61`, one byte) sets two mode bits that stay until the next `SET_DEPTH`: bit 0 `TEST`, bit 1 `WRITE`. Both are 0 at power-up, so everything from Chapter 6.3 works as before.

| `TEST` | `WRITE` | A shaded pixel is... | Used for |
| --- | --- | --- | --- |
| 1 | 1 | drawn if nearer; its depth is stored | normal opaque 3D |
| 1 | 0 | drawn if nearer; depth left alone | decals and overlays that must not hide what comes after |
| 0 | 1 | always drawn; its depth is stored | a backdrop that later triangles must be tested against |
| 0 | 0 | always drawn; depth left alone | the painter's algorithm of Chapter 6.3 |

Only `DRAW_TRI_SHADED` uses depth. A flat `DRAW_TRI` has no `z`, so it always draws and never changes the buffer; `PLOT`, `FILL_RECT`, `DRAW_LINE` and `BLIT` ignore it too. Keeping the mode in a register, not in each packet, keeps the 22-byte triangle packet unchanged.

## 2. The memory

One depth value per pixel, 76,800 of them:

    76,800 x 16 bits = 1,228,800 bits = 150 KB

A RAMB36E1 holds 2,048 words of 16 bits when it is used 16 bits wide (the two parity bits of the 18-bit mode stay unused), so the buffer needs 76,800 / 2,048 = 37.5, that is **38 RAMB36** (or 75 RAMB18). Together with the 55 blocks of Chapter 5.7 that is **93 of the 140**, which the build below confirms. The address is the framebuffer's, `y * 320 + x`.

There is **one** depth buffer, not one per color buffer. Only the buffer being drawn needs depth; the one on the screen needs color only, so a `FLIP` swaps colors and leaves depth where it is. Consequence: depth carries over from frame to frame until you clear it, and a `CLEAR` does not clear it (Section 5).

**Ports.** Each clock the pipeline reads one word (the old depth of the pixel entering) and writes one word (the new depth of the pixel leaving), at different addresses. That is the simple dual-port shape: one read port, one write port, same clock. The block RAM has a one-clock read latency (the address is registered, the data appears a clock later), which is the first stage of the pipeline.

**Power-up.** Block RAM starts at 0 on the Xilinx parts and `gpu_model` starts at 0 too, but no test depends on it (an uninitialized RAM passes them all) and no host should: with the test on and depth 0, no pixel is ever nearer. Always clear before the first tested draw. So leave the RAM without an `initial` loop: yosys unrolls a 76,800-word loop very slowly (one review build spent about 25 minutes in it, against under a minute without).

## 3. The read-compare-write pipeline

A pixel arrives from the rasterizer with its address, its depth, and the color it would be drawn in. The unit must read the old depth, compare, and then write both the color and the new depth, and it must do so for **one pixel every clock** to keep the rasterizer's speed. So it is a pipeline:

| Stage | What happens to a pixel |
| --- | --- |
| 1 | the address, `z`, color and mode bits are registered; the RAM read is issued with this address |
| 2 | the old depth arrives from the RAM; compare `z < old` (or pass, if test is off); a passing pixel makes `o_we` high with its address and color; if write is on, `z` goes to the RAM at the end of this clock |

Two clocks from the rasterizer's `o_valid` to the decision; the command engine registers the color write once more before it reaches the framebuffer port. The framebuffer is written exactly as before (`o_fb_we`, `o_fb_addr`, `o_fb_wdata`), only two clocks later, so the rest of the design is untouched and no port changes anywhere.

```
module depth_unit #(parameter N = 76800) (
    input  wire        i_clk,
    input  wire        i_valid,  input wire [16:0] i_addr,  input wire [15:0] i_z,  input wire [7:0] i_color,
    input  wire        i_test,   input wire i_zwrite,
    input  wire        i_cw_en,  input wire [16:0] i_cw_addr, input wire [15:0] i_cw_data,   // clear port
    output wire        o_we,     output wire [16:0] o_addr,   output wire [7:0] o_color,
    output wire        o_busy                                      // pixels in flight
);
```

`i_test` and `i_zwrite` travel with each pixel, like its address. The engine feeds the unit every pixel of **both** triangle commands: for a flat triangle it passes `i_test = i_zwrite = 0`, which makes the unit a plain delay, so there is one pixel path and one timing. When `o_busy` is high the engine stays in its triangle state, so a triangle command is over only when its last pixel has been written.

## 4. The hazard

Pixel B follows pixel A one clock behind, at the **same address**. B's read is issued in the clock in which A is compared, and A's new depth reaches the RAM only at the end of that clock, so B reads the depth from before A. If A passed and wrote, B is compared with a value that is no longer true.

    clock:      t          t+1                t+2
    A           stage 1    stage 2: compare,  
                           write z_A at end
    B                      stage 1: read      stage 2: compare with the old
                           (same address)     depth: WRONG, A's z is in the RAM

**The fix is forwarding.** Keep the address and depth of the last write in a register. When the pixel being compared has that address, use the remembered depth instead of what the RAM returned: a 17-bit comparator and a 16-bit multiplexer. A pixel two or more clocks behind A reads the RAM after A's write and needs nothing. Forwarding also hides what the RAM returns when one address is read and written in the same clock, which some block RAM modes leave undefined.

A **deeper pipeline** (say a register between the RAM and the comparator, for timing) has more writes in flight and must forward from each of them, the newest first. The other cure is a **stall**: hold a pixel back while its address matches one in flight. Chapter 4.6's rasterizer cannot be stopped, so a stall would need a small FIFO in front of the unit; this chapter forwards. The unit test accepts all of these, because it checks only which writes come out and in what order, not when.

**When can it happen?** Not within one triangle: the walk visits each pixel once. Only between triangles, the last pixels of one against the first of the next. In this chapter's engine not even then, because a triangle command ends only when the unit is empty (`o_busy` low), and the next triangle's first pixel comes dozens of clocks later. The forwarding is there because the unit must be right for any pixel stream, and the stream will change: overlapping the next triangle's setup with the current one's pixels (Section 7), two rasterizers, or a walk in tiles all bring same-address pixels close together. A depth buffer that is correct only because of the controller's timing is a bug waiting for an optimization.

**How it is tested.** `tb_depth_unit` drives the unit directly: 32,006 random pixels from a pool of 16 addresses (half of them repeat the address before), with depths from a pool of 8 (so ties are common), all four modes, back to back or with gaps, each verdict checked against a plain array. It counts 6,474 pixels that follow a depth write to the same address. The frame test cannot provoke the hazard in this engine, so this unit test is the only one that does.

## 5. Clearing depth with the color

A new frame must start with a cleared color buffer and a cleared depth buffer. As two separate passes that is 2 x 76,800 clocks, 3.1 ms at 50 MHz, a tenth of a 30 Hz frame. The two buffers have separate write ports, so one pass can write both: `CLEAR_ALL` (`0x63`) does, in 76,800 clocks. `CLEAR_DEPTH` (`0x62`) writes only the depth, and the plain `CLEAR` (`0x02`) still writes only the color.

The engine's clear loop counts addresses 0 to 76,799 and sets a write enable per clock; `CLEAR_ALL` sets both enables. The depth side uses the unit's **clear port**, which takes the place of the pixel write while nothing is in flight. Clear values: 65,535 for a normal frame, but any value is allowed, and a clear to 30,000 hides everything at depth 30,000 or more, a cheap far clipping plane.

**What not to do:** forgetting to clear depth between frames. The old frame's depths stay, nothing new is nearer than a wall that was there last frame, and the screen freezes where the old picture was. `f5` of the frame test shows depth surviving a `FLIP` and a `CLEAR`.

## 6. The commands

| Opcode | Command | Length | Payload |
| --- | --- | --- | --- |
| `0x61` | `SET_DEPTH` | 1 | mode: bit 0 test, bit 1 write; bits 7:2 ignored |
| `0x62` | `CLEAR_DEPTH` | 2 | z (unsigned 16-bit, low byte first) |
| `0x63` | `CLEAR_ALL` | 3 | color, then z as for `CLEAR_DEPTH` |
| `0x64` | `GET_STATS` | 0 | reply 12 bytes (Section 7) |
| `0x65` | `CLEAR_STATS` | 0 | |

Wrong lengths are skipped and set `BAD_LEN`; `0x66`-`0x6F` stay unknown. **Capability bit 11** (`DEPTH`) is set by this chapter: `GET_INFO` from `gpu_link` reports `47 01 FF 0F ...`; `uart_gpu` still reports `0F 00`. `docs/protocol.md` has the table rows, the new section 16 and the updated rules.

**Display lists.** `SET_DEPTH`, `CLEAR_DEPTH`, `CLEAR_ALL` and `CLEAR_STATS` have no reply and may be in a list, so a 3D frame list (Chapter 5.7's game mode) can start with `CLEAR_ALL` and `SET_DEPTH`. **`GET_STATS` may not**: it makes a reply and a list has nowhere to send one, so it is skipped with `BAD_OP` like `GET_INFO` and `FB_CRC`.

**Engine changes**, all inside `cmd_engine`: the mode bits (two registers, written by `SET_DEPTH`), a `depth_unit` with the triangle pixels in and the framebuffer write out, the clear loop's second enable, the counters, one more reply format, `0x64` in the list's forbidden set, and `S_TRI` waiting for `o_busy` as well as `o_done`. Nothing outside `cmd_engine` changes except the capability constant in `gpu_link`.

## 7. The performance counter

How fast is the rasterizer, really? Three 32-bit counters, 0 at power-up, answer it:

| Counter | Counts |
| --- | --- |
| `pixels` | pixels the rasterizer produced, for flat and shaded triangles: the covered pixels of the clamped bounding box, **before** the depth test |
| `written` | pixels written to the color buffer: `pixels` minus those the test refused |
| `clocks` | clocks the engine spent drawing triangles: from the clock after a triangle starts until its last pixel is written, setup included |

Nothing else counts: not 2D drawing, not clears, not the waiting in `FLIP`. All three saturate at 4,294,967,295 and none wraps. `GET_STATS` (`0x64`) replies with all three, low byte first (12 bytes, so it fits the 16-byte SPI reply queue) and **changes nothing**; `CLEAR_STATS` (`0x65`) sets them to 0. A typical use is `CLEAR_STATS` at the start of a frame and `GET_STATS` at the end; `host/depth_demo.py` does that.

**What is exact and what is not.** `pixels` and `written` follow from the geometry and the depth rule, and the Python model computes them exactly. `clocks` depends on the design (the setup, the pipeline depth), so a test can check only a window: at least `pixels`, because the rasterizer produces at most one pixel per clock, and at most the allowance of Chapter 6.3 per triangle (box + 2 x rows + 60 for a flat one, 2 x box + 1,000 for a shaded one) plus a few clocks for the drain. `tb_depth_stats` checks exactly that, and that `GET_STATS` twice in a row gives the same thing.

**Expected throughput.** The reference design takes **one clock per pixel of the bounding box, plus setup**, and refused pixels cost as much as written ones, since the walk visits them anyway. Measured with `GET_STATS` on triangles with legs of n pixels:

| Triangle | n = 8 | 16 | 32 | 64 | 128 |
| --- | --- | --- | --- | --- | --- |
| covered pixels | 36 | 136 | 528 | 2,080 | 8,256 |
| flat, clocks | 90 | 298 | 1,098 | 4,234 | 16,650 |
| shaded, clocks | 201 | 409 | 1,209 | 4,345 | 16,761 |
| shaded, pixels per clock | 0.18 | 0.33 | 0.44 | 0.48 | 0.49 |

The clocks are the box, (n + 1)^2, plus 9 for a flat triangle and 120 for a shaded one. Two things keep the rate below 1. A triangle covers at most half of its bounding box, so walking the box gives at most about 0.5 pixel per clock, which large triangles approach; and the setup dominates small ones. This corrects the Phase 6 estimate: 200 triangles of 500 pixels are not 100,000 clocks (2 ms) but about 200 x (1,089 + 120) = 242,000 clocks, **4.8 ms at 50 MHz**. With 1.5 ms for `CLEAR_ALL` that is 6.4 ms, 19% of a 33 ms frame: still comfortable.

The depth test itself costs no pixel rate: the pipeline takes a pixel every clock, refused pixels cost as much as written ones, and `tb_gpu_tri`'s one-clock-per-box-pixel limits still pass. Two optional speed-ups follow from the numbers: walk only the covered span of each row (up to 2x for large triangles), and overlap the next triangle's setup with the current one's pixels (10% at 500 pixels, more than half the time for tiny ones). The second is the one that needs Section 4's forwarding.

## 8. The Python model and the tests

`host/gpu_model.py` adds the five commands (`set_depth()`, `clear_depth()`, `clear_all()`, `get_stats()`, `clear_stats()`, `parse_stats()`), the depth array `Gpu.depth`, the mode registers, the counters `stat_cov` and `stat_wr` and the rule of Section 1. The model cannot know `clocks`; `GET_STATS` reports `Gpu.stat_clocks` (0 unless a test sets it). Run it to self-check; `host/raster_model.py` does not change.

```sh
.venv/bin/python sim/py/ref_depth.py --stream sim     # once: writes sim/depth_stream.hex, depth_replies.hex
.venv/bin/python sim/py/ref_depth.py --stats sim      # once: writes sim/depth_stats_stream.hex, depth_stats_expect.hex
make test TESTS="tb_depth_unit tb_depth_stats"
make frame-depth
```

**`tb_depth_unit`** (about a second): Section 4.

    PASS: tb_depth_unit (32006 pixels, 16155 drawn, 15851 rejected, 6474 back-to-back same-address pixels where the first one wrote depth)

**`tb_depth_stats`** (about 10 seconds): `uart_gpu` over a serial line, no framebuffer model. It sends triangles with the depth test on and off, flat and shaded, partly or wholly refused, off-screen and degenerate, 2D drawing and clears in between, and checks five `GET_STATS` replies against `depth_stats_expect.hex` (pixels and written exactly, clocks in the window of Section 7) and two more for "unchanged".

    PASS: tb_depth_stats (5 GET_STATS checks, 2 repeat checks; last reply: 6240 pixels, 4160 written, 13035 clocks)

Your clocks will differ from the reference's and still pass. The first check, `GET_STATS` right after `CLEAR_STATS`, must be all zeros, clocks included; the fourth, one triangle after a second `CLEAR_STATS`, catches a counter that `CLEAR_STATS` misses.

**`tb_frame_depth`** and `make frame-depth` (about 4 minutes): the harness of Chapter 6.3's `tb_frame_shaded` plays `sim/depth_stream.hex` through `gpu_link` and dumps six frames; `compare.py` checks them against `ref_depth.py`, which draws them with `gpu_model.py`:

- **f0, f1:** two intersecting shaded triangles (green and yellow) plus two with equal constant depth (pink and cyan), over a background, with test and write on: f0 draws them in one order and f1 in the other. `ref_depth.py` checks both against an independent painter written straight from `raster_model.shade_pixels`, and that f0 and f1 differ in exactly the 287 pixels where the nearest depths tie, and nowhere else.
- **f2:** four panels, each three triangles with different modes (both on; test only; write only; neither, then write only): what each mode leaves in the buffer for the third triangle to meet.
- **f3:** clears: `CLEAR_ALL` with a color and depth 45,000, a triangle at 46,000 that must stay invisible (it shows if the clear's z bytes are swapped), a near triangle, a `CLEAR_DEPTH` and a far triangle that is visible only because the depth was cleared, a `CLEAR_DEPTH` to 30,000 and two triangles, one nearer (drawn) and one farther (refused everywhere).
- **f4:** back-to-back triangles on the same pixels: sixteen with the same footprint and scrambled depths (several equal, several one apart), twelve three-pixel triangles at two places, a flat triangle that must ignore depth, and a shaded triangle that is only visible if the flat one left depth alone.
- **f5:** depth persists. The host sends four bad packets (`SET_DEPTH` with 2 bytes, `CLEAR_DEPTH` with 3, `CLEAR_ALL` with 4, unknown `0x66`) that must change nothing, then runs a list: `CLEAR` (which must leave depth alone), triangles at z = 60,000 that are refused where frame f4 left depth at or below that, equality included, and a `GET_STATS` that a list may not hold (`BAD_OP`), then `FLIP`. The two `GET_INFO` replies say capabilities `0FFF`, flags `00` and then `06`.

```
PASS: tb_frame_depth (latency 7 clocks, 745 SPI frames, 3193 bytes; build/frame_depth_f0 to f5 .ppm written)
PASS: compare depth (6 frames match exactly)
```

What failures usually mean:

| Symptom | Likely cause |
| --- | --- |
| `tb_depth_unit` fails, frames pass | forwarding missing |
| `tb_depth_unit` and f0 fail | forwarding without its valid bit: a stale address from an earlier triangle matches |
| f0, f1, f4 and f5 wrong, `tb_depth_stats` `written` wrong | `<=` instead of `<` (ties go to the last triangle) |
| every frame wrong | the test ignored or reversed; the depth address differs from the pixel's |
| f2, f4 and f5 wrong | depth stored for refused pixels too |
| only f2 wrong | `TEST` and `WRITE` bits swapped |
| only f3 wrong | z byte order in `CLEAR_DEPTH` or `CLEAR_ALL` |
| f4 and f5 wrong | a flat triangle tests or writes depth |
| only f5 wrong | `CLEAR` clears depth too; a bad-length depth packet was executed |
| `tb_frame_depth`: the second `GET_INFO` reply is wrong (it holds counter bytes) | `GET_STATS` allowed in a list |
| `tb_gpu_fuzz` replies wrong in a "wrong length ... alone" round | a wrong-length depth command sets no `BAD_LEN` |
| `tb_depth_stats` clocks outside the window | the counter counts other commands or idle clocks, or `CLEAR_STATS` misses it |

## 9. Older files and resources

- **Replace** `host/gpu_model.py`; add `host/depth_demo.py`. `host/raster_model.py` is unchanged.
- **Fuzz:** replace `sim/tb_gpu_fuzz.v` (it expects caps `FF 0F`) and `sim/py/ref_fuzz.py` (the depth commands among the valid packets, in lists and with wrong lengths; any whole `GET_STATS` the garbage happens to contain is turned into `CLEAR_STATS`, because the model cannot know the clock count); run `ref_fuzz.py sim`.
- **Capabilities:** in `rtl/common/gpu_link.v` change `.CAPS(16'h07FF)` to `.CAPS(16'h0FFF)`. In `host/spi_info.py` append `"DEPTH"` to the `CAPS` list.
- **Older frame tests:** change `0xFF, 0x07, 0x40` to `0xFF, 0x0F, 0x40` in `sim/py/ref_layers.py` (1 place), `ref_sprite_table.py` (1), `ref_compositor.py` (1), `ref_streaming.py` (2), `ref_display_lists.py` (3) and `ref_shaded.py` (2), and `FF 07 40 01` to `FF 0F 40 01` in the failure messages of `sim/tb_frame_layers.v`, `tb_frame_sprite_table.v`, `tb_frame_compositor.v`, `tb_frame_streaming.v`, `tb_frame_display_lists.v` and `tb_frame_shaded.v` (that one twice, plus `07FF` to `0FFF` in its header comment).
- **`ref_shaded.py`** also sends `g.packet(0x61, bytes(22))` as its "unknown opcode": `0x61` is a command now, and with 22 bytes it would be `BAD_LEN`, not `BAD_OP`. Change it to `0x66`; in its docstring change `0x61` to `0x66` and `07FF` to `0FFF`.
- **Regenerate** with `--stream sim` for `ref_layers`, `ref_sprite_table`, `ref_compositor`, `ref_streaming`, `ref_display_lists`, `ref_shaded` and with `ref_replay.py --stream sim` (no edit): only the `*_replies.hex` files change, plus `shaded_stream.hex` (the new unknown opcode).
- **Tie-offs:** none. No port of any module changes: the depth buffer is inside `cmd_engine`, so `fb_scanout`, `gpu_link`'s ports, `uart_gpu`'s ports and every older testbench stay as they are.
- **Makefile:** `FRAME_TESTS += depth`; `ref_depth.py` goes to `sim/py/`.

**Resources.** One run of the full Arty design (`yosys synth_xilinx`, `nextpnr-xilinx`), against the build of Chapter 6.3 that kept the depth lane alive:

| | Chapter 6.3 (z lane kept) | with the depth buffer |
| --- | --- | --- |
| LUTs (`SLICE_LUTX`, nextpnr) | 9,273 | 9,888 |
| Flip-flops | 3,016 | 3,267 |
| DSP48E1 | 36 | 36 |
| RAMB36E1 + RAMB18E1 | 55 + 2 | **93 + 2** |
| `clk_sys` maximum | 58.6 MHz | 60.4 MHz |

The 38 extra RAMB36 are the buffer, as computed. The unit, the counters and the clear logic cost about 600 LUTs and 250 flip-flops, and the design has 47 RAMB36 to spare. An independently written design gave 10,002 LUTs, 3,292 flip-flops, the same 36 DSP and 93 + 2 block RAMs, and 61.2 MHz. The timing is one run; runs differ by a few MHz and the margin over 50 MHz is still thin, as in Chapter 6.3 (the critical path was not inspected).

**The setup multipliers.** Chapter 6.3 left open whether the 16 DSP slices of the triangle setup should become one shared multiplier, for about 20 more setup clocks. This chapter keeps them: they are 7% of the part's 220, and the 20 clocks would be 17% of a 120-clock setup. Revisit it only if a later chapter runs short of DSPs.

**Done when:** `tb_depth_unit`, `tb_depth_stats` and `make frame-depth` pass; Chapter 4.6 to 6.3's tests still pass (`make test`, `make frame`); `depth_demo.py` shows two triangles passing through each other on the monitor; and you can say why the hazard cannot happen in this engine, why the unit handles it anyway, and what the counter says about the rasterizer's speed.

## Phase 6 conventions set here

Later chapters use these without restating them.

- **Depth test:** `z < stored` (strict; equal fails, the first drawn stays); clear value 65,535. One depth buffer, 320 x 240 x 16 bits, address `y * 320 + x`, 38 RAMB36, inside `cmd_engine`; `FLIP` and `CLEAR` leave it alone.
- **Modes:** `SET_DEPTH` bit 0 test, bit 1 write, power-up 0; only `DRAW_TRI_SHADED` uses depth; a flat triangle and the 2D commands ignore it.
- **Commands:** `0x61` `SET_DEPTH` (1), `0x62` `CLEAR_DEPTH` (2: z), `0x63` `CLEAR_ALL` (3: color, z), `0x64` `GET_STATS` (0; reply 12 bytes), `0x65` `CLEAR_STATS` (0); `0x66`-`0x6F` free. Allowed in lists: all but `GET_STATS`. Capability bit 11; caps now `0x0FFF`; bits 12-15 free.
- **Counters:** `pixels` (rasterizer output, both triangle commands), `written` (color writes), `clocks` (engine in its triangle state), 32 bits each, saturating, reply order pixels, written, clocks. Tests check `clocks` only as a window.
- **Hazard rule:** a depth unit accepts one pixel per clock in any address pattern and behaves as if pixels were processed one at a time in order; the reference forwards.
- **Throughput:** reference = one clock per bounding-box pixel + 9 (flat) or 120 (shaded) setup clocks per triangle; refused pixels cost the same. `CLEAR_DEPTH` and `CLEAR_ALL` take 76,800 clocks.

## Testbench requirements

**Modules you provide.** `depth_unit` with the ports of Section 3 in `rtl/common/depth_unit.v`, and `cmd_engine` with the five commands. The unit contract: one pixel per clock may arrive on any clock, in any address pattern; each pixel that passes gives one `o_we` pulse with its address and color, in input order, after any fixed or varying latency; `o_busy` is high while any pixel is in flight and falls after the last one; `i_cw_en` writes `i_cw_data` at `i_cw_addr` once per clock and is used only when no pixel is in flight; with `i_test = 0` every pixel passes; with `i_zwrite = 1` a passing pixel's depth is stored and is the one compared with the next pixel at that address, however close behind it is.

- **`tb_depth_unit`** (from `build/sim`, `make test`): compares the sequence of `(o_addr, o_color)` of every `o_we` with a behavioural model; the tests clear the 16 pool addresses before each burst, so no power-up value is assumed; it needs at least 1,000 back-to-back same-address pixels in its stream (it counts them).
- **`tb_depth_stats`** (from `build/sim`, `make test`): `uart_gpu` at 2.5 Mbaud; replies must be exactly 12 bytes per `GET_STATS`; `pixels` and `written` exact; `clocks` from `pixels` to the sum over the triangles of (box + 2 x rows + 60 + 8 flat, 2 x box + 1,000 + 8 shaded), box being the clamped bounding box; two `GET_STATS` in a row identical; 2D drawing, clears and `GET_STATS` itself change nothing.
- **`tb_frame_depth`** (from the repo root, `make frame-depth`): the wiring of `tb_frame_display_lists`, six frames, capabilities `0FFF`, flags `00` then `06`; the pixels are checked by `compare.py`, not the testbench.

**Files from this chapter:** `tb_depth_unit.v`, `tb_depth_stats.v`, `tb_frame_depth.v` and `tb_gpu_fuzz.v` go to `sim/`; `ref_depth.py` and `ref_fuzz.py` to `sim/py/`; `gpu_model.py` and `depth_demo.py` to `host/`; `protocol.md` to `docs/`. Run `sim/py/ref_depth.py --stream sim`, `sim/py/ref_depth.py --stats sim` and `sim/py/ref_fuzz.py sim` once and commit the files they write.
