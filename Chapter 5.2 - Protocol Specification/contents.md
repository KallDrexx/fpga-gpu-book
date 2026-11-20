# Chapter 5.2 - Protocol Specification

Chapter 5.1 gave the GPU a second way in. Before the next chapters add tile layers, sprites and display lists, the protocol needs two things: a written definition that stays put while commands are added, and a parser that cannot be knocked off course. An MCU will crash, reset in the middle of a packet, and suffer wiring glitches. After each of those, raising `CS` must be enough to make the GPU usable again.

By the end of this chapter you will have:

- `docs/protocol.md`, the written spec, supplied as [`protocol.md`](protocol.md) next to this chapter,
- a frame-start bit in the command FIFO, so each SPI frame starts the parser afresh,
- `GET_INFO` (`0x0B`), `CLEAR_ERRORS` (`0x0C`) and four sticky error flags,
- `sim/tb_gpu_fuzz.v` passing: garbage and valid packets, frame after frame, with every reply, both framebuffers and the palette checked against the Python model,
- `host/gpu_model.py` updated to the same rules, and `host/spi_info.py`.

## 1. The spec

Copy [`protocol.md`](protocol.md) to `docs/protocol.md` and read it through once now. It is the page to open when you write host code. Most of it collects what Chapters 4.2 to 5.1 decided: links, packet layout, byte order, the opcode table, replies and the status byte. The new parts are what this chapter builds: frame starts (its section 4), `GET_INFO` (section 8) and the error flags (section 9). Two more help the chapters to come:

- **Opcode ranges.** Each later chapter gets a block of 16 opcodes: `0x10` tile layers, `0x20` sprites, `0x30` compositor, `0x40` streamed maps, `0x50` display lists, `0x60` 3D. Until a chapter defines its commands they are unknown opcodes, which the parser skips. The names in the spec are only a plan; each chapter fixes its own commands and payloads.
- **Capability bits.** `GET_INFO` says which features a build has, so a host script can ask "do you have sprites?" instead of guessing from a version number.

## 2. Resetting the parser on `CS`

Chapter 4.2's parser reads a stream. It is always somewhere inside a packet, expecting an opcode, a length or a payload byte. If one byte is lost, every byte after it is misread. The UART cannot help, since it has no frames. SPI can: a frame carries whole packets, so the start of a frame is always the start of a packet.

**The rule:** raising `CS` ends the packet in progress. Whatever was half-read is dropped, and the next byte starts a new packet.

The parser does not see `CS`. The FIFO sits between it and the SPI slave, and by the time a byte comes out, `CS` may have risen and fallen many times. There are three ways to carry the news through the FIFO:

1. **Flush the FIFO when `CS` rises.** Wrong: the FIFO may hold up to 512 bytes of good commands the engine has not run yet, such as the last packets of this very frame.
2. **Write an end-of-frame marker into the FIFO.** Correct, but each marker takes a place. A master that sends 257 one-byte frames needs 514 places, although `READY` promised that 257 bytes would fit. And when the FIFO is full the marker itself is dropped.
3. **Mark the first byte of the next frame.** This costs no FIFO places, and it is what we use.

**The frame-start bit.** Make the FIFO 9 bits wide. Bits 7:0 are the byte, as before. Bit 8, `SOF`, is 1 for a byte that must start a new packet:

- the first byte of each SPI frame that reaches the FIFO, and
- a UART byte that follows SPI bytes. The links changed hands, so the UART must not continue a packet the SPI master left unfinished.

Every other byte has `SOF` = 0. When the parser reads a byte with `SOF` set it treats it as an opcode, whatever it was expecting, and if it was in the middle of a packet it sets the `TRUNC` flag.

Why mark the first byte of a frame and not the last? The first byte is known to be first when it arrives. The last is only known to be last when `CS` rises, after it has gone into the FIFO.

**Is this the same as resetting at the `CS` rise?** Look at what a half-read packet can do while it waits: nothing. It cannot execute, reply or draw until more bytes arrive. So it makes no difference whether it is dropped at the `CS` rise or when the next byte arrives, as long as that next byte carries `SOF`. Go through the cases:

- A frame that ends exactly at a packet boundary: the parser expects an opcode anyway. `SOF` changes nothing and `TRUNC` stays clear.
- A frame cut inside a packet, then another frame: the new frame's first byte drops the old packet.
- A frame cut inside a packet, then UART bytes: the first UART byte has `SOF`, for the same effect.
- A long command (a `CLEAR`, a `FLIP` waiting for vertical blanking) still running when the next frame's first byte arrives: the byte waits in the FIFO, since the engine fetches nothing while a command runs. Completed commands are never touched.
- Empty frames and read frames put no byte into the FIFO, so they carry no mark and change nothing. A half-read packet stays half-read until the next byte, which drops it if it comes from a new SPI frame. (A UART packet in progress continues, since only bytes that enter the FIFO count.)
- A truncated `UPLOAD_ASSET` is the one case where waiting is not doing nothing: it acts on its header once five payload bytes are in and writes each data byte as it arrives. Those bytes stay written. The spec's section 4 says so, and a host just re-sends the image.

**In `gpu_link`.** Chapter 5.1's link already knows which byte is first: call the flag `r_first`, set while `CS` is high and cleared by the frame's first byte (the one that decides whether this is a read frame). And `r_src` already says whether the last byte written came from SPI. Together:

```verilog
wire [8:0] wr_data = sv ? {r_first, sd} : {r_src, rd};    // 9 bits: {SOF, byte}
```

A read frame's first byte (`0xFF`) is never written, so it cannot carry the mark. Change the FIFO to `fifo #(.WIDTH(9), ...)` and its read data to `[8:0]`. Do the same in `uart_gpu`, writing `{1'b0, rd}`, so the Phase 4 testbenches still run through it. A 512 x 9 FIFO still fits in one 18 Kbit block RAM.

**In `cmd_engine`.** The FIFO data input becomes `i_rd_data[8:0]`. In the state that consumes a byte, use the opcode case whenever `SOF` is set:

```verilog
wire       sof   = i_rd_data[8];
wire [7:0] dbyte = i_rd_data[7:0];
// in the state that consumes one byte:
if (sof && phase != 0) err[3] <= 1'b1;            // TRUNC (Section 4)
case (sof ? 2'd0 : phase)
    0: begin op <= dbyte; phase <= 1; end        // opcode
    1: ...                                        // length: unchanged
    default: ...                                  // payload, upload data included: unchanged
endcase
```

Nothing else in the parser changes. The payload counter is loaded when the length byte arrives, so an abandoned packet leaves nothing behind that the next packet could inherit. In Chapter 4.8's engine the `UPLOAD_ASSET` data bytes pass through this same state; if yours reads them somewhere else, check `SOF` there too.

**What if bytes are lost?** If the first byte of a frame reaches a full FIFO, it is dropped and `OVERFLOW` is set. Its mark is lost with it, but the next frame has its own. The host must treat `OVERFLOW` as "re-send", whatever else it sees.

## 3. A parser that cannot hang

"Hang" means the engine stops making progress although bytes are waiting. The defense is one rule: **every state has a way out that does not depend on the data.** List the states and what ends each:

| What the engine is doing | What ends it |
| --- | --- |
| waiting for a byte (opcode, length or payload) | a byte arrives. A packet left half-read is not a hang: the next frame start abandons it |
| reading a payload | the counter reaches `len`, at most 255 bytes |
| `CLEAR`, `FB_CRC`, `FILL_RECT` | a counter over at most 76,800 pixels |
| `DRAW_LINE` | at most 65,536 points (a 16-bit span) |
| `DRAW_TRI` | the bounding box is clipped to the screen, so at most 76,800 pixels |
| `BLIT` | at most 255 x 255 pixels |
| `UPLOAD_ASSET` data | one byte per FIFO byte |
| `FLIP` | vertical blanking, at most 17 ms away |
| a reply byte | room in the reply queue (or the UART) |

Two entries wait for something outside the engine. `FLIP` waits for the pixel clock's vertical blanking, which always comes. A reply waits for the **host**: if an SPI master never reads, the engine stops once the 16-byte reply queue is full, the FIFO fills and `READY` falls. Nothing is lost, and nothing is stuck for good, because a read frame needs no `READY`. That is the one place the GPU waits for the host, by design.

Two more rules complete the picture:

- **Wrong lengths are consumed, not trusted.** A packet with the wrong `len` for its opcode is read to its end and ignored. A garbage `len` of 255 swallows up to 255 bytes of whatever follows, until a frame start ends it. Without frames, the UART has no such way out.
- **A long command finishes.** The engine fetches bytes only while idle, so it reads the `SOF` bit only between commands. A frame start never interrupts a running `CLEAR` or `FLIP`.

## 4. `GET_INFO` and the error flags

A host needs two more answers: "what are you?" and "did anything go wrong?"

**`GET_INFO`** (`0x0B`, length 0) replies with the nine bytes in the spec's section 8. A fresh `gpu_link` answers:

```
47 01 0F 02 40 01 F0 00 00
G  v1 caps   320   240   errors
```

The capability word is little endian like every other field: `0x020F` is `DRAW`, `FLIP`, `TRI`, `BLIT` and `SPI`. Make the reply a function of the reply byte number:

```verilog
function [7:0] info(input [3:0] i);
    case (i)
    0: info = 8'h47;            1: info = 8'h01;
    2: info = CAPS[7:0];        3: info = CAPS[15:8];
    4: info = 8'h40;            5: info = 8'h01;      // 320
    6: info = 8'hF0;            7: info = 8'h00;      // 240
    default: info = {4'h0, err, i_overflow};
    endcase
endfunction
```

`CAPS` is a parameter of `cmd_engine`: `16'h000F` by default (what `uart_gpu` reports) and `16'h020F` from `gpu_link`. Send the bytes through the `FB_CRC` reply path, the same wait-for-busy handshake, with a byte counter that stops at 3 for `FB_CRC` and at 8 for `GET_INFO`.

**Four flags**, set by events and cleared only by `CLEAR_ERRORS`:

| Bit | Flag | Where it is set |
| --- | --- | --- |
| 0 | `OVERFLOW` | the link: a write into a full FIFO (the sticky `o_overflow` you already have) |
| 1 | `BAD_LEN` | the engine, executing a known opcode with a wrong `len` |
| 2 | `BAD_OP` | the engine, executing an unknown opcode |
| 3 | `TRUNC` | the engine, at a frame start inside a packet |

The engine keeps its three flags in `reg [3:1] err`, numbered like the flag byte. It sets `BAD_LEN` and `BAD_OP` where it already decides to skip a packet. A `case (op)` that sets `known` and `len_ok` covers both. In Chapter 4.8's engine a valid `UPLOAD_ASSET` never reaches that point, so its entry is always "not ok": only an upload with fewer than five payload bytes gets there, and it sets `BAD_LEN`. A packet of the right length whose arguments the command ignores, such as a `BLIT` of an unknown id, sets no flag.

**Sticky** means a flag stays up until cleared, so a host sees every error since its last `CLEAR_ERRORS`, not only the latest. Reading never clears a flag, and neither does the reset button. `CLEAR_ERRORS` (`0x0C`, one payload byte) clears the flags whose bit is set in the mask:

```verilog
8'h0C: if (len == 1) begin
           err <= err & ~arg[0][3:1];                       // bits 1-3: the engine's flags
           if (arg[0][0]) o_clr_ovf <= 1'b1;                // bit 0: a pulse to the link
       end
```

`OVERFLOW` lives in the link, next to the FIFO. Give the engine an input `i_overflow` to report it and an output pulse `o_clr_ovf` to clear it. In the link, a clear and a new overflow in the same clock leave the flag set:

```verilog
always @(posedge i_clk) begin
    if (clr_ovf) o_overflow <= 1'b0;
    if (wr_en && full) o_overflow <= 1'b1;        // the later assignment wins
end
```

Update `uart_gpu` the same way: connect `i_overflow` and `o_clr_ovf` to its `o_overflow` logic. The status byte does not change. It already shows `OVERFLOW`, and bits 5 and 1 stay reserved and 0, as Chapter 5.1's testbenches check. A host that wants the other flags sends `GET_INFO`.

## 5. The UART

The UART has no frames, so the hardware cannot tell where a UART packet starts and does not resynchronize it. There is no break detector and no idle timeout. A UART host must send whole packets; to get out of a bad state it can send any SPI command frame, or press reset.

A break detector is a good exercise: count how long `RX` stays low, and when it exceeds two frame times, give the next byte written into the FIFO its `SOF` bit. Check that `uart_rx` does not turn the break into a stray `0x00` byte, and that a legitimate `0x00` (nine bit times low) does not count as a break. It isn't needed here: the MCU link is SPI, and the UART stays a bring-up tool, where "press reset" is a fine answer.

## 6. The Python model

`host/gpu_model.py` follows the same rules. Replace Chapter 4.8's version; everything it did before works unchanged. What is new:

- `GET_INFO` and `CLEAR_ERRORS`, with `get_info()`, `clear_errors(mask)` and `parse_info(reply)`,
- `Gpu.errors`, the flags the engine sets, and `Gpu.overflow`, which a test sets by hand, since the model cannot know when the FIFO fills,
- `Gpu(caps=...)`: `CAPS_SPI` (`0x020F`, the default) or `CAPS_UART` (`0x000F`),
- `Gpu.feed(data, sof=False)`: `sof=True` means the data starts with a byte that has `SOF` set. A partly received packet is then dropped and `TRUNC` set; a truncated `UPLOAD_ASSET` keeps what its header and data already did. `feed(data)` alone continues the packet in progress, as before.

```python
g = Gpu()
g.feed(plot(1, 1, 7)[:5])                            # cut inside the payload
g.feed(get_info(), sof=True)                         # a new frame: the PLOT is gone, TRUNC set
```

`host/spi_test.py` changes in three small ways: its stand-in link calls `feed(tx, sof=True)`, it clears the flags first, and a final step sends `GET_INFO` and checks the magic byte and that no flag is set. `host/spi_info.py` prints the `GET_INFO` reply in words. With `--resync` it first sends a cut-off `PLOT`: the next frame should still work, with `TRUNC` set.

## 7. Simulate

**`sim/py/ref_fuzz.py`** builds the test. It runs bytes through the model and records what the GPU must do:

```sh
.venv/bin/python sim/py/ref_fuzz.py sim
```

It writes `sim/fuzz_stream.hex` (bytes and directives), `fuzz_replies.hex`, `fuzz_labels.txt`, `fuzz_fb0.hex`, `fuzz_fb1.hex` and `fuzz_pal.hex`. Commit them as before.

The stream starts with scripted rounds: a `PLOT` and a `FILL_RECT` cut after various bytes, every unknown opcode once and every known opcode with each wrong length from 0 to 15, a length byte of 255 with little behind it, empty frames, frames cut inside a byte, a `CLEAR` followed by a half packet, a truncated `UPLOAD_ASSET`, and the UART cases of Section 2 (a cut SPI frame then UART bytes, a cut UART packet then an SPI frame, a UART packet with a read frame and an empty frame in its middle). Then come 120 random rounds, each:

1. up to three items of **garbage**: frames of random bytes, headers promising more payload than they bring, valid packets with bits flipped or bytes deleted or added, unknown opcodes, wrong lengths, long `UPLOAD_ASSET` or `BLIT` claims that are cut off; some frames end with `CS` rising in the middle of a byte; some items are empty frames, read frames, or valid or cut-off packets on the UART;
2. one frame of **valid commands**: palette, plots, rectangles, lines, triangles, uploads, blits, `FLIP`, `CLEAR`, `NOP`, `CLEAR_ERRORS`, always ending with `GET_INFO` and sometimes `FB_CRC`;
3. a **fence**: the master reads replies until all of the round's have arrived.

Some garbage is, by chance, a real command. The model runs it just as the GPU must, so its effect is part of the expectation. Three things are kept out: a frame never starts with `0xFF` (that would be a read frame), UART bytes never ask for a reply (it would go to the UART), and a round is made again if its garbage would `BLIT` asset bytes that no upload wrote, since the asset RAM is undefined there.

**`tb_gpu_fuzz`** plays the stream into `gpu_link`: SPI at random clock rates with jitter, random gaps and `READY` honored, UART bytes at 2.5 Mbaud between frames. The flip acknowledge comes only during the testbench's vertical blanking, so a `FLIP` waits up to 300 µs while frames keep arriving. It checks:

- every reply byte (`FB_CRC` and `GET_INFO`, error flags included) against `fuzz_replies.hex`, and that there are no more or fewer,
- both framebuffers and the palette at the end, and that nothing writes the front buffer while a `FLIP` waits,
- the status byte of every frame (`01` in bits 7:6, no `OVERFLOW`, reserved bits 0), and that no reply goes out on the UART,
- **that nothing hangs.** If no new reply byte arrives for 40 ms of simulated time at a fence (the longest command here takes about 1.6 ms), or `READY` stays low for 50 ms, the test fails with a message containing "HANG". Every round ends in a fence, so an engine state that never ends shows up within a round. The testbench looks at nothing inside the GPU, so any correct design passes.

Then it tests the flags directly: it clears them, checks the first eight `GET_INFO` bytes (`47 01 0F 02 40 01 F0 00`) and a zero error byte, causes `BAD_OP`, `BAD_LEN` and `TRUNC`, checks that they stay set when read and that `CLEAR_ERRORS` clears exactly the bits in its mask. Finally it overflows the FIFO on purpose (a `CLEAR`, which keeps the engine busy for 1.5 ms, then 700 bytes of `NOP`s sent without looking at `READY`) and checks that `OVERFLOW` shows in `GET_INFO` and in the status byte, stays set, survives `CLEAR_ERRORS 0E` and goes away with `CLEAR_ERRORS 01`.

Expect `PASS` and a line like:

```
PASS: tb_gpu_fuzz (15463 bytes in 4511 frames and 1100 on the UART, ... cut inside a byte, ... empty, ... polls, 145 fences, 1813 reply bytes match, framebuffers and palette match, error flags checked)
```

It takes about 3 minutes with Icarus:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_gpu_fuzz.vvp sim/tb_gpu_fuzz.v
cd build/sim && vvp -n tb_gpu_fuzz.vvp
```

Add `tb_gpu_fuzz` to `TESTS` in `projects/5.1-spi/Makefile`. This chapter adds no pins and no project, so keep building there. Chapter 5.1's three testbenches and Chapters 4.2 to 4.8's (through `uart_gpu`) must still pass unchanged.

**When it fails.** Each message says where:

- *HANG* at the very first cut packet, or *reply byte N is wrong* with a garbage round in the label: the frame start does not abandon the packet. The `SOF` bit is not set, not stored, or not read, or a counter is not loaded fresh.
- *Reply byte N is wrong* in a scripted round: the label says which packet was cut and where.
- *Wrong* in the round "SPI frame cut inside a PLOT, then a PLOT on the UART": a UART byte after SPI bytes does not get `SOF`.
- *`GET_INFO` byte N is wrong*: byte order, a wrong capability value or a wrong constant. The message shows the expected byte.
- *HANG: no new reply byte for 40 ms*: an engine state waits for something that never comes. The label names the round; its last garbage item is the usual cause.
- *Flags*: the message names the step. "Flags stay set when read" fails when `GET_INFO` clears flags; "CLEAR_ERRORS 01 clears OVERFLOW" fails when the link ignores `o_clr_ovf`.
- *Framebuffer bytes wrong at the end* with all replies right: a command ran on a half packet.
- *A reply went out on the UART*: a reply followed the wrong link (Chapter 5.1's `r_src` rule).
- *`o_overflow` was high outside the overflow test*: `READY` is wrong, or the new bit costs a FIFO place.

## 8. On the board

This chapter adds no pins. Rebuild `projects/5.1-spi/` with the new `cmd_engine.v`, `gpu_link.v` and `uart_gpu.v`, program it, and run:

```sh
.venv/bin/python host/spi_test.py --model                 # no hardware: PASS
.venv/bin/python host/spi_info.py --model --resync --clear
.venv/bin/python host/spi_test.py --freq 1000000          # on the board: the Chapter 5.1 check plus a GET_INFO line
.venv/bin/python host/spi_info.py --resync --clear        # TRUNC set by the cut-off packet, then cleared
```

The reference build (yosys about 35 s, place and route about 65 s) is essentially Chapter 5.1's:

| Resource | Used | Of |
| --- | --- | --- |
| LUTs | about 5,900 | 106,400 |
| Flip-flops | about 1,600 | 106,400 |
| RAMB36E1 | 46 | 140 |
| DSP48E1 | 20 | 220 |

`clk_sys` closes at about 70 MHz against 50 required. The frame-start bit, the flags and `GET_INFO` add only a few dozen flip-flops, and the 512 x 9 FIFO stays in the same 18 Kbit block RAM. (The LUT count came out slightly lower than Chapter 5.1's; small changes move synthesis results by a few percent either way.) On the logic analyzer the only new traffic is the `GET_INFO` exchange.

## Testbench requirements

**Modules.** All ports of `gpu_link` and `uart_gpu` are as in Chapter 5.1. The new behavior is inside them:

- **`gpu_link`**: the first byte of every SPI command frame starts a packet, whatever the parser was doing, and so does a UART byte that follows SPI bytes. A frame that puts no byte into the FIFO (empty, or first byte `0xFF`) changes nothing. A truncated `UPLOAD_ASSET` leaves what the model says: header acted on once five payload bytes are in, data written as it arrives.
- `GET_INFO` and `CLEAR_ERRORS` as in `protocol.md`. `GET_INFO` replies `47 01 CL CH 40 01 F0 00 ER`, with `CL CH` = `0F 02` for `gpu_link` and `ER` the error flags.
- The flags as in `protocol.md` section 9: a frame start inside a packet sets `TRUNC`, a wrong length `BAD_LEN`, an unknown opcode `BAD_OP`, a write into a full FIFO `OVERFLOW`. They are sticky, cleared only by `CLEAR_ERRORS` with their bit in the mask, and a new event in the same clock as a clear stays set. The status byte is unchanged.
- **`uart_gpu`** gets the same engine, with `CAPS` = `0x000F` and its `o_overflow` cleared by `o_clr_ovf`; the Phase 4 testbenches must still pass through it.
- **`host/gpu_model.py`** from this chapter, `sim/py/ref_scanout.py` (Chapter 4.1) for the test image, and Chapter 4.8's `fb_test.hex` and `pal_test.hex`, as in Chapter 5.1.

**`tb_gpu_fuzz`** (about 3 minutes): as in Section 7. It reads `../../sim/fuzz_*` and `../../rtl/common/*.hex` and uses only `gpu_link`'s ports. Prints `PASS: tb_gpu_fuzz (...)`. It fails on any wrong or missing reply byte, framebuffer byte or palette entry, any bad status byte, a write to the front buffer during a `FLIP`, a reply on the UART, `o_overflow` high outside the overflow test, a hang as defined in Section 7, or any wrong flag step. `+half=NS` forces one `SCK` half-period for the whole stream (for example `vvp -n tb_gpu_fuzz.vvp +half=80`). Watchdog 3 s simulated.

**Files from this chapter:** `protocol.md` to `docs/`, `tb_gpu_fuzz.v` to `sim/`, `ref_fuzz.py` to `sim/py/`, and `gpu_model.py`, `spi_test.py` and `spi_info.py` to `host/`.

By hand:

```sh
.venv/bin/python sim/py/ref_fuzz.py sim
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_gpu_fuzz.vvp sim/tb_gpu_fuzz.v
cd build/sim && vvp -n tb_gpu_fuzz.vvp
```
