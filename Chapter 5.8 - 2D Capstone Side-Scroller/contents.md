# Chapter 5.8 - 2D Capstone: Side-Scroller

Chapters 5.1 to 5.7 built a GPU that draws two scrolling tile layers, 64 sprites and a display-list HUD by itself, and showed each piece on its own. This chapter adds no hardware and no opcodes. The host now plays a whole game through it: a parallax background, 26 sprites and a HUD, at 60 frames a second, in under 1 KB per frame.

The new work is on the host side:

- **budgeting:** knowing where every byte of a frame goes and how long it takes to send,
- **reproducibility:** logging every byte the host sends, and replaying that log in the simulator to get the same frames back.

By the end of this chapter you will have:

- `host/side_scroller.py`: the game. It runs against the Python model (`--model`) or the board, reports the bytes and time of every frame, and writes a **log** of everything it put on the link (`--log`),
- `sim/py/ref_replay.py` and `sim/tb_frame_replay.v`: a script that turns the log into a simulation stream and the expected frames, and a testbench that plays it into the whole design,
- the numbers: 138 bytes per frame on average, 317 at most, about 4 ms of the 15.36 ms visible part even through a USB bridge.

The capability bits stay `03FF` and `protocol.md` does not change. The game follows the frame loop that `protocol.md` section 14 recommends ("Driving the frame with `RUN_LIST`").

## 1. The game

| Source | What | How it is kept up to date |
| --- | --- | --- |
| Far layer (layer 0) | hills and clouds, a 64 x 64-tile map that repeats | written once at setup; only its scroll changes |
| Near layer (layer 1) | Chapter 5.6's level of 256 x 96 tiles: ground, grass, trees | **streamed**: a new column or row as the camera moves |
| Sprites 0 to 25 | the player (0), three enemies (1 to 3), a pool of 22 coins (4 to 25) | `MOVE_SPRITE` for each sprite that moved |
| Framebuffer | the HUD: a panel, a four-digit score, a health bar | **display list** 0, patched when a value changes |

`SET_PRIORITY` puts the framebuffer in front, then sprites, the near layer and the far layer. Index 0 of the framebuffer is transparent, so everything outside the HUD panel shows the scene behind it.

The player stays at screen x = 40 while the world scrolls past at 2 pixels a frame. The camera follows the ground up and down by at most one pixel a frame (Chapter 5.6's `Camera`). The only input is a scripted jump button.

**Parallax** is just two scroll values. The near layer moves 2 pixels a frame and the far layer 1 (`stream_demo.far_scroll`: half the near x, an eighth of the near y). The far layer moves less, so it looks farther away.

## 2. The frame loop

Every frame, the host does this:

```
wait for VSYNC to fall                   the visible part of frame N begins
  new map column or row (if any)         immediate writes, into map slots that are not on screen
  MOVE_SPRITE / SET_SPRITE               shadowed: copied at the next blanking
  SET_SCROLL x 2                         shadowed
  WRITE_LIST_WORD (digits, bar)          patches of list 0, only when a value changed
  RUN_LIST 0                             clears and draws the HUD, ends in FLIP
(send nothing more until VSYNC falls again)
blanking: shadows copied, FLIP swaps     frame N + 1 shows all of it
```

Each rule has a reason:

- **Start after VSYNC falls.** The previous `FLIP` finished in the blanking, so the engine is idle and each command runs as its bytes arrive. Sending at the start of the visible part also keeps sprite commands well away from the first 5 µs of blanking, which Chapter 5.4 forbids.
- **Shadowed settings all together.** The sprite table and both scrolls are copied at the same blanking, so a frame never mixes a new scroll with old sprite positions. The host computes sprite screen positions from **this frame's** camera for the same reason.
- **Map writes are immediate.** They land while the beam is still drawing the old view, so they must only touch map slots outside it. `level_stream.StreamedLayer.plan` asserts this (Chapter 5.6).
- **`RUN_LIST` last, and nothing after it.** The list's `FLIP` waits for blanking, and every byte behind it waits too. Past about 250 waiting bytes `READY` falls. `side_scroller.py` checks that the `RUN_LIST` is the last packet of every frame.
- **The list draws the whole framebuffer.** It starts with `CLEAR` and ends in `FLIP`, so every buffer that reaches the screen holds a complete HUD, whichever of the two buffers it is. The host never draws.

**Latency.** Everything sent in frame N is on the screen in frame N + 1. This includes the HUD: the patches go in before the `RUN_LIST` of the same batch, so the run that the swap at the end of frame N displays already has the new digit. (Chapter 5.7's automatic loop takes two frames, because patches wait for the run that is already in progress.)

## 3. The byte budget

What a frame can contain:

| Part | Bytes | When | Worst case |
| --- | --- | --- | --- |
| `SET_SCROLL` x 2 | 7 each | every frame | 14 |
| `MOVE_SPRITE` | 7 each | each sprite that moved (26 at most) | 182 |
| `SET_SPRITE` | 11 | an enemy turns round (its flip bit changes) | 11 |
| `WRITE_TILE_COLUMN` or `_ROW` | 132 | about every 4 frames, never both in one frame | 132 |
| `WRITE_LIST_WORD` | 7 each | a digit or the bar changed | 35 |
| `RUN_LIST` | 3 | every frame | 3 |

The worst cases add up to about 377 bytes. What the game actually sends over 600 frames (ten seconds):

```
600 frames: 138.3 bytes per frame on average, at most 317, at least 17 (budget 1024)
  by kind, mean / max per frame: map 30.1 / 132, sprites 90.9 / 194, scroll 14.0 / 14, hud 0.3 / 14, run 3.0 / 3
```

That is about 8,300 bytes a second. For comparison, without this phase's hardware a host would redraw the scene into the framebuffer: 76,800 bytes a frame as raw pixels, or about 10 KB a frame as one 8-byte `BLIT` per visible tile.

The **setup** is sent once and is not part of the per-frame figures: 19,362 bytes of palette, tile and sprite art, the far map, the first 64 near-layer columns and the HUD list.

## 4. Does it fit in a frame?

Bytes are only half the budget. The batch and the HUD drawing must both finish before blanking, because the list's `FLIP` must be requested in the visible part (15.36 ms) to swap at the next blanking.

The time for one frame is:

- **The wire.** At 5 MHz SPI a byte takes 1.6 µs, so the 317-byte worst frame takes 0.51 ms.
- **The CS frames.** `SpiGpu.send` (Chapter 5.1) packs whole packets into CS frames of at most 257 bytes and checks `READY` before each one. The worst frame needs two CS frames. On the FT232H each CS frame costs a USB round trip, plus another round trip to read `READY`. `--cs-overhead-us` charges a fixed amount per CS frame for this (default 1000 µs, a deliberately pessimistic figure). On a microcontroller it is a few microseconds.
- **The HUD list.** The `CLEAR` writes 76,800 pixels at one per 50 MHz clock (1.54 ms). The rest of the list adds about 0.07 ms.

`side_scroller.py` adds these up for every frame and reports the slowest:

| SPI clock | CS frame overhead | Slowest frame |
| --- | --- | --- |
| 5 MHz | 1000 µs (FT232H) | 0.51 + 2.0 + 1.6 = 4.1 ms of 15.36: fits |
| 5 MHz | 5 µs (MCU) | 0.52 + 1.6 = 2.1 ms: fits |
| 1 MHz (the script's default) | 1000 µs | 2.5 + 2.0 + 1.6 = 6.1 ms: fits |
| 100 kHz | 1000 µs | 25.4 + 2.0 + 1.6 = 29 ms: `TOO SLOW` |

The game needs an SPI clock above about 250 kHz.

**`READY`.** In this loop `READY` is high at every check. After the swap the FIFO is empty, and the engine takes bytes faster than SPI delivers them (one every 1.6 µs), so only a few bytes are ever waiting. The one exception is behind the `RUN_LIST`, where the loop sends nothing. The largest CS frame the game sends is 254 bytes, under the 257 that one `READY` check allows.

The setup takes 31 ms on the wire at 5 MHz, or 142 ms with 111 CS frames at 1 ms each. That is a few frames before the game starts.

## 5. Sprites, and the 16-per-line limit

The host remembers what the sprite table holds and sends nothing for a sprite whose x, y and flags have not changed. A sprite that moved costs a 7-byte `MOVE_SPRITE`. When an enemy turns round its `FLIP_X` bit changes, which `MOVE_SPRITE` cannot do, so that frame it costs an 11-byte `SET_SPRITE`.

**A sprite that is not on screen is parked at y = 256.** Chapter 5.4's limit counts every enabled sprite whose y range covers a line, whatever its x. A sprite parked at x = -32 would still take a slot on its lines. At y = 256 it covers no visible line, so it takes no slot, and once parked it costs no bytes.

**The coin chains hit the limit on purpose.** The first chain is 22 coins in one row, 12 pixels apart. Every coin in the row covers the same scan lines, and Chapter 5.4 draws only the 16 lowest-numbered sprites on each line. While the player approaches the chain, up to 23 sprites share a line, so the highest-numbered coins (the ones still far to the right) are missing. The limit applies **per line**, so a coin can be cut off: where the player or an enemy also crosses the row, one more coin loses those lines and shows only its top. Missing coins appear as the player collects the earlier ones.

A real game would avoid this. It could curve the chain (the second chain is an arc, so fewer coins share a line), or rotate which entries the coins use each frame so that the dropout flickers instead of staying fixed. This game keeps the flat chain because the replay test should show the limit, and its window includes those frames.

## 6. The two layers and the HUD

The two layers have different upkeep.

- **The near layer is streamed.** The level is four times as wide as the 64-column map, so the host writes a new 132-byte column about every 4 frames, and a row when the camera has moved 8 pixels up or down. Chapter 5.6's camera puts a vertical step off by one frame if a column is due in the same frame, so a frame never carries both.
- **The far layer is not streamed.** Its hills repeat every 64 tiles, exactly the 512-pixel width of a layer, so the map written at setup is the whole background and the scroll wraps around it. A far layer that did not repeat would be streamed the same way at half the rate: about 17 more bytes a frame on average.

**The HUD** is list 0: `CLEAR`, a `FILL_RECT` for the panel, four `BLIT`s of digit glyphs, two `FILL_RECT`s for the health bar and its background, and `FLIP`. That is 70 bytes, sent once. A collected coin changes the score, and each changed digit costs one 7-byte `WRITE_LIST_WORD` on the asset id of its `BLIT` (Chapter 5.7's `digit_patch`). A hit moves the bar's right edge, which is one more patch. Frames with no change send no HUD bytes, so the HUD averages 0.3 bytes a frame.

Why not let the list run itself with `SET_AUTO_LIST`? It would save the 3 bytes of `RUN_LIST`. But during an automatic loop the host must not send shadowed settings (Chapter 5.7), and this game sends scrolls and sprites every frame.

## 7. Determinism and the log

A replay only means something if the host sends **the same bytes every time**. `side_scroller.py` has no clock and no unseeded randomness. The jump button comes from a small integer generator (`Lcg`) seeded by `--seed`, and everything else follows from the camera and the level. The script builds the whole run (`generate`) twice and aborts if the two byte streams differ.

The `--model` run also checks after every frame that the model holds what the host believes it sent: the near map for the view, both scrolls, the sprite table, the HUD digits and bar. At the end it checks that no error flag is set.

```sh
.venv/bin/python host/side_scroller.py --model --log sim/side_scroller.log --ppm last.ppm
```

```
seed 1: 600 frames, byte stream sha256 a4dc315ff4321364
600 frames: 138.3 bytes per frame on average, at most 317, at least 17 (budget 1024)
  ...
  slowest frame: 317 bytes in 2 CS frames = 4.54 ms on the wire (1000 us overhead per CS frame) + 1.61 ms for the HUD list = 6.14 ms of the 15.36 ms visible part (fits)
  nothing is sent after the RUN_LIST: True; largest CS frame 254 bytes (READY allows 257)
log written to sim/side_scroller.log (1387 records)
PASS: side_scroller (model)
```

(The model run takes about a second. The timing line uses `--freq`, which defaults to 1 MHz.)

`--log FILE` writes every byte that crossed the link, as text:

```
S <hex bytes>    one SPI command frame (one CS-low period), exactly the bytes on MOSI
Q <n>            the host read n reply bytes (read frames themselves are not logged)
F <k> <nbytes>   video frame k begins: the host saw VSYNC fall; its S lines add up to nbytes
E                the last frame is over
```

The `S` lines come from a wrapper (`LogPort`) around the SPI port's `exchange()`, so they record the CS frames the host actually used, not a re-packing of the packets. `ref_replay.py` checks every `F` line's byte count, so a damaged or truncated log is rejected before any simulation. The board run writes the same log, with `F` lines placed where the loop saw the `VSYNC` pin fall. Try `--seed 2`: the player jumps at other times and the bytes change, but any one seed always gives the same log.

## 8. Replaying the log in the simulator

The test must show that the **logged bytes** make the **same frames** in the Verilog design as in the model. Simulating all 187 frames before the window at video speed would take over an hour (each simulated frame takes about 25 seconds). Instead the test dumps a **window** of six frames and gets there quickly.

`ref_replay.py` picks the earliest window in which:

- the batches write a map column and a map row,
- a HUD digit is patched,
- some frame shows 20 or more sprites, and
- some frame has more than 16 sprites on one scan line.

For the seed-1 log that window is frames 187 to 192. Each of those frames shows 21 to 24 sprites, with 21 to 23 of them on one line, so the hardware drops 5 to 7 on that line.

The stream the testbench plays has four parts:

1. **The setup**, as logged: 19 KB in the host's CS frames, with the `GET_INFO` it read.
2. **The prefix:** frames 0 to 185 as logged, CS frame by CS frame, **with their `RUN_LIST` packets taken out** and no waiting between frames (23.9 KB).
3. **The warm-up frame** (186), as logged, starting when `VSYNC` falls. Its `RUN_LIST` draws the HUD that the first dumped frame shows.
4. **The window**, frames 187 to 192, each batch sent as logged right after `VSYNC` falls. The dumpers capture these six frames. A final `GET_INFO` must report no error flags.

**Why the `RUN_LIST`s may be dropped.** They are the only reason the prefix would need real time: each one ends in a `FLIP` that waits for a blanking. Everything else is state that is simply overwritten in order. The scrolls and the sprite table end up holding their last values. The map slots and list bytes end up holding what was last written to them. A run of list 0 only draws into the framebuffer, and the next run clears and redraws all of it. So without those runs, the state at frame 186 is the same, apart from which physical buffer is in front, and the screen does not show that. `ref_replay.py` checks the assumption: every `RUN_LIST` in the log must run a list that starts with `CLEAR` and holds only drawing commands and `FLIP`.

That is the whole difference between the log and the stream. Of the log's 45,436 bytes up to the end of the window, the stream drops 558 (186 `RUN_LIST`s of 3 bytes) and sends the rest in the order and CS frames the host used: 44,880 bytes in all, including the final `GET_INFO`. Sending the prefix at full SPI speed adds only about 40 ms of simulated time.

**The expected frames do not come from the stream.** `ref_replay.py` plays the **whole** log through `gpu_model.py`, frame by frame: `vblank()`, take the screen, feed the frame's bytes. The test therefore compares the shortened replay in Verilog against a model that saw every logged byte, `RUN_LIST`s included.

`tb_frame_replay` is Chapter 5.7's testbench with this stream. For each window frame it also checks:

- every CS frame closed, and the list's `FLIP` was requested, before blanking began (the loop's timing rule),
- the frame's byte count matches the log and is under 1024,
- every reply byte matches the model's.

`compare.py` checks the pixels.

```sh
.venv/bin/python sim/py/ref_replay.py --choose            # prints the window and why it was chosen
.venv/bin/python sim/py/ref_replay.py --stream sim         # replay_stream.hex, replay_replies.hex, replay_bytes.hex
make frame-replay                                          # about 5 minutes
```

`REPLAY_START=240` picks another window (`REPLAY_COUNT` must stay 6, the testbench's `NFRAMES`).

## 9. Does it catch mistakes?

A test that never fails proves nothing. The replay was run against planted faults: six one-line edits of the design, and faults in the log, the stream and the timing of the replay.

| Fault | Where | Result |
| --- | --- | --- |
| `MOVE_SPRITE` writes eight bytes, not four | `cmd_engine.v` | compare: 6 of 6 frames differ |
| `WRITE_LIST_WORD` writes one byte higher | `cmd_engine.v` | compare: 1 of 6 differs (f5, the frame that shows the window's one patch) |
| a column write lands one map row lower | `cmd_engine.v` | compare: 6 of 6 frames differ |
| scroll registers not shadowed (live at once) | `tile_layers.v` | compare: 6 of 6 frames differ |
| sprite table not shadowed | `sprite_lines.v` | compare: 6 of 6 frames differ |
| 17 sprites per line instead of 16 | `sprite_lines.v` | compare: 6 of 6 frames differ |
| prefix without its map writes | stream | compare: 6 of 6 frames differ |
| prefix without frame 150 | stream | **PASS** (see below) |
| prefix without frame 184 | stream | compare: 6 of 6 frames differ |
| warm-up frame left out | stream | compare: f0 differs |
| stream built for a window one frame later | stream | testbench PASS, compare: 6 of 6 differ |
| each window batch sent at line 440 instead of when `VSYNC` falls | testbench | testbench FAIL: `FLIP` requests fell in blanking |
| one byte deleted from a window frame | stream | testbench FAIL: the last `GET_INFO` shows flags `06` |
| an `F` line's byte count changed | log | `ref_replay.py` stops: the frame has 17 bytes, its `F` line says 117 |
| a patch that turns the HUD list's `CLEAR` into something else | log | `ref_replay.py` stops: the `RUN_LIST` does not just redraw the framebuffer |

The shifted window is a case for `compare.py`. The stream is consistent with itself, so the testbench passes it; only the pixels show that it is the wrong frames.

The missing frame 150 is a warning about what a window can prove. A lost write is caught only if its effect lasts into the window. Frame 150 sent only sprite moves and scrolls, and later frames overwrote all of them, so the window looks the same. (So does dropping frame 185, for the same reason.) Dropping frame 184, whose map column is still on screen in the window, is caught in all six frames. The test checks the frames it dumps; the argument of section 8 covers the rest.

## 10. On the board, and on a microcontroller

With Chapter 5.1's wiring and the Chapter 5.7 bitstream:

```sh
.venv/bin/python host/side_scroller.py --freq 5000000 --log run.log
```

It prints the same report, plays 600 frames and ends with the error flags and the number of **late** frames: frames whose batch plus the HUD drawing did not end before blanking. The PC busy-waits on the `VSYNC` pin through USB, so a frame can start a millisecond or so late. That is still well inside the budget of section 4. Experiments:

- lower `--freq` until the check says `TOO SLOW`,
- send a `WRITE_LIST_WORD` after the `RUN_LIST` and watch it show a frame late,
- lengthen the first chain to 30 coins and watch more of it disappear.

The project's goal is a GPU driven by a **microcontroller**, and this loop moves to one unchanged. The packets are the same bytes; only timing and pins change.

- **SPI and pins.** Use an SPI master in mode 0, MSB first, at 5 MHz or below (the slave's limit is about 6 MHz, Chapter 5.1). An ordinary GPIO can serve as chip select, and `READY` and `VSYNC` are two input pins.
- **Timing.** Put a falling-edge interrupt on `VSYNC`. Its handler starts sending the batch the main loop prepared during the previous frame, one CS frame at a time, and checks `READY` before each (one pin read). The CS overhead drops from about 1 ms to a few microseconds, and the slowest frame takes 2.1 ms of the 15.36.
- **The game.** `Game.frame` is integer arithmetic over 26 sprites. The terrain heights, the coin arc and the level become tables or integer functions. The art and the setup bytes can be built on a PC and stored in flash.

The log is itself a complete program for the GPU. An MCU can play `side_scroller.log` back with no game code at all: send the `S` lines, each frame's after its `VSYNC` fall. The board then shows the frames the simulator dumped, because both were fed the same bytes. No microcontroller is needed for this chapter; the Python host and the model are the reference.

## Testbench requirements

**Hardware:** Chapter 5.7's design, unchanged (`GET_INFO` replies `47 01 FF 03 40 01 F0 00 ER`).

**Python:** `host/side_scroller.py` (this chapter) uses, from earlier chapters, `host/gpu_model.py` (5.7, with `raster_model.py` from 4.6), `host/spi_test.py` (5.2), `host/layer_demo.py` (5.3), `host/level_stream.py` and `host/stream_demo.py` (5.6) and `host/hud_demo.py` (5.7). `sim/py/ref_replay.py` uses `sim/py/ppm.py` and `host/gpu_model.py`.

**`side_scroller.py --model`** prints `PASS: side_scroller (model)` only if:

- every frame is under 1024 bytes and ends with its only `RUN_LIST`,
- no CS frame exceeds 257 bytes,
- the slowest frame fits in the visible part at `--freq` and `--cs-overhead-us`,
- two builds of the run give the same bytes,
- after every frame the model's map, scrolls, sprite table and HUD match the host's, and no error flag is set at the end.

**`ref_replay.py`** reads `sim/side_scroller.log` (or `$REPLAY_LOG`), validates it (every `F` count, an `E` record, every `RUN_LIST` a full redraw), picks the window (or `$REPLAY_START`), writes the stream, the replies, and the window's byte counts (`--stream sim`), and writes the expected frames (`ref_replay.py build`).

**`tb_frame_replay`** (about 5 minutes, `make frame-replay` from the repo root) plays `sim/replay_stream.hex` over SPI at 5 MHz, dumps six consecutive 640 x 480 frames and prints `PASS: tb_frame_replay (...)`. It fails on:

- a frame-dump geometry or X/Z error,
- unequal sync/`de` delay (latency 1 to 24 clocks),
- a status byte without the `01` signature, or a FIFO overflow,
- a window frame whose CS frames or list `FLIP` fall in blanking,
- a reply byte different from `sim/replay_replies.hex` (the last `GET_INFO` must show flags `00`),
- a window frame whose byte count differs from `sim/replay_bytes.hex` or reaches 1024,
- a stream whose frame count differs from `NFRAMES`, a link that stops answering, or the watchdog.

It does **not** check pixels: `compare.py` does, with zero tolerance.

**Files:** `side_scroller.py` to `host/`; `tb_frame_replay.v` to `sim/`; `ref_replay.py` to `sim/py/`. Add `FRAME_TESTS += replay`. Commit the generated log, stream, replies and byte counts. Then:

```sh
.venv/bin/python host/side_scroller.py --model --log sim/side_scroller.log
.venv/bin/python sim/py/ref_replay.py --stream sim
make frame-replay
```
