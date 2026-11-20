# Chapter 4.5 - Double Buffering and Vsync

Chapter 4.4's starburst looked right only because nothing moved. Animate something and it stops looking right: clear the framebuffer, draw a bar in a new place, and for a few milliseconds the screen shows a half-drawn picture. That is **tearing**, and this chapter removes it.

The cure is a second framebuffer. The monitor reads one buffer, the **front buffer**, while the engine draws into the other, the **back buffer**. When a picture is finished, a new command, `FLIP`, swaps their roles, but only while the beam is in vertical blanking, so every frame the monitor receives is one whole picture.

The swap request starts in the 50 MHz engine and takes effect in the 25 MHz scanout, so this chapter is also about clock-domain crossing done properly: a request that can't be lost, an acknowledgement that can't come early, and no multi-bit value sampled while it changes.

By the end of this chapter you will have:

- `rtl/common/fb_scanout.v` with two framebuffers, a buffer select, a flip request and acknowledge, and a `VSYNC` output,
- `rtl/common/cmd_engine.v` and `uart_gpu.v` extended with `FLIP` (opcode `0x07`),
- `host/gpu_model.py` replaced by this chapter's version, which models two buffers,
- `sim/tb_flip_sync.v` passing against `sim/py/ref_flip_sync.py`, and `make frame-flip` passing, with Chapters 4.2 to 4.4's testbenches still passing,
- `projects/4.5-flip/` on the board, and `host/flip_demo.py` showing the same animation torn and then clean.

## 1. Why tearing happens

The beam reads the framebuffer from the first row to the last, once every 16.8 ms. The engine writes whenever it likes. A write to a row the beam hasn't reached yet shows in this frame; a write to a row it has passed shows only in the next. So a picture redrawn while the beam crosses it is split at the beam's row:

```
    frame N-1: bar at x=100        beam at row 90 when the engine draws frame N
    ┌──────────────────┐
    │   █              │  rows 0-89:   already scanned, still the old bar
    │   █              │
    ├──────────────────┤  <- the beam, at the moment of the redraw
    │        █         │  rows 90-239: scanned after the redraw, the new bar
    │        █         │
    └──────────────────┘
```

A `CLEAR` makes it worse: rows the beam reaches between the `CLEAR` and the redraw show up blank.

Drawing only during blanking doesn't work either. Vertical blanking lasts 45 lines, 1.44 ms, less than one 1.5 ms `CLEAR`, and it would leave the engine idle for the other 15 ms of every frame. The fix is never to draw into the buffer being displayed.

## 2. Two buffers and `FLIP`

| Opcode | Command | Length | Payload |
| --- | --- | --- | --- |
| `0x07` | `FLIP` | 0 | none, no reply |

These rules define it. The hardware and the Python model both follow them.

1. **Drawing goes to the back buffer.** `CLEAR`, `PLOT`, `FILL_RECT` and `DRAW_LINE` write the back buffer, and nothing the engine does changes the front buffer.
2. **`FLIP` swaps the buffers during vertical blanking.** The back buffer, with everything drawn so far, becomes the front buffer at some point in a vertical blanking interval, never while the beam is in the visible area. Every frame is therefore entirely the old picture or entirely the new one.
3. **The engine stalls until the swap.** After `FLIP` the engine starts no further command until the swap has happened. That can take up to a frame, 16.8 ms. Bytes keep arriving into the FIFO meanwhile, at most about 190 per frame at 115,200 baud, which the 512-byte FIFO holds easily. A host that queues several `FLIP`s ahead can still fill it, so it should pace itself (below) or use CTS.
4. **At most one swap per frame.** Because each `FLIP` waits for its swap, a program that draws and flips as fast as it can runs at the display rate, 59.5 frames a second.
5. **The old picture stays.** After the swap the old front buffer is the back buffer, **unchanged**: it still holds the picture that was just on the screen. A program that wants an empty canvas starts with `CLEAR`. One that redraws only what moved must remember that this buffer is two frames old.
6. **`FB_CRC` reads the back buffer**, the same one the drawing commands write. A host checks a picture by asking for its CRC before the `FLIP`, and that is also the CRC of what appears on screen after it. The engine's draw port then never needs to reach the front buffer.
7. **A `FLIP` of any other length is ignored**, like any malformed packet: no swap, no stall.
8. **The palette is not double-buffered.** `SET_PALETTE` changes colors on the screen at once, even mid-frame.

Rules 3 and 6 together give the host a **fence**. An `FB_CRC` queued behind a `FLIP` is answered only after the swap, so waiting for that reply tells the host the new picture is up. Its value (the CRC of the old picture, now in the back buffer) rarely matters. The testbench and `flip_demo.py` both pace themselves this way.

## 3. Two framebuffers in `fb_scanout`

One buffer is 76,800 bytes, 19 `RAMB36E1`. Two take 38 of the chip's 140, about a quarter, as Chapter 4.1 predicted, and leave room for Phase 5's tile and sprite memories and Phase 6's depth buffer.

The simplest layout is two instances of Chapter 4.1's `framebuffer` inside `fb_scanout`, buffers 0 and 1.

- **Draw side (`clk_sys`).** A new input, `i_fb_buf`, picks the buffer for the whole draw port. It gates the write enables (`we0 = i_fb_we & ~i_fb_buf`, `we1 = i_fb_we & i_fb_buf`) and picks which buffer's port-A data becomes `o_fb_rdata`.
- **Scanout side (`clk_pix`).** Both buffers' port B get the same reader address, and a register `r_front` picks which output goes to the palette. The address logic is shared, so the second buffer costs only an 8-bit multiplexer.

A RAM read takes a clock, so each multiplexer should use its select delayed by one clock, the value that went with the address. In this design neither select changes while its data is in use (`i_fb_buf` only changes while the engine is stalled, `r_front` only in blanking), so it is good practice rather than a fix for a visible bug.

One array of 153,600 bytes, with buffer 1 at address `i_fb_addr + 76800`, also works and yosys builds the same 38 blocks. Don't form the address as `{buffer, i_fb_addr}`: buffer 1 then starts at 131,072 and runs past the end of the array.

**At power-up both buffers hold Chapter 4.1's test image**, buffer 0 is the front, and buffer 1 is the back. Drawing before the first `FLIP` therefore starts from the same image as in Chapters 4.2-4.4, and the monitor shows the test image as before.

Chapter 4.1 noted that a write on one port and a read of the same address on the other return one invalid byte. With two buffers that byte is never shown: the engine writes only the back buffer, and the scanout uses only the front buffer's output. (The `--single` demo in Section 11 breaks this rule on purpose.)

## 4. The swap point

The swap must happen while the beam is outside the visible area, with enough margin for the scanout pipeline: after the last visible pixel of one frame has left the pipeline, and before the first visible pixel of the next is read. Any clock in that window is correct.

The reference swaps at the first clock of vertical blanking, when the beam counters read `x = 0, y = 480`:

```verilog
wire w_swap = (i_x == 10'd0) && (i_y == 10'd480);        // start of vertical blanking
```

The last visible pixel is (639, 479), so the 160 clocks of line 479's horizontal blanking lie between it and the swap, far more than the pipeline's few clocks. Swapping at the start gives the engine the most time: it resumes right away and has the whole rest of the frame to draw the next picture.

A swap at the HDMI sync pulse (lines 490-491) or on the last line of blanking is just as legal, and the testbench accepts any of them. It rejects a swap anywhere between the first and last visible pixels, including on lines 0 and 479. A design that "swaps as soon as the request arrives", with no `w_swap` at all, is correct only for requests that happen to arrive in blanking and tears for all others.

A request that reaches the scanout after this frame's swap point waits for the next one. A design may instead swap later in the same blanking. Both are fine.

## 5. Crossing the clock domains

The engine decodes `FLIP` on `clk_sys`. The swap happens on `clk_pix`, where the beam is, and the engine must hear back that it happened. One bit crosses each way.

**Why not a pulse?** Phase 0's two-flop `synchronizer` makes a single bit safe to sample from another clock. A pulse is a different matter: a one-clock pulse on `clk_sys` lasts 20 ns, and `clk_pix` samples every 40 ns, so about half of all such pulses fall between two sampling edges and are never seen. A lost request is a hung engine.

**Toggle instead.** The request is a flip-flop, `o_flip_req`, that **inverts at every `FLIP`**. A toggle is a level that lasts until the next `FLIP`, so the other clock can't miss it. The scanout side synchronizes it and, at the swap point, copies it:

```verilog
synchronizer u_req (.i_clk(i_clk_pix), .i_async(i_flip_req), .o_sync(w_req));
always @(posedge i_clk_pix) if (w_swap) r_front <= w_req;
```

It is `r_front <= w_req`, a copy, not `r_front <= ~r_front`. When no request is pending, `r_front` already equals the request, so the swap point changes nothing. When one is pending, the next swap point applies it. A request that arrives a clock too late is simply taken one frame later.

**The acknowledgement is `r_front` coming back.** `r_front` changes once per swap, so it is a toggle too. Pass it through a `synchronizer` clocked by `clk_sys`; the result, `o_flip_ack`, is both the acknowledgement and the answer to "which buffer is on the screen". The engine waits for the two toggles to agree:

```verilog
// in S_EXEC:
8'h07: if (r_len == 8'd0) begin o_flip_req <= ~o_flip_req; r_state <= S_FLIP; end
// the new state:
S_FLIP: if (i_flip_ack == o_flip_req) r_state <= S_FETCH;   // the screen has caught up
```

`o_flip_ack` can equal the new `o_flip_req` only after `r_front` has taken its value, that is, after the swap. So the engine can't resume early.

**The draw buffer is derived, not stored.** With nothing pending, the buffer on screen equals `o_flip_req`, so the draw buffer is the other one: `o_fb_buf = ~o_flip_req`, a wire. It changes at the request, while the engine is stalled and draws nothing. There is no second copy of "which buffer" that could disagree with the first.

The protocol is safe because:

- only one bit crosses in each direction, each through a `synchronizer`. Nothing samples a counter or a pair of bits from the other domain,
- each toggle holds its value until the other side has seen it,
- the engine never starts a second `FLIP` before the first is acknowledged, because it stalls.

**What simulation can't check.** In RTL simulation every flip-flop resolves at once, so a design with a synchronizer missing works perfectly. The testbench catches a protocol that loses information (the pulse above fails it), but not a forgotten synchronizer. Check by reading your code: each signal from the other domain must enter exactly one flip-flop, the first of a `synchronizer`. nextpnr's report lists cross-domain paths too, but only the worst one for each pair of clocks.

**Latency.** The request reaches the scanout two or three `clk_pix` after it toggles. Then it waits for the next swap point, anywhere from a few clocks to a frame away, and the acknowledgement takes two or three `clk_sys` to come back. A `FLIP` therefore completes in 0 to 16.8 ms, and the engine waits all of that time. That costs nothing: if a picture takes 4 ms to draw, the other 12 ms of the frame would be idle anyway.

## 6. The engine and the new ports

Three modules change. `cmd_engine` and `uart_gpu` each gain three ports, and `fb_scanout` four:

| Port | Direction | Domain | Module: meaning |
| --- | --- | --- | --- |
| `o_fb_buf` | out | `clk_sys` | `cmd_engine`, `uart_gpu`: the buffer the draw port addresses (the back buffer) |
| `o_flip_req` | out | `clk_sys` | `cmd_engine`, `uart_gpu`: toggles once per `FLIP` |
| `i_flip_ack` | in | `clk_sys` | `cmd_engine`, `uart_gpu`: the displayed buffer, already synchronized to `clk_sys` |
| `i_fb_buf` | in | `clk_sys` | `fb_scanout`: the draw port's buffer |
| `i_flip_req` | in | `clk_sys` | `fb_scanout`: the request toggle, synchronized inside |
| `o_flip_ack` | out | `clk_sys` | `fb_scanout`: `r_front` synchronized into `clk_sys` |
| `o_vblank` | out | `clk_pix` | `fb_scanout`: the `VSYNC` pin (Section 7) |

In the top, `o_fb_buf` goes to `i_fb_buf`, `o_flip_req` to `i_flip_req`, and `fb_scanout`'s `o_flip_ack` back to `uart_gpu`'s `i_flip_ack`.

The engine change is Section 5's two lines: one opcode in `S_EXEC` and one state, `S_FLIP`, plus the wire `o_fb_buf`. That makes 11 states, which still fit the 4-bit state register. A `FLIP` with the wrong length is skipped like any other.

**Reset during a `FLIP`.** `BTN0` resets the parser. If it is pressed while the engine waits in `S_FLIP`, and the reset sends the engine back to `S_FETCH`, the next commands run before the swap. `o_fb_buf` already names the buffer that is still on the screen, so they draw into the displayed picture. Let a reset leave `S_FLIP` alone (reset the parser, but stay in `S_FLIP`, for example `if (r_state != S_FLIP) r_state <= S_FETCH`). The swap always comes within a frame, and then the engine continues normally. The testbench pulses `i_rst` during one `FLIP` to check this.

**Chapters 4.2-4.4's testbenches** instantiate `uart_gpu` without the new ports, and `iverilog -Wall` warns that the input `i_flip_ack` is floating. That's expected: they never send `FLIP`, so the engine never enters `S_FLIP` and never reads it, and they still pass.

**Chapter 4.1's `tb_frame_scanout` needs one edit.** It instantiates `fb_scanout` directly, so the new inputs float. A floating `i_fb_buf` sends its writes and read-backs to neither buffer, and the test fails. Add `.i_fb_buf(1'b0), .i_flip_req(1'b0)` to its `fb_scanout` instance: buffer 0 is then both the one it draws into and the one on screen, exactly the single-buffer setup it was written for, and it passes again.

## 7. The `VSYNC` pin

Step 5.1 gives the MCU a `VSYNC` pin, so that it knows when a frame starts. The scanout side has everything it needs, so build it now:

```verilog
always @(posedge i_clk_pix) o_vblank <= (i_y >= 10'd480);
```

| Property | Value |
| --- | --- |
| Port | `fb_scanout.o_vblank`, board port `vsync`, Pmod JA pin 4 (Y17), 3.3 V |
| Polarity | **active high**: high during vertical blanking, low during the 480 visible lines |
| Rising edge | the first clock of line 480, the reference's swap point |
| High time | 45 lines = 1.44 ms, once every 16.8 ms (59.5 Hz) |
| Domain | `clk_pix`, registered, so it never glitches; asynchronous to the MCU |

It is not the HDMI sync pulse, which is active low, two lines long and sits inside blanking. The MCU should sample it with its own synchronizer and act on the rising edge. Treat it as a frame clock only. To know that a `FLIP` has finished, wait for an `FB_CRC` reply queued behind it (Section 2).

Add the pin to `boards/arty-z7-20/arty-z7-20.xdc`, after the UART pins (JA1 `uart_rx`, JA2 `uart_tx`, JA3 `uart_cts`):

```
set_property -dict {PACKAGE_PIN Y17 IOSTANDARD LVCMOS33} [get_ports {vsync}]
```

## 8. The Python model

Replace `host/gpu_model.py` with this chapter's version. It adds `FLIP = 0x07` with length 0, the builder `flip()`, and two buffers in `Gpu`: `bufs`, `front`, `flips` and `front_fb`. Both buffers start as copies of the `fb` argument. The attribute `fb` is now a property that returns the **back** buffer, so everything written for Chapters 4.2-4.4, which draws into `g.fb` and compares `g.fb`, works unchanged. The model's swap is instant, and `FB_CRC` is the CRC of the back buffer.

Check that nothing from the earlier chapters moved. These commands rewrite the files already in `sim/` byte for byte, so `git status` stays clean:

```sh
for n in cmd rect line; do .venv/bin/python sim/py/ref_$n.py sim; done
```

Run `.venv/bin/python host/gpu_model.py` for the self-check. Among other things it checks that `FB_CRC` after a `FLIP` is the CRC of the old front buffer, and that a malformed `FLIP` is ignored.

## 9. Simulate

### `tb_flip_sync`: swaps only in blanking

Copy `ref_flip_sync.py` to `sim/py/` and `tb_flip_sync.v` to `sim/`. Run the generator once, and commit the four files it writes:

```sh
.venv/bin/python sim/py/ref_flip_sync.py sim
```

    ref_flip_sync: 657 bytes, 10 FLIPs (5 across the frame, 5 around the start of blanking), 23 FB_CRC replies -> sim/flipsync_*

The testbench is the host and the monitor at once. It runs the chain from Section 6, `uart_gpu`, `fb_scanout` and a real `vga_timing`, sends the stream over real serial at 2.5 Mbaud, and watches the pixels that come out. The engine clock has a period of 19.936 ns, not 20, so the phase between the two clocks drifts through every value instead of sitting at one lucky value.

```
$ make test TESTS="tb_uart_gpu tb_gpu_rect tb_gpu_line tb_flip_sync"
  PASS  tb_uart_gpu
  PASS  tb_gpu_rect
  PASS  tb_gpu_line
  PASS  tb_flip_sync
$ cat build/sim/tb_flip_sync.log
tb_flip_sync: timed FLIP requests toggled at -30 -3 -2 -1 0 pixel clocks from the start of line 480
PASS: tb_flip_sync (10 FLIPs, 15 frames each one picture, FLIP took 0-16800 us to acknowledge, 23 FB_CRC replies match, 15 vblank rises)
```

`tb_flip_sync` takes about 2.5 minutes, because it simulates 15 full video frames with the engine running beside them. Each `FLIP` costs at least a frame, so the stream keeps their number small.

**The stream.** It draws a **different picture into the back buffer each time**, checks it with `FB_CRC`, and `FLIP`s, 10 times. Behind every `FLIP` it queues a `PLOT`, a `FILL_RECT` and an `FB_CRC` with no wait, so an engine that doesn't stall runs them at once.

- Five `FLIP`s are requested at different places in the frame: as soon as the engine is free, at lines 240 and 479, and in blanking at lines 500 and 524. Two of them have two malformed `FLIP`s (lengths 1 and 2) in front, and one gets an `i_rst` pulse while it waits for its swap.
- Five are timed against the start of blanking: the request toggles 30, 3, 2, 1 and 0 pixel clocks before line 480 begins. The testbench measures how long your design takes from the start of a `FLIP` packet to the toggle and starts each timed `FLIP` that much earlier, so these land within a clock of the target whatever your latency (the log line above shows where they landed). With a start-of-blanking swap point, the request just makes the swap or just misses it.

**What the monitor saw.** Every frame gets a **signature**: the sum of `color * (2p + 1)` over the 307,200 visible pixels, where `p` is the pixel's number in scan order, modulo 2^32. `ref_flip_sync.py` computes the signature of the picture on screen after each `FLIP` into `sim/flipsync_sigs.hex`. A frame of one buffer matches one signature. A frame torn by a swap during the picture matches none.

It checks, and prints what's wrong in each case:

1. Every complete frame matches the signature of **some one picture**.
2. From one frame to the next the picture number stays the same or goes up by exactly 1: one swap per blanking, no swap back.
3. A frame never shows a picture whose `FLIP` hadn't been requested before the frame's first pixel, and never one older than the `FLIP`s already acknowledged: the acknowledge doesn't come before the swap.
4. Between a request and its acknowledge the engine writes nothing and sends nothing, and the acknowledge comes within 40 ms. `o_flip_ack` never changes without a request pending.
5. Every `FB_CRC` reply equals the model's. This catches drawing into the wrong buffer, `FB_CRC` reading the wrong one, and malformed `FLIP`s that do something.
6. `o_vblank` rises at the start of line 480 and falls at the start of line 0 (within a few clocks), and is right at every line's hsync.
7. At the end all `FLIP`s are acknowledged, the last frame shows the last picture, and `o_overflow` never rose.

It does **not** check where in blanking the swap happens, or whether a request that arrives during blanking takes effect at once or a frame later.

When it fails, the first message says what happened. Some typical bugs print:

- **Swap as soon as the request arrives** (no wait for blanking), or a swap point inside the visible area, even on line 0 or 479: `frame 1 (signature ...) is not any one picture: it mixes two buffers (a swap in the visible area?)`.
- **A swap at every blanking, not only when asked** (`r_front <= ~r_front` instead of the copy): `o_flip_ack changed with no FLIP pending`.
- **The request as a one-clock pulse into the slower domain:** `FLIP 1 was not acknowledged within 40 ms`, then a fence that never completes. It fails with an exact 2:1 clock too: only every other `clk_sys` edge has a `clk_pix` edge inside its pulse.
- **The engine doesn't stall:** `the engine is not stalled after FLIP 1: a write happens at ..., 28050 ns after the request and before the acknowledge`.
- **A reset ends the wait** (`i_rst` sends `S_FLIP` back to `S_FETCH`): the same message, for `FLIP 3`.
- **The acknowledge comes from the synchronized request, before the swap** (the engine resumes into the buffer still on screen): `frame 1 ... mixes two buffers`.
- **Drawing goes to the front buffer** (`o_fb_buf` the wrong way round): `frame 1 shows picture 2, but 1 FLIPs had been requested and 1 acknowledged before its first pixel`.
- **Drawing goes to both buffers:** `FB_CRC reply 3 is 60245de7, expected fcc6cf51. It covers: flip 1: the new back buffer (the old picture) ...`. The old picture was overwritten.
- **`FB_CRC` reads the front buffer:** `FB_CRC reply 2 is a9a79db7, expected e64bceea. It covers: picture 1 in the back buffer, before its FLIP ...`. `a9a79db7` is the untouched test image, still on the screen.
- **A `FLIP` with a payload is accepted:** `frame 2 ... mixes two buffers`, with `3 FLIPs had been requested and 2 acknowledged when it began`: a malformed `FLIP` swapped.
- **`o_vblank` from the HDMI sync region** (`y >= 490`): `o_vblank is 0 with the beam at x=656 y=480`.

### `make frame-flip`: look at it

Copy `tb_frame_flip.v` to `sim/` and `ref_flip.py` to `sim/py/`, and add `FRAME_TESTS += flip` to the top-level Makefile. It uses Chapter 1.2's harness with the same chain and clocks as `tb_flip_sync`.

```
$ make frame-flip
...
PASS: tb_frame_flip (77 bytes sent, 3 replies; build/frame_flip_{drawn,flipped,again}.ppm written)
  ok    frame_flip_again.ppm: 640x480 match
  ok    frame_flip_drawn.ppm: 640x480 match
  ok    frame_flip_flipped.ppm: 640x480 match
PASS: compare flip (3 frames match exactly)
```

It takes about a minute. The testbench draws picture A, a box with two diagonals, into the back buffer and `FLIP`s. Then it draws picture B into the other buffer, which still holds Chapter 4.1's test image, so B is the test image with a rectangle and a line on it. An `FB_CRC` queued behind each `FLIP` tells it when the swap is done. It dumps three frames:

| Frame | When | Must show |
| --- | --- | --- |
| `drawn` | after B was drawn into the back buffer | A, with nothing of B on it |
| `flipped` | after the second `FLIP` | B |
| `again` | after the third | A again, exactly as before |

`drawn` shows that drawing went to the back buffer. `again` shows that the buffers keep their pictures: the test image under B survived, and A was not disturbed. When one fails, `compare.py` names the first bad pixel. Open `build/frame_flip_drawn.ppm` next to `build/ref_flip_drawn.ppm`: B showing through A means a draw hit the front buffer.

## 10. The Arty top

Copy `projects/4.4-line/` to `projects/4.5-flip/`, set `PROJ := flip` and `TESTS := tb_uart_gpu tb_gpu_rect tb_gpu_line tb_flip_sync`. Four things change in `top_artyz7.v`:

- `uart_gpu`'s `o_fb_buf`, `o_flip_req` and `i_flip_ack` connect to `fb_scanout` as in Section 6.
- A new output `vsync` takes `fb_scanout.o_vblank`.
- **A single-buffer switch for the demo.** With double buffering working, there is no way left to *see* tearing: drawing without `FLIP` goes into the hidden back buffer. So bring `sw[0]` in through a `synchronizer` on `clk_sys`, and while it is up, point the draw port at the displayed buffer instead: `.i_fb_buf(single ? flip_ack : eng_buf)`, where `flip_ack` is `fb_scanout.o_flip_ack` (the displayed buffer, as seen from `clk_sys`) and `eng_buf` is `uart_gpu.o_fb_buf`. With `SW0` up the board behaves like Chapters 4.2-4.4, with one buffer, and Section 1's tearing comes back.
- `led[3]` shows `flip_ack`. It inverts at every swap, so at 60 swaps a second it flickers at half brightness.

Add `vsync` to the XDC (Section 7) and run `make`. The reference build took 45 seconds:

```
== flip: resource use (xc7z020clg400-1) ==
  SLICE_LUTX:    2266/ 106400     2%
  SLICE_FFX:     672/ 106400     0%
  CARRY4:     137/  13300     1%
  ...
  RAMB18E1:       1/    280     0%
  RAMB36E1:      38/    140    27%
  Max frequency for clock 'clk_pix': 157.23 MHz (PASS at 25.00 MHz)
  Max frequency for clock 'clk_sys': 95.27 MHz (PASS at 50.00 MHz)
```

- **`RAMB36E1` 38**: two framebuffers. `build/yosys.log` has a `$__XILINX_BLOCKRAM_TDP_` line for each of `u_fb0.mem` and `u_fb1.mem`, and the palette is still `LUTRAM_SDP`.
- Everything else is close to Chapter 4.4's numbers (2,253 LUTs and 653 flip-flops). The read multiplexers and the write-enable gating cost a few dozen LUTs.
- `clk_sys` reaches 95 MHz. The critical path is still the engine's `FILL_RECT` sort-and-clamp chain, not the new logic.

## 11. On the board

Wiring and programming are as in Step 4.2: adapter TXD to JA pin 1, RXD to JA pin 2, GND to JA pin 5. Put `SW0` down and run `make prog`. The monitor shows Chapter 4.1's test image.

1. **Double buffered.** Run

    ```
    $ .venv/bin/python host/flip_demo.py /dev/ttyUSB0
    double buffer: SW0 must be down, drawing goes to the back buffer, FLIP after each picture, 10 s
    ...
    PASS: flip_demo
    ```

    A tall red bar sweeps right across a dark blue screen with a white border, and a yellow square sweeps left. Both move smoothly and stay whole: no horizontal break, no flicker, no blank stripes. Each frame the script sends `CLEAR` and six `FILL_RECT`s, an `FB_CRC` that checks the picture, a `FLIP`, and a second `FB_CRC` whose reply comes only after the swap. Waiting for that reply paces the script to the monitor, with no timer. It prints the frame rate and `PASS` when every CRC matched. Expect close to 59.5 frames a second; if your USB-serial adapter adds several milliseconds of latency to each reply, the script misses every other blanking and reports about 30, which is just as clean.
2. **Single buffered.** Put `SW0` up. The engine now draws into the displayed buffer. Run

    ```
    $ .venv/bin/python host/flip_demo.py /dev/ttyUSB0 --single
    ```

    It is the same animation without `FLIP`, paced by a timer instead of the monitor, so the beam meets the redraw at a different place every frame. The bar flickers, breaks into pieces with its parts at different places, and blank stripes flash where the `CLEAR` has run but the bar has not been drawn yet. The CRC checks still pass: the drawing is correct, only the timing is bad. A phone photo with a fast shutter (1/1000 s) freezes one frame and shows the split.
3. Put `SW0` down again and rerun step 1. It is clean again. The script starts by clearing both buffers, so nothing from the `--single` run is left behind.
4. If you have a logic analyzer or scope, look at `vsync` (JA4): it should be high for 1.44 ms once every 16.8 ms.

**Done when:** `tb_flip_sync` passes, `make frame-flip` passes, `tb_uart_gpu`, `tb_gpu_rect` and `tb_gpu_line` still pass, and the animation tears in single-buffer mode and is clean in double-buffer mode.

## Testbench requirements

**Modules you provide** (in `rtl/common/`, found by `-y rtl/common`): all of Chapter 4.4's requirements still hold for `uart_gpu`, with these additions.

- `uart_gpu #(CLK_HZ, BAUD)`: Chapter 4.4's ports plus `output o_fb_buf`, `output o_flip_req`, `input i_flip_ack` (all `clk_sys`). Opcode `0x07` `FLIP`, length exactly 0, no reply (any other length is skipped). On `FLIP` the engine toggles `o_flip_req`, then starts no further command and sends no byte until `i_flip_ack == o_flip_req`. An `i_rst` pulse during that wait must not end it early. Drawing commands and `FB_CRC` use the buffer `o_fb_buf` names, which is the one not on the screen; `o_fb_buf` doesn't change while the engine writes or reads. With no `FLIP` sent, `i_flip_ack` is never used (the older testbenches leave it unconnected).
- `fb_scanout #(FB_FILE, PAL_FILE)`: Chapter 4.1's ports plus `input i_fb_buf`, `input i_flip_req`, `output o_flip_ack` (all `clk_sys`) and `output o_vblank` (`clk_pix`). Two 320x240x8 buffers, both preloaded from `FB_FILE`. `i_fb_buf` picks the buffer for `i_fb_we`/`i_fb_addr`/`i_fb_wdata` and for `o_fb_rdata` (one clock of latency, as before). Buffer 0 is displayed at power-up. A toggle of `i_flip_req` makes the scanout show the other buffer from some point inside a vertical blanking interval, no earlier than the toggle, and never between the first and last visible pixel of a frame (any scanout latency from 1 to 24 clocks is allowed, as in Chapter 4.1). `o_flip_ack` toggles when that has happened, and not before. The palette is shared and takes effect at once. `o_vblank` is high from the start of beam line 480 to the start of line 0 (within 4 clocks at each end), registered.
- `vga_timing` from Chapter 1.1, plus whatever those modules use.
- Each `FLIP` is acknowledged within 40 ms, and no request is lost, whatever the phase between the two clocks.

**Files from this chapter:** `tb_flip_sync.v`, `tb_frame_flip.v` to `sim/`, `ref_flip_sync.py`, `ref_flip.py` to `sim/py/`, `gpu_model.py` (replacing Chapter 4.4's) and `flip_demo.py` to `host/`. They also need Chapter 4.1's `ref_scanout.py`, `fb_test.hex` and `pal_test.hex`, and Chapter 1.2's `frame_dump.v`, `ppm.py`, `compare.py` and `frame.mk`. Run `.venv/bin/python sim/py/ref_flip_sync.py sim` once to write `sim/flipsync_stream.hex`, `flipsync_replies.hex`, `flipsync_sigs.hex` and `flipsync_labels.txt`, and commit them. Add `FRAME_TESTS += flip`.

**Testbenches:**

- `sim/tb_flip_sync.v`, top module `tb_flip_sync`, run from `build/sim/` by `make test` (about 2.5 minutes). `clk_pix` 25 MHz and `clk_sys` 19.936 ns; `+syspd_ps=20000` makes it an exact 2:1. It sends the stream at 2.5 Mbaud into `uart_gpu`, drives `fb_scanout` from a real `vga_timing`, and checks every complete frame's signature against `sim/flipsync_sigs.hex` (one picture, in order, within the requested and acknowledged bounds), the stall between each request and its acknowledge (including across an `i_rst` pulse), every `FB_CRC` reply, `o_vblank`, and the final state. It prints one `PASS` or `FAIL` line. The watchdog is 3 s simulated.
- `sim/tb_frame_flip.v`, top module `tb_frame_flip`, run from the repo root by `make frame-flip` (about a minute). It dumps `build/frame_flip_{drawn,flipped,again}.ppm`, and `compare.py` checks them against `ref_flip.py`.

By hand:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_flip_sync.vvp sim/tb_flip_sync.v
cd build/sim && vvp -n tb_flip_sync.vvp
```
