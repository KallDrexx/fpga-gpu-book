# Chapter 5.7 - Display Lists

Chapter 5.6 got a scrolling level down to about 150 bytes a frame. What is left in a typical game frame is the HUD: a score, a lives counter, a timer. It looks the same from one frame to the next, yet a host that draws it with ordinary commands sends all of it every frame, 100 bytes or more, to show a number that changes once a second. In the framebuffer it has no choice: Chapter 4.5's back buffer holds the picture from two frames ago, so something drawn only once shows on every second frame.

The fix is to **store the commands in the GPU** and let the host say "run them" instead of sending them again. This chapter adds a small command memory, two ways to run what is in it (on request, or by itself at every vertical blanking) and a command that changes **one word** of it. The host then sends only the digits that changed.

By the end of this chapter you will have:

- a **list RAM** (8 lists of up to 512 bytes), five commands from `DEFINE_LIST` (`0x50`) to `SET_AUTO_LIST` (`0x54`), and capability bit 8 in `GET_INFO`,
- a second byte source in front of the command parser, so that a list is run by the parser that already runs everything else,
- `sim/tb_frame_display_lists.v` and `sim/py/ref_display_lists.py`: eleven frames in which the GPU redraws a game screen with a four-digit score at every blanking while the host sends nothing but the digits that changed,
- `host/hud_demo.py`: the same HUD on the board, with a `--model` mode.

## 1. Display lists

The idea is old: **record** a sequence of commands once, **replay** it as often as needed, and **patch** the few values that change. OpenGL called such a recording a *display list* (`glNewList`, `glCallList`), which is where this chapter's name comes from, and today's GPUs replay recorded command buffers the same way. The Amiga's Copper coprocessor went one step further: it ran a short list by itself at every frame, at no cost to the CPU. `SET_AUTO_LIST` does the same.

What we build is the smallest useful form: a list is **a byte string of the commands you already have**. It has no jumps, no loops and no commands of its own. The parser already knows how to run commands; it only needs a second place to read them from.

## 2. Storing and patching lists

Chapter 5.2 reserved `0x50` to `0x5F` for this. The choices below are the reference design's; where there was a reasonable alternative, it is named.

**Memory: 8 slots of 512 bytes.** The list RAM is 4,096 bytes, exactly one RAMB36E1. Byte `offset` of list `id` is at address `{id, offset}` (3 bits and 9 bits), so a slot is found by concatenation, like Chapter 5.6's map addresses. Chapter 4.8's bump allocator would waste nothing, but it needs an allocation table and a policy for a list that no longer fits. Fixed slots waste RAM (a 20-byte list takes 512) and give every list a hard limit that no offset can pass by mistake. 512 bytes is about 40 drawing commands or 70 `MOVE_SPRITE`s. Each list also has a **length**, 0 to 512, kept in a table of eight 10-bit registers. Only the first `length` bytes are the list; bytes behind it stay in the RAM but never run.

**Writing: `DEFINE_LIST`, then `APPEND_LIST`.** `DEFINE_LIST id` sets the length to 0. `APPEND_LIST id, bytes` adds up to 254 bytes at the end. The host never computes an offset, and the length is right by construction. Like `UPLOAD_TILES`, `APPEND_LIST` is **streamed**: each byte is written as it leaves the FIFO, so the engine needs no 254-byte buffer. Bytes past 512 are dropped with no flag, the rule of Chapter 5.3's tile uploads.

**Patching: `WRITE_LIST_WORD id, offset, value`** writes two bytes, low first, at a **byte** offset. A word, because many command fields are 16 bits (a `BLIT`'s x and y, a `FILL_RECT` corner); a byte offset, because a field sits wherever its command put it, odd or even. The host works the offsets out when it builds the list (`hud_demo.py` keeps them in a dictionary). To change a one-byte field, such as the asset id of a digit's `BLIT`, write the word that starts there and give the second byte (here the low byte of the `BLIT`'s x) its old value.

**What a list may not hold.** A list goes through the same parser, so almost any command can be in it. These cannot:

| Not allowed | Why |
| --- | --- |
| `GET_INFO`, `FB_CRC` | They make replies, and a list has no link to send them to. |
| `CLEAR_ERRORS` | A list must not hide the flags that its own run sets. |
| `UPLOAD_ASSET`, `UPLOAD_TILES` | Long uploads are the host's job, and a list that rewrote an asset it draws from would be a bug waiting to happen. |
| every opcode from `0x50` to `0x5F` | **No nesting.** A list that could run a list could run itself forever, and the engine has no stack to remember the way back. A list that could patch lists would modify itself. Without them, a run is at most 512 bytes long. |

Such a packet is **skipped whole** (its length byte is read as usual, so the next packet is found) and sets `BAD_OP`: inside a list these opcodes count as unknown. The check is made **when the list runs**, because that is when the parser sees the bytes. `FLIP` is allowed; section 3 shows why a list needs it.

## 3. Running a list

**A second byte source.** The parser reads an opcode, a length and a payload, and does not care where the bytes come from. Put a multiplexer in front of it. While a list runs, the parser reads list RAM at `{id, position}`, from 0 up to the length the list had when the run started, and takes **nothing from the FIFO**: the host's bytes wait there in order (and `READY` falls as usual if they pile up). Every parser rule holds inside a list: the length checks, `BAD_LEN`, `BAD_OP`, the streamed commands. List bytes carry no start-of-frame bit, so Chapter 5.2's frame-start reset never applies to them.

A run starts only **between packets** and ends after its last byte. If the length **ends inside a packet**, that packet is treated as a frame start would treat it: it does nothing (a streamed command keeps what it wrote), `TRUNC` is set, and the parser is left between packets, so the host's next byte starts a packet.

A run is never interrupted, so a host's `WRITE_LIST_WORD` cannot land in the middle of one: it waits in the FIFO until the run ends. Every run draws wholly with the old word or wholly with the new one.

**Two ways to start a run.** `RUN_LIST id` starts one at the end of its own packet. `SET_AUTO_LIST id` makes the GPU run list `id` by itself at every vertical blanking, until `SET_AUTO_LIST FF`. The automatic run follows four rules:

1. The start of vertical blanking (the rising edge of `i_vblank`) makes a run **due**. At most one run is due at a time: blankings that pass while one is due add nothing.
2. A due run **starts when the parser is between packets and the FIFO is empty.** The host's waiting bytes go first. This is a rule, not a preference; the next paragraphs show why.
3. A `SET_AUTO_LIST` executed after a blanking has started does not make that blanking's run due. If it turns the automatic run on, the first run is due at the next blanking. If it turns it off, a run that is due is cancelled.
4. When nothing holds it up, a due run starts within **100 µs**. The reference starts it a few clocks after it falls due.

**A list that is a whole frame.** The HUD must be on *every* displayed frame, and the framebuffer is double-buffered. So make the list the whole frame: it clears the back buffer, draws the scene and the HUD, and ends with `FLIP`. Whichever buffer it draws into, every picture that goes on the screen is complete. Run automatically, such a list is a loop of one redraw per frame:

```
blanking k     swap: run k-1's picture goes on screen
               host bytes waiting in the FIFO run          (rule 2)
               run k starts                                 (due since the start of this blanking)
frame k        run k draws for about 3.4 ms, reaches its FLIP and waits
blanking k+1   swap: run k's picture goes on screen
               ...
```

The list's `FLIP` ends at the swap in blanking k+1, the same blanking at which the next run falls due, so the loop never loses a frame. This relies on the list drawing for longer than the blanking lasts (1.44 ms). Chapter 4.5 allows the swap anywhere in the blanking, and a `FLIP` requested *inside* a blanking may swap in that blanking or in the next one, depending on the design. A list that takes longer than the blanking requests its `FLIP` in the visible part of the frame, so every legal design swaps at the next blanking.

Now rule 2 makes sense. The engine is busy all frame (drawing, then waiting in `FLIP`) and is free only at the swap. If the due run went first there, the host's bytes would wait behind the next run too, and the next: they would never run.

**When a patch shows.** A patch sent in the visible part of frame N waits behind run N's `FLIP`, runs at the swap in the blanking after frame N, is drawn by the run that starts there, and that picture goes on screen at the following blanking: the change shows in **frame N + 2**. Send patches in the visible part of the frame, not in blanking or in its first few lines: a design may swap at the end of the blanking and then start the run up to 100 µs later, and a patch that arrives before the run has started is drawn by it, one frame early.

**Sharing the frame with the host.** In this loop every host command runs in the short gap after a swap, in vertical blanking, just before the next run. That suits some commands and not others:

- **Fine:** patches, and commands that write memory at once (`WRITE_TILE_MAP`, the column and row writes, `SET_PALETTE`, `UPLOAD_ASSET`, `UPLOAD_TILES`). Written in blanking, they are absent from the frame that just ended and complete in the next one.
- **Not fine:** commands that change shadowed settings (`SET_SCROLL`, `SET_TILE_LAYER`, the sprite commands, the compositor commands). The GPU copies the shadows at the start of blanking, the same moment, so which frame they reach is not defined, and protocol section 11 forbids sprite commands in the first 5 µs of blanking.
- **Not fine either:** drawing commands and `FLIP`. The list owns the framebuffer.
- **Bytes:** everything the host sends in a frame waits in the FIFO until the next swap. Past about 250 bytes, `READY` falls and stays low until then.
- **A busy host delays the run.** If the host's bytes keep the engine busy after the swap, the run starts late. If it then misses the next blanking, the previous picture (with its HUD) stays up for one more frame, and the blanking it missed adds no extra run.

So the automatic run is for a host with little to say: it is what makes "the host sends only the changed digits" true. A game that also scrolls and moves sprites every frame (Chapter 5.8) drives the frame itself: once per frame, **after VSYNC falls** (vertical blanking has ended), it sends that frame's updates followed by `RUN_LIST` of the frame's list. The previous `FLIP` finished during the blanking, so the engine runs the batch at once, in the visible part of the frame. The shadowed settings are copied at the next blanking, and the list's `FLIP` swaps at that same blanking: everything sent in frame N shows in frame N + 1, for the 3 bytes of a `RUN_LIST`. Send nothing more in that frame: bytes behind the `RUN_LIST` wait for its `FLIP` and run in blanking.

**Errors and frame starts.** A list sets the flags by the usual rules (`BAD_LEN`, `BAD_OP` for an unknown or forbidden opcode, `TRUNC` when it ends inside a packet) and cannot clear any. A frame start (Chapter 5.2) in the host's bytes affects only the host's packets: a list never starts in the middle of a host packet, and the first host byte after a list is the first byte of a packet. An `APPEND_LIST` cut by a frame start keeps the bytes that arrived and sets `TRUNC`, like the other streamed commands.

## 4. The commands

| Op | Name | len | Payload |
| --- | --- | --- | --- |
| `50` | `DEFINE_LIST` | 1 | id (0 to 7): length := 0 |
| `51` | `APPEND_LIST` | 1 + n | id, then n bytes (n at most 254) added at the end |
| `52` | `RUN_LIST` | 1 | id: run the list now |
| `53` | `WRITE_LIST_WORD` | 5 | id, byte offset (16-bit), value (16-bit): two bytes, low first |
| `54` | `SET_AUTO_LIST` | 1 | id, or `FF` to stop |

- An id of 8 or more, or a `RUN_LIST` of an empty list: **nothing happens and no flag is set**. `SET_AUTO_LIST` with any value other than 0 to 7 or `FF` does nothing.
- `WRITE_LIST_WORD`: a byte whose offset is 512 or more is dropped, **byte by byte**: offset 511 writes one byte, and nothing ever spills into the next slot or wraps to the start of this one. The length does not change, even if the offset is behind it.
- `APPEND_LIST` with only the id appends nothing and is legal. Wrong lengths, including `APPEND_LIST` with no payload at all, are `BAD_LEN`. `55` to `5F` stay unknown (`BAD_OP`).
- **Capability bit 8** (`DISPLAY_LISTS`): `gpu_link` answers `GET_INFO` with `47 01 FF 03 40 01 F0 00 ER`. `uart_gpu` keeps `0F 00`.

The updated spec is [`protocol.md`](protocol.md): five rows in section 3, bit 8 in section 8, the flags in section 9 and a new section 14. Replace your `docs/protocol.md` with it.

## 5. The command engine

Only `cmd_engine` and the capability word change. In `gpu_link`, wire `i_vblank` (already there for the status byte) to a new engine input and make the capability word `16'h03FF`. In `uart_gpu`, tie the new input to 0. Nothing on the video side changes: a list uses no new port.

**List RAM.** `reg [7:0] lmem [0:4095]`, with one write port for the engine and a registered read on every clock (a 1-clock read like the FIFO's), which yosys maps to one RAMB36E1. Beside it the table of eight 10-bit lengths.

**Registers.** `l_act` (a list is running), the list's id, the position (10 bits, so that it can reach 512) and the end; for the automatic run `auto_en`, `auto_id` and `auto_pend`; and the previous value of `i_vblank`.

1. **The mux.** The parser's byte and start-of-frame bit come from `l_act ? {0, list byte} : FIFO`. The FIFO's `rd_en` must not fire while `l_act` is set.
2. **Starting a run.** `RUN_LIST` in the execute state: if `id < 8` and the length is not 0, set `l_act`, the id, position 0 and the end (the length now), and return to the fetch state.
3. **Fetching a list byte.** In the fetch state with `l_act`: if the position equals the end, the run is over: clear `l_act`, and if the parser is not between packets (`phase != 0`), set `TRUNC` and reset the phase. Otherwise advance the position and go to the byte state. The RAM was read during the fetch cycle, so the byte is waiting in the byte state: the FIFO's timing, with no extra state.
4. **Forbidden opcodes.** In the byte state, for an opcode byte that came from the list: if it is `04`, `09`, `0B`, `0C`, `12` or `50` to `5F`, store instead an opcode that will never be assigned (the reference uses `FE`). The existing code then skips the payload by its length and sets `BAD_OP` in the execute state.
5. **The commands.** `DEFINE_LIST`: the length of `id` := 0. `APPEND_LIST` is streamed: for payload byte *n* of 1 or more, if `arg[0] < 8` and the length is below 512, write the byte at `{id, length[8:0]}` and add one to the length; the packet ends at its last byte, like `UPLOAD_TILES`, so the length table marks `0x51` as never valid in the execute state (only a packet with no payload gets there, and it is `BAD_LEN`). `WRITE_LIST_WORD`: two writes, the second in a one-state follow-up; check each byte's own offset (`offset < 512`, then `offset + 1 < 512`) before writing it. `SET_AUTO_LIST`: `FF` clears `auto_en` and `auto_pend`; 0 to 7 sets `auto_en` and `auto_id`; anything else does nothing.
6. **The automatic run.** On a rising edge of `i_vblank` while `auto_en` is set, set `auto_pend`. In the fetch state, when no list runs, the FIFO is empty, `phase == 0` and `auto_pend` is set: clear `auto_pend` and start list `auto_id` as in step 2. Test the FIFO **before** the pending flag: that is rule 2. A reset clears `l_act`, `auto_en` and `auto_pend`.

**Pitfalls.**

- Make a run due only at the blanking edge, never when `SET_AUTO_LIST` executes (rule 3). A design that does shows a picture in frame `f0` of the test.
- Every use of the incoming byte in the byte state must take the muxed byte, including the test for a zero length. One forgotten use of the FIFO's output still passes every earlier test and misparses lists.
- Require `phase == 0` before starting a run. Otherwise a host packet whose tail has not arrived yet is cut in two by the list.
- In the `WRITE_LIST_WORD` follow-up, compute `offset + 1` with enough bits. A 9-bit sum wraps offset 511 to byte 0 of the same list; a 12-bit `{id, offset} + 1` spills into the next list.
- A list's `FLIP` stalls the engine in the existing `FLIP` state; it needs nothing special. Read the length when the run starts, not at every byte.

## 6. The Python model

`host/gpu_model.py` (a backward-compatible replacement) has the five commands, capability bit 8 (`CAPS_LISTS`; `CAPS_SPI` is now `0x03FF`) and the builders:

```python
import gpu_model as g
g.define_list(id); g.append_list(id, data); g.list_packets(id, data)   # define + append, in 254-byte packets
g.run_list(id); g.write_list_word(id, offset, value); g.set_auto_list(id)   # g.AUTO_OFF stops
gpu = g.Gpu()
gpu.run_list(id)    # what RUN_LIST does
gpu.auto_run()      # what the GPU does at a blanking when the automatic run is on
```

`Gpu.run_list()` is section 3's rules in about 25 lines: a forbidden opcode skips its packet and sets `BAD_OP`, and a packet the length cuts short goes through `abandon()`, as a frame start would send it. The model has no clock: `feed()` runs a `RUN_LIST` to the end, `FLIP` included, and whoever plays the blanking calls `auto_run()`. The self-check still passes: `.venv/bin/python host/gpu_model.py` (it needs Chapter 4.6's `host/raster_model.py` next to it).

## 7. Simulate

The testbench is Chapter 5.6's: `vga_timing`, `gpu_link` and `fb_scanout` wired as in `top_artyz7.v`, and an SPI master playing a stream. Copy `ref_display_lists.py` to `sim/py/` (it imports `ref_layers.py` and `host/hud_demo.py`), `tb_frame_display_lists.v` to `sim/` and `hud_demo.py` to `host/`. Add `FRAME_TESTS += display_lists` (not `lists`: a test name must not be a prefix of another's), write the stream once (commit the three `sim/display_lists_*.hex` files) and run:

```sh
.venv/bin/python sim/py/ref_display_lists.py --stream sim
make frame-display_lists
```

**Setup.** Both framebuffers cleared (`CLEAR`, `FLIP`, `CLEAR`), a palette, the ten digit glyphs and a diamond marker as 8 x 8 assets, and six lists:

| List | Holds | Tests |
| --- | --- | --- |
| 0 | The game frame: `CLEAR`, a big triangle, a stripe, a line, the HUD panel, four `BLIT`s of digit glyphs (the score), the marker, a small triangle, `FLIP` (108 bytes) | the automatic run, patches |
| 1 | A second picture. It is first written 2 bytes longer (a second `FLIP`), then **defined again** shorter | `DEFINE_LIST` empties; nothing behind the length runs |
| 2 | `CLEAR`, a rectangle, then **every forbidden command** (`RUN_LIST 3`, `GET_INFO`, `FB_CRC`, `UPLOAD_ASSET`, `UPLOAD_TILES`, a `WRITE_LIST_WORD` that would change list 0, `DEFINE_LIST`, `SET_AUTO_LIST`, `CLEAR_ERRORS`), a rectangle, `FLIP` | the forbidden opcodes: nothing reaches the replies, the flags or the other lists |
| 3 | A full-screen red fill that only a nested `RUN_LIST` would draw | no nesting |
| 4 | 508 bytes of `NOP`, then the first 4 bytes of an 11-byte `FILL_RECT`; afterwards a `WRITE_LIST_WORD` at offset 511, whose second byte (`07`, a `FLIP` opcode) must go nowhere | the 512-byte limit; `TRUNC` at the end of a run; no spill or wrap |
| 5 | `CLEAR`, a rectangle, `FLIP`: list 4's neighbour | |

List 0 draws for about 3.4 ms before its `FLIP` (the `CLEAR` writes one pixel per clock, 1.5 ms at 50 MHz, and the big triangle takes about as long; measured in simulation; yours may differ a little). That is comfortably more than the 1.44 ms of blanking, as section 3 requires.

Then packets that must **change nothing and set no flag** (ids 8, 9 and 200, an empty list, offsets 512, 600 and 65,535, `SET_AUTO_LIST` with 8, 100 and 254, an `APPEND_LIST` with only an id), checked by a `GET_INFO` (flags `00`); then the five commands with wrong lengths and the reserved `0x55` and `0x5F`, checked by a second `GET_INFO` (`06`, `BAD_LEN | BAD_OP`), and `CLEAR_ERRORS`.

**Eleven frames**, `f0` to `fa`. "Frame *k*" is the *k*-th video frame after the setup, and `fk-1` is the file dumped for it. The host sends everything at line 20 of the frame: in the visible part, about 2 ms after blanking began, while the reference's run of list 0 is still drawing.

| Frame | The host sends | The screen shows |
| --- | --- | --- |
| 0 | `SET_AUTO_LIST 0` | (not dumped) |
| 1 (f0) | nothing | empty: **no run before the first blanking** |
| 2 (f1) | patch: the ones digit becomes 1 | list 0, score 0000 |
| 3 (f2) | two patches: tens 1, ones 0 | 0000: the loop redraws with no change |
| 4 (f3) | the marker's x, one word | **0001**: two frames after its patch |
| 5 (f4) | `SET_AUTO_LIST 1` | **0010** |
| 6 (f5) | list 1's colour (one word, with the next opcode behind it); list 0's hundreds digit (list 0 is not running) | list 0 with the marker moved |
| 7 (f6) | `SET_AUTO_LIST FF`; a second patch of list 1's colour (no run follows: it never shows) | list 1 |
| 8 (f7) | `RUN_LIST 2` (the engine is idle, so it runs at once) | list 1 with the first new colour |
| 9 (f8) | `SET_AUTO_LIST 0`, `RUN_LIST 4`, `RUN_LIST 5` | list 2's picture (the forbidden commands skipped) |
| 10 (f9) | `GET_INFO` | list 5's picture |
| 11 (fa) | | **list 0 with the score 0510**: the automatic run, back on |

Frames 1 to 4 are the **HUD window**: the host sends 0, 7, 14 and 7 bytes, all of them `WRITE_LIST_WORD`s, and frames `f1` to `f5` show the whole game screen with its score. That is the "Done when" of this step. The stream marks the window with two directives (`5000`, `5001`), and the testbench checks its own traffic there: every packet must be opcode `53` with length 5, and the byte counts must match `sim/display_lists_bytes.hex`. This checks the stream, not your design (a stream you edit or regenerate must still keep the host's side of the bargain); the design's side, the full picture on every frame, is checked by `compare.py`.

The last `GET_INFO` shows `BAD_OP | TRUNC` (`0C`): the first from list 2, the second from list 4. The three replies are in `sim/display_lists_replies.hex`. The testbench dumps `build/frame_display_lists_f0.ppm` to `fa.ppm` and takes about 6 minutes.

**Legal variants.** Three behaviours must pass and are worth trying on your design: a swap at another point of the blanking (Chapter 4.5's `SWAP_*` variants, especially the last line of blanking), an automatic run that starts up to 100 µs late, and another patch line: `HUD_PATCH_LINE=100` in the environment of both `--stream` and `make` (any line from about 10 to 400 is legal).

**When it fails**, read the testbench's message first, then the first frame and pixel that `compare.py` reports:

- `f0` is not empty: the automatic run started when `SET_AUTO_LIST` executed, not at the blanking.
- "The link stopped answering" (a `GET_INFO` reply that never comes): the due run starts before the host's waiting bytes. Rule 2 is broken, and the host never gets a turn.
- Patches appear one frame early, or the flags are wrong: host packets were let in between the packets of a running list.
- One digit changes but its neighbour changes too, or the marker jumps: the patch went to the wrong offset, or its two bytes were swapped.
- A frame that should show list 1 shows list 0: the list id.
- Red in list 2's frame: the nested `RUN_LIST` ran. A frame that shows the previous picture again: a list ran past its length into stale bytes (list 1's second `FLIP`), or offset 511's second byte landed in list 4 or 5.
- The reply bytes are wrong: a forbidden `GET_INFO` or `FB_CRC` answered, or a flag is missing or extra.
- `f9` is not list 5's picture: the end of list 4 left the parser in the middle of a packet, and the host's `RUN_LIST 5` was taken as the rest of it.

**The other testbenches.** Everything from Chapter 5.6 still passes, with these edits:

- **`tb_frame_layers`, `tb_frame_sprite_table`, `tb_frame_compositor` and `tb_frame_streaming`**: the capability word is now `FF 03`. In `main()` of `sim/py/ref_layers.py`, `ref_sprite_table.py`, `ref_compositor.py` and `ref_streaming.py`, change `0xFF, 0x02` to `0xFF, 0x03` in the `GET_INFO` assertion (`ref_streaming.py` has it twice), and change `FF 02` to `FF 03` in the failure messages of the four testbenches. Rerun the four `ref_*.py --stream sim` (they rewrite their `*_replies.hex`; nothing else changes).
- **No new ports**: `gpu_link` already has `i_vblank`, so no testbench needs a tie-off.
- **`tb_gpu_fuzz`**: this chapter ships replacements for `tb_gpu_fuzz.v` (only the capability byte changes, to `03`) and `ref_fuzz.py`. The list commands are now known opcodes, with wrong-length rounds; rounds that define, append, run and patch lists (garbage included, and lists that hold forbidden commands, end inside a packet or are defined again shorter); and an `APPEND_LIST` cut inside its data. The fuzz never turns the automatic run on: its blankings come at times the model does not know. Regenerate with `.venv/bin/python sim/py/ref_fuzz.py sim`. It takes about 5 minutes.

## 8. On the board

| Resource | Chapter 5.6 | Chapter 5.7 | Of |
| --- | --- | --- | --- |
| LUTs | about 7,090 | about 7,350 | 106,400 |
| RAMB36E1 | 54 | 55 | 140 |
| RAMB18E1 | 2 | 2 | 280 |
| DSP48E1 | 20 | 20 | 220 |

The list RAM is the one new RAMB36E1. The rest, 250 to 300 LUTs and about 70 flip-flops, is the engine's new states, the mux and the eight lengths (two builds gave 7,330 and 7,410 LUTs). `clk_pix` closes above 140 MHz against 25. `clk_sys` closed at 61 and 65 MHz against 50; as in Chapters 5.5 and 5.6 the critical path is in the triangle rasterizer and moves from build to build, so expect your numbers to differ by a few percent.

## 9. The demo

`host/hud_demo.py` builds section 7's list 0, uploads the glyphs and the list once, sends `SET_AUTO_LIST 0`, and plays a made-up game: the score goes up by 10 every 7 frames, by 100 every 50 and by 1000 once, and every 25 frames the marker moves with a one-word patch. Each frame, once `VSYNC` has fallen, it sends the patches for the digits that changed, and at the end it prints the bytes it used.

Try it without the board first: `.venv/bin/python host/hud_demo.py --model` runs 120 frames against the Python model and, after every frame, checks the screen against the same picture drawn with ordinary commands and no list. It prints:

```
120 frames: 168 bytes sent in all, 1.40 per frame on average, at most 14; 98 frames sent nothing
the list is 108 bytes, sent once; drawing the same picture with ordinary commands every frame would cost 108 bytes each, 12960 in all
PASS: hud_demo (model)
```

Then, with Chapter 5.1's wiring: program the bitstream; `.venv/bin/python host/spi_info.py` lists `DISPLAY_LISTS` (caps `03FF`); run `.venv/bin/python host/hud_demo.py --freq 5000000`. For ten seconds a green hill, a red stripe, a white line and a cyan triangle stay still while four yellow digits count and a diamond steps to the right. Experiments: stop the loop with `SET_AUTO_LIST FF` and watch the picture freeze with the last score; make a list that draws for longer than a frame and watch the score lag; patch a digit with a word whose high byte is wrong and watch the glyph jump.

## Testbench requirements

**Modules.**

- **`gpu_link`**: Chapter 5.6's ports, unchanged; `i_vblank` (on `clk_sys`) is also the signal whose rising edge makes the automatic run due. `GET_INFO` replies `47 01 FF 03 40 01 F0 00 ER`. **`fb_scanout`** and `uart_gpu`'s interface: unchanged.
- **List RAM and commands** `0x50` to `0x54` as in `protocol.md` sections 3 and 14: ids 0 to 7, 512 bytes each; `DEFINE_LIST` sets the length to 0; `APPEND_LIST` streamed, up to 512 bytes, the rest dropped, a cut packet keeps its bytes and sets `TRUNC`; `RUN_LIST` runs the length read when it starts, an empty list does nothing; `WRITE_LIST_WORD` writes `offset` and `offset + 1` of the same slot, each byte dropped if its offset is 512 or more; ids of 8 or more and `SET_AUTO_LIST` values other than 0 to 7 and `FF`: no effect, no flag; wrong lengths `BAD_LEN`; `55` to `5F` unknown.
- **Running a list:** no FIFO byte is taken while a list runs; `GET_INFO`, `FB_CRC`, `CLEAR_ERRORS`, `UPLOAD_ASSET`, `UPLOAD_TILES` and `50` to `5F` in a list are skipped whole and set `BAD_OP`; `FLIP` is allowed; a list that ends inside a packet sets `TRUNC` and leaves the parser between packets.
- **Automatic run:** due at the rising edge of `i_vblank` while on; at most one due; starts only when the parser is between packets and the FIFO is empty, then within 100 µs; a `SET_AUTO_LIST` executed after the edge does not make that edge's run due, and one that turns the run off cancels a due run; a reset stops the list and the automatic run.
- **Python**: this chapter's `host/gpu_model.py`, `host/hud_demo.py` and `ref_display_lists.py`.

**`tb_frame_display_lists`** (about 6 minutes, `make frame-display_lists` from the repo root). Plays `sim/display_lists_stream.hex` over SPI at 5 MHz, dumps eleven consecutive 640 x 480 frames at 8 bits per channel, and prints `PASS: tb_frame_display_lists (...)`. It fails on a frame-dump geometry or X/Z error, unequal sync/`de` delay (any latency from 1 to 24 clocks), a status byte without the `01` signature, a FIFO overflow, a command frame sent too late, `GET_INFO` replies other than `sim/display_lists_replies.hex` (flags `00`, `06` and `0C`), or HUD-window traffic other than `WRITE_LIST_WORD`s in the counts of `sim/display_lists_bytes.hex` (0, 7, 14, 7). It does **not** check pixels: `compare.py` does. Any swap point in the blanking and an automatic run up to 100 µs late must pass.

**`tb_gpu_fuzz`**: Chapter 5.6's test with this chapter's `tb_gpu_fuzz.v` and `ref_fuzz.py`. **`tb_frame_layers`, `tb_frame_sprite_table`, `tb_frame_compositor` and `tb_frame_streaming`** need the capability edit of section 7.

**Files:** `protocol.md` to `docs/`; `tb_frame_display_lists.v` and `tb_gpu_fuzz.v` to `sim/`; `ref_display_lists.py` and `ref_fuzz.py` to `sim/py/`; `gpu_model.py` and `hud_demo.py` to `host/`. Add `FRAME_TESTS += display_lists`. Then:

```sh
.venv/bin/python sim/py/ref_layers.py --stream sim          # after the 0xFF, 0x03 edits
.venv/bin/python sim/py/ref_sprite_table.py --stream sim
.venv/bin/python sim/py/ref_compositor.py --stream sim
.venv/bin/python sim/py/ref_streaming.py --stream sim
.venv/bin/python sim/py/ref_display_lists.py --stream sim
.venv/bin/python sim/py/ref_fuzz.py sim
make frame-display_lists
```
